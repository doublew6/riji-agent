"""Allowlisted owner projections over the authoritative memory services."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from riji_agent.memory.backend import MemoryBackendError
from riji_agent.memory.journal_privacy import PURPOSES
from riji_agent.memory.capture import contains_credentials
from riji_agent.mobile.store import MobileError


def memory_version(item) -> str:
    payload = json.dumps(asdict(item), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def sources(item) -> list[dict]:
    result = []
    for source in item.metadata.get("journal_evidence", ())[:20]:
        if not isinstance(source, dict):
            continue
        identifier = source.get("source_id", "")
        path = identifier.removeprefix("riji/") + ".md" if identifier.startswith("riji/") else ""
        parts = PurePosixPath(path).parts
        if not path or path.startswith("/") or "\\" in path or any(p in {".", ".."} for p in parts):
            continue
        result.append({"id": identifier, "relative_path": path, "line": source.get("line", 1),
                       "section": source.get("section", ""), "version": source.get("version", ""),
                       "kind": source.get("kind", "daily")})
    return result


def memory_view(item) -> dict:
    return {"id": item.id, "content": item.content, "version": memory_version(item),
            "scope": item.scope.value, "persona_id": item.persona_id, "status": item.status.value,
            "state": item.metadata.get("journal_state", "current"),
            "privacy": item.metadata.get("privacy", "cloud"), "updated_at": item.updated_at,
            "reviewed_at": item.metadata.get("reviewed_at"), "sources": sources(item)}


class MobileMemory:
    def __init__(self, service) -> None:
        self.service = service

    def available(self) -> None:
        if self.service is None:
            raise MobileError("memory_unavailable", 503)

    def item(self, owner: str, identifier: str):
        self.available()
        try:
            item = self.service.backend.get(identifier)
        except MemoryBackendError as exc:
            if exc.code == "memory_not_found":
                raise MobileError("memory_not_found", 404) from None
            raise MobileError("memory_unavailable", 503) from None
        if item.user_id != owner:
            raise MobileError("memory_not_found", 404)
        return item

    def listing(self, owner: str, cursor: str = "") -> dict:
        self.available()
        records = sorted(self.service.list_memories(user_id=owner, include_archived=True), key=lambda item: item.id)
        version = hashlib.sha256("".join(memory_version(item) for item in records).encode()).hexdigest()
        offset = self._cursor(cursor, version)
        page = records[offset:offset + 100]
        next_cursor = None
        if offset + 100 < len(records):
            next_cursor = base64.urlsafe_b64encode(json.dumps([version, offset + 100]).encode()).decode().rstrip("=")
        return {"items": [memory_view(item) for item in page], "next_cursor": next_cursor,
                "updated_at": datetime.now(timezone.utc).isoformat()}

    @staticmethod
    def _cursor(cursor: str, version: str) -> int:
        if not cursor:
            return 0
        try:
            old, offset = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if old != version or type(offset) is not int or offset < 0:
                raise ValueError()
            return offset
        except Exception:
            raise MobileError("memory_page_changed") from None

    def mutate(self, owner: str, identifier: str, body: dict) -> dict:
        self.available()
        guard = getattr(self.service.backend, "mutation_guard", None)
        if guard is None:
            raise MobileError("memory_mutation_guard_unavailable", 503)
        with guard():
            return self._mutate_locked(owner, identifier, body)

    def _mutate_locked(self, owner: str, identifier: str, body: dict) -> dict:
        item = self.item(owner, identifier)
        self._not_restoring()
        if body["expected_version"] != memory_version(item):
            raise MobileError("memory_version_changed")
        action = body["action"]
        if action == "update":
            content = body.get("content", "").strip()
            if not content or len(content) > 2000 or contains_credentials(content):
                raise MobileError("invalid_content", 422)
            self.service.update_memory(identifier, user_id=owner, content=content)
        elif action == "delete":
            if body.get("confirmation") != "DELETE":
                raise MobileError("delete_confirmation_required", 422)
            self.service.delete_memory(identifier, user_id=owner)
        elif action == "permission":
            permission = body.get("permission")
            if permission not in {"cloud", "local", "none"}:
                raise MobileError("invalid_memory_permission", 422)
            self.service.set_memory_permission(identifier, user_id=owner, permission=permission)
        else:
            methods = {"reconfirm": self.service.reconfirm_memory, "archive": self.service.archive_memory,
                       "restore": self.service.restore_memory}
            if action not in methods:
                raise MobileError("unsupported_action", 422)
            methods[action](identifier, user_id=owner)
        return {"memory_id": identifier, "deleted": action == "delete"}

    def _not_restoring(self) -> None:
        if self.service.journal is not None and self.service.journal.store.get_control("restore_in_progress"):
            raise MobileError("memory_restore_in_progress")

    def privacy(self, owner: str) -> dict:
        engine = self._engine(owner, required=False)
        if engine is None:
            return {"available": False}
        consent, policy = engine.privacy.status(), engine.policy
        progress = engine.store.progress()
        targets = {kind: {"provider": getattr(policy, kind + "_provider"),
                          "model": getattr(policy, kind + "_model"),
                          "host": urlsplit(getattr(policy, kind + "_destination")).hostname or ""}
                   for kind in ("extraction", "recall")}
        return {"available": True, "valid": consent["valid"], "binding": consent["binding"],
                "permissions": {key: consent["permissions"][key] for key in PURPOSES},
                "paused": engine.store.get_control("paused") == "1", "targets": targets,
                "source_scope": {"sections": list(policy.sections), "date_from": policy.date_from,
                                 "date_to": policy.date_to, "mentors": list(policy.mentors)},
                "limits": {"daily_chars": policy.daily_chars, "fragment_chars": policy.segment_chars,
                           "source_chars": policy.source_chars},
                "progress": {"discovered": progress["discovered"], "completed": progress["completed"],
                             "pending": progress["pending"],
                             "failed": sum(row["status"] == "failed" for row in progress["sources"])}}

    def _engine(self, owner: str, *, required: bool = True):
        engine = self.service.journal if self.service is not None else None
        if engine is None or engine.policy.user_id != owner:
            if required:
                raise MobileError("journal_memory_unavailable", 503)
            return None
        return engine

    def control(self, owner: str, body: dict) -> dict:
        engine = self._engine(owner)
        self._not_restoring()
        if body["binding"] != engine.privacy.binding:
            raise MobileError("privacy_scope_changed_or_invalid")
        action = body["action"]
        if action == "grant":
            permissions = body.get("permissions", {})
            if body.get("acknowledged") is not True or set(permissions) != set(PURPOSES):
                raise MobileError("privacy_acknowledgment_required", 422)
            engine.privacy.grant(body["binding"], permissions, paused=True)
        elif action == "revoke":
            engine.privacy.revoke()
        elif action in {"pause", "resume"}:
            if action == "resume" and not engine.privacy.status()["valid"]:
                raise MobileError("journal_consent_required")
            engine.store.set_control("paused", "1" if action == "pause" else "0")
        elif action == "scan":
            engine.store.set_control("scan_requested", "1")
        else:
            raise MobileError("unsupported_action", 422)
        return {"applied": True}
