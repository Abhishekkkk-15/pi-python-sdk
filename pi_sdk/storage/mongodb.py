"""MongoDB-backed session store (optional: pip install pi-sdk[mongodb])."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from pi_sdk.models import Message, Role, Session
from pi_sdk.storage.base import SessionStore
from pi_sdk.storage.disk import generate_chat_id


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_pymongo():
    try:
        from pymongo import ASCENDING, MongoClient
        from pymongo.collection import Collection
        from pymongo.database import Database
    except ImportError as e:
        raise ImportError(
            "MongoDB storage requires pymongo. Install with: pip install pi-sdk[mongodb]"
        ) from e
    return MongoClient, ASCENDING, Collection, Database


def _message_to_doc(session_id: str, seq: int, msg: Message, user_id: str | None) -> dict:
    doc: dict[str, Any] = {
        "session_id": session_id,
        "seq": seq,
        "role": msg.role.value if isinstance(msg.role, Role) else str(msg.role),
        "content": msg.content or "",
        "user_id": user_id,
    }
    if getattr(msg, "name", None) is not None:
        doc["name"] = msg.name
    if getattr(msg, "tool_calls", None) is not None:
        doc["tool_calls"] = msg.tool_calls
    if getattr(msg, "tool_call_id", None) is not None:
        doc["tool_call_id"] = msg.tool_call_id
    if getattr(msg, "reasoning_content", None) is not None:
        doc["reasoning_content"] = msg.reasoning_content
    return doc


def _doc_to_message(data: dict) -> Message:
    return Message(
        role=Role.from_val(data.get("role", "system")),
        content=data.get("content", "") or "",
        name=data.get("name"),
        tool_calls=data.get("tool_calls"),
        tool_call_id=data.get("tool_call_id"),
        reasoning_content=data.get("reasoning_content"),
    )


def _session_to_doc(session: Session) -> dict:
    return {
        "_id": session.id,
        "title": session.title,
        "workspace": str(session.workspace),
        "permissions": session.permissions or {
            "allow_all": False,
            "allowed_tools": [],
            "allowed_targets": {},
        },
        "prompt_tokens": int(session.prompt_tokens or 0),
        "completion_tokens": int(session.completion_tokens or 0),
        "total_tokens": int(session.total_tokens or 0),
        "cached_tokens": int(session.cached_tokens or 0),
        "estimated_cost_usd": float(session.estimated_cost_usd or 0.0),
        "compaction_summary": session.compaction_summary or "",
        "compacted_until": int(session.compacted_until or 0),
        "user_id": session.user_id,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def _doc_to_session(data: dict) -> Session:
    return Session(
        id=str(data.get("_id") or data.get("id")),
        title=str(data.get("title") or ""),
        workspace=Path(data["workspace"]) if data.get("workspace") else Path.cwd(),
        history_path=None,
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


class MongoSessionStore(SessionStore):
    def __init__(
        self,
        uri: str,
        *,
        database: str = "pi_sdk",
        sessions_collection: str = "sessions",
        messages_collection: str = "messages",
    ) -> None:
        MongoClient, ASCENDING, _, _ = _require_pymongo()
        if not uri or not str(uri).strip():
            raise ValueError("mongodb_uri is required for MongoSessionStore")
        self._client = MongoClient(uri)
        self._db = self._client[database]
        self._sessions = self._db[sessions_collection]
        self._messages = self._db[messages_collection]
        self._sessions.create_index("user_id")
        self._messages.create_index(
            [("session_id", ASCENDING), ("seq", ASCENDING)], unique=True
        )

    def create_session(
        self,
        *,
        title: str,
        workspace: Path,
        user_id: str | None = None,
        permissions: dict | None = None,
    ) -> Session:
        sid = generate_chat_id()
        now = _utc_now()
        session = Session(
            id=sid,
            title=title,
            workspace=Path(workspace),
            history_path=None,
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
        self._sessions.insert_one(_session_to_doc(session))
        return session

    def get_session(
        self, session_id: str, *, user_id: str | None = None
    ) -> Session | None:
        query: dict[str, Any] = {"_id": session_id}
        if user_id is not None:
            query["user_id"] = user_id
        data = self._sessions.find_one(query)
        if not data:
            return None
        return _doc_to_session(data)

    def list_sessions(self, *, user_id: str | None = None) -> list[Session]:
        query: dict[str, Any] = {}
        if user_id is not None:
            query["user_id"] = user_id
        cursor = self._sessions.find(query).sort("updated_at", -1)
        return [_doc_to_session(doc) for doc in cursor]

    def save_session(self, session: Session) -> None:
        session.updated_at = _utc_now()
        self._sessions.replace_one(
            {"_id": session.id}, _session_to_doc(session), upsert=True
        )

    def load_messages(self, session_id: str) -> list[Message]:
        cursor = self._messages.find({"session_id": session_id}).sort("seq", 1)
        return [_doc_to_message(doc) for doc in cursor]

    def _next_seq(self, session_id: str) -> int:
        last = self._messages.find_one(
            {"session_id": session_id}, sort=[("seq", -1)]
        )
        if not last:
            return 0
        return int(last.get("seq", -1)) + 1

    def append_messages(
        self, session_id: str, messages: Sequence[Message]
    ) -> None:
        if not messages:
            return
        meta = self._sessions.find_one({"_id": session_id})
        user_id = meta.get("user_id") if meta else None
        start = self._next_seq(session_id)
        docs = [
            _message_to_doc(session_id, start + i, msg, user_id)
            for i, msg in enumerate(messages)
        ]
        self._messages.insert_many(docs)
        self._sessions.update_one(
            {"_id": session_id}, {"$set": {"updated_at": _utc_now()}}
        )

    def replace_messages(
        self, session_id: str, messages: Sequence[Message]
    ) -> None:
        meta = self._sessions.find_one({"_id": session_id})
        user_id = meta.get("user_id") if meta else None
        self._messages.delete_many({"session_id": session_id})
        if messages:
            docs = [
                _message_to_doc(session_id, i, msg, user_id)
                for i, msg in enumerate(messages)
            ]
            self._messages.insert_many(docs)
        self._sessions.update_one(
            {"_id": session_id}, {"$set": {"updated_at": _utc_now()}}
        )

    def delete_session(
        self, session_id: str, *, user_id: str | None = None
    ) -> bool:
        session = self.get_session(session_id, user_id=user_id)
        if not session:
            return False
        self._messages.delete_many({"session_id": session_id})
        result = self._sessions.delete_one({"_id": session_id})
        return result.deleted_count > 0
