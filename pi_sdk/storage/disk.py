"""Disk-backed session store (metadata.json + conversation_history.jsonl)."""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from pi_sdk.models import Message, Role, Session
from pi_sdk.paths import get_data_root, get_workspace
from pi_sdk.storage.base import SessionStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_chat_id() -> str:
    return uuid.uuid4().hex


def write_json(path: Path | str, data: Any) -> None:
    file_path = Path(path)
    payload = asdict(data) if is_dataclass(data) else data  # type: ignore
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=4, default=str)
    except TypeError as e:
        raise TypeError(
            f"Failed to serialize data for {file_path.name}: {e}"
        ) from e
    except OSError as e:
        raise RuntimeError(f"Could not write to {file_path}: {e}") from e


def read_json(path: Path | str) -> Any:
    file_path = Path(path)
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _default_serializer(obj: Any) -> Any:
    if isinstance(obj, Role):
        return obj.value
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)


def write_jsonl(path: Path | str, data_list: Sequence[Any], mode: str = "w") -> None:
    with open(path, mode, encoding="utf-8") as file:
        for item in data_list:
            if hasattr(item, "to_dict"):
                payload = item.to_dict()
            elif is_dataclass(item):
                payload = asdict(item)
            else:
                payload = item
            file.write(json.dumps(payload, default=_default_serializer) + "\n")


def read_jsonl(path: Path | str) -> list[Message]:
    messages: list[Message] = []
    file_path = Path(path)
    if not file_path.exists():
        return messages
    with open(file_path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            messages.append(
                Message(
                    role=Role.from_val(data.get("role", "system")),
                    content=data.get("content", ""),
                    name=data.get("name", None),
                    tool_calls=data.get("tool_calls", None),
                    tool_call_id=data.get("tool_call_id", None),
                    reasoning_content=data.get("reasoning_content", None),
                )
            )
    return messages


def session_from_metadata(data: dict, folder: Path) -> Session:
    return Session(
        id=data.get("id", folder.name),
        title=data.get("title", folder.name),
        workspace=Path(data["workspace"]) if "workspace" in data else get_workspace(),
        history_path=Path(data["history_path"])
        if data.get("history_path")
        else folder / "conversation_history.jsonl",
        permissions=data.get(
            "permissions",
            {
                "allow_all": False,
                "allowed_tools": [],
                "allowed_targets": {},
            },
        ),
        prompt_tokens=int(data.get("prompt_tokens", 0) or 0),
        completion_tokens=int(data.get("completion_tokens", 0) or 0),
        total_tokens=int(data.get("total_tokens", 0) or 0),
        cached_tokens=int(data.get("cached_tokens", 0) or 0),
        estimated_cost_usd=float(data.get("estimated_cost_usd", 0.0) or 0.0),
        compaction_summary=str(data.get("compaction_summary", "") or ""),
        compacted_until=int(data.get("compacted_until", 0) or 0),
        user_id=data.get("user_id"),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
    )


class DiskSessionStore(SessionStore):
    def __init__(self, data_root: Path | None = None) -> None:
        self._root = Path(data_root) if data_root is not None else None

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else get_data_root()

    def _session_dir(self, session_id: str) -> Path:
        return self.root / session_id

    def _history_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "conversation_history.jsonl"

    def _metadata_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "metadata.json"

    def create_session(
        self,
        *,
        title: str,
        workspace: Path,
        user_id: str | None = None,
        permissions: dict | None = None,
    ) -> Session:
        sid = generate_chat_id()
        history_dir = self._session_dir(sid)
        history_dir.mkdir(parents=True, exist_ok=True)
        conversation_jsonl = self._history_path(sid)
        conversation_jsonl.touch()
        now = _utc_now()
        session = Session(
            id=sid,
            title=title,
            workspace=Path(workspace),
            history_path=conversation_jsonl,
            permissions=permissions
            or {
                "allow_all": False,
                "allowed_tools": [],
                "allowed_targets": {},
            },
            user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        write_json(self._metadata_path(sid), session)
        return session

    def get_session(
        self, session_id: str, *, user_id: str | None = None
    ) -> Session | None:
        meta_path = self._metadata_path(session_id)
        if not meta_path.exists():
            return None
        data = read_json(meta_path)
        if not isinstance(data, dict):
            return None
        session = session_from_metadata(data, self._session_dir(session_id))
        if user_id is not None and session.user_id != user_id:
            return None
        return session

    def list_sessions(self, *, user_id: str | None = None) -> list[Session]:
        memory_path = self.root
        if not memory_path.exists():
            return []
        sessions: list[Session] = []
        for folder in memory_path.iterdir():
            if not folder.is_dir():
                continue
            metadata_file = folder / "metadata.json"
            if not metadata_file.exists():
                continue
            try:
                data = read_json(metadata_file)
                if not isinstance(data, dict):
                    continue
                session = session_from_metadata(data, folder)
                if user_id is not None and session.user_id != user_id:
                    continue
                sessions.append(session)
            except (KeyError, OSError, TypeError, ValueError):
                continue
        return sessions

    def save_session(self, session: Session) -> None:
        session.updated_at = _utc_now()
        if session.history_path is None:
            session.history_path = self._history_path(session.id)
        write_json(self._metadata_path(session.id), session)

    def load_messages(self, session_id: str) -> list[Message]:
        return read_jsonl(self._history_path(session_id))

    def append_messages(
        self, session_id: str, messages: Sequence[Message]
    ) -> None:
        if not messages:
            return
        path = self._history_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()
        write_jsonl(path, messages, mode="a")

    def replace_messages(
        self, session_id: str, messages: Sequence[Message]
    ) -> None:
        path = self._history_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_jsonl(path, messages, mode="w")

    def delete_session(
        self, session_id: str, *, user_id: str | None = None
    ) -> bool:
        session = self.get_session(session_id, user_id=user_id)
        if not session:
            return False
        folder = self._session_dir(session_id)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
            return True
        return False
