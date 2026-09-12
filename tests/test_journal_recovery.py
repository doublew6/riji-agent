from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

from riji_agent.memory.backend import MemoryBackendError
from riji_agent.memory.capture import ExtractedMemories
from riji_agent.memory.service import CaptureProcessor
from riji_agent.memory.journal_engine import JournalMemoryEngine
from test_journal_lifecycle import attached_service
from test_journal_memory import runtime, write_note


def test_recently_modified_source_waits_for_stable_save(tmp_path: Path) -> None:
    engine, model = runtime(tmp_path)
    path = write_note(engine.policy.root)
    os.utime(path, None)
    progress = engine.scan()
    assert progress["sources"][0]["reason"] == "source_still_changing"
    assert not engine.process_next() and not model.calls
    os.utime(path, (time.time() - 3, time.time() - 3))
    engine.scan()
    assert engine.process_next()
    assert engine.store.progress()["initialized"]


def test_lost_backend_response_reuses_the_existing_write(tmp_path: Path) -> None:
    engine, model = runtime(tmp_path)
    write_note(engine.policy.root)
    original = engine.backend.add
    def uncertain(*args, **kwargs):
        original(*args, **kwargs)
        raise MemoryBackendError("mem0_unavailable")
    engine.backend.add = uncertain
    engine.scan()
    assert engine.process_next()
    assert len(engine.backend.records) == 1
    engine.backend.add = original
    engine.store.retry()
    assert engine.process_next()
    assert len(engine.backend.records) == 1 and engine.store.progress()["initialized"]
    assert len(model.calls) == 1


def test_native_statement_can_support_journal_fact_after_diary_is_withdrawn(tmp_path: Path) -> None:
    engine, _ = runtime(tmp_path)
    path = write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    service = attached_service(tmp_path, engine)
    content = engine.backend.get("m1").content
    class Extractor:
        def extract(self, *args, **kwargs):
            return ExtractedMemories((content,), ())
    service.enqueue_capture(source_request_id="native-reaffirmation", user_id="u1", persona_id="coach",
                            session_id="chat", content=content, source_message_id=101, source_created_at="2026-09-09")
    assert CaptureProcessor(service.backend, service.operations, Extractor(), None).process_next()
    assert len(engine.backend.records) == 1
    path.unlink()
    facts = service.retrieve("目标", user_id="u1", persona_id="coach").shared
    assert not facts
    assert service.backend.get("m1").metadata["source_id"] == "conversation/101"
    # Native support remains local; it cannot widen the withdrawn diary permission.


def test_model_empty_result_is_reported_separately_from_pending(tmp_path: Path) -> None:
    engine, _ = runtime(tmp_path)
    write_note(engine.policy.root, text="这段没有值得记的长期内容，只是一次输入测试。")
    engine.scan()
    engine.process_next()
    progress = engine.store.progress()
    assert progress["initialized"] and progress["sources"][0]["outcome"] == "no_durable_memory"
    assert not engine.backend.records


def test_bom_private_frontmatter_is_excluded_before_any_model_call(tmp_path: Path) -> None:
    engine, model = runtime(tmp_path)
    write_note(engine.policy.root, extra="\ufeff---\nprivate: true\n---\n")
    assert engine.scan()["sources"][0]["reason"] == "private"
    assert not engine.process_next() and not model.calls


def test_fenced_examples_are_excluded_and_padding_does_not_shift_evidence(tmp_path: Path) -> None:
    from riji_agent.memory.journal_sources import discover_sources
    engine, _ = runtime(tmp_path)
    path = write_note(engine.policy.root, text="真实记录：正在学习产品设计。", extra="---\nprivate: false\n---\n\n\n")
    text = path.read_text().replace("## Private section", "~~~\n这是代码示例，不是用户真实经历。\n~~~\n\n## Private section") + "\n" * 20
    path.write_text(text)
    os.utime(path, (time.time() - 3, time.time() - 3))
    evidence = next(discover_sources(engine.policy)).evidence
    assert len(evidence) == 1
    assert path.read_text().splitlines()[evidence[0].line - 1] == evidence[0].text


def test_revoked_running_job_can_resume_after_scope_is_allowed_again(tmp_path: Path) -> None:
    engine, model = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.scan()
    assert engine.store.claim()
    JournalMemoryEngine(replace(engine.policy, enabled=False), engine.store, engine.backend, model)
    resumed = JournalMemoryEngine(engine.policy, engine.store, engine.backend, model)
    resumed.scan()
    assert resumed.process_next()
    assert resumed.store.progress()["initialized"]


def test_organization_failure_preserves_valid_fact_retrieval(tmp_path: Path) -> None:
    engine, _ = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    service = attached_service(tmp_path, engine)
    def unavailable(*args, **kwargs):
        raise RuntimeError("unavailable")
    service.operations.organization.latest = unavailable
    context = service.retrieve("目标", user_id="u1", persona_id="coach")
    assert len(context.shared) == 1 and "暂不可用" in context.notice


def test_template_bold_heading_matches_explicit_plain_section(tmp_path: Path) -> None:
    from riji_agent.memory.journal_sources import discover_sources
    engine, _ = runtime(tmp_path)
    path = write_note(engine.policy.root)
    path.write_text(path.read_text().replace("## 🧠 Notes", "### 🧠  **Notes**"))
    os.utime(path, (time.time() - 3, time.time() - 3))
    evidence = next(discover_sources(engine.policy)).evidence
    assert len(evidence) == 1 and evidence[0].section == "🧠  **Notes**"
    assert "不应出云" not in evidence[0].text
