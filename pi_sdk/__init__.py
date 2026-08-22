"""PI SDK — headless coding agent for local or cloud integration."""

from pi_sdk.agent import (
    Agent,
    AgentError,
    AuthenticationError,
    PermissionDenied,
    RunResult,
    UsageSummary,
)
from pi_sdk.config import AgentOptions, Config, BUILTIN_PROVIDERS
from pi_sdk.events import AgentEvent, EventType
from pi_sdk.models import Message, Role, Session
from pi_sdk.permissions import PermissionDecision
from pi_sdk.storage import SessionStore, create_store, DiskSessionStore
from pi_sdk.tool_registry import ToolSpec, BUILTIN_TOOL_NAMES

__version__ = "0.3.0"

__all__ = [
    "Agent",
    "AgentError",
    "AgentEvent",
    "AgentOptions",
    "AuthenticationError",
    "BUILTIN_PROVIDERS",
    "BUILTIN_TOOL_NAMES",
    "Config",
    "DiskSessionStore",
    "EventType",
    "Message",
    "PermissionDecision",
    "PermissionDenied",
    "Role",
    "RunResult",
    "Session",
    "SessionStore",
    "ToolSpec",
    "UsageSummary",
    "create_store",
    "__version__",
]
