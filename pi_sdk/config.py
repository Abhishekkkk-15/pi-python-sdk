"""SDK configuration — programmatic options, no CLI auth UI."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from dotenv import load_dotenv

from pi_sdk.memory import Memory

load_dotenv()

BUILTIN_PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-large-latest",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
    },
    "vertex": {
        "base_url": "us-central1",
        "default_model": "gemini-2.5-flash",
    },
}

DEFAULT_MAX_HISTORY_MESSAGES = 80
DEFAULT_INPUT_PRICE_PER_MTOK = 0.0
DEFAULT_OUTPUT_PRICE_PER_MTOK = 0.0
DEFAULT_COMPACT_AT_TOKENS = 80_000
DEFAULT_KEEP_RECENT_TOKENS = 20_000

# Process-wide Tavily key (Agent.create can set this)
_TAVILY_API_KEY: Optional[str] = None

PermissionCallback = Callable[[str, str, str], bool]
"""(tool_name, target, action_details) -> allow?"""


def estimate_cost(
    prompt_tokens: int,
    completion_tokens: int,
    input_price: float,
    output_price: float,
    cached_tokens: int = 0,
    provider: str = "openai",
) -> float:
    provider_lower = str(provider).lower()
    if (
        "anthropic" in provider_lower
        or "claude" in provider_lower
        or "deepseek" in provider_lower
    ):
        discount = 0.1
    else:
        discount = 0.5

    non_cached = max(0, prompt_tokens - cached_tokens)
    input_cost = (
        non_cached * input_price + cached_tokens * input_price * discount
    ) / 1_000_000
    output_cost = (completion_tokens * output_price) / 1_000_000
    return input_cost + output_cost


def set_tavily_api_key(key: str | None) -> None:
    global _TAVILY_API_KEY
    _TAVILY_API_KEY = (key or "").strip() or None


def get_tavily_api_key(auth: Any = None) -> str | None:
    if _TAVILY_API_KEY:
        return _TAVILY_API_KEY
    env = (os.getenv("TAVILY_API_KEY") or "").strip()
    if env:
        return env
    if isinstance(auth, dict):
        for path in (
            ("credentials", "tavily_api_key"),
            ("tavily", "api_key"),
        ):
            cur: Any = auth
            for key in path:
                if not isinstance(cur, dict):
                    cur = None
                    break
                cur = cur.get(key)
            if cur and str(cur).strip():
                return str(cur).strip()
    data = Memory.read_from_json(Memory().root / "auth.json") or {}
    if isinstance(data, dict):
        creds = data.get("credentials") if isinstance(data.get("credentials"), dict) else {}
        key = creds.get("tavily_api_key") if creds else None
        if key and str(key).strip():
            return str(key).strip()
    return None


@dataclass
class AgentOptions:
    """Options for Agent.create(...)."""

    api_key: Optional[str] = None
    api_keys: list[str] = field(default_factory=list)
    provider: str = "mistral"
    model: Optional[str] = None
    base_url: Optional[str] = None
    cwd: Optional[str] = None
    data_dir: Optional[str] = None
    tavily_api_key: Optional[str] = None
    autonomous: bool = True
    permission_callback: Optional[PermissionCallback] = None
    compaction_enabled: bool = True
    compact_at_tokens: int = DEFAULT_COMPACT_AT_TOKENS
    keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS
    max_tokens: Optional[int] = None
    reasoning_effort: Optional[str] = None
    input_price_per_mtok: float = DEFAULT_INPUT_PRICE_PER_MTOK
    output_price_per_mtok: float = DEFAULT_OUTPUT_PRICE_PER_MTOK
    max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES
    skill_names: Optional[list[str]] = None
    base_prompt: Optional[str] = None
    system_prompt_extra: Optional[str] = None
    storage: str = "disk"
    mongodb_uri: Optional[str] = None
    mongodb_db: str = "pi_sdk"
    user_id: Optional[str] = None
    # Injected SessionStore instance (not serialized); set via Agent.create(store=...)
    store: Any = None
    extra_tools: list[Any] = field(default_factory=list)
    # Builtin tool filtering (see build_builtin_registry)
    default_tools: bool = True
    enable_tools: Optional[list[str]] = None
    disable_tools: list[str] = field(default_factory=list)
    docker_container: Optional[str] = None
    docker_workdir: Optional[str] = None


@dataclass
class Config:
    api_key: Optional[str] = None
    api_keys: list[str] = field(default_factory=list)
    active_key_index: int = 0
    provider: str = "mistral"
    model: str = "mistral-large-latest"
    base_url: str = BUILTIN_PROVIDERS["mistral"]["base_url"]
    tavily_api_key: Optional[str] = None
    autonomous_risk: bool = True
    permission_callback: Optional[PermissionCallback] = None
    compaction_enabled: bool = True
    compact_at_tokens: int = DEFAULT_COMPACT_AT_TOKENS
    keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS
    max_tokens: Optional[int] = None
    reasoning_effort: Optional[str] = None
    input_price_per_mtok: float = DEFAULT_INPUT_PRICE_PER_MTOK
    output_price_per_mtok: float = DEFAULT_OUTPUT_PRICE_PER_MTOK
    max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES
    cwd: Optional[str] = None
    data_dir: Optional[str] = None
    skill_names: Optional[list[str]] = None
    base_prompt: Optional[str] = None
    system_prompt_extra: Optional[str] = None
    storage: str = "disk"
    mongodb_uri: Optional[str] = None
    mongodb_db: str = "pi_sdk"
    user_id: Optional[str] = None
    store: Any = None
    extra_tools: list[Any] = field(default_factory=list)
    default_tools: bool = True
    enable_tools: Optional[list[str]] = None
    disable_tools: list[str] = field(default_factory=list)
    docker_container: Optional[str] = None
    docker_workdir: Optional[str] = None

    @classmethod
    def from_options(cls, options: AgentOptions | None = None, **kwargs: Any) -> "Config":
        opts = options or AgentOptions()
        # kwargs override dataclass fields
        for key, value in kwargs.items():
            if hasattr(opts, key):
                setattr(opts, key, value)

        provider = (opts.provider or os.getenv("LLM_PROVIDER") or "mistral").lower()
        builtin = BUILTIN_PROVIDERS.get(provider, {})

        keys = [k.strip() for k in (opts.api_keys or []) if k and str(k).strip()]
        if opts.api_key and str(opts.api_key).strip():
            primary = str(opts.api_key).strip()
            if primary not in keys:
                keys.insert(0, primary)
        env_key = (os.getenv("LLM_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
        if env_key and env_key not in keys:
            keys.append(env_key)

        model = (
            opts.model
            or os.getenv("LLM_MODEL")
            or builtin.get("default_model")
            or "mistral-large-latest"
        )
        base_url = opts.base_url or builtin.get("base_url") or "https://api.openai.com/v1"

        if opts.tavily_api_key:
            set_tavily_api_key(opts.tavily_api_key)

        reasoning = opts.reasoning_effort
        if reasoning is not None:
            reasoning = str(reasoning).strip().lower()
            if reasoning not in ("low", "medium", "high"):
                reasoning = None

        return cls(
            api_key=keys[0] if keys else None,
            api_keys=keys,
            active_key_index=0,
            provider=provider,
            model=str(model),
            base_url=str(base_url),
            tavily_api_key=get_tavily_api_key(),
            autonomous_risk=bool(opts.autonomous),
            permission_callback=opts.permission_callback,
            compaction_enabled=bool(opts.compaction_enabled),
            compact_at_tokens=int(opts.compact_at_tokens),
            keep_recent_tokens=int(opts.keep_recent_tokens),
            max_tokens=opts.max_tokens,
            reasoning_effort=reasoning,
            input_price_per_mtok=float(opts.input_price_per_mtok),
            output_price_per_mtok=float(opts.output_price_per_mtok),
            max_history_messages=int(opts.max_history_messages),
            cwd=opts.cwd,
            data_dir=opts.data_dir,
            skill_names=opts.skill_names,
            base_prompt=opts.base_prompt,
            system_prompt_extra=opts.system_prompt_extra,
            storage=str(getattr(opts, "storage", None) or "disk"),
            mongodb_uri=getattr(opts, "mongodb_uri", None),
            mongodb_db=str(getattr(opts, "mongodb_db", None) or "pi_sdk"),
            user_id=getattr(opts, "user_id", None),
            store=getattr(opts, "store", None),
            extra_tools=list(getattr(opts, "extra_tools", None) or []),
            default_tools=bool(getattr(opts, "default_tools", True)),
            enable_tools=(
                list(opts.enable_tools)
                if getattr(opts, "enable_tools", None) is not None
                else None
            ),
            disable_tools=list(getattr(opts, "disable_tools", None) or []),
            docker_container=getattr(opts, "docker_container", None),
            docker_workdir=getattr(opts, "docker_workdir", None),
        )

    def rotate_api_key(self) -> bool:
        """Advance to the next configured API key. Returns True if rotated."""
        if len(self.api_keys) < 2:
            return False
        self.active_key_index = (self.active_key_index + 1) % len(self.api_keys)
        self.api_key = self.api_keys[self.active_key_index]
        return True

    @property
    def key_count(self) -> int:
        return len(self.api_keys)
