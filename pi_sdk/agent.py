"""Headless PI coding agent — create / run / stream / resume."""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pi_sdk import prompts
from pi_sdk.compaction import Compaction
from pi_sdk.config import BUILTIN_PROVIDERS, AgentOptions, Config, estimate_cost
from pi_sdk.events import AgentEvent, EventCallback, EventEmitter, EventType
from pi_sdk.history_stub import (
    age_out_large_payloads,
    stub_assistant_tool_call,
    tool_succeeded,
)
from pi_sdk.memory import Memory
from pi_sdk.paths import get_workspace, set_data_root, set_workspace
from pi_sdk.models import Message, Role, Session
from pi_sdk.permissions import PermissionDecision, PermissionManager
from pi_sdk.providers import create_provider
from pi_sdk.providers.base import LLMProvider, StreamHandler
from pi_sdk.skills import Skills
from pi_sdk.storage import create_store
from pi_sdk.tool_registry import (
    ToolRegistry,
    ToolSpec,
    build_builtin_registry,
    coalesce_extra_tools,
)


class AgentError(Exception):
    """Base SDK error."""


class AuthenticationError(AgentError):
    """Missing or invalid API credentials."""


class RateLimitError(AgentError):
    """LLM rate limit (HTTP 429) after retries were exhausted."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PermissionDenied(AgentError):
    """Tool execution was denied by the permission policy."""


KNOWN_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-5.4-mini": 400000,
    "gpt-5.4": 400000,
    "gpt-5-mini": 400000,
    "gpt-5": 400000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "o1": 200000,
    "o3-mini": 200000,
    "mistral-large-latest": 128000,
    "mistral-small-latest": 32768,
    "llama-3.3-70b-versatile": 128000,
    "gemini-2.5-flash": 1000000,
    "gemini-2.5-pro": 1000000,
}


def sanitize_api_messages(raw_messages: list[dict]) -> list[dict]:
    sanitized: list[dict] = []
    i = 0
    n = len(raw_messages)
    while i < n:
        msg = raw_messages[i]
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            tool_calls = msg.get("tool_calls") or []
            expected_ids = []
            for tc in tool_calls:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tc_id:
                    expected_ids.append(tc_id)
            sanitized.append(msg)
            i += 1
            tool_responses_by_id = {}
            while i < n and raw_messages[i].get("role") == "tool":
                tool_msg = raw_messages[i]
                t_id = tool_msg.get("tool_call_id")
                if t_id:
                    tool_responses_by_id[t_id] = tool_msg
                i += 1
            for t_id in expected_ids:
                if t_id in tool_responses_by_id:
                    sanitized.append(tool_responses_by_id[t_id])
                else:
                    sanitized.append(
                        {
                            "role": "tool",
                            "content": "Tool execution was interrupted.",
                            "tool_call_id": t_id,
                        }
                    )
        elif role == "tool":
            i += 1
        else:
            sanitized.append(msg)
            i += 1
    return sanitized


@dataclass
class UsageSummary:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    estimated_cost_usd: float = 0.0


@dataclass
class RunResult:
    """Outcome of a single agent.run(...) call."""

    status: str = "ok"  # ok | error | cancelled
    text: str = ""
    reasoning: str | None = None
    session_id: str | None = None
    usage: UsageSummary = field(default_factory=UsageSummary)
    events: list[AgentEvent] = field(default_factory=list)
    error: str | None = None
    messages: list[Message] = field(default_factory=list)


class _EventStreamHandler(StreamHandler):
    def __init__(self, emitter: EventEmitter) -> None:
        self.emitter = emitter
        self._thinking: list[str] = []
        self._content: list[str] = []
        self._pending: list[tuple[EventType, dict[str, Any]]] = []

    def _buffer(self, event_type: EventType, **data: Any) -> None:
        self._pending.append((event_type, data))

    async def flush(self) -> None:
        for event_type, data in self._pending:
            await self.emitter.emit(event_type, **data)
        self._pending.clear()

    def thinking_start(self) -> None:
        self._thinking.clear()

    def thinking_chunk(self, text: str) -> None:
        self._thinking.append(text)
        self._buffer(EventType.THINKING_DELTA, text=text)

    def thinking_end(self) -> None:
        full = "".join(self._thinking)
        if full:
            self._buffer(EventType.THINKING, text=full)

    def content_start(self) -> None:
        self._content.clear()

    def content_chunk(self, text: str) -> None:
        self._content.append(text)
        self._buffer(EventType.TEXT_DELTA, text=text)

    def content_end(self) -> None:
        full = "".join(self._content)
        if full:
            self._buffer(EventType.TEXT, text=full)

    def tool_args_progress(self, names: str, kb: float) -> None:
        self._buffer(
            EventType.STATUS,
            message=f"Generating arguments for {names} ({kb:.1f} KB)",
        )

    def stop_loading(self) -> None:
        return None


class Agent:
    """Programmatic coding agent (no CLI)."""

    def __init__(self, config: Config) -> None:
        self.config = config
        if config.data_dir:
            set_data_root(config.data_dir)
        if config.cwd:
            set_workspace(config.cwd)
            try:
                os.chdir(get_workspace())
            except OSError:
                pass

        self.prompt = prompts.Prompt()
        store = create_store(
            config.storage or "disk",
            data_dir=config.data_dir,
            mongodb_uri=config.mongodb_uri,
            mongodb_db=config.mongodb_db or "pi_sdk",
            store=config.store,
        )
        self.memory = Memory(
            store=store,
            user_id=config.user_id,
            workspace_id=getattr(config, "workspace_id", None),
        )
        self.llm: LLMProvider | None = self._create_provider()
        self.tools: ToolRegistry = build_builtin_registry(
            default_tools=bool(getattr(config, "default_tools", True)),
            enable_tools=getattr(config, "enable_tools", None),
            disable_tools=getattr(config, "disable_tools", None) or None,
            docker_container=getattr(config, "docker_container", None),
            docker_workdir=getattr(config, "docker_workdir", None),
        )
        for spec in coalesce_extra_tools(getattr(config, "extra_tools", None)):
            self.tools.add_spec(spec, replace=True)
        self.manual_skill_names: Optional[list[str]] = config.skill_names
        self._pending_prompt_tokens = 0
        self._pending_completion_tokens = 0
        self._pending_total_tokens = 0
        self._pending_cached_tokens = 0
        self._emitter = EventEmitter()
        self.current_session: Session | None = None

        sys_prompt = self._build_system_prompt(cwd=str(get_workspace()))
        self.memory.messages = [Message(role=Role.SYSTEM, content=sys_prompt)]

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        api_key: str | None = None,
        provider: str = "mistral",
        model: str | None = None,
        base_url: str | None = None,
        cwd: str | Path | None = None,
        data_dir: str | Path | None = None,
        tavily_api_key: str | None = None,
        autonomous: bool = True,
        permission_callback: Any = None,
        on_event: EventCallback | None = None,
        storage: str = "disk",
        mongodb_uri: str | None = None,
        mongodb_db: str = "pi_sdk",
        user_id: str | None = None,
        workspace_id: str | None = None,
        store: Any = None,
        extra_tools: list[Any] | None = None,
        default_tools: bool = True,
        enable_tools: list[str] | None = None,
        disable_tools: list[str] | None = None,
        docker_container: str | None = None,
        docker_workdir: str | None = None,
        max_retries: int = 3,
        retry_on_rate_limit: bool = True,
        base_prompt: str | None = None,
        **kwargs: Any,
    ) -> "Agent":
        """
        Create a configured agent.

        Builtin tools (read/write/edit/bash/grep/web_search) can be filtered::

            Agent.create(..., disable_tools=["bash", "write"])
            Agent.create(..., enable_tools=["read", "grep"])
            Agent.create(..., default_tools=False)  # custom tools only

        Override only the opening identity paragraph (tools/guidelines unchanged)::

            Agent.create(
                ...,
                base_prompt=(
                    "You are a senior security reviewer. "
                    "You help users by reading files, executing commands, editing code, and writing new files."
                ),
            )
        """
        options = AgentOptions(
            api_key=api_key,
            provider=provider,
            model=model,
            base_url=base_url,
            cwd=str(cwd) if cwd is not None else None,
            data_dir=str(data_dir) if data_dir is not None else None,
            tavily_api_key=tavily_api_key,
            autonomous=autonomous,
            permission_callback=permission_callback,
            storage=storage,
            mongodb_uri=mongodb_uri,
            mongodb_db=mongodb_db,
            user_id=user_id,
            workspace_id=workspace_id,
            store=store,
            extra_tools=list(extra_tools or []),
            default_tools=default_tools,
            enable_tools=list(enable_tools) if enable_tools is not None else None,
            disable_tools=list(disable_tools or []),
            docker_container=docker_container,
            docker_workdir=docker_workdir,
            max_retries=max_retries,
            retry_on_rate_limit=retry_on_rate_limit,
            base_prompt=base_prompt,
            **{
                k: v
                for k, v in kwargs.items()
                if k
                in {
                    "compaction_enabled",
                    "compact_at_tokens",
                    "keep_recent_tokens",
                    "max_tokens",
                    "reasoning_effort",
                    "input_price_per_mtok",
                    "output_price_per_mtok",
                    "max_history_messages",
                    "skill_names",
                    "base_prompt",
                    "system_prompt_extra",
                }
            },
        )
        agent = cls(Config.from_options(options))
        if on_event is not None:
            agent._emitter._on_event = on_event
        if not agent.config.api_key and agent.config.provider != "vertex":
            raise AuthenticationError(
                "No API key configured. Pass api_key= to Agent.create "
                "or set LLM_KEY / OPENAI_API_KEY."
            )
        return agent

    def on_event(self, callback: EventCallback | None) -> None:
        self._emitter._on_event = callback

    # ------------------------------------------------------------------
    # Custom tools
    # ------------------------------------------------------------------

    async def add_tool(
        self,
        name: str,
        *,
        description: str,
        parameters: dict[str, Any] | None = None,
        handler: Any,
        require_permission: bool = True,
        permission_arg: str | None = None,
        replace: bool = False,
        update_system_prompt: bool = True,
    ) -> ToolSpec:
        """
        Register an extra tool for this agent (name, description, JSON Schema params, handler).

        Example::

            agent.add_tool(
                name="get_weather",
                description="Return current weather for a city",
                parameters={
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                    },
                    "required": ["city"],
                },
                handler=lambda city: f"Sunny in {city}",
            )

        ``parameters`` may also be a shorthand properties map::

            {"city": {"type": "string", "description": "City name"}}
        """
        spec = self.tools.add(
            name,
            description=description,
            parameters=parameters,
            handler=handler,
            require_permission=require_permission,
            permission_arg=permission_arg,
            replace=replace,
        )
        if update_system_prompt:
            await self._sync_system_prompt_tools()
        return spec

    async def remove_tool(self, name: str, *, update_system_prompt: bool = True) -> bool:
        """Unregister a tool by name. Returns True if it existed."""
        removed = self.tools.remove(name)
        if removed and update_system_prompt:
            await self._sync_system_prompt_tools()
        return removed

    async def disable_tools(
        self,
        *names: str,
        update_system_prompt: bool = True,
    ) -> list[str]:
        """
        Remove one or more tools from this agent (builtins or custom).

        Example::

            agent.disable_tools("bash", "write", "web_search")
        """
        removed: list[str] = []
        for name in names:
            if self.tools.remove(str(name).strip()):
                removed.append(str(name).strip())
        if removed and update_system_prompt:
            await self._sync_system_prompt_tools()
        return removed

    def list_tools(self) -> list[str]:
        """Return registered tool names (built-ins + custom)."""
        return self.tools.names()

    def _build_system_prompt(
        self,
        *,
        cwd: str | None = None,
        active_skills: dict[str, str] | None = None,
    ) -> str:
        names = self.tools.names()
        snippets = self.tools.descriptions()
        prompt = self.prompt.get_system_prompt(
            active_skills=active_skills,
            cwd=cwd,
            selected_tools=names,
            tool_snippets=snippets,
            base_prompt=self.config.base_prompt,
        )
        if self.config.system_prompt_extra:
            prompt = f"{prompt}\n\n{self.config.system_prompt_extra}"
        return prompt

    async def _sync_system_prompt_tools(self) -> None:
        workspace = (
            str(self.memory.session.workspace)
            if self.memory.session
            else str(get_workspace())
        )
        # Preserve active skills block by re-reading current skill names if any
        active = None
        if self.manual_skill_names:
            from pi_sdk.skills import Skills

            filtered = [
                n
                for n in self.manual_skill_names
                if await Skills.exists(n)
            ]
            active = await Skills.load_many(filtered) if filtered else None
            if not active:
                active = None
        sys_prompt = self._build_system_prompt(cwd=workspace, active_skills=active)
        if self.memory.messages and self.memory.messages[0].role == Role.SYSTEM:
            self.memory.messages[0].content = sys_prompt
            if self.memory.session:
                await self.memory.replace_messages()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send(self, prompt: str, *, collect_events: bool = False) -> RunResult:
        """Alias for run()."""
        return await self.run(prompt, collect_events=collect_events)

    async def run(self, prompt: str, *, collect_events: bool = False) -> RunResult:
        """Run one user turn to completion (tool loop included)."""
        self._emitter.collect = collect_events
        self._emitter.drain()
        try:
            choice = await self._chat(prompt)
            session = self.memory.session
            usage = UsageSummary()
            if session:
                usage = UsageSummary(
                    prompt_tokens=session.prompt_tokens,
                    completion_tokens=session.completion_tokens,
                    total_tokens=session.total_tokens,
                    cached_tokens=session.cached_tokens,
                    estimated_cost_usd=session.estimated_cost_usd,
                )
            if choice is None:
                result = RunResult(
                    status="error",
                    text="",
                    session_id=session.id if session else None,
                    usage=usage,
                    events=self._emitter.drain() if collect_events else [],
                    error="Run failed or returned no assistant message",
                    messages=list(self.memory.messages),
                )
                await self._emitter.emit(
                    EventType.RUN_FAILED,
                    error=result.error,
                    session_id=result.session_id,
                )
                return result

            text = (choice.message.content or "") if choice else ""
            reasoning = getattr(choice.message, "reasoning_content", None) if choice else None
            result = RunResult(
                status="ok",
                text=text,
                reasoning=reasoning,
                session_id=session.id if session else None,
                usage=usage,
                events=self._emitter.drain() if collect_events else [],
                messages=list(self.memory.messages),
            )
            await self._emitter.emit(
                EventType.RUN_COMPLETED,
                text=result.text,
                session_id=result.session_id,
            )
            return result
        except RateLimitError:
            await self._emitter.emit(EventType.RUN_FAILED, error="Rate limit exceeded")
            raise
        except Exception as exc:
            await self._emitter.emit(EventType.RUN_FAILED, error=str(exc))
            session = self.memory.session
            return RunResult(
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                session_id=session.id if session else None,
                events=self._emitter.drain() if collect_events else [],
                messages=list(self.memory.messages),
            )
        finally:
            self._emitter.collect = False

    async def stream(self, prompt: str) -> AsyncIterator[AgentEvent]:
        """
        Yield events while running a turn.

        Events are buffered via an internal collector and flushed after the
        run completes (providers stream deltas through on_event during the run
        when you also set Agent.on_event / create(on_event=...)).

        For live streaming, prefer:
            Agent.create(..., on_event=handler)
            await agent.run(prompt)
        or pass on_event and iterate collected events with collect_events=True.
        """
        collected: list[AgentEvent] = []

        def _capture(event: AgentEvent) -> None:
            collected.append(event)
            prev = getattr(self, "_user_on_event", None)
            if prev:
                prev(event)

        prev_cb = self._emitter._on_event
        self._user_on_event = prev_cb
        self._emitter._on_event = _capture
        try:
            await self.run(prompt, collect_events=False)
            for event in collected:
                yield event
        finally:
            self._emitter._on_event = prev_cb
            self._user_on_event = None

    async def resume(self, session_id: str) -> "Agent":
        """Load an existing session into this agent and return self."""
        session = await self.memory.get_session_by_id(session_id)
        if not session:
            raise AgentError(f"Session not found: {session_id}")
        set_workspace(session.workspace)
        self.memory.session = session
        self.current_session = session
        # Keep Memory default in sync so new sessions from this agent reuse it
        if session.workspace_id is not None:
            self.memory.workspace_id = session.workspace_id
        sys_prompt = self._build_system_prompt(cwd=str(session.workspace))
        await self.memory.load_session_chat(session, system_prompt=sys_prompt)
        return self

    async def set_workspace_id(self, workspace_id: str | None) -> None:
        """
        Attach (or clear) an app-owned workspace id on the active session and persist it.

        Also updates the default used for future ``new_session`` / first ``run`` creates.
        """
        self.memory.workspace_id = workspace_id
        if self.config is not None:
            self.config.workspace_id = workspace_id
        session = self.memory.session
        if session is None:
            return
        session.workspace_id = workspace_id
        await self.memory.save_session()

    async def new_session(self, title: str = "session") -> Session:
        """Start a fresh session (keeps config / provider)."""
        self.reset_conversation()
        session = await self.memory.init_session(
            title,
            initial_messages=self.memory.messages,
            workspace=get_workspace(),
        )
        self.current_session = session
        return session

    def reset_conversation(self) -> None:
        self.memory.session = None
        self.current_session = None
        self._pending_prompt_tokens = 0
        self._pending_completion_tokens = 0
        self._pending_total_tokens = 0
        self._pending_cached_tokens = 0
        sys_prompt = self._build_system_prompt(cwd=str(get_workspace()))
        self.memory.messages = [Message(role=Role.SYSTEM, content=sys_prompt)]

    async def list_sessions(self) -> list[Session]:
        return await self.memory.load_old_sessions()

    @property
    def session_id(self) -> str | None:
        return self.memory.session.id if self.memory.session else None

    @property
    def messages(self) -> list[Message]:
        return list(self.memory.messages)

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    async def apply_active_skills(self, skill_names: list[str]) -> None:
        workspace = (
            self.memory.session.workspace
            if self.memory.session
            else get_workspace()
        )
        active = await Skills.load_many(skill_names) if skill_names else None
        sys_prompt = self._build_system_prompt(
            cwd=str(workspace),
            active_skills=active or None,
        )
        if self.memory.messages and self.memory.messages[0].role == Role.SYSTEM:
            self.memory.messages[0].content = sys_prompt
            if self.memory.session:
                await self.memory.replace_messages()

    async def select_relevant_skills(self, user_query: str) -> list[str]:
        available = await Skills.names()
        if not available:
            return []
        prompt_str = (
            f"You are a skill selection system for an AI coding assistant.\n"
            f'User Task: "{user_query}"\n\n'
            f"Available Skills: {available}\n\n"
            f"Select which skill names from the Available Skills list are relevant.\n"
            f"Max 3 skills. Return ONLY a JSON array, e.g. [\"react\"]. "
            f"If none, return []."
        )
        try:
            response = await self._create_completion(
                messages=[{"role": "user", "content": prompt_str}],
                use_tools=False,
                stream=False,
            )
            await self._record_usage(response)
            content = (response.choices[0].message.content or "").strip()
            match = re.search(r"\[.*?\]", content, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    return [s for s in parsed if isinstance(s, str) and s in available]
            return []
        except Exception:
            q_lower = user_query.lower()
            return [s for s in available if s.lower() in q_lower]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _create_provider(self) -> LLMProvider | None:
        endpoint = (
            self.config.base_url
            or BUILTIN_PROVIDERS.get(self.config.provider, {}).get("base_url")
            or "https://api.openai.com/v1"
        )
        return create_provider(
            name=self.config.provider,
            api_key=self.config.api_key,
            base_url=endpoint,
        )

    @property
    def model_name(self) -> str:
        return str(self.config.model)

    def get_model_context_window(self, model_name: str | None = None) -> int:
        target = (model_name or self.model_name).strip().lower()
        if target in KNOWN_MODEL_CONTEXT_WINDOWS:
            return KNOWN_MODEL_CONTEXT_WINDOWS[target]
        for key in sorted(KNOWN_MODEL_CONTEXT_WINDOWS.keys(), key=len, reverse=True):
            if key in target or target in key:
                return KNOWN_MODEL_CONTEXT_WINDOWS[key]
        m_match = re.search(r"(\d+)\s*m\b", target)
        if m_match:
            return int(m_match.group(1)) * 1_000_000
        k_match = re.search(r"(\d+)\s*k\b", target)
        if k_match:
            return int(k_match.group(1)) * 1_000
        return 128000

    async def _append_message(self, msg: Message) -> None:
        await self.memory.append_message(msg)

    async def _rewrite_session_history(self) -> None:
        if not self.memory.session:
            return
        await self.memory.replace_messages()

    async def _persist_session_usage(self) -> None:
        await self.memory.save_session()

    async def _flush_pending_usage(self) -> None:
        session = self.memory.session
        if not session:
            return
        if not (
            self._pending_total_tokens
            or self._pending_prompt_tokens
            or self._pending_completion_tokens
            or self._pending_cached_tokens
        ):
            return
        session.prompt_tokens += self._pending_prompt_tokens
        session.completion_tokens += self._pending_completion_tokens
        session.total_tokens += self._pending_total_tokens
        session.cached_tokens += self._pending_cached_tokens
        session.estimated_cost_usd += estimate_cost(
            self._pending_prompt_tokens,
            self._pending_completion_tokens,
            self.config.input_price_per_mtok,
            self.config.output_price_per_mtok,
            self._pending_cached_tokens,
            self.config.provider,
        )
        self._pending_prompt_tokens = 0
        self._pending_completion_tokens = 0
        self._pending_total_tokens = 0
        self._pending_cached_tokens = 0
        await self._persist_session_usage()

    async def _record_usage(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        if isinstance(usage, dict):
            prompt = int(usage.get("prompt_tokens") or 0)
            completion = int(usage.get("completion_tokens") or 0)
            total = int(usage.get("total_tokens") or (prompt + completion))
            cached = int(usage.get("cached_tokens") or 0)
        else:
            prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion = int(getattr(usage, "completion_tokens", 0) or 0)
            total = int(getattr(usage, "total_tokens", 0) or (prompt + completion))
            cached = int(getattr(usage, "cached_tokens", 0) or 0)

        if prompt == 0 and completion == 0 and total == 0 and cached == 0:
            return

        session = self.memory.session
        if session is None:
            self._pending_prompt_tokens += prompt
            self._pending_completion_tokens += completion
            self._pending_total_tokens += total
            self._pending_cached_tokens += cached
            return

        session.prompt_tokens += prompt
        session.completion_tokens += completion
        session.total_tokens += total
        session.cached_tokens += cached
        session.estimated_cost_usd += estimate_cost(
            prompt,
            completion,
            self.config.input_price_per_mtok,
            self.config.output_price_per_mtok,
            cached,
            self.config.provider,
        )
        await self._persist_session_usage()
        await self._emitter.emit(
            EventType.USAGE,
            prompt_tokens=session.prompt_tokens,
            completion_tokens=session.completion_tokens,
            total_tokens=session.total_tokens,
            estimated_cost_usd=session.estimated_cost_usd,
        )

    def _compaction(self) -> Compaction:
        return Compaction(
            compact_at_tokens=self.config.compact_at_tokens,
            keep_recent_tokens=self.config.keep_recent_tokens,
            provider=self.config.provider,
        )

    def _build_api_messages(self) -> list[dict]:
        comp = self._compaction()
        working = comp.working_messages(self.memory.messages, self.memory.session)
        return sanitize_api_messages([m.to_dict() for m in working])

    async def run_compaction(self, *, force: bool = False) -> str:
        session = self.memory.session
        if not session:
            return "No active session."
        if not force and not self.config.compaction_enabled:
            return "Compaction is disabled."
        comp = self._compaction()
        if not force and not comp.should_compact(self.memory.messages, session):
            return "Below threshold — nothing to compact."
        planned = comp.plan_segment(self.memory.messages, session)
        if not planned:
            return "Nothing new to fold."
        segment, new_until, keep_used = planned
        await self._emitter.emit(
            EventType.COMPACTION,
            message=f"Compacting {len(segment)} message(s)...",
        )
        prompt = comp.build_prompt(session.compaction_summary, segment)
        try:
            response = await self._create_completion(
                messages=[{"role": "user", "content": prompt}],
                use_tools=False,
                stream=False,
            )
            await self._record_usage(response)
            summary = (response.choices[0].message.content or "").strip()
        except Exception as e:
            return f"Compaction failed: {e}"
        if not summary:
            return "Summarizer returned empty text."
        session.compaction_summary = summary
        session.compacted_until = new_until
        await self._persist_session_usage()
        msg = (
            f"Compacted through message {new_until} "
            f"({len(segment)} folded; keep={keep_used})."
        )
        await self._emitter.emit(EventType.COMPACTION, message=msg)
        return msg

    async def _create_completion(
        self,
        messages: list[dict],
        use_tools: bool = True,
        stream: bool = True,
    ) -> Any:
        import asyncio

        from pi_sdk.retry import (
            is_rate_limit_error,
            is_retryable_error,
            retry_after_seconds,
        )

        def _estimate_usage(msgs: list[dict], completion_text: str):
            from pi_sdk.tokenizer import count_messages

            msg_objs = [
                Message(
                    role=Role.from_val(m.get("role")),
                    content=m.get("content") or "",
                    name=m.get("name"),
                    tool_calls=m.get("tool_calls"),
                    tool_call_id=m.get("tool_call_id"),
                )
                for m in msgs
            ]
            _, prompt_tokens, _ = count_messages(
                msg_objs, provider=self.config.provider
            )
            _, completion_tokens, _ = count_messages(
                [Message(role=Role.ASSISTANT, content=completion_text)],
                provider=self.config.provider,
            )

            class EstimatedUsage:
                def __init__(self, prompt: int, completion: int) -> None:
                    self.prompt_tokens = prompt
                    self.completion_tokens = completion
                    self.total_tokens = prompt + completion
                    self.prompt_tokens_details = None
                    self.cache_read_input_tokens = 0

            return EstimatedUsage(prompt_tokens, completion_tokens)

        async def _run_once() -> tuple[Any, Optional[BaseException]]:
            try:
                if not self.llm:
                    return None, AuthenticationError("No LLM client configured.")
                handler = _EventStreamHandler(self._emitter) if stream else None
                response = await self.llm.complete(
                    messages,
                    model=self.model_name,
                    tools=self.tools.schemas() if use_tools else None,
                    max_tokens=self.config.max_tokens,
                    reasoning_effort=self.config.reasoning_effort,
                    stream_handler=handler,
                    count_usage=_estimate_usage,
                )
                if handler is not None:
                    await handler.flush()
                return response, None
            except Exception as exc:
                return None, exc

        max_attempts = 1
        if self.config.retry_on_rate_limit:
            max_attempts = 1 + max(0, int(self.config.max_retries))

        last_error: BaseException | None = None
        for attempt in range(max_attempts):
            response, error = await _run_once()
            if error is None:
                return response

            last_error = error
            if not self.config.retry_on_rate_limit or not is_retryable_error(error):
                raise error

            if attempt >= max_attempts - 1:
                if is_rate_limit_error(error):
                    raise RateLimitError(
                        str(error),
                        retry_after=retry_after_seconds(error, attempt),
                    ) from error
                raise error

            delay = retry_after_seconds(error, attempt)
            await self._emitter.emit(
                EventType.STATUS,
                message=(
                    f"Rate limited; retrying in {delay:.1f}s "
                    f"(attempt {attempt + 2}/{max_attempts})..."
                ),
            )
            await asyncio.sleep(delay)

        if last_error is not None:
            raise last_error
        raise RuntimeError("Completion failed without an error")

    async def _ensure_session(self, user_query: str) -> Session:
        if self.memory.session is not None:
            return self.memory.session
        session = await self.memory.init_session(
            user_query[:80] or "session",
            initial_messages=self.memory.messages,
            workspace=get_workspace(),
        )
        self.current_session = session
        if self.memory.messages and self.memory.messages[0].role == Role.SYSTEM:
            self.memory.messages[0].content = self._build_system_prompt(
                cwd=str(session.workspace)
            )
            await self.memory.replace_messages()
        await self._flush_pending_usage()
        return session

    async def _chat(self, user_query: str) -> Any:
        session = await self._ensure_session(user_query)
        await self._emitter.emit(
            EventType.RUN_STARTED,
            prompt=user_query,
            session_id=session.id,
        )
        await self._emitter.emit(EventType.USER_MESSAGE, text=user_query)

        available_skills = await Skills.names()
        if available_skills:
            if self.manual_skill_names is not None:
                selected = [n for n in self.manual_skill_names if n in available_skills]
            else:
                selected = await self.select_relevant_skills(user_query)
            await self.apply_active_skills(selected)

        await self._append_message(Message(role=Role.USER, content=user_query))

        while True:
            if self.config.compaction_enabled:
                await self.run_compaction(force=False)
            api_messages = self._build_api_messages()
            try:
                res = await self._create_completion(api_messages, use_tools=True, stream=True)
            except Exception as e:
                detail = f"{type(e).__name__}: {e}"
                await self._emitter.emit(EventType.ERROR, error=detail)
                await self._append_message(
                    Message(role=Role.ASSISTANT, content=f"[LLM Error] {detail}")
                )
                return None

            if not res or not res.choices:
                await self._emitter.emit(EventType.ERROR, error="LLM returned no choices")
                return None

            await self._record_usage(res)
            llm_res = res.choices[0]

            reasoning = getattr(llm_res.message, "reasoning_content", None)
            if reasoning and not getattr(llm_res.message, "already_printed", False):
                await self._emitter.emit(EventType.THINKING, text=reasoning)

            if (
                llm_res.message.tool_calls
                and llm_res.message.content
                and not getattr(llm_res.message, "already_printed", False)
            ):
                await self._emitter.emit(EventType.TEXT, text=llm_res.message.content)

            tool_calls_raw = llm_res.message.tool_calls
            tool_calls_dicts = (
                [tc.model_dump() for tc in tool_calls_raw] if tool_calls_raw else None
            )
            chat_msg = Message(
                role=Role.ASSISTANT,
                content=llm_res.message.content or "",
                tool_calls=tool_calls_dicts,
                reasoning_content=reasoning,
            )
            await self._append_message(chat_msg)

            if not llm_res.message.tool_calls:
                return llm_res

            history_dirty = False
            for tool in llm_res.message.tool_calls:
                tool_name = tool.function.name
                tool_arguments = tool.function.arguments
                await self._emitter.emit(
                    EventType.TOOL_CALL,
                    name=tool_name,
                    arguments=tool_arguments,
                    id=getattr(tool, "id", None),
                )
                try:
                    fn_output = await self.dispatch_tool_call(tool_name, tool_arguments)
                except Exception as e:
                    fn_output = f"Error executing tool {tool_name}: {e}"
                    await self._emitter.emit(
                        EventType.ERROR, error=fn_output, title="Tool Error"
                    )
                await self._emitter.emit(
                    EventType.TOOL_RESULT,
                    name=tool_name,
                    content=fn_output,
                    id=getattr(tool, "id", None),
                )

                if tool_name in ("write", "edit") and tool_succeeded(fn_output):
                    if stub_assistant_tool_call(
                        chat_msg,
                        tool_call_id=getattr(tool, "id", None),
                        tool_name=tool_name,
                    ):
                        history_dirty = True

                await self._append_message(
                    Message(
                        role=Role.TOOL,
                        name=tool_name,
                        content=fn_output,
                        tool_call_id=tool.id,
                    )
                )

            if age_out_large_payloads(self.memory.messages, keep_recent=16):
                history_dirty = True
            if history_dirty:
                await self._rewrite_session_history()

    async def check_permission(self, tool_name: str, target: str, action_details: str) -> bool:
        if PermissionManager.check_permission(
            self.memory.session,
            tool_name,
            target,
            self.config.autonomous_risk,
        ):
            return True

        cb = self.config.permission_callback
        if cb is not None:
            await self._emitter.emit(
                EventType.PERMISSION_REQUEST,
                tool=tool_name,
                target=target,
                details=action_details,
            )
            allowed = await PermissionManager.resolve_callback(
                cb, tool_name, target, action_details
            )
            if allowed and self.memory.session:
                # Treat callback approval as allow-once (caller can persist via session)
                return True
            return allowed

        # Headless default without callback: deny (safe for cloud integrations)
        await self._emitter.emit(
            EventType.PERMISSION_REQUEST,
            tool=tool_name,
            target=target,
            details=action_details,
            denied=True,
        )
        return False

    async def grant_permission(
        self,
        grant_type: str,
        tool_name: str,
        target: str,
    ) -> None:
        """Persist a permission grant on the active session."""
        if not self.memory.session:
            return
        await PermissionManager.save_permission_grant(
            self.memory, self.memory.session, grant_type, tool_name, target
        )

    async def dispatch_tool_call(self, tool_name: str, function_arguments: str) -> str:
        try:
            args = json.loads(function_arguments) if function_arguments else {}
        except json.JSONDecodeError:
            return f"Error: invalid tool arguments JSON for {tool_name}"
        if not isinstance(args, dict):
            return f"Error: tool arguments must be a JSON object for {tool_name}"

        if not self.tools.has(tool_name):
            return f"Unknown tool: {tool_name}"

        spec = self.tools.get(tool_name)
        assert spec is not None
        if spec.require_permission:
            target = self.tools.permission_target(tool_name, args)
            details = f"Agent wants to run {tool_name}: {target or args}"
            if not await self.check_permission(tool_name, target or tool_name, details):
                return "User permission denied"

        try:
            return await self.tools.dispatch(tool_name, args)
        except Exception as e:
            return f"Error executing tool {tool_name}: {e}"


# Re-export permission decision constants for SDK users
__all__ = [
    "Agent",
    "AgentError",
    "AuthenticationError",
    "RateLimitError",
    "PermissionDenied",
    "PermissionDecision",
    "RunResult",
    "ToolSpec",
    "UsageSummary",
]
