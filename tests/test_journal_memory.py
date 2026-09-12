from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from riji_agent.memory.journal_engine import JournalMemoryEngine
from riji_agent.memory.journal_extract import JournalMemoryExtractor, parse_decisions
from riji_agent.memory.journal_sources import discover_sources
from riji_agent.memory.journal_store import JournalMemoryStore
from riji_agent.memory.journal_types import JournalMemoryError, JournalMemoryPolicy
from riji_agent.models.types import AssistantTurn
from test_mem0_long_term_memory import FakeBackend


class JournalModel:
    def __init__(self, *, action="new"):
        self.calls = []
        self.action = action

    def complete(self, messages, tools):
        self.calls.append(messages)
        payload = json.loads(messages[-1]["content"])
        if "text" in payload:
            text = payload["text"]
            memories = [] if "没有值得记" in text else [{
                "content": text.lstrip("- "), "kind": "goal", "certainty": "explicit",
                "valid_from": payload["observed_at"], "quotes": [text[:200]],
            }]
            return AssistantTurn(json.dumps({"complete": True, "memories": memories}, ensure_ascii=False))
        return AssistantTurn(json.dumps({"decisions": [{
            "index": index, "action": self.action,
            "target_id": None if self.action == "new" else payload["existing"][0]["id"],
            "reason": "Synthetic fixture decision.",
        } for index in range(len(payload["candidates"]))]}))


def write_note(root, name="daily/2026/08/2026-08-01.md", text="准备转岗，接下来学习产品设计。", extra=""):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{extra}# Journal\n\n## 🧠 Notes\n\n- {text}\n\n## Private section\n\n不应出云的完整内容。\n")
    os.utime(path, (time.time() - 3, time.time() - 3))
    return path


def runtime(tmp_path, *, model=None, **policy_options):
    root = tmp_path / "journal"
    root.mkdir(exist_ok=True)
    policy = JournalMemoryPolicy(root, "u1", ("🧠 Notes",), **policy_options)
    store = JournalMemoryStore(tmp_path / "data" / "journal.sqlite3")
    backend = FakeBackend()
    model = model or JournalModel()
    engine = JournalMemoryEngine(policy, store, backend, model)
    engine.privacy.grant(engine.privacy.binding, dict.fromkeys(("history", "incremental", "organization", "recall"), True))
    return engine, model


def test_recursive_initialization_preserves_files_and_scoped_egress(tmp_path):
    engine, model = runtime(tmp_path)
    path = write_note(engine.policy.root)
    write_note(engine.policy.root, "templates/example.md")
    write_note(engine.policy.root, "daily/.hidden/2026-08-02.md")
    original = path.read_bytes()
    progress = engine.scan()
    assert progress["discovered"] == 1 and not progress["initialized"]
    assert not model.calls
    assert engine.process_next()
    assert engine.store.progress()["initialized"]
    assert len(engine.backend.records) == 1
    record = next(iter(engine.backend.records.values()))
    assert record.metadata["valid_from"] == "2026-08-01"
    assert engine.is_valid(record)
    assert path.read_bytes() == original
    assert "不应出云" not in json.dumps(model.calls, ensure_ascii=False)
    assert "text" not in json.loads(engine.store.rows("SELECT payload FROM evidence")[0]["payload"])


def test_private_and_symlink_sources_are_excluded(tmp_path):
    engine, model = runtime(tmp_path)
    private = write_note(engine.policy.root, extra="---\nprivate: true\n---\n")
    outside = tmp_path / "outside.md"
    outside.write_text("private outside content")
    (private.parent / "linked.md").symlink_to(outside)
    (engine.policy.root / "weekly").symlink_to(tmp_path, target_is_directory=True)
    result = engine.scan()
    assert result["discovered"] == 1
    assert result["sources"][0]["reason"] == "private"
    assert not engine.process_next()
    assert not model.calls


def test_invalid_layout_is_not_empty_success(tmp_path):
    engine, _ = runtime(tmp_path)
    result = engine.scan()
    assert result["error"] == "journal_layout_unrecognized"
    assert not result["initialized"]


