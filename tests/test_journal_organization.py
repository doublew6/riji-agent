from __future__ import annotations

import json
from pathlib import Path

from riji_agent.memory.journal_organization import JournalOrganization
from riji_agent.memory.models import MemoryScope
from riji_agent.memory.organization import MemoryOrganizer
from riji_agent.memory.worker import MemoryWorker
from riji_agent.models.types import AssistantTurn
from test_journal_lifecycle import attached_service
from test_journal_memory import JournalModel, runtime, write_note


class EventExtractor(JournalModel):
    def complete(self, messages, tools):
        result = super().complete(messages, tools)
        payload = json.loads(result.content)
        for item in payload.get("memories", []):
            item["kind"] = "event"
        return AssistantTurn(json.dumps(payload, ensure_ascii=False))


class ObservationModel:
    def __init__(self) -> None:
        self.batches = []

    def complete(self, messages, tools):
        batch = json.loads(messages[-1]["content"])
        self.batches.append(batch)
        ids = [item["id"] for item in batch]
        support = [item["id"] for item in batch if "深呼吸有效" in item["content"] and item["independent_days"]]
        counter = [item["id"] for item in batch if "深呼吸失败" in item["content"]]
        observations = [{"summary": "深呼吸可能对部分紧张场景有帮助。", "support_ids": support, "counter_ids": counter,
                         "limitation": "也有失败的反例，不是总能奏效。" if counter else "只有少量经历支持，需要继续验证。"}] if len(support) >= 2 else []
        return AssistantTurn(json.dumps({"topics": [{"category": "events", "title": "记录的经历", "summary": "保留不同时间的具体经历。",
            "evidence_ids": ids}], "comparisons": [], "time_bound_ids": [], "observations": observations}, ensure_ascii=False))


def test_cross_batch_observation_enters_recall_and_counterexample_updates_it(tmp_path: Path) -> None:
    engine, _ = runtime(tmp_path, model=EventExtractor(), daily_chars=500000)
    first = write_note(engine.policy.root, "daily/2026-01-01.md", "面试前试了深呼吸有效，紧张减轻了。")
    for day in range(2, 25):
        write_note(engine.policy.root, f"daily/2026-01-{day:02}.md", f"当天完成了第{day}次常规项目记录。")
    write_note(engine.policy.root, "daily/2026-02-01.md", "演讲之前做了深呼吸有效，放松了下来。")
    engine.scan()
    while engine.process_next():
        pass
    original_search = engine.backend.search
    def related_search(query, **kwargs):
        if "深呼吸" in query:
            return tuple(item for item in engine.backend.records.values() if "深呼吸" in item.content)
        return original_search(query, **kwargs)
    engine.backend.search = related_search
    service = attached_service(tmp_path, engine)
    model = ObservationModel()
    organizer = MemoryOrganizer(service.backend, service.operations.organization, model)
    service.operations.organization.request("u1")
    for _ in range(10):
        if not organizer.process_next():
            break
    facts = service.retrieve("深呼吸", user_id="u1", persona_id="coach").shared
    assert facts[0].metadata["source_type"] == "derived-observation"
    assert "少量经历" in facts[0].content
    write_note(engine.policy.root, "daily/2026-03-01.md", "这次重要谈话前深呼吸失败，仍旧很紧张。")
    engine.scan()
    while engine.process_next():
        pass
    service.operations.organization.request("u1")
    assert organizer.process_next()
    facts = service.retrieve("深呼吸", user_id="u1", persona_id="coach").shared
    assert "失败的反例" in facts[0].content
    assert len(facts[0].metadata["evidence_ids"]) == 3
    assert all(len(batch) <= 12 for batch in model.batches)
    first.unlink()
    facts = service.retrieve("深呼吸", user_id="u1", persona_id="coach").shared
    assert not any(item.metadata["source_type"] == "derived-observation" for item in facts)


def test_summaries_do_not_count_as_independent_experiences(tmp_path: Path) -> None:
    engine, _ = runtime(tmp_path, model=EventExtractor())
    write_note(engine.policy.root, "daily/2026-01-01.md", "面试前试了深呼吸有效，紧张减轻了。")
    write_note(engine.policy.root, "weekly/2026-01-08.md", "回顾：面试前试了深呼吸有效，紧张减轻了。")
    engine.scan()
    while engine.process_next():
        pass
    service = attached_service(tmp_path, engine)
    model = ObservationModel()
    service.operations.organization.request("u1")
    organizer = MemoryOrganizer(service.backend, service.operations.organization, model)
    assert organizer.process_next()
    assert not any(group["observations"] for group in service.operations.organization.latest("u1", ready_only=True)["report"]["groups"])
    assert all(not item["independent_days"] for batch in model.batches for item in batch if "回顾" in item["content"])


def test_organization_respects_pause_and_shared_daily_budget(tmp_path: Path) -> None:
    engine, _ = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    service = attached_service(tmp_path, engine)
    model = ObservationModel()
    engine.store.set_control("paused", "1")
    service.operations.organization.request("u1")
    organizer = MemoryOrganizer(service.backend, service.operations.organization, model)
    assert organizer.process_next() and not model.batches
    engine.store.set_control("paused", "0")
    service.operations.organization._conn.execute("UPDATE memory_organization_runs SET available_at=''")
    service.operations.organization._conn.commit()
    engine.store.reserve_daily(engine.policy, engine.policy.daily_chars - engine.store.progress()["budgets"][0]["chars"])
    assert organizer.process_next() and not model.batches
    assert service.operations.organization.latest("u1")["error_code"] == "journal_daily_budget"


def test_busy_capture_cannot_starve_journal_or_organization() -> None:
    class Busy:
        def __init__(self) -> None:
            self.calls = 0
        def process_next(self) -> bool:
            self.calls += 1
            return True
    capture, journal, organizer = Busy(), Busy(), Busy()
    worker = MemoryWorker(capture, journal=journal, organizer=organizer)
    for _ in range(9):
        worker.run_once()
    assert [item.calls for item in (capture, journal, organizer)] == [3, 3, 3]
