from __future__ import annotations

import os
from pathlib import Path


class DevPathError(ValueError):
    pass


PROTECTED_PREFIXES = (
    "main.js",
    "preload.js",
    "home-core/core-client.js",
    "home-core/core-supervisor.js",
    "home-core/supervisor.js",
    "home-core/lan-gateway.js",
    "home-core/protocol.js",
    "isaac-core/src/isaac_core/api/",
    "isaac-core/src/isaac_core/brain/",
    "isaac-core/src/isaac_core/config.py",
    "isaac-core/src/isaac_core/conversations/service.py",
    "isaac-core/src/isaac_core/dev/",
    "isaac-core/src/isaac_core/main.py",
    "isaac-core/src/isaac_core/web/security.py",
)


class ProjectSandbox:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)

    def resolve(self, supplied: str, *, must_exist: bool = True, write: bool = False) -> Path:
        raw = str(supplied or "")
        if not raw or raw.startswith(("\\\\", "//")) or "%" in raw or "$" in raw or "\x00" in raw:
            raise DevPathError("Invalid project-relative path")
        candidate = Path(raw)
        if ".." in candidate.parts:
            raise DevPathError("Parent traversal is not allowed")
        if candidate.is_absolute():
            resolved = candidate.resolve(strict=must_exist)
        else:
            resolved = (self.root / candidate).resolve(strict=must_exist)
        try:
            relative = resolved.relative_to(self.root).as_posix()
        except ValueError as error:
            raise DevPathError("Path is outside the approved project root") from error
        if write and self.is_protected(relative):
            raise DevPathError("Protected Dev Mode path is read-only")
        # resolve() follows symlinks and Windows junctions. Recheck the final target.
        if os.path.commonpath((str(self.root), str(resolved))) != str(self.root):
            raise DevPathError("Resolved path escapes the approved project root")
        return resolved

    @staticmethod
    def is_protected(relative: str) -> bool:
        normalized = relative.replace("\\", "/").casefold().lstrip("./")
        return any(normalized == prefix.rstrip("/").casefold() or normalized.startswith(prefix.casefold()) for prefix in PROTECTED_PREFIXES)

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()
