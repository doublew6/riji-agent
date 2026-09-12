"""Admin-only explicit writes, complete local exports and history erasure.

Installed into the pinned Mem0 server image; no journal filesystem access.
"""

from __future__ import annotations

import fcntl
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field


class ExplicitWrite(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    user_id: str = Field(min_length=1, max_length=200)
    scope: Literal["shared", "persona"] = "shared"
    persona_id: Optional[str] = None
    operation_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    metadata: dict[str, Any] = Field(default_factory=dict)


@contextmanager
def _write_lock(memory: Any) -> Iterator[None]:
    path = Path(memory.db.db_path).parent / "riji-explicit-write.lock"
    with path.open("a") as handle:
        path.chmod(0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _find_operation(memory: Any, user_id: str, operation_id: str) -> list[dict[str, Any]]:
    rows = memory.vector_store.list(filters={"user_id": user_id,
                                    "journal_operation_id": operation_id}, top_k=2)[0]
    if len(rows) > 1:
        raise HTTPException(409, "ambiguous_operation")
    return [memory.get(str(row.id)) for row in rows]


def _explicit_write(memory: Any, payload: ExplicitWrite) -> dict[str, Any]:
    if (payload.scope == "persona") != bool(payload.persona_id):
        raise HTTPException(400, "invalid_scope")
    with _write_lock(memory):
        if _forgotten_operation(memory, payload.user_id, payload.operation_id):
            raise HTTPException(410, "operation_forgotten")
        existing = _find_operation(memory, payload.user_id, payload.operation_id)
        if existing:
            row = existing[0]
            metadata = row.get("metadata") or {}
            if (row.get("memory") != payload.content or row.get("agent_id") != payload.persona_id
                    or metadata.get("scope", "shared") != payload.scope):
                raise HTTPException(409, "operation_payload_changed")
            return {"results": existing}
        metadata = dict(payload.metadata, journal_operation_id=payload.operation_id, scope=payload.scope)
        result = memory.add([{"role": "user", "content": payload.content}], user_id=payload.user_id,
                            agent_id=payload.persona_id, metadata=metadata, infer=False)
        return {"results": [memory.get(row["id"]) for row in result["results"]]}


def _purge(memory: Any, memory_id: str) -> dict[str, bool]:
    with _write_lock(memory):
        row = memory.vector_store.get(vector_id=memory_id)
        if row is not None:
            _forgotten_operation(memory, row.payload.get("user_id", ""),
                                 row.payload.get("journal_operation_id", ""), remember=True)
            memory.delete(memory_id)
        database = memory.db
        with database._lock, database.connection:
            columns = {row[1] for row in database.connection.execute("PRAGMA table_info(history)")}
            if not {"memory_id", "old_memory", "new_memory"} <= columns:
                raise HTTPException(503, "unsupported_history_schema")
            database.connection.execute("DELETE FROM history WHERE memory_id=?", (memory_id,))
    return {"ok": True}


def _forgotten_operation(memory: Any, user_id: str, operation_id: str, *, remember: bool = False) -> bool:
    database = memory.db
    with database._lock, database.connection:
        database.connection.execute("CREATE TABLE IF NOT EXISTS riji_forgotten_operations "
                                    "(user_id TEXT, operation_id TEXT, PRIMARY KEY(user_id,operation_id))")
        if remember and operation_id:
            database.connection.execute("INSERT OR IGNORE INTO riji_forgotten_operations VALUES (?,?)",
                                        (user_id, operation_id))
        return database.connection.execute("SELECT 1 FROM riji_forgotten_operations WHERE user_id=? AND operation_id=?",
                                           (user_id, operation_id)).fetchone() is not None


def install_routes(app: FastAPI, get_memory: Callable[[], Any], require_admin: Callable) -> None:
    # The SDK's INFO messages can contain full memory bodies during updates.
    logging.getLogger("mem0.memory.main").disabled = True

    @app.post("/riji/memories/explicit", dependencies=[Depends(require_admin)])
    def explicit_write(payload: ExplicitWrite) -> dict[str, Any]:
        try:
            return _explicit_write(get_memory(), payload)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "explicit_write_unavailable") from None

    @app.get("/riji/memories/operation", dependencies=[Depends(require_admin)])
    def find_operation(user_id: str, operation_id: str) -> dict[str, Any]:
        try:
            memory = get_memory()
            with _write_lock(memory):
                return {"results": _find_operation(memory, user_id, operation_id)}
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "operation_lookup_unavailable") from None

    _install_local_data_routes(app, get_memory, require_admin)


def _install_local_data_routes(app: FastAPI, get_memory: Callable[[], Any], require_admin: Callable) -> None:
    @app.get("/riji/memories/export", dependencies=[Depends(require_admin)])
    def export_memories(user_id: str = Query(min_length=1), limit: int = Query(default=10001, ge=2, le=100001)) -> dict:
        try:
            result = get_memory().get_all(filters={"user_id": user_id}, top_k=limit, show_expired=True)
            rows = result["results"]
            if len(rows) >= limit:
                raise HTTPException(409, "export_limit_reached")
            return {"results": rows, "complete": True}
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "export_unavailable") from None

    @app.delete("/riji/memories/{memory_id}/purge", dependencies=[Depends(require_admin)])
    def purge(memory_id: str) -> dict[str, bool]:
        try:
            return _purge(get_memory(), memory_id)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "purge_unavailable") from None
