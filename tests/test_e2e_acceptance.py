"""End-to-end acceptance for PRD US-01..US-04, Wang Yangming citation, and the
privacy boundary, exercising the full gateway stack with a scripted model.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

import pytest

from riji_agent.agent.tools import ToolRegistry
from riji_agent.audit.store import AuditStore
from riji_agent.drafts.service import DraftService
from riji_agent.drafts.store import DraftStore
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.gateway import HermesGateway
from riji_agent.hermes.models import IncomingMessage
from riji_agent.hermes.responder import AgentResponder
from riji_agent.journal.index import JournalIndex
from riji_agent.models.types import AssistantTurn, ToolCall
from riji_agent.memory.store import MemoryStore
from riji_agent.personas.registry import PersonaRegistry
from riji_agent.retrieval.service import RetrievalService
from riji_agent.yangming.seed import load_seed
from riji_agent.yangming.store import YangmingKB

SECRET = "secret"
PRIVATE_TEXT = "机密的私人想法记录"
PRIVATE_ID = "riji/daily/2026-06-25"


class FakeProvider:
    def __init__(self, turns: Sequence[AssistantTurn]) -> None:
        self._turns = list(turns)
        self.calls: List[Dict[str, Any]] = []

    def complete(self, messages, tools) -> AssistantTurn:
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        return self._turns.pop(0) if self._turns else AssistantTurn(content="done")

    def all_sent_text(self) -> str:
        return json.dumps(self.calls, ensure_ascii=False)


def _tool(name: str, args: dict, call_id: str = "c1") -> AssistantTurn:
    return AssistantTurn(content=None, tool_calls=(ToolCall(call_id, name, json.dumps(args)),))


def _msg(text: str, *, event_id: str = "e1", chat_type: str = "p2p") -> IncomingMessage:
    return IncomingMessage(event_id=event_id, feishu_user_id="ou_1", chat_id="c1", chat_type=chat_type, text=text)


def _build(tmp_path: Path, turns):
    root = tmp_path / "riji"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "daily.md").write_text("# {{date}}\n\n## 🌆 Evening\n\n## 🧠 Notes\n", encoding="utf-8")
    (root / "daily").mkdir()
    (root / "daily" / "2026-06-24.md").write_text(
        "---\ndate: 2026-06-24\ntags: [ai]\n---\n# 2026-06-24\n项目进展评审通过。\n", encoding="utf-8"
    )
    (root / "daily" / "2026-06-25.md").write_text(
        f"---\ndate: 2026-06-25\nprivate: true\n---\n# 2026-06-25\n{PRIVATE_TEXT}。\n", encoding="utf-8"
    )
    d = tmp_path / "d"
    index = JournalIndex(database_path=d / "idx.sqlite3", journal_root=root)
    index.build_index()
    kb = YangmingKB(d / "ym.sqlite3")
    load_seed(kb)
    draft_service = DraftService(DraftStore(d / "drafts.sqlite3"), root, index)
    registry = ToolRegistry(RetrievalService(index), draft_service=draft_service, yangming_kb=kb)
    audit = AuditStore(d / "audit.sqlite3")
    provider = FakeProvider(turns)
    store = MemoryStore(d / "mem.sqlite3")
    gateway = HermesGateway(
        hermes_secret=SECRET,
        allowed_user_ids={"ou_1"},
        registry=PersonaRegistry(),
        store=store,
        events=EventLog(d / "ev.sqlite3"),
        responder=AgentResponder(provider, registry, audit_store=audit),
        draft_service=draft_service,
    )
    return gateway, provider, audit, root, store


def test_us01_sourced_qa(tmp_path: Path) -> None:
    gateway, _provider, audit, _root, _store = _build(
        tmp_path,
        [_tool("search_journal", {"query": "项目进展"}),
         AssistantTurn(content="项目进展评审通过 [[riji/daily/2026-06-24]]")],
    )
    reply = gateway.handle(SECRET, _msg("最近项目如何"))
    assert "[[riji/daily/2026-06-24]]" in reply.text
    assert "riji/daily/2026-06-24" in audit.all_source_ids()


def test_us02_timeline_review(tmp_path: Path) -> None:
    gateway, _provider, audit, _root, _store = _build(
        tmp_path,
        [_tool("timeline", {"topic": "项目进展", "date_from": "2026-06-01", "date_to": "2026-06-30", "granularity": "month"}),
         AssistantTurn(content="时间线已整理")],
    )
    gateway.handle(SECRET, _msg("帮我回顾项目时间线"))
    assert any(e.tool == "timeline" for e in audit.all())


def test_us03_shared_memory_reaches_model_after_switch(tmp_path: Path) -> None:
    gateway, provider, _audit, _root, store = _build(
        tmp_path, [_tool("search_journal", {"query": "项目进展"}), AssistantTurn(content="好的")]
    )
    cid = store.add_candidate("ou_1", "gentle_reviewer", "用户正在写一本书")
    store.confirm_candidate(cid)

    reply = gateway.handle(SECRET, _msg("/导师 直率教练 我最近怎么样"))
    assert reply.persona_id == "blunt_coach"
    system_prompt = provider.calls[0]["messages"][0]["content"]
    assert "用户正在写一本书" in system_prompt  # confirmed memory shared across personas


def test_us04_draft_then_confirm_writes(tmp_path: Path) -> None:
    gateway, _provider, audit, root, _store = _build(
        tmp_path,
        [_tool("draft_daily_entry", {"operations": [{"section": "🌆 Evening", "content": "今天评审通过"}], "target_date": "2026-06-26"}),
         AssistantTurn(content="请回复「确认保存」以写入")],
    )
    gateway.handle(SECRET, _msg("记录一下今天的事", event_id="e1"))
    confirm = gateway.handle(SECRET, _msg("确认保存", event_id="e2"))

    assert "已写入" in confirm.text and "riji/daily/2026-06-26" in confirm.text
    assert (root / "daily" / "2026-06-26.md").exists()
    assert any(e.tool == "draft_daily_entry" for e in audit.all())


def test_us05_yangming_citation(tmp_path: Path) -> None:
    gateway, provider, audit, _root, _store = _build(
        tmp_path,
        [_tool("search_yangming", {"query": "心即理"}), AssistantTurn(content="《传习录》有云……")],
    )
    reply = gateway.handle(SECRET, _msg("/导师 王阳明 谈谈心即理"))
    assert reply.persona_id == "wang_yangming"
    assert any(e.tool == "search_yangming" for e in audit.all())
    offered = {t["function"]["name"] for t in provider.calls[0]["tools"]}
    assert "search_yangming" in offered  # only this persona is offered the tool


def test_private_content_never_egresses(tmp_path: Path) -> None:
    gateway, provider, audit, _root, _store = _build(
        tmp_path,
        [_tool("search_journal", {"query": "私人想法"}),
         _tool("read_note", {"source_id": PRIVATE_ID}, call_id="c2"),
         AssistantTurn(content="日记中未找到足够证据")],
    )
    gateway.handle(SECRET, _msg("我有什么私人想法"))

    # private note never surfaced as a source, nor sent to the model
    assert PRIVATE_ID not in audit.all_source_ids()
    assert PRIVATE_TEXT not in provider.all_sent_text()
    # read_note on the private id was blocked
    assert any(e.tool == "read_note" and e.error == "no_evidence" for e in audit.all())
