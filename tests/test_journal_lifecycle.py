from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import pytest

from riji_agent.memory.backend import MemoryBackendError
from riji_agent.memory.journal_backend import JournalEvidenceBackend
from riji_agent.memory.journal_cli import run_journal_command
from riji_agent.memory.journal_engine import JournalMemoryEngine
from riji_agent.memory.journal_extract import parse_candidate, parse_decisions
from riji_agent.memory.journal_sources import discover_sources
from riji_agent.memory.journal_transfer import JournalMemoryTransfer
from riji_agent.memory.journal_types import JournalMemoryError
from riji_agent.memory.models import MemoryScope
from test_journal_memory import JournalModel, runtime, write_note
from test_mem0_long_term_memory import FakeBackend, _service


def attached_service(tmp_path: Path, engine: JournalMemoryEngine):
    backend = JournalEvidenceBackend(engine.backend, engine)
    service, _, _ = _service(tmp_path, backend)
    service.journal = engine
    return service


def test_effective_state_is_independent_of_processing_order(tmp_path: Path) -> None:
    for first, second in (("2026-08-01", "2026-09-01"), ("2026-09-01", "2026-08-01")):
        folder = tmp_path / first
        folder.mkdir()
        engine, model = runtime(folder)
        contents = {"2026-08-01": "准备转岗，正在学习产品设计。", "2026-09-01": "决定暂缓转岗，先完成当前项目。"}
        write_note(engine.policy.root, f"daily/{first}.md", contents[first])
        engine.scan()
        assert engine.process_next()
        model.action = "state_change"
        write_note(engine.policy.root, f"daily/{second}.md", contents[second])
        engine.scan()
        assert engine.process_next()
        backend = JournalEvidenceBackend(engine.backend, engine)
        facts = backend.search("现在", user_id="u1", scope=MemoryScope.SHARED)
        assert [item.metadata["valid_from"] for item in facts] == ["2026-09-01", "2026-08-01"]
        assert [item.metadata["journal_state"] for item in facts] == ["current", "historical"]
        assert "准备转岗" in next(item.content for item in facts if item.metadata["journal_state"] == "historical")


def test_manual_correction_survives_source_change_and_conflicting_extraction(tmp_path: Path) -> None:
    engine, model = runtime(tmp_path)
    path = write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    service = attached_service(tmp_path, engine)
    service.update_memory("m1", user_id="u1", content="已经确定留在目前的岗位。")
    path.unlink()
    model.action = "state_change"
    write_note(engine.policy.root, "daily/2026-09-01.md", "又开始考虑转岗了，需要进一步评估。")
    engine.scan()
    engine.process_next()
    facts = service.retrieve("现在", user_id="u1", persona_id="coach").shared
    assert len(facts) == 1 and facts[0].id == "m2"
    # The correction remains locally reviewable; its deleted source no longer
    # grants cloud processing permission to the historical derived record.
    assert service.backend.get("m1").content == "已经确定留在目前的岗位。"
    assert engine.backend.get("m1").content == "已经确定留在目前的岗位。"


def test_source_change_after_backend_write_stays_out_of_recall(tmp_path: Path) -> None:
    engine, _ = runtime(tmp_path)
    path = write_note(engine.policy.root)
    original = engine.backend.add
    def delayed_write(*args, **kwargs):
        result = original(*args, **kwargs)
        path.unlink()
        return result
    engine.backend.add = delayed_write
    engine.scan()
    engine.process_next()
    assert not JournalEvidenceBackend(engine.backend, engine).search("目标", user_id="u1", scope=MemoryScope.SHARED)
    assert not engine.store.rows("SELECT * FROM records")


def test_pause_during_model_call_prevents_write(tmp_path: Path) -> None:
    engine, model = runtime(tmp_path)
    write_note(engine.policy.root)
    original = model.complete
    def pause(messages, tools):
        result = original(messages, tools)
        engine.store.set_control("paused", "1")
        return result
    model.complete = pause
    engine.scan()
    engine.process_next()
    assert not engine.backend.records
    assert engine.store.rows("SELECT status FROM evidence")[0]["status"] == "pending"


def test_changed_section_scope_revokes_old_running_engine(tmp_path: Path) -> None:
    engine, _ = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    JournalMemoryEngine(replace(engine.policy, sections=("Private section",)), engine.store, engine.backend, JournalModel())
    assert not engine.is_valid(engine.backend.get("m1"))


