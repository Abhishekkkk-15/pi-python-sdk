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

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "AgentError",
    "AgentEvent",
    "AgentOptions",
    "AuthenticationError",
    "BUILTIN_PROVIDERS",
    "Config",
    "EventType",
    "Message",
    "PermissionDecision",
    "PermissionDenied",
    "Role",
    "RunResult",
    "Session",
    "UsageSummary",
    "__version__",
]
