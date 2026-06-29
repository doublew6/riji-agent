"""Cross-platform default locations for riji-agent local state.

These are *defaults only*. An explicitly configured ``RIJI_DATA_DIR`` always
takes precedence (resolved in :mod:`riji_agent.config`); these functions just
decide where state lives when the user has set nothing.

Backward compatibility is the hard constraint: existing POSIX installs must
never have their data moved. Linux and macOS therefore both keep the historical
XDG-style ``~/.local/share/riji-agent`` and ``~/.riji-agent/logs`` locations.
Windows has no existing installs, so it uses the native ``%LOCALAPPDATA%``
location instead of an alien dotfile path. (A future opt-in migration could move
macOS to ``~/Library/Application Support`` without breaking anyone today.)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "riji-agent"


def default_data_dir(platform: str | None = None) -> Path:
    """Default directory for local SQLite state (index, memory, drafts, audit)."""
    if (platform or sys.platform) == "win32":
        return _windows_local_appdata() / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def default_log_dir(platform: str | None = None) -> Path:
    """Default directory for service stdout/stderr logs."""
    if (platform or sys.platform) == "win32":
        return _windows_local_appdata() / APP_NAME / "logs"
    return Path.home() / ".riji-agent" / "logs"


def _windows_local_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) if base else Path.home() / "AppData" / "Local"