def test_symlink_parent_swap_never_reuses_authorized_evidence(tmp_path: Path) -> None:
    engine, _ = runtime(tmp_path)
    path = write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    original_parent = path.parent
    moved = engine.policy.root / "daily" / "moved"
    original_parent.rename(moved)
    original_parent.symlink_to(moved, target_is_directory=True)
    assert not engine.is_valid(engine.backend.get("m1"))


def test_source_frontmatter_line_location_is_original_file_line(tmp_path: Path) -> None:
    engine, _ = runtime(tmp_path)
    path = write_note(engine.policy.root, extra="---\nprivate: false\n---\n")
    evidence = next(discover_sources(engine.policy)).evidence[0]
    assert path.read_text().splitlines()[evidence.line - 1] == evidence.text


@pytest.mark.parametrize("field,value", [("kind", []), ("certainty", {}), ("quotes", ["invented quote"]), ("valid_from", "2099-99-99")])
def test_invalid_candidate_types_are_sanitized(tmp_path: Path, field: str, value: object) -> None:
    engine, _ = runtime(tmp_path)
    write_note(engine.policy.root)
    evidence = next(discover_sources(engine.policy)).evidence[0]
    item = {"content": "准备转岗", "kind": "goal", "certainty": "explicit", "valid_from": None, "quotes": [evidence.text]}
    item[field] = value
    with pytest.raises(JournalMemoryError):
        parse_candidate(item, evidence)


@pytest.mark.parametrize("action,target", [([], None), ("related", []), ("duplicate", {"id": "m1"})])
def test_invalid_decision_types_are_sanitized(action: object, target: object) -> None:
    with pytest.raises(JournalMemoryError):
        parse_decisions({"decisions": [{"index": 0, "action": action, "target_id": target, "reason": "test"}]}, 1, {"m1"})


def test_one_withdrawn_source_does_not_remove_other_support(tmp_path: Path) -> None:
    engine, model = runtime(tmp_path)
    first = write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    model.action = "duplicate"
    second = write_note(engine.policy.root, "daily/2026-08-02.md", "目前仍准备转岗，继续学产品设计。")
    engine.scan()
    engine.process_next()
    first.unlink()
    assert engine.is_valid(engine.backend.get("m1"))
    second.unlink()
    assert not engine.is_valid(engine.backend.get("m1"))


def test_delete_clears_audit_snapshot_and_retry_caches(tmp_path: Path) -> None:
    engine, _ = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    service = attached_service(tmp_path, engine)
    old = engine.backend.get("m1").content
    service.update_memory("m1", user_id="u1", content="人工纠正后的长期目标。")
    service.delete_memory("m1", user_id="u1")
    assert all(item.before is None and item.after is None for item in service.operations.list_changes(user_id="u1"))
    assert old not in service.snapshot.path.read_text()
    assert "人工纠正" not in service.snapshot.path.read_text()
    engine.scan()
    engine.store.retry()
    assert not engine.process_next()
    assert not engine.backend.records
    assert service.backend.is_suppressed(old, user_id="u1")


def test_delete_backend_failure_is_hidden_and_retried(tmp_path: Path) -> None:
    engine, _ = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    service = attached_service(tmp_path, engine)
    def unavailable(memory_id: str) -> None:
        raise MemoryBackendError("mem0_unavailable")
    engine.backend.purge = unavailable
    with pytest.raises(MemoryBackendError):
        service.delete_memory("m1", user_id="u1")
    assert not service.retrieve("目标", user_id="u1", persona_id="coach").shared
    assert engine.store.progress()["cleanup_pending"] == 1
    engine.backend.purge = engine.backend.delete
    assert engine.process_cleanup()
    assert engine.store.progress()["cleanup_pending"] == 0


def test_export_restore_retains_provenance_manual_correction_and_suppression(tmp_path: Path) -> None:
    engine, _ = runtime(tmp_path)
    write_note(engine.policy.root)
    write_note(engine.policy.root, "daily/2026-08-02.md", "每周学习三次，每次半小时。")
    engine.scan()
    while engine.process_next():
        pass
    service = attached_service(tmp_path, engine)
    service.update_memory("m2", user_id="u1", content="每周学习两次，这是本人纠正。")
    path = tmp_path / "bundle.json"
    transfer = JournalMemoryTransfer(service.backend)
    assert transfer.export(path)["memories"] == 2
    assert path.stat().st_mode & 0o777 == 0o600
    assert "text" not in json.loads(json.loads(path.read_text())["tables"]["evidence"][0]["payload"])
    service.delete_memory("m1", user_id="u1")
    fresh = FakeBackend()
    fresh.next_id = 100
    restored_engine = JournalMemoryEngine(engine.policy, engine.store, fresh, JournalModel())
    restored = JournalEvidenceBackend(fresh, restored_engine)
    result = JournalMemoryTransfer(restored).restore(path, apply=True)
    assert result["restored"] == 1 and result["suppressed"] == 1
    facts = restored.search("目标", user_id="u1", scope=MemoryScope.SHARED)
    assert len(facts) == 1 and facts[0].metadata["manually_corrected"]
    assert facts[0].content == "每周学习两次，这是本人纠正。"
    assert not restored_engine.process_next()


