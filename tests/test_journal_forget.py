from __future__ import annotations

from pathlib import Path

import pytest

from riji_agent.memory.journal_forget import apply_forget_plan, forget_plan
from test_journal_lifecycle import attached_service
from test_journal_memory import runtime, write_note


def test_broader_forget_requires_explicit_allowed_selection_and_current_plan(tmp_path: Path) -> None:
    engine, model = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    model.action = "state_change"
    write_note(engine.policy.root, "daily/2026-09-01.md", "暂缓转岗，先做好当前项目。")
    engine.scan()
    engine.process_next()
    service = attached_service(tmp_path, engine)
    plan = forget_plan(service, "m1", "u1")
    assert {row["id"] for row in plan["memories"]} == {"m1", "m2"}
    payload = {"base_id": "m1", "user_id": "u1", "plan_hash": plan["plan_hash"], "confirmation": "DELETE",
               "memory_ids": ["outside-id"], "evidence_ids": []}
    with pytest.raises(ValueError, match="invalid_forget_scope"):
        apply_forget_plan(service, payload)
    assert len(engine.backend.records) == 2
    payload["memory_ids"] = ["m1"]
    service.update_memory("m1", user_id="u1", content="这是人工纠正的历史目标。")
    with pytest.raises(ValueError, match="changed_reload"):
        apply_forget_plan(service, payload)
    payload["plan_hash"] = forget_plan(service, "m1", "u1")["plan_hash"]
    assert apply_forget_plan(service, payload)["selected_count"] == 1
    assert set(engine.backend.records) == {"m2"}


def test_source_selection_suppresses_selected_fragment_and_deletes_its_memory(tmp_path: Path) -> None:
    engine, _ = runtime(tmp_path)
    path = write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    service = attached_service(tmp_path, engine)
    plan = forget_plan(service, "m1", "u1")
    result = apply_forget_plan(service, {"base_id": "m1", "user_id": "u1", "plan_hash": plan["plan_hash"],
        "confirmation": "DELETE", "memory_ids": [], "evidence_ids": [plan["evidence"][0]["id"]]})
    assert result["selected_count"] == 1 and path.is_file()
    engine.scan()
    assert not engine.process_next()
    assert not engine.backend.records
