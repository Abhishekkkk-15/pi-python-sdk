"""In-memory conversation state backed by a SessionStore."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

from pi_sdk.models import Message, Role, Session
from pi_sdk.paths import get_data_root, get_workspace, set_data_root, set_workspace
from pi_sdk.storage.base import SessionStore
from pi_sdk.storage.disk import DiskSessionStore, read_json, read_jsonl, write_json, write_jsonl

__all__ = [
    "Memory",
    "get_data_root",
    "get_workspace",
    "set_data_root",
    "set_workspace",
]


class Memory:
    """Façade: in-RAM messages + durable SessionStore."""

    def __init__(
        self,
        store: SessionStore | None = None,
        *,
        user_id: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        self.store: SessionStore = store or DiskSessionStore()
        self.user_id = user_id
        self.workspace_id = workspace_id
        self.messages: list[Message] = []
        self.session: Session | None = None

    @property
    def root(self) -> Path:
        return get_data_root()

    async def init_session(
        self,
        title: str,
        initial_messages: list[Message] | None = None,
        *,
        workspace: Path | None = None,
    ) -> Session:
        ws = Path(workspace) if workspace is not None else get_workspace()
        session = await self.store.create_session(
            title=title,
            workspace=ws,
            user_id=self.user_id,
            workspace_id=self.workspace_id,
        )
        self.session = session
        if initial_messages:
            await self.store.replace_messages(session.id, initial_messages)
        return session

    async def load_old_sessions(self) -> list[Session]:
        return await self.store.list_sessions(user_id=self.user_id)

    async def get_session_by_id(self, session_id: str) -> Optional[Session]:
        return await self.store.get_session(session_id, user_id=self.user_id)

    async def load_session_chat(
        self,
        session_or_path: Session | Path | str | None = None,
        system_prompt: str = "",
        *,
        session_id: str | None = None,
    ) -> list[Message]:
        """Load messages into self.messages from store (or legacy disk path)."""
        sid = session_id
        if isinstance(session_or_path, Session):
            sid = session_or_path.id
        elif sid is None and session_or_path is not None:
            path = Path(session_or_path)
            old_chat = await read_jsonl(path) if path.exists() else []
            if not old_chat or old_chat[0].role != Role.SYSTEM:
                if system_prompt:
                    old_chat.insert(
                        0, Message(role=Role.SYSTEM, content=system_prompt)
                    )
            self.messages = old_chat
            return old_chat

        if not sid and self.session:
            sid = self.session.id
        if not sid:
            self.messages = []
            return []

        old_chat = await self.store.load_messages(sid)
        if not old_chat or old_chat[0].role != Role.SYSTEM:
            if system_prompt:
                old_chat.insert(0, Message(role=Role.SYSTEM, content=system_prompt))
        self.messages = old_chat
        return old_chat

    async def append_message(self, msg: Message) -> None:
        self.messages.append(msg)
        if self.session:
            await self.store.append_messages(self.session.id, [msg])

    async def replace_messages(self, messages: list[Message] | None = None) -> None:
        if messages is not None:
            self.messages = list(messages)
        if self.session:
            await self.store.replace_messages(self.session.id, self.messages)

    async def save_session(self) -> None:
        if self.session:
            await self.store.save_session(self.session)

    @staticmethod
    async def write_to_json(path: Union[str, Path], data: Any) -> None:
        await write_json(path, data)

    @staticmethod
    async def read_from_json(path: Union[str, Path]) -> Any:
        return await read_json(path)

    async def write_to_jsonl(
        self, path: Union[str, Path], data_list: list[Any], mode: str = "w"
    ) -> None:
        """Legacy path-based API; prefer append_message / replace_messages."""
        await write_jsonl(path, data_list, mode=mode)

    async def read_from_jsonl(self, path: Union[str, Path]) -> list[Message]:
        return await read_jsonl(path)
