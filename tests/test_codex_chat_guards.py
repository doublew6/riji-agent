from __future__ import annotations

import copy
import json
from contextlib import contextmanager

import pytest

from riji_agent.agent.loop import AgentLimits, AgentRunner
from riji_agent.agent.tools import ToolRegistry
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.gateway import HermesGateway
from riji_agent.hermes.models import IncomingMessage
from riji_agent.hermes.responder import AgentResponder
from riji_agent.journal.index import JournalIndex
from riji_agent.memory.store import MemoryStore
from riji_agent.memory.organization import MemoryOrganizer
from riji_agent.models.types import AssistantTurn, LLMError, ToolCall
from riji_agent.personas.registry import PersonaRegistry
from riji_agent.retrieval.models import ToolContext
from riji_agent.retrieval.service import RetrievalService
from test_journal_lifecycle import attached_service
from test_journal_memory import runtime, write_note
from test_journal_organization import EventExtractor, ObservationModel


class QueuedProvider:
    """Run a controlled queue-time mutation before the actual send guard."""

    def __init__(self, turns, *, before_call=None):
        self.turns = list(turns)
        self.before_call = before_call or (lambda count: None)
        self.sent = []
        self.attempts = 0
        self.scopes = 0
        self.active = False

    @contextmanager
    def request_scope(self):
        self.scopes += 1
        self.active = True
        try:
            yield
        finally:
            self.active = False

    def complete(self, messages, tools):
        raise AssertionError("guarded provider must not bypass before_send")

    def complete_with_guard(self, messages, tools, *, before_send):
        assert self.active
        self.attempts += 1
        self.before_call(self.attempts)
        before_send()
        self.sent.append(copy.deepcopy(messages))
        return self.turns.pop(0)


def tool_turn(name="search_journal", args=None):
    return AssistantTurn(None, (ToolCall("call-1", name, json.dumps(args or {"query": "学习"})),))


def registry_for(tmp_path, root):
    index = JournalIndex(tmp_path / "index.sqlite3", root)
    index.build_index()
    return ToolRegistry(RetrievalService(index))


def context(persona="gentle_reviewer"):
    return ToolContext("request", "u1:" + persona + ":chat", "u1", persona)


@pytest.mark.parametrize("change", ["local", "none", "private", "edit", "delete"])
def test_queued_journal_result_is_not_sent_after_source_changes(tmp_path, change):
    engine, _ = runtime(tmp_path)
    path = write_note(engine.policy.root, text="学习一项只有旧日记包含的技能。")
    tools = registry_for(tmp_path, engine.policy.root)

    def change_source(count):
        if count != 2:
            return
        if change == "delete":
            path.unlink()
        elif change == "edit":
            path.write_text(path.read_text() + "\nNew source version.\n")
        else:
            rule = "private: true" if change == "private" else f"memory: {change}"
            path.write_text(f"---\n{rule}\n---\n" + path.read_text())

    provider = QueuedProvider([tool_turn(), AssistantTurn("unsafe old evidence")], before_call=change_source)
    with pytest.raises(LLMError, match="^chat_context_changed$"):
        AgentRunner(provider, tools).run(context(), "学习计划是什么？")
    assert len(provider.sent) == 1
    assert "只有旧日记包含" not in json.dumps(provider.sent, ensure_ascii=False)
    assert provider.scopes == 1 and not provider.active


@pytest.mark.parametrize("change", ["revoke", "source", "memory"])
def test_queued_mentor_context_rechecks_memory_permission(tmp_path, change):
    engine, _ = runtime(tmp_path)
    path = write_note(engine.policy.root)
    assert engine.process_next()
    service = attached_service(tmp_path, engine)

    def withdraw(_count):
        if change == "revoke":
            engine.privacy.revoke()
        elif change == "source":
            path.write_text("---\nmemory: local\n---\n" + path.read_text())
        else:
            memory = service.backend.get("m1")
            service.backend.update("m1", metadata=dict(memory.metadata, privacy="local"))

    provider = QueuedProvider([AssistantTurn("must not send")], before_call=withdraw)
    store = MemoryStore(tmp_path / "chat.sqlite3")
    events = EventLog(tmp_path / "events.sqlite3")
    gateway = HermesGateway(
        hermes_secret="test-secret", allowed_user_ids={"u1"}, registry=PersonaRegistry(),
        store=store, events=events, memory_service=service,
        responder=AgentResponder(provider, registry_for(tmp_path, engine.policy.root)),
    )
    try:
        message = IncomingMessage("e1", "u1", "chat", "p2p", "学习计划是什么？")
        with pytest.raises(LLMError, match="^chat_context_changed$"):
            gateway.handle("test-secret", message)
        assert provider.sent == []
        assert not provider.active
    finally:
        store.close()
        events.close()


