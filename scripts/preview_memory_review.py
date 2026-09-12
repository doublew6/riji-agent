"""Serve the real review UI with synthetic fixtures only, on loopback port 18765."""

from __future__ import annotations

import json
import sys
import tempfile
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import uvicorn
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_mem0_long_term_memory import FakeBackend, FakeExtractor, _record, _service, _settings
from riji_agent.memory.models import MemoryScope, NewMemoryChange
from riji_agent.memory.organization import MemoryOrganizer
from riji_agent.memory.review import build_memory_review_router
from riji_agent.memory import review_ui
from riji_agent.memory.service import CaptureProcessor
from riji_agent.memory.worker import MemoryWorker
from riji_agent.models.types import AssistantTurn


class ExampleProvider:
    def complete(self, messages, tools):
        ids = [item["id"] for item in json.loads(messages[-1]["content"])]
        if ids == ["m6"]:
            topics = [("observations", "推进方式的观察", "这位导师认为，小步骤可能更适合推进计划；仍需后续验证。", ids)]
            comparisons = []
        else:
            topics = [
                ("preferences", "先给结论，再展开", "偏好直接说明结论，并在需要时补充推理。两条记忆可能表达同一个偏好。", ["m1", "m2"]),
                ("goals", "跑步计划的调整", "3 月曾计划每周跑步三次；9 月的自述调整为一次。保留时间线以免把旧计划当成现状。", ["m3", "m4"]),
                ("work", "个人工具的长期建设", "正在开发个人日记 Agent，并保持每周五复盘的习惯。", ["m5", "m7"]),
            ]
            comparisons = [
                {"kind": "duplicate", "evidence_ids": ["m1", "m2"], "reason": "两条都指向先给结论的沟通偏好。核对后可归档一条，保留各自原始出处。"},
                {"kind": "update", "evidence_ids": ["m3", "m4"], "reason": "同一个跑步计划有明确时间变化。新旧记录可以组成时间线，旧计划不应直接当作当前频次。"},
            ]
        return AssistantTurn(json.dumps({
            "topics": [dict(category=c, title=t, summary=s, evidence_ids=e) for c, t, s, e in topics],
            "comparisons": comparisons, "time_bound_ids": [mid for mid in ids if mid in {"m3", "m4", "m6"}],
        }, ensure_ascii=False))


def build_preview(directory: Path) -> FastAPI:
    content = [
        "希望先看到结论，再看推理。", "回答先给结论，必要时再展开。",
        "2026-03-01：计划每周跑步三次。", "2026-09-01：每周跑步已调整为一次。",
        "正在开发个人日记 Agent。", "可能更适合用小步骤推进计划。", "每周五做一次复盘。",
    ]
    records = []
    for number, text in enumerate(content, 1):
        record = _record(f"m{number}", text, scope=MemoryScope.PERSONA if number == 6 else MemoryScope.SHARED,
                         persona_id="coach" if number == 6 else None)
        records.append(replace(record, metadata=dict(record.metadata,
            source_id=f"example/{number}", source_created_at=("2026-03-01" if number == 3 else "2026-09-01") + "T08:00:00+08:00")))
    backend = FakeBackend(records)
    service, operations, snapshot = _service(directory, backend)
    organizer = MemoryOrganizer(backend, operations.organization, ExampleProvider())
    operations.organization.request("u1")
    organizer.process_next()
    for record in records:
        operations.record_change(NewMemoryChange(
            memory_id=record.id, user_id="u1", persona_id=record.persona_id, scope=record.scope,
            action="ADD", before=None, after=record.content, source_request_id=f"example-source-{record.id}",
        ))
    operations.record_change(NewMemoryChange(
        memory_id="m7", user_id="u1", persona_id=None, scope=MemoryScope.SHARED,
        action="UPDATE", before="每周四做一次复盘。", after="每周五做一次复盘。",
    ))
    service.refresh_snapshot()
    settings = _settings(directory)
    settings.memory_snapshot_path = snapshot.path if hasattr(snapshot, "path") else directory / "memory" / "MEMORY.md"
    original_header = review_ui._header
    review_ui._header = lambda settings: original_header(settings).replace(
        "看见记忆如何积累、如何改变，也保留重新理解的余地。",
        "交互预览 · 全部为示例数据，操作仅影响此预览。Air 当前页面尚未更新。",
    )
    worker = MemoryWorker(CaptureProcessor(backend, operations, FakeExtractor(), snapshot), organizer=organizer)

    @asynccontextmanager
    async def lifespan(app):
        worker.start()
        yield
        worker.stop()
        operations.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(build_memory_review_router(service, settings))
    return app


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="riji-memory-preview-") as temp:
        app = build_preview(Path(temp))
        print("Synthetic preview login: review-token-long-enough", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=18765, access_log=False)