def test_restore_rejects_different_owner_and_nonempty_target(tmp_path: Path) -> None:
    engine, _ = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    transfer = JournalMemoryTransfer(JournalEvidenceBackend(engine.backend, engine))
    path = tmp_path / "bundle.json"
    transfer.export(path)
    assert transfer.restore(path)["mode"] == "dry-run"
    with pytest.raises(JournalMemoryError, match="empty_target"):
        transfer.restore(path, apply=True)
    payload = json.loads(path.read_text())
    payload["user_id"] = "u2"
    path.write_text(json.dumps(payload))
    with pytest.raises(JournalMemoryError, match="invalid_memory_bundle"):
        transfer.restore(path, apply=True)
    assert len(engine.backend.records) == 1


def test_plan_cli_scans_without_model_and_status_reports_coverage(tmp_path: Path, capsys) -> None:
    engine, model = runtime(tmp_path)
    write_note(engine.policy.root)
    service = attached_service(tmp_path, engine)
    args = argparse.Namespace(journal_action="plan", max_jobs=10)
    assert run_journal_command(args, service) == 0
    assert not model.calls
    assert json.loads(capsys.readouterr().out)["pending"] == 1
    args.journal_action = "initialize"
    assert run_journal_command(args, service) == 0
    assert json.loads(capsys.readouterr().out)["initialized"]


def test_restore_remaps_relation_targets_and_resumes_after_lost_response(tmp_path: Path) -> None:
    engine, model = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    model.action = "state_change"
    write_note(engine.policy.root, "daily/2026-09-01.md", "决定暂缓转岗，先完成当前项目。")
    engine.scan()
    engine.process_next()
    path = tmp_path / "bundle.json"
    JournalMemoryTransfer(JournalEvidenceBackend(engine.backend, engine)).export(path)
    fresh = FakeBackend()
    fresh.next_id = 100
    target = JournalMemoryEngine(engine.policy, engine.store, fresh, JournalModel())
    transfer = JournalMemoryTransfer(JournalEvidenceBackend(fresh, target))
    original = fresh.add
    def uncertain(*args, **kwargs):
        original(*args, **kwargs)
        raise MemoryBackendError("mem0_unavailable")
    fresh.add = uncertain
    with pytest.raises(MemoryBackendError):
        transfer.restore(path, apply=True)
    assert not transfer.backend.search("目标", user_id="u1", scope=MemoryScope.SHARED)
    fresh.add = original
    assert transfer.restore(path, apply=True)["restored"] == 2
    newer = next(item for item in fresh.records.values() if "暂缓" in item.content)
    assert newer.metadata["relation_target"] in fresh.records
    assert newer.metadata["relation_target"] != "m1"
    assert len(fresh.records) == 2


def test_restore_dry_run_rejects_invalid_evidence_reference(tmp_path: Path) -> None:
    engine, _ = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    transfer = JournalMemoryTransfer(JournalEvidenceBackend(engine.backend, engine))
    path = tmp_path / "bundle.json"
    transfer.export(path)
    payload = json.loads(path.read_text())
    payload["tables"]["supports"][0]["evidence_id"] = "missing"
    path.write_text(json.dumps(payload))
    with pytest.raises(JournalMemoryError, match="invalid_memory_bundle_support"):
        transfer.restore(path)


def test_deletion_clears_linked_reason_without_deleting_other_fact(tmp_path: Path) -> None:
    engine, model = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    model.action = "enrich"
    write_note(engine.policy.root, "daily/2026-09-01.md", "计划每周学习三次，每次半小时。")
    engine.scan()
    engine.process_next()
    service = attached_service(tmp_path, engine)
    service.delete_memory("m1", user_id="u1")
    remaining = engine.backend.get("m2")
    assert remaining.metadata["relation_target"] is None
    assert remaining.metadata["relation_reason"] == "Related memory was forgotten."
    assert len(service.retrieve("学习", user_id="u1", persona_id="coach").shared) == 1
