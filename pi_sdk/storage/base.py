"""Abstract session persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Sequence

from pi_sdk.models import Message, Session


class SessionStore(ABC):
    """Durable store for session metadata and conversation messages."""

    @abstractmethod
    async def create_session(
        self,
        *,
        title: str,
        workspace: Path,
        user_id: str | None = None,
        workspace_id: str | None = None,
        permissions: dict | None = None,
    ) -> Session:
        ...

    @abstractmethod
    async def get_session(
        self, session_id: str, *, user_id: str | None = None
    ) -> Session | None:
        ...

    @abstractmethod
    async def list_sessions(self, *, user_id: str | None = None) -> list[Session]:
        ...

    @abstractmethod
    async def save_session(self, session: Session) -> None:
        """Persist metadata (usage, permissions, compaction, timestamps)."""
        ...

    @abstractmethod
    async def load_messages(self, session_id: str) -> list[Message]:
        ...

    @abstractmethod
    async def append_messages(
        self, session_id: str, messages: Sequence[Message]
    ) -> None:
        ...

    @abstractmethod
    async def replace_messages(
        self, session_id: str, messages: Sequence[Message]
    ) -> None:
        ...

    @abstractmethod
    async def delete_session(
        self, session_id: str, *, user_id: str | None = None
    ) -> bool:
        ...
