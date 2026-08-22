from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import aiofiles

from pi_sdk.paths import get_data_root, get_workspace


class Skills:
    FILENAME = "SKILL.md"

    _cache: Dict[str, str] = {}
    _paths: Dict[str, Path] = {}
    _scanned_cwd: Optional[Path] = None

    @classmethod
    def search_dirs(cls) -> List[Path]:
        workspace = get_workspace()
        candidates = [
            workspace / ".pi-sdk" / "skills",
            workspace / ".pi-python" / "skills",
            workspace / "skills",
            get_data_root() / "skills",
        ]

        seen: set[Path] = set()
        dirs: List[Path] = []
        for path in candidates:
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                dirs.append(path)
        return dirs

    @classmethod
    async def refresh(cls) -> None:
        """Reload all skills into memory, supporting skills/<skill_name>/SKILL.md structure."""
        cls._cache.clear()
        cls._paths.clear()
        cls._scanned_cwd = get_workspace()

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
        if not cls._cache or cls._scanned_cwd != get_workspace():
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
    async def get_metadata(cls, skill_name: str) -> Optional[dict]:
        path = cls.path(skill_name)
        if not path or not path.exists():
            return None

        return {
            "name": skill_name,
            "path": str(path),
            "size": path.stat().st_size,
            "modified": path.stat().st_mtime,
        }

    @classmethod
    def path(cls, skill_name: str) -> Optional[Path]:
        return cls._paths.get(skill_name)
