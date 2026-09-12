"""Atomic, read-only MEMORY.md projection of active Mem0 records."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from riji_agent.memory.backend import LongTermMemoryBackend
from riji_agent.memory.capture import contains_credentials
from riji_agent.memory.models import LongTermMemory, MemoryScope


class MemorySnapshotWriter:
    def __init__(
        self,
        backend: LongTermMemoryBackend,
        path: Path,
        *,
        user_ids: Iterable[str],
        persona_names: Mapping[str, str],
    ) -> None:
        self._backend = backend
        self.path = Path(path)
        self._user_ids = tuple(sorted(user_ids))
        self._persona_names = dict(persona_names)

    def write(self) -> tuple[str, int]:
        generated_at = datetime.now().astimezone().isoformat()
        records = self._load_records()
        body = self._render(records, generated_at)
        self._write_atomic(body)
        return generated_at, len(records)

    def invalidate(self) -> None:
        self.path.unlink(missing_ok=True)

    def _load_records(self) -> Sequence[LongTermMemory]:
        records = []
        for user_id in self._user_ids:
            records.extend(
                self._backend.list_memories(
                    user_id=user_id, include_archived=False,
                    limit=100000 if hasattr(self._backend, "engine") else 1000
                )
            )
        return tuple(
            sorted(records, key=lambda item: (item.scope.value, item.persona_id or "", item.id))
        )

    def _render(self, records: Sequence[LongTermMemory], generated_at: str) -> str:
        lines = [
            "---",
            "format_version: 1",
            f"generated_at: {generated_at}",
            "source: mem0",
            "read_only: true",
            "---",
            "",
            "# Agent Long-Term Memory",
            "",
            "> Automatically generated. Edit memories through Memory Review; manual changes are overwritten.",
            "",
            "## Shared User Facts",
            "",
        ]
        shared = [item for item in records if item.scope is MemoryScope.SHARED]
        lines.extend(self._render_group(shared) or ["_No active shared memories._"])
        lines.extend(["", "## Persona Observations", ""])
        persona_ids = sorted({item.persona_id for item in records if item.persona_id})
        if not persona_ids:
            lines.append("_No active persona observations._")
        for persona_id in persona_ids:
            lines.extend(
                [
                    f"### {self._persona_names.get(persona_id, persona_id)}",
                    "",
                    *self._render_group(
                        [item for item in records if item.persona_id == persona_id]
                    ),
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_group(records: Sequence[LongTermMemory]) -> list[str]:
        lines = []
        for item in records:
            updated = (item.updated_at or item.created_at or "unknown").split("T", 1)[0]
            source = str(item.metadata.get("source_type", "unknown"))
            content = " ".join(item.content.splitlines()).strip()
            if contains_credentials(content):
                content = "[redacted: credential-like content; review in Memory Review]"
            lines.extend(
                [
                    f"- [{item.id}] {content}",
                    f"  - Updated: {updated}",
                    f"  - Source: {source}",
                    f"  - Evidence: {item.metadata.get('source_id', 'unknown')}",
                    f"  - Observed: {item.metadata.get('source_created_at', 'unknown')}",
                    f"  - State: {item.metadata.get('journal_state', 'active')}",
                    f"  - Effective: {item.metadata.get('valid_from') or 'unknown'}",
                ]
            )
        return lines

    def _write_atomic(self, body: str) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, delete=False
        )
        temp_path = Path(handle.name)
        try:
            with handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            temp_path.chmod(0o600)
            os.replace(temp_path, self.path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
