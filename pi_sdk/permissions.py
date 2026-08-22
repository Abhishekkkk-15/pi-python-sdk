from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from pi_sdk.models import Session


class PermissionDecision:
    ALLOW_ONCE = "y"
    ALWAYS_TOOL = "a"
    ALWAYS_TARGET = "f"
    ALWAYS_ALL = "all"
    DENY = "n"


PermissionCallback = Callable[..., bool | Awaitable[bool]]


class PermissionManager:
    @staticmethod
    def extract_target_key(tool_name: str, raw_target: str) -> str:
        """Extract a clean target key for matching (e.g., 'npm' for bash command 'npm run dev')."""
        target = raw_target.strip()
        if tool_name == "bash":
            parts = target.split()
            return parts[0] if parts else target
        elif tool_name in ("read", "write", "edit", "grep"):
            try:
                return str(Path(target).resolve())
            except Exception:
                return target
        return target

    @classmethod
    def is_target_allowed(cls, tool_name: str, target: str, allowed_targets: Dict[str, List[str]]) -> bool:
        """Check if target matches any stored allowed target for the given tool."""
        if not allowed_targets or tool_name not in allowed_targets:
            return False

        saved_list = allowed_targets[tool_name]
        clean_target = cls.extract_target_key(tool_name, target)
        target_lower = clean_target.lower()

        for saved in saved_list:
            saved_lower = saved.lower()
            if tool_name == "bash":
                if target_lower.startswith(saved_lower) or target_lower == saved_lower:
                    return True
            elif tool_name in ("read", "write", "edit", "grep"):
                if target_lower == saved_lower or target_lower.startswith(saved_lower + os.sep):
                    return True
            else:
                if saved_lower in target_lower or target_lower.startswith(saved_lower):
                    return True

        return False

    @classmethod
    def check_permission(
        cls,
        session: Optional[Session],
        tool_name: str,
        target: str,
        autonomous_risk: bool = False
    ) -> bool:
        """
        Returns True if the tool execution is pre-approved without prompting.
        Returns False if interactive prompt confirmation is required.
        """
        if autonomous_risk:
            return True

        if not session or not hasattr(session, "permissions"):
            return False

        perms = session.permissions or {}

        if perms.get("allow_all", False):
            return True

        if tool_name in perms.get("allowed_tools", []):
            return True

        allowed_targets = perms.get("allowed_targets", {})
        if cls.is_target_allowed(tool_name, target, allowed_targets):
            return True

        return False

    @classmethod
    async def save_permission_grant(
        cls,
        memory: Any,
        session: Session,
        grant_type: str,
        tool_name: str,
        target: str
    ) -> None:
        """Persists granted permission into session.permissions and updates metadata."""
        if not session:
            return

        if not hasattr(session, "permissions") or session.permissions is None:
            session.permissions = {
                "allow_all": False,
                "allowed_tools": [],
                "allowed_targets": {}
            }

        perms = session.permissions

        if grant_type == PermissionDecision.ALWAYS_ALL:
            perms["allow_all"] = True

        elif grant_type == PermissionDecision.ALWAYS_TOOL:
            if tool_name not in perms["allowed_tools"]:
                perms["allowed_tools"].append(tool_name)

        elif grant_type == PermissionDecision.ALWAYS_TARGET:
            target_key = cls.extract_target_key(tool_name, target)
            if tool_name not in perms["allowed_targets"]:
                perms["allowed_targets"][tool_name] = []
            if target_key not in perms["allowed_targets"][tool_name]:
                perms["allowed_targets"][tool_name].append(target_key)

        try:
            if memory and hasattr(memory, "save_session"):
                memory.session = session
                await memory.save_session()
            elif memory and hasattr(memory, "store") and hasattr(memory.store, "save_session"):
                await memory.store.save_session(session)
        except Exception:
            pass

    @staticmethod
    async def resolve_callback(
        callback: PermissionCallback | None,
        tool_name: str,
        target: str,
        action_details: str,
    ) -> bool:
        if callback is None:
            return False
        result = callback(tool_name, target, action_details)
        if inspect.isawaitable(result):
            return bool(await result)
        return bool(result)
