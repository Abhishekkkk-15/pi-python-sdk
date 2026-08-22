"""Typed events emitted by a headless Agent run."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    USER_MESSAGE = "user_message"
    THINKING_DELTA = "thinking_delta"
    THINKING = "thinking"
    TEXT_DELTA = "text_delta"
    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    PERMISSION_REQUEST = "permission_request"
    COMPACTION = "compaction"
    USAGE = "usage"
    ERROR = "error"
    STATUS = "status"


@dataclass
class AgentEvent:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return str(self.data.get("text") or self.data.get("content") or "")


EventCallback = Callable[[AgentEvent], None | Awaitable[None]]


class EventEmitter:
    """Fan-out for streaming consumers and optional on_event callbacks."""

    def __init__(self, on_event: Optional[EventCallback] = None) -> None:
        self._on_event = on_event
        self._buffer: list[AgentEvent] = []
        self.collect = False

    async def emit(self, event_type: EventType, **data: Any) -> AgentEvent:
        event = AgentEvent(type=event_type, data=data)
        if self.collect:
            self._buffer.append(event)
        if self._on_event is not None:
            result = self._on_event(event)
            if inspect.isawaitable(result):
                await result
        return event

    def drain(self) -> list[AgentEvent]:
        events = list(self._buffer)
        self._buffer.clear()
        return events