def test_restart_and_duplicate_scan_reuse_success(tmp_path):
    engine, model = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    restarted = JournalMemoryEngine(engine.policy, JournalMemoryStore(engine.store.path), engine.backend, model)
    restarted.scan()
    assert not restarted.process_next()
    assert len(model.calls) == 1 and len(engine.backend.records) == 1


def test_old_note_added_later_is_incremental_and_changes_invalidate_immediately(tmp_path):
    engine, _ = runtime(tmp_path)
    path = write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    old = next(iter(engine.backend.records.values()))
    write_note(engine.policy.root, "daily/2020/2020-01-01.md", "以前计划学习写作并持续记录。")
    engine.scan()
    assert engine.process_next()
    assert len(engine.backend.records) == 2
    path.write_text(path.read_text().replace("准备转岗", "决定暂缓转岗"))
    assert not engine.is_valid(old)
    os.utime(path, (time.time() - 3, time.time() - 3))
    engine.scan()
    assert engine.process_next()
    assert len(engine.backend.records) == 3


def test_budget_pause_and_manual_pause_do_not_claim_completion(tmp_path):
    engine, model = runtime(tmp_path, daily_chars=10)
    write_note(engine.policy.root)
    engine.scan()
    engine.store.set_control("paused", "1")
    assert not engine.process_next() and not model.calls
    engine.store.set_control("paused", "0")
    assert engine.process_next() and not model.calls
    progress = engine.store.progress()
    assert not progress["initialized"]
    assert progress["sources"][0]["jobs"][0]["error"] == "journal_daily_budget"


def test_semantic_duplicate_keeps_both_sources(tmp_path):
    engine, model = runtime(tmp_path)
    write_note(engine.policy.root, text="喜欢先看结论，然后再看解释。")
    engine.scan()
    engine.process_next()
    model.action = "duplicate"
    write_note(engine.policy.root, "daily/2026-09-01.md", "回答先说结论更适合我。")
    engine.scan()
    engine.process_next()
    assert len(engine.backend.records) == 1
    assert len(engine.store.rows("SELECT * FROM supports")) == 2


def test_state_changes_preserve_old_record_and_link_series(tmp_path):
    engine, model = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    model.action = "state_change"
    write_note(engine.policy.root, "daily/2026-09-01.md", "决定暂缓转岗，先完成当前项目。")
    engine.scan()
    engine.process_next()
    records = engine.store.rows("SELECT * FROM records ORDER BY valid_from")
    assert len(records) == 2 and records[0]["series"] == records[1]["series"]
    assert records[0]["valid_from"] == "2026-08-01"
    assert engine.store.rows("SELECT kind FROM relations")[0]["kind"] == "state_change"


def test_source_change_during_model_call_never_writes_old_fact(tmp_path):
    engine, model = runtime(tmp_path)
    path = write_note(engine.policy.root)
    original = model.complete
    def change(messages, tools):
        result = original(messages, tools)
        path.unlink()
        return result
    model.complete = change
    engine.scan()
    engine.process_next()
    assert not engine.backend.records
    assert not engine.store.progress()["initialized"]


def test_fabricated_quote_and_incomplete_output_are_rejected(tmp_path):
    engine, _ = runtime(tmp_path)
    write_note(engine.policy.root)
    evidence = next(discover_sources(engine.policy)).evidence[0]
    class BadModel:
        def complete(self, messages, tools):
            return AssistantTurn(json.dumps({"complete": True, "memories": [{
                "content": "不存在的事实", "kind": "fact", "certainty": "explicit",
                "valid_from": None, "quotes": ["这里没有这样的原文"],
            }]}))
    extractor = JournalMemoryExtractor(BadModel(), charge=lambda size: None)
    with pytest.raises(JournalMemoryError, match="invalid_evidence_quote"):
        extractor.extract(evidence)


@pytest.mark.parametrize("action,target", [("delete", "m1"), ("duplicate", "other-user"), ("new", "m1")])
def test_invalid_relation_actions_and_ids_are_rejected(action, target):
    with pytest.raises(JournalMemoryError):
        parse_decisions({"decisions": [{"index": 0, "action": action, "target_id": target, "reason": "x"}]}, 1, {"m1"})
