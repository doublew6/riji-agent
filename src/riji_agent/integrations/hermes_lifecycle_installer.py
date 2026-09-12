"""Conservative, reversible source patch for the existing Feishu SDK owner."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path

from riji_agent.integrations.hermes_lifecycle_hook import (
    BUILD_ANCHOR, METHOD_BEGIN, METHOD_END, ORIGINAL_BOT_REGISTRATION,
    REGISTER_BEGIN, REGISTER_END, lifecycle_block, registration_block,
)


@dataclass(frozen=True)
class LifecyclePatch:
    path: Path
    original: bytes
    updated: bytes


def adapter_path(gateway: Path) -> Path | None:
    if gateway.name != "run.py" or gateway.parent.name != "gateway":
        return None
    return gateway.parent.parent / "plugins/platforms/feishu/adapter.py"


def _error() -> Exception:
    from riji_agent.integrations.hermes_installer import HermesBridgeInstallError
    return HermesBridgeInstallError("Feishu lifecycle adapter shape is unsupported.")


def _replace(text: str, begin: str, end: str, replacement: str) -> str:
    if begin not in text and end not in text:
        return text
    if text.count(begin) != 1 or text.count(end) != 1:
        raise _error()
    start, finish = text.index(begin), text.index(end)
    if finish < start:
        raise _error()
    finish += len(end)
    if text[finish:finish + 1] == "\n":
        finish += 1
    return text[:start] + replacement + text[finish:]


def remove_lifecycle(text: str) -> str:
    methods = METHOD_BEGIN in text or METHOD_END in text
    registrations = REGISTER_BEGIN in text or REGISTER_END in text
    if methods != registrations:
        raise _error()
    text = _replace(text, REGISTER_BEGIN, REGISTER_END, ORIGINAL_BOT_REGISTRATION + "\n")
    return _replace(text, METHOD_BEGIN, METHOD_END, "")


def install_text(text: str) -> str:
    text = remove_lifecycle(text)
    if (text.count(BUILD_ANCHOR) != 1 or text.count(ORIGINAL_BOT_REGISTRATION) != 1
            or "def _riji_on_lifecycle_event" in text or "def _riji_forward_lifecycle" in text):
        raise _error()
    if any(".register_p2_" + name + "(" in text for name in (
            "im_chat_member_user_added_v1", "im_chat_member_user_deleted_v1",
            "im_chat_member_user_withdrawn_v1", "im_chat_updated_v1")):
        raise _error()
    updated = text.replace(ORIGINAL_BOT_REGISTRATION, registration_block(), 1)
    updated = updated.replace(BUILD_ANCHOR, lifecycle_block() + "\n" + BUILD_ANCHOR, 1)
    try:
        ast.parse(updated)
    except (SyntaxError, ValueError):
        raise _error() from None
    return updated


def prepare(gateway: Path, *, remove: bool = False) -> LifecyclePatch | None:
    path = adapter_path(gateway)
    if path is None or not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise _error()
    original = path.read_bytes()
    try:
        text = original.decode("utf-8")
        updated = remove_lifecycle(text) if remove else install_text(text)
        ast.parse(updated)
    except (UnicodeError, SyntaxError, ValueError):
        raise _error() from None
    return LifecyclePatch(path, original, updated.encode("utf-8"))


def apply(patch: LifecyclePatch | None, *, backup: bool = True) -> None:
    if patch is None or patch.original == patch.updated:
        return
    if patch.path.read_bytes() != patch.original:
        raise _error()
    if backup:
        from riji_agent.integrations.hermes_installer import _backup
        _backup(patch.path)
    patch.path.write_bytes(patch.updated)


def installed(gateway: Path) -> bool:
    path = adapter_path(gateway)
    if path is None or not path.is_file() or path.is_symlink():
        return False
    try:
        text = path.read_bytes().decode("utf-8")
        return (METHOD_BEGIN in text and REGISTER_BEGIN in text
                and hashlib.sha256(install_text(text).encode()).digest()
                == hashlib.sha256(path.read_bytes()).digest())
    except Exception:
        return False
