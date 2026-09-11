"""Workspace skill discovery, prompt loading, and optional npx installer."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

import aiofiles

from pi_sdk.paths import get_workspace

SkillRef = Union[str, Sequence[str], None]

# Shared/default skill roots (cloud platform templates, etc.)
_EXTRA_SKILLS_DIRS: list[Path] = []


def _normalize_skills_dirs(dirs: Sequence[str | Path] | None) -> list[Path]:
    if not dirs:
        return []
    out: list[Path] = []
    seen: set[Path] = set()
    for raw in dirs:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        try:
            path = Path(text).expanduser().resolve()
        except OSError:
            path = Path(text).expanduser()
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def _skills_dirs_from_env() -> list[Path]:
    """
    Parse PI_SDK_SKILLS_DIRS.

    Accepts comma-separated and/or OS path-separator lists, e.g.:
    ``/opt/pi-skills:/shared/skills`` or ``/opt/a,/opt/b``.
    """
    raw = (os.getenv("PI_SDK_SKILLS_DIRS") or "").strip()
    if not raw:
        return []
    parts: list[str] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if os.pathsep != "," and os.pathsep in chunk:
            parts.extend(p.strip() for p in chunk.split(os.pathsep) if p.strip())
        else:
            parts.append(chunk)
    return _normalize_skills_dirs(parts)


class Skills:
    FILENAME = "SKILL.md"

    _cache: Dict[str, str] = {}
    _paths: Dict[str, Path] = {}
    _scanned_key: Optional[tuple] = None

    @classmethod
    def set_extra_dirs(cls, dirs: Sequence[str | Path] | None) -> list[Path]:
        """
        Set process-wide shared skill directories (after project ``.agents/skills``).

        Typically called from ``Agent.create(skills_dirs=...)``.
        """
        global _EXTRA_SKILLS_DIRS
        _EXTRA_SKILLS_DIRS = _normalize_skills_dirs(dirs)
        cls._scanned_key = None  # force refresh on next load
        return list(_EXTRA_SKILLS_DIRS)

    @classmethod
    def get_extra_dirs(cls) -> list[Path]:
        return list(_EXTRA_SKILLS_DIRS)

    @classmethod
    def search_dirs(cls) -> List[Path]:
        """
        Skill roots scanned by the SDK (first match wins).

        1. ``<cwd>/.agents/skills/`` — project / template workspace
        2. ``Agent.create(skills_dirs=...)`` — shared platform defaults
        3. ``PI_SDK_SKILLS_DIRS`` — ops/env defaults

        ``npx skills add -g`` home dirs are not scanned unless you point
        ``skills_dirs`` at them explicitly.
        """
        candidates: list[Path] = [get_workspace() / ".agents" / "skills"]
        candidates.extend(_EXTRA_SKILLS_DIRS)
        candidates.extend(_skills_dirs_from_env())

        seen: set[Path] = set()
        dirs: list[Path] = []
        for path in candidates:
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if resolved in seen:
                continue
            seen.add(resolved)
            dirs.append(path)
        return dirs

    @classmethod
    def _scan_key(cls) -> tuple:
        return (
            str(get_workspace().resolve()),
            tuple(str(p) for p in cls.search_dirs()),
        )

    @classmethod
    async def refresh(cls) -> None:
        """Reload all skills into memory, supporting skills/<skill_name>/SKILL.md structure."""
        cls._cache.clear()
        cls._paths.clear()
        cls._scanned_key = cls._scan_key()

        for base_dir in cls.search_dirs():
            if not base_dir.is_dir():
                continue

            for folder in sorted(base_dir.iterdir()):
                if not folder.is_dir() or folder.name.startswith("."):
                    continue

                skill_name = folder.name
                if skill_name in cls._cache:
                    continue

                skill_file = folder / cls.FILENAME
                if not skill_file.exists():
                    skill_file = folder / "skill.md"
                if not skill_file.exists():
                    md_files = sorted(folder.glob("*.md"))
                    if md_files:
                        skill_file = md_files[0]

                if skill_file.is_file():
                    try:
                        async with aiofiles.open(skill_file, "r", encoding="utf-8") as f:
                            cls._cache[skill_name] = await f.read()
                    except OSError:
                        continue
                    cls._paths[skill_name] = skill_file

            for file in sorted(base_dir.glob("*.md")):
                if not file.is_file() or file.stem.upper() == "SKILL":
                    continue
                skill_name = file.stem
                if skill_name in cls._cache:
                    continue
                try:
                    async with aiofiles.open(file, "r", encoding="utf-8") as f:
                        cls._cache[skill_name] = await f.read()
                except OSError:
                    continue
                cls._paths[skill_name] = file

    @classmethod
    async def _ensure_loaded(cls) -> None:
        if not cls._cache or cls._scanned_key != cls._scan_key():
            await cls.refresh()

    @classmethod
    async def names(cls) -> List[str]:
        await cls._ensure_loaded()
        return sorted(cls._cache.keys())

    @classmethod
    async def exists(cls, skill_name: str) -> bool:
        await cls._ensure_loaded()
        return skill_name in cls._cache

    @classmethod
    async def load(cls, skill_name: str) -> Optional[str]:
        await cls._ensure_loaded()
        return cls._cache.get(skill_name)

    @classmethod
    async def load_many(cls, skill_names: List[str]) -> Dict[str, str]:
        await cls._ensure_loaded()
        return {
            name: content
            for name, content in cls._cache.items()
            if name in skill_names
        }

    @classmethod
    async def search(
        cls,
        query: str,
        *,
        search_content: bool = True,
    ) -> List[str]:
        await cls._ensure_loaded()

        query = query.lower().strip()
        matches = []

        for name, content in cls._cache.items():
            if query in name.lower():
                matches.append(name)
                continue

            if search_content and query in content.lower():
                matches.append(name)

        return sorted(matches)

    @classmethod
    def path(cls, skill_name: str) -> Optional[Path]:
        return cls._paths.get(skill_name)

    @classmethod
    def skill_root(cls, skill_name: str) -> Optional[Path]:
        """
        Directory that owns the skill (for relative scripts/references/assets).

        ``skills/foo/SKILL.md`` → ``skills/foo``
        Flat ``skills/foo.md`` → path of that file (no sibling root)
        """
        path = cls._paths.get(skill_name)
        if path is None:
            return None
        if path.parent.name == skill_name:
            return path.parent
        return path

    @classmethod
    async def get_metadata(cls, skill_name: str) -> Optional[dict]:
        await cls._ensure_loaded()
        path = cls.path(skill_name)
        if not path or not path.exists():
            return None
        root = cls.skill_root(skill_name)
        return {
            "name": skill_name,
            "path": str(path),
            "root": str(root) if root is not None else str(path),
            "size": path.stat().st_size,
            "modified": path.stat().st_mtime,
        }

    @staticmethod
    def _npx_executable() -> str:
        for name in ("npx.cmd", "npx") if sys.platform == "win32" else ("npx",):
            found = shutil.which(name)
            if found:
                return found
        raise RuntimeError(
            "npx not found on PATH. Install Node.js to use Skills.install() "
            "(https://nodejs.org/), or place skills under "
            "<cwd>/.agents/skills/<name>/SKILL.md."
        )

    @staticmethod
    def _as_list(value: SkillRef) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return [str(v) for v in value]

    @classmethod
    async def install(
        cls,
        source: str,
        *,
        skill: SkillRef = None,
        agent: SkillRef = "universal",
        global_install: bool = False,
        yes: bool = True,
        copy: bool = False,
        cwd: str | Path | None = None,
        refresh: bool = True,
    ) -> dict:
        """
        Install skills via the Agent Skills CLI::

            npx skills add <source> ...

        Defaults to ``-a universal`` so files land in ``<cwd>/.agents/skills/``,
        the only directory this SDK scans.

        ``global_install=True`` passes ``--global`` to the CLI, but discovery
        still only reads the **project** ``.agents/skills`` folder.

        Examples::

            await Skills.install("vercel-labs/agent-skills", skill="frontend-design")
            await Skills.install(
                "vercel-labs/agent-skills",
                skill=["frontend-design", "skill-creator"],
            )
        """
        source = (source or "").strip()
        if not source:
            raise ValueError("source is required (e.g. 'owner/repo' or a skill URL)")

        workdir = Path(cwd) if cwd is not None else get_workspace()
        npx = cls._npx_executable()

        cmd: list[str] = [npx, "--yes", "skills", "add", source]

        for name in cls._as_list(skill):
            cmd.extend(["--skill", name])

        agents = cls._as_list(agent) or ["universal"]
        for name in agents:
            cmd.extend(["--agent", name])

        if global_install:
            cmd.append("--global")
        if yes:
            cmd.append("--yes")
        if copy:
            cmd.append("--copy")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr = (stderr_b or b"").decode("utf-8", errors="replace")
        code = int(proc.returncode or 0)

        if code != 0:
            detail = (stderr or stdout or "").strip() or f"exit code {code}"
            raise RuntimeError(f"skills install failed: {detail}")

        if refresh:
            # Ensure discovery uses the install workspace
            from pi_sdk.paths import set_workspace

            set_workspace(workdir)
            await cls.refresh()

        return {
            "ok": True,
            "source": source,
            "cwd": str(workdir),
            "command": cmd,
            "stdout": stdout,
            "stderr": stderr,
            "skills": await cls.names(),
        }
