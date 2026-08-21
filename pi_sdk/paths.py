"""Process-wide data_dir / workspace paths for the PI SDK."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

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
    Directory for auth/sessions (disk backend).

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
