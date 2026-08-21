"""Session persistence and data-root helpers for the PI SDK."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

from pi_sdk.models import Message, Role, Session

# Process-wide defaults (overridden by Agent.create)
_DATA_ROOT: Optional[Path] = None
_WORKSPACE: Optional[Path] = None


def set_data_root(path: str | Path | None) -> Path:
    global _DATA_ROOT
    if path is None:
        _DATA_ROOT = None
        return get_data_root()
    _DATA_ROOT = Path(path).expanduser().resolve()
    _DATA_ROOT.mkdir(parents=True, exist_ok=True)
    return _DATA_ROOT


def set_workspace(path: str | Path | None) -> Path:
    global _WORKSPACE
    if path is None:
        _WORKSPACE = None
        return get_workspace()
    _WORKSPACE = Path(path).expanduser().resolve()
    return _WORKSPACE


def get_workspace() -> Path:
    if _WORKSPACE is not None:
        return _WORKSPACE
    return Path.cwd()


def get_data_root() -> Path:
    """
    Directory for auth/sessions.

    Priority:
    1. set_data_root(...) / Agent.create(data_dir=...)
    2. PI_SDK_DATA_DIR env
    3. ~/.pi-sdk
    """
    if _DATA_ROOT is not None:
        return _DATA_ROOT
    env = (os.getenv("PI_SDK_DATA_DIR") or "").strip()
    if env:
        root = Path(env).expanduser().resolve()
    else:
        root = Path.home() / ".pi-sdk"
    root.mkdir(parents=True, exist_ok=True)
    return root


def generate_chat_id() -> str:
    return uuid.uuid4().hex


class Memory:
    messages: list[Message]
    session: Union[Session, None]

    def __init__(self) -> None:
        self.messages = []
        self.session = None

    @property
    def root(self) -> Path:
        return get_data_root()

    def init_session(
        self,
        title: str,
        initial_messages: list[Message] | None = None,
        *,
        workspace: Path | None = None,
    ) -> Session:
        sid = generate_chat_id()
        ws = Path(workspace) if workspace is not None else get_workspace()
        data_root = get_data_root()
        history_dir = data_root / sid
        history_dir.mkdir(parents=True, exist_ok=True)
        conversation_jsonl = history_dir / "conversation_history.jsonl"
        conversation_jsonl.touch()
        session = Session(
            id=sid,
            title=title,
            workspace=ws,
            history_path=conversation_jsonl,
        )
        self.session = session
        self.write_to_json(history_dir / "metadata.json", session)
        if initial_messages:
            self.write_to_jsonl(conversation_jsonl, initial_messages, mode="a")
        return session

    def load_old_sessions(self) -> list[Session]:
        memory_path = get_data_root()
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
                with open(metadata_file, "r", encoding="utf-8") as file:
                    data = json.load(file)
                sessions.append(
                    Session(
                        id=data.get("id", folder.name),
                        title=data.get("title", folder.name),
                        workspace=Path(data["workspace"])
                        if "workspace" in data
                        else get_workspace(),
                        history_path=Path(data["history_path"])
                        if "history_path" in data
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
                        estimated_cost_usd=float(
                            data.get("estimated_cost_usd", 0.0) or 0.0
                        ),
                        compaction_summary=str(
                            data.get("compaction_summary", "") or ""
                        ),
                        compacted_until=int(data.get("compacted_until", 0) or 0),
                    )
                )
            except (json.JSONDecodeError, KeyError, OSError):
                continue
        return sessions

    def get_session_by_id(self, session_id: str) -> Optional[Session]:
        for session in self.load_old_sessions():
            if session.id == session_id:
                return session
        return None

    def load_session_chat(self, path: Path, system_prompt: str = "") -> list[Message]:
        if not path or not path.exists():
            return []
        old_chat = self.read_from_jsonl(path=path)
        if not old_chat or old_chat[0].role != Role.SYSTEM:
            if system_prompt:
                old_chat.insert(0, Message(role=Role.SYSTEM, content=system_prompt))
        self.messages = old_chat
        return old_chat

    @staticmethod
    def write_to_json(path: Union[str, Path], data: Any) -> None:
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

    @staticmethod
    def read_from_json(path: Union[str, Path]) -> Any:
        file_path = Path(path)
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def write_to_jsonl(
        self, path: Union[str, Path], data_list: list[Any], mode: str = "w"
    ) -> None:
        def default_serializer(obj: Any) -> Any:
            if isinstance(obj, Role):
                return obj.value
            if isinstance(obj, Enum):
                return obj.value
            if isinstance(obj, Path):
                return str(obj)
            return str(obj)

        with open(path, mode, encoding="utf-8") as file:
            for item in data_list:
                if hasattr(item, "to_dict"):
                    payload = item.to_dict()
                elif is_dataclass(item):
                    payload = asdict(item)
                else:
                    payload = item
                file.write(json.dumps(payload, default=default_serializer) + "\n")

    def read_from_jsonl(self, path: Union[str, Path]) -> list[Message]:
        messages: list[Message] = []
        with open(path, "r", encoding="utf-8") as file:
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
