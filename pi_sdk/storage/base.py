"""Abstract session persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Sequence

from pi_sdk.models import Message, Session


class SessionStore(ABC):
    """Durable store for session metadata and conversation messages."""

    @abstractmethod
    def create_session(
        self,
        *,
        title: str,
        workspace: Path,
        user_id: str | None = None,
        permissions: dict | None = None,
    ) -> Session:
        ...

    @abstractmethod
    def get_session(
        self, session_id: str, *, user_id: str | None = None
    ) -> Session | None:
        ...

    @abstractmethod
    def list_sessions(self, *, user_id: str | None = None) -> list[Session]:
        ...

    @abstractmethod
    def save_session(self, session: Session) -> None:
        """Persist metadata (usage, permissions, compaction, timestamps)."""
        ...

    @abstractmethod
    def load_messages(self, session_id: str) -> list[Message]:
        ...

    @abstractmethod
    def append_messages(
        self, session_id: str, messages: Sequence[Message]
    ) -> None:
        ...

    @abstractmethod
    def replace_messages(
        self, session_id: str, messages: Sequence[Message]
    ) -> None:
        ...

    @abstractmethod
    def delete_session(
        self, session_id: str, *, user_id: str | None = None
    ) -> bool:
        ...
