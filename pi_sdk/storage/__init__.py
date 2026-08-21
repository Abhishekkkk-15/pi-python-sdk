"""Session storage backends (disk default, MongoDB optional)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from pi_sdk.storage.base import SessionStore
from pi_sdk.storage.disk import DiskSessionStore


def create_store(
    kind: str = "disk",
    *,
    data_dir: str | Path | None = None,
    mongodb_uri: str | None = None,
    mongodb_db: str = "pi_sdk",
    store: SessionStore | None = None,
) -> SessionStore:
    """
    Build a SessionStore.

    kind: "disk" | "mongodb"
    store: if provided, returned as-is (wins over kind).
    """
    if store is not None:
        return store

    name = (kind or "disk").strip().lower()
    if name in ("disk", "file", "local"):
        root = Path(data_dir).expanduser().resolve() if data_dir else None
        return DiskSessionStore(data_root=root)

    if name in ("mongodb", "mongo"):
        uri = (
            (mongodb_uri or "").strip()
            or (os.getenv("PI_SDK_MONGODB_URI") or "").strip()
            or (os.getenv("MONGODB_URI") or "").strip()
        )
        if not uri:
            raise ValueError(
                "storage='mongodb' requires mongodb_uri= or "
                "PI_SDK_MONGODB_URI / MONGODB_URI env"
            )
        from pi_sdk.storage.mongodb import MongoSessionStore

        return MongoSessionStore(uri, database=mongodb_db or "pi_sdk")

    raise ValueError(f"Unknown storage kind: {kind!r} (expected 'disk' or 'mongodb')")


__all__ = [
    "DiskSessionStore",
    "SessionStore",
    "create_store",
]
