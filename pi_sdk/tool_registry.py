"""Custom + built-in tool registration for the agent."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

ToolHandler = Callable[..., Any]


@dataclass
class ToolSpec:
    """
    One tool exposed to the LLM.

    parameters: JSON Schema object, or a shorthand properties map::

        {"city": {"type": "string", "description": "City name"}}

    which is wrapped as ``{"type": "object", "properties": ..., "required": [...]}``.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    handler: ToolHandler | None = None
    # If True, Agent.check_permission runs before the handler (autonomous still bypasses).
    require_permission: bool = True
    # Arg name used as permission "target" (default: first required/property key).
    permission_arg: str | None = None


def normalize_parameters(parameters: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize user parameters into a JSON Schema object."""
    if not parameters:
        return {"type": "object", "properties": {}, "required": []}

    if parameters.get("type") == "object" and "properties" in parameters:
        schema = dict(parameters)
        schema.setdefault("properties", {})
        if "required" not in schema:
            schema["required"] = [
                k
                for k, v in schema["properties"].items()
                if isinstance(v, dict) and v.get("required") is True
            ]
        return schema

    # Shorthand: {name: {type, description}, ...}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for key, val in parameters.items():
        if key in ("type", "properties", "required", "additionalProperties"):
            continue
        if isinstance(val, dict):
            prop = {k: v for k, v in val.items() if k != "required"}
            properties[key] = prop
            if val.get("required") is True:
                required.append(key)
        else:
            properties[key] = {"type": "string", "description": str(val)}
    return {"type": "object", "properties": properties, "required": required}


def tool_to_openai_schema(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description or spec.name,
            "parameters": normalize_parameters(spec.parameters),
        },
    }


def _call_handler(handler: ToolHandler, args: dict[str, Any]) -> str:
    """Invoke handler with kwargs filtered to its signature when possible."""
    try:
        sig = inspect.signature(handler)
        params = sig.parameters
        accepts_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        if accepts_kwargs:
            result = handler(**args)
        else:
            allowed = {
                name
                for name, p in params.items()
                if p.kind
                in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                )
            }
            filtered = {k: v for k, v in args.items() if k in allowed}
            # Fill defaults-only params that are required without default? skip
            result = handler(**filtered)
    except TypeError:
        # Fall back to full kwargs
        result = handler(**args)
    if result is None:
        return ""
    return result if isinstance(result, str) else str(result)


class ToolRegistry:
    """Registry of tools available to one Agent instance."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def add(
        self,
        name: str,
        *,
        description: str,
        parameters: dict[str, Any] | None = None,
        handler: ToolHandler | None = None,
        require_permission: bool = True,
        permission_arg: str | None = None,
        replace: bool = False,
    ) -> ToolSpec:
        name = (name or "").strip()
        if not name:
            raise ValueError("tool name is required")
        if name in self._tools and not replace:
            raise ValueError(
                f"tool {name!r} already registered; pass replace=True to overwrite"
            )
        if handler is None:
            raise ValueError(f"tool {name!r} requires a handler callable")

        spec = ToolSpec(
            name=name,
            description=(description or "").strip() or name,
            parameters=normalize_parameters(parameters),
            handler=handler,
            require_permission=require_permission,
            permission_arg=permission_arg,
        )
        self._tools[name] = spec
        return spec

    def add_spec(self, spec: ToolSpec, *, replace: bool = False) -> ToolSpec:
        if not spec.handler:
            raise ValueError(f"tool {spec.name!r} requires a handler")
        return self.add(
            spec.name,
            description=spec.description,
            parameters=spec.parameters,
            handler=spec.handler,
            require_permission=spec.require_permission,
            permission_arg=spec.permission_arg,
            replace=replace,
        )

    def remove(self, name: str) -> bool:
        return self._tools.pop(name, None) is not None

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def schemas(self) -> list[dict[str, Any]]:
        return [tool_to_openai_schema(spec) for spec in self._tools.values()]

    def descriptions(self) -> dict[str, str]:
        return {name: spec.description for name, spec in self._tools.items()}

    def has(self, name: str) -> bool:
        return name in self._tools

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        spec = self._tools.get(name)
        if not spec or not spec.handler:
            raise KeyError(name)
        return _call_handler(spec.handler, arguments)

    def permission_target(self, name: str, args: dict[str, Any]) -> str:
        spec = self._tools.get(name)
        if not spec:
            return ""
        key = spec.permission_arg
        if not key:
            params = normalize_parameters(spec.parameters)
            required = params.get("required") or []
            props = list((params.get("properties") or {}).keys())
            key = required[0] if required else (props[0] if props else None)
        if key and key in args:
            return str(args[key])
        return name


