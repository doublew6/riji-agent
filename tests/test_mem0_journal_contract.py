"""HTTP adapter tests using the inspected Mem0 2.0.20 method signatures."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from runpy import run_path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from riji_agent.memory.backend import MemoryBackendError
from riji_agent.memory.journal_types import fingerprint
from riji_agent.memory.mem0 import Mem0Client


class SDKFixture:
    def __init__(self, path: Path) -> None:
        self.rows = {}
        connection = sqlite3.connect(path, check_same_thread=False)
        connection.execute("CREATE TABLE history(memory_id TEXT,old_memory TEXT,new_memory TEXT)")
        self.db = SimpleNamespace(db_path=str(path), connection=connection, _lock=threading.Lock())
        self.vector_store = SimpleNamespace(list=self.list, get=lambda *, vector_id: self.rows.get(vector_id))
        self.writes = 0

    def list(self, filters=None, top_k=100):
        return [[row for row in self.rows.values() if all(row.payload.get(key) == value for key, value in (filters or {}).items())][:top_k]]

    def get(self, memory_id: str):
        row = self.rows.get(memory_id)
        if row is None:
            return None
        return {"id": row.id, "memory": row.payload["data"], "user_id": row.payload["user_id"],
                "agent_id": row.payload.get("agent_id"), "metadata": row.payload["metadata"]}

    def get_all(self, *, filters=None, top_k=20, show_expired=False):
        return {"results": [self.get(row.id) for row in self.list(filters, top_k)[0]]}

    def add(self, messages, *, user_id=None, agent_id=None, metadata=None, infer=True):
        assert infer is False and len(messages) == 1
        self.writes += 1
        memory_id = f"m{self.writes}"
        content = messages[0]["content"]
        payload = dict(metadata, data=content, user_id=user_id, agent_id=agent_id, metadata=metadata)
        self.rows[memory_id] = SimpleNamespace(id=memory_id, payload=payload)
        with self.db._lock, self.db.connection:
            self.db.connection.execute("INSERT INTO history VALUES (?,NULL,?)", (memory_id, content))
        return {"results": [{"id": memory_id, "memory": content, "event": "ADD"}]}

    def delete(self, memory_id: str) -> None:
        row = self.rows.pop(memory_id)
        with self.db._lock, self.db.connection:
            self.db.connection.execute("INSERT INTO history VALUES (?,?,NULL)", (memory_id, row.payload["data"]))


def api_fixture(tmp_path: Path):
    module = run_path(str(Path(__file__).parents[1] / "infra/mem0/riji_routes.py"))
    sdk = SDKFixture(tmp_path / "history.db")
    app = FastAPI()
    def admin(x_api_key: str = Header(default="")) -> None:
        if x_api_key != "admin-fixture":
            raise HTTPException(403, "admin_required")
    module["install_routes"](app, lambda: sdk, admin)
    http = TestClient(app)
    return sdk, http, Mem0Client("http://testserver", "admin-fixture", client=http)


def test_explicit_write_is_idempotent_hydrated_and_admin_only(tmp_path: Path) -> None:
    sdk, http, client = api_fixture(tmp_path)
    metadata = {"journal_operation_id": fingerprint("one"), "scope": "shared", "status": "active"}
    assert http.post("/riji/memories/explicit", json={"content": "fact", "user_id": "u1", "operation_id": fingerprint("one")}).status_code == 403
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.add_explicit("fact", user_id="u1", metadata=metadata), range(2)))
    assert sdk.writes == 1 and results[0][0].id == results[1][0].id
    assert results[0][0].user_id == "u1" and results[0][0].metadata["journal_operation_id"] == fingerprint("one")
    assert client.find_operation(fingerprint("one"), user_id="u1", content="fact").id == "m1"
    assert client.find_operation(fingerprint("one"), user_id="u2", content="fact") is None
    with pytest.raises(MemoryBackendError, match="409"):
        client.add_explicit("changed", user_id="u1", metadata=metadata)


def test_purge_removes_only_selected_history_and_prevents_delayed_rewrite(tmp_path: Path) -> None:
    sdk, _, client = api_fixture(tmp_path)
    for value in ("one", "two"):
        client.add_explicit(value, user_id="u1", metadata={"journal_operation_id": fingerprint(value)})
    client.purge("m1")
    client.purge("m1")
    assert set(sdk.rows) == {"m2"}
    assert sdk.db.connection.execute("SELECT * FROM history WHERE memory_id='m1'").fetchall() == []
    assert sdk.db.connection.execute("SELECT new_memory FROM history WHERE memory_id='m2'").fetchone()[0] == "two"
    with pytest.raises(MemoryBackendError, match="410"):
        client.add_explicit("one", user_id="u1", metadata={"journal_operation_id": fingerprint("one")})
    assert sdk.writes == 2


def test_complete_export_is_scoped_and_never_silently_truncated(tmp_path: Path) -> None:
    sdk, http, client = api_fixture(tmp_path)
    for value, user in (("one", "u1"), ("two", "u2"), ("three", "u1")):
        client.add_explicit(value, user_id=user, metadata={"journal_operation_id": fingerprint(value)})
    assert {item.content for item in client.export_memories(user_id="u1")} == {"one", "three"}
    response = http.get("/riji/memories/export", params={"user_id": "u1", "limit": 2}, headers={"X-API-Key": "admin-fixture"})
    assert response.status_code == 409
    assert http.get("/riji/memories/export", params={"user_id": "u1"}).status_code == 403