def test_final_round_and_regular_round_share_one_request_scope(tmp_path):
    engine, _ = runtime(tmp_path)
    write_note(engine.policy.root)
    provider = QueuedProvider([tool_turn(), AssistantTurn("final")])
    runner = AgentRunner(provider, registry_for(tmp_path, engine.policy.root), limits=AgentLimits(max_rounds=1))
    result = runner.run(context(), "学习计划是什么？")
    assert result.answer == "final" and result.exceeded_rounds
    assert provider.scopes == 1 and len(provider.sent) == 2


def test_background_coverage_progress_does_not_invalidate_allowed_memory(tmp_path, monkeypatch):
    engine, _ = runtime(tmp_path)
    write_note(engine.policy.root)
    assert engine.process_next()
    service = attached_service(tmp_path, engine)
    progress = ["processed 1 source"]
    monkeypatch.setattr(engine, "coverage_notice", lambda: progress[0])
    provider = QueuedProvider(
        [AssistantTurn("当前目标仍然有有效依据。")],
        before_call=lambda _count: progress.__setitem__(0, "processed 2 sources"),
    )
    store = MemoryStore(tmp_path / "chat.sqlite3")
    events = EventLog(tmp_path / "events.sqlite3")
    gateway = HermesGateway(
        hermes_secret="test-secret", allowed_user_ids={"u1"}, registry=PersonaRegistry(),
        store=store, events=events, memory_service=service,
        responder=AgentResponder(provider, registry_for(tmp_path, engine.policy.root)),
    )
    try:
        message = IncomingMessage("e1", "u1", "chat", "p2p", "学习计划是什么？")
        reply = gateway.handle("test-secret", message)
        assert reply.text == "当前目标仍然有有效依据。"
        assert len(provider.sent) == 1
    finally:
        store.close()
        events.close()


def test_queued_context_rejects_invalidated_observation_even_if_facts_remain(tmp_path):
    engine, _ = runtime(tmp_path, model=EventExtractor())
    write_note(engine.policy.root, "daily/2026-01-01.md", "面试前试了深呼吸有效。")
    write_note(engine.policy.root, "daily/2026-02-01.md", "演讲前试了深呼吸有效。")
    engine.scan()
    while engine.process_next():
        pass
    service = attached_service(tmp_path, engine)
    service.operations.organization.request("u1")
    organizer = MemoryOrganizer(service.backend, service.operations.organization, ObservationModel())
    assert organizer.process_next()
    recalled = service.retrieve("深呼吸", user_id="u1", persona_id="gentle_reviewer")
    assert any(item.metadata.get("source_type") == "derived-observation" for item in recalled.shared)
    provider = QueuedProvider([AssistantTurn("unsafe observation")], before_call=lambda _: engine.store.bump_epoch())
    store = MemoryStore(tmp_path / "chat.sqlite3")
    events = EventLog(tmp_path / "events.sqlite3")
    gateway = HermesGateway(
        hermes_secret="test-secret", allowed_user_ids={"u1"}, registry=PersonaRegistry(),
        store=store, events=events, memory_service=service,
        responder=AgentResponder(provider, registry_for(tmp_path, engine.policy.root)),
    )
    try:
        message = IncomingMessage("e1", "u1", "chat", "p2p", "深呼吸有帮助吗？")
        with pytest.raises(LLMError, match="^chat_context_changed$"):
            gateway.handle("test-secret", message)
        assert provider.sent == []
        assert len(service.list_memories(user_id="u1")) == 2
    finally:
        store.close()
        events.close()


def test_guard_state_is_request_local_and_persona_history_stays_scoped(tmp_path):
    engine, _ = runtime(tmp_path)
    path = write_note(engine.policy.root)
    provider = QueuedProvider([tool_turn(), AssistantTurn("first"), AssistantTurn("second")])
    runner = AgentRunner(provider, registry_for(tmp_path, engine.policy.root))
    runner.run(context("gentle_reviewer"), "学习", history=[{"role": "user", "content": "first mentor private"}])
    path.write_text("---\nmemory: local\n---\n" + path.read_text())
    runner.run(context("blunt_coach"), "你好", history=[{"role": "user", "content": "second mentor private"}])
    final_messages = json.dumps(provider.sent[-1], ensure_ascii=False)
    assert "second mentor private" in final_messages
    assert "first mentor private" not in final_messages
    assert "学习产品设计" not in final_messages
    assert provider.scopes == 2


def test_codex_tool_intent_cannot_commit_without_user_confirmation(tmp_path):
    engine, _ = runtime(tmp_path)
    path = write_note(engine.policy.root)
    original = path.read_bytes()
    provider = QueuedProvider([
        tool_turn("commit_draft", {"draft_id": "invented", "confirmation_token": "test-invented-token"}),
        AssistantTurn("需要用户确认。"),
    ])
    result = AgentRunner(provider, registry_for(tmp_path, engine.policy.root)).run(context(), "讨论一下")
    assert result.audit[0].error == "unknown_tool"
    assert path.read_bytes() == original
