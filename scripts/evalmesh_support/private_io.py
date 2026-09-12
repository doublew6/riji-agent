"""Bounded owner-only artifacts outside all Git worktrees."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def check_private_path(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("private_path_required")
    for part in (path, *path.parents):
        if part.is_symlink() or (part / ".git").exists():
            raise ValueError("private_path_required")
    return path


def make_private_directory(path: Path) -> Path:
    check_private_path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    return path


def write_private_json(path: Path, value: Any) -> None:
    check_private_path(path)
    if path.parent.stat().st_mode & 0o077:
        raise ValueError("private_directory_permissions")
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode()
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("private_artifact_too_large")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags, 0o600), "wb") as stream:
        stream.write(data + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