BUILTIN_TOOL_NAMES: tuple[str, ...] = (
    "read",
    "write",
    "edit",
    "bash",
    "web_search",
    "grep",
)


def build_builtin_registry(
    *,
    enable_tools: Sequence[str] | None = None,
    disable_tools: Sequence[str] | None = None,
    default_tools: bool = True,
) -> ToolRegistry:
    """
    Register default PI coding tools.

    - default_tools=False → empty registry (custom tools only)
    - enable_tools=["read", "grep"] → only those builtins (allowlist)
    - disable_tools=["bash", "write"] → all builtins except these (denylist)
    If both enable_tools and disable_tools are set, allowlist is applied first,
    then denylist subtracts from it.
    """
    from pi_sdk.tools import (
        TOOLS,
        execute_bash,
        execute_edit,
        execute_grep,
        execute_read,
        execute_web_search,
        execute_write,
    )

    handlers: dict[str, ToolHandler] = {
        "read": lambda path, offset=None, limit=None, **_: execute_read(
            path, offset=offset, limit=limit
        ),
        "write": lambda path, content="", **_: execute_write(path, content),
        "edit": lambda path, edits=None, **_: execute_edit(path, edits or []),
        "bash": lambda command, timeout=30, is_background=False, **_: execute_bash(
            command, timeout=timeout, is_background=is_background
        ),
        "web_search": lambda query, max_results=5, **_: execute_web_search(
            query, max_results=max_results
        ),
        "grep": lambda pattern, path=".", glob="", case_insensitive=False, max_results=50, **_: execute_grep(
            pattern=pattern,
            path=path or ".",
            glob=glob or "",
            case_insensitive=bool(case_insensitive),
            max_results=max_results,
        ),
    }
    permission_args = {
        "read": "path",
        "write": "path",
        "edit": "path",
        "bash": "command",
        "web_search": "query",
        "grep": "path",
    }

    registry = ToolRegistry()
    if not default_tools and enable_tools is None:
        return registry

    if enable_tools is not None:
        allowed = {str(n).strip() for n in enable_tools if str(n).strip()}
    else:
        allowed = set(BUILTIN_TOOL_NAMES)

    disabled = {str(n).strip() for n in (disable_tools or []) if str(n).strip()}
    allowed -= disabled

    unknown = allowed - set(BUILTIN_TOOL_NAMES)
    if unknown:
        raise ValueError(
            f"Unknown builtin tool(s): {sorted(unknown)}. "
            f"Valid names: {list(BUILTIN_TOOL_NAMES)}"
        )

    for item in TOOLS:
        fn = item.get("function") or {}
        name = fn.get("name")
        if not name or name not in handlers or name not in allowed:
            continue
        registry.add(
            name,
            description=fn.get("description") or name,
            parameters=fn.get("parameters") or {},
            handler=handlers[name],
            require_permission=True,
            permission_arg=permission_args.get(name),
            replace=True,
        )
    return registry


def coalesce_extra_tools(
    tools: Sequence[ToolSpec | dict[str, Any]] | None,
) -> list[ToolSpec]:
    """Normalize Agent.create(extra_tools=...) entries into ToolSpec list."""
    if not tools:
        return []
    out: list[ToolSpec] = []
    for item in tools:
        if isinstance(item, ToolSpec):
            out.append(item)
            continue
        if not isinstance(item, dict):
            raise TypeError(f"extra tool must be ToolSpec or dict, got {type(item)}")
        handler = item.get("handler")
        if handler is None:
            raise ValueError(f"extra tool {item.get('name')!r} needs handler=")
        out.append(
            ToolSpec(
                name=str(item["name"]),
                description=str(item.get("description") or item["name"]),
                parameters=item.get("parameters") or {},
                handler=handler,
                require_permission=bool(item.get("require_permission", True)),
                permission_arg=item.get("permission_arg"),
            )
        )
    return out
