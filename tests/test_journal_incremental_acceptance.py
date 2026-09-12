"""Offline acceptance across extraction, restart, fresh conversation and privacy.

The model and Mem0 transport are synthetic fixtures. Journal parsing, SQLite
provenance, consent, budget accounting, MemoryService, gateway, context assembly
and AgentResponder are the production implementations. No model quality, cloud
availability or real Feishu delivery is asserted by these tests.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path

import pytest

from riji_agent.agent.tools import ToolRegistry
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.gateway import HermesGateway
from riji_agent.hermes.models import IncomingMessage
from riji_agent.hermes.responder import AgentResponder
from riji_agent.memory.journal_backend import JournalEvidenceBackend
from riji_agent.memory.journal_engine import JournalMemoryEngine
from riji_agent.memory.journal_store import JournalMemoryStore
from riji_agent.memory.models import MemoryScope
from riji_agent.memory.operations import MemoryOperationsStore
from riji_agent.memory.service import MemoryService
from riji_agent.memory.store import MemoryStore
from riji_agent.models.types import AssistantTurn
from riji_agent.personas.registry import PersonaRegistry
from test_journal_initialization_budget import budget, grant
from test_journal_memory import runtime, write_note
from test_mem0_long_term_memory import _record

FACT = "纸船项目的新规则是先画三格草图，再折叠蓝色纸船。"
SOURCE = "[[riji/daily/2026-09-10]]"
OLD_TURN = "SYNTHETIC_OLD_CONVERSATION_CANARY"
PRIVATE_OBSERVATION = "SYNTHETIC_GENTLE_ONLY_OBSERVATION"
OTHER_USER_FACT = "SYNTHETIC_OTHER_USER_ONLY_FACT"
SECRET = "test-gateway-secret"
QUESTION = "纸船项目有什么新的长期事实？请给出来源。"
GENTLE, BLUNT = "gentle_reviewer", "blunt_coach"


class PromptWitness:
    """Echo only journal fact lines actually received, without doing retrieval."""

    def __init__(self):
        self.calls = []

    def complete(self, messages, tools):
        self.calls.append(json.loads(json.dumps(messages)))
        prompt = "\n".join(item["content"] for item in messages if item["role"] == "system")
        lines = [line for line in prompt.splitlines() if line.startswith("- ") and "[[riji/" in line]
        return AssistantTurn("\n".join(lines) if lines else "没有可引用的日记事实。")


@dataclass
class Scenario:
    directory: Path
    engine: JournalMemoryEngine
    model: object
    incremental_path: Path
    initial_chars: int
    frozen_batch: list

    def restart_engine(self) -> None:
        previous = self.engine
        previous.store.close()
        self.engine = JournalMemoryEngine(previous.policy, JournalMemoryStore(previous.store.path),
                                          previous.backend, previous.provider)


def _finished_history(tmp_path: Path) -> Scenario:
    engine, model = runtime(tmp_path, initialization_unlimited=True, daily_chars=100000,
                            settle_seconds=0, mentors=(GENTLE, BLUNT))
    write_note(engine.policy.root, "daily/2026-08-01.md", "旧项目已经完成一张黄色纸卡。")
    engine.scan()
    grant(engine, organization=False)
    assert engine.process_next() and not engine.process_next()
    assert engine.initialization_status()["state"] == "completed"
    assert budget(engine, "initialization:") > 0 and budget(engine, "day:") == 0
    return Scenario(tmp_path, engine, model, engine.policy.root / "daily/2026-09-10.md",
                    budget(engine, "initialization:"),
                    engine.store.rows("SELECT id,version FROM initialization_evidence ORDER BY id"))


@pytest.fixture
def scenario(tmp_path):
    result = _finished_history(tmp_path)
    yield result
    result.engine.store.close()


def _add_increment(scenario: Scenario) -> None:
    write_note(scenario.engine.policy.root, "daily/2026-09-10.md", FACT)
    original = scenario.incremental_path.read_bytes()
    previous_calls = len(scenario.model.calls)
    scenario.engine.scan()
    assert scenario.engine.process_next() and not scenario.engine.process_next()
    assert len(scenario.model.calls) > previous_calls
    assert any(item.content == FACT for item in scenario.engine.backend.records.values())
    assert scenario.incremental_path.read_bytes() == original
    assert budget(scenario.engine, "day:") > 0
    assert budget(scenario.engine, "initialization:") == scenario.initial_chars
    assert scenario.engine.store.rows("SELECT id,version FROM initialization_evidence ORDER BY id") == scenario.frozen_batch
    assert scenario.engine.initialization_status()["state"] == "completed"


def _ask_new_service(scenario: Scenario, chat: str, *, user="u1", persona=GENTLE):
    scenario.restart_engine()
    sessions = MemoryStore(scenario.directory / "conversation.sqlite3")
    events = EventLog(scenario.directory / "gateway-events.sqlite3")
    operations = MemoryOperationsStore(scenario.directory / "memory-operations.sqlite3")
    service = MemoryService(JournalEvidenceBackend(scenario.engine.backend, scenario.engine),
                            operations, None, auto_capture=False)
    service.journal = scenario.engine
    witness = PromptWitness()
    gateway = HermesGateway(
        hermes_secret=SECRET, allowed_user_ids={"u1", "u2"}, registry=PersonaRegistry(),
        store=sessions, events=events, responder=AgentResponder(witness, ToolRegistry(None)),
        memory_service=service,
    )
    try:
        reply = gateway.handle(SECRET, IncomingMessage(
            event_id=f"acceptance-{user}-{persona}-{chat}", feishu_user_id=user,
            chat_id=chat, chat_type="p2p", text=f"@{persona} {QUESTION}"))
        assert len(witness.calls) == 1
        assert not operations.list_jobs()
        return reply, witness.calls[0]
    finally:
        sessions.close()
        events.close()
        operations.close()


def _seed_previous_conversation(scenario: Scenario) -> None:
    store = MemoryStore(scenario.directory / "conversation.sqlite3")
    try:
        store.append_message("u1", GENTLE, "previous-chat", "user", OLD_TURN)
        store.append_message("u1", GENTLE, "previous-chat", "assistant", FACT + " " + SOURCE)
    finally:
        store.close()


def test_new_diary_reaches_fresh_conversation_after_closed_history_and_restart(scenario):
    _add_increment(scenario)
    _seed_previous_conversation(scenario)
    before_calls = len(scenario.model.calls)
    reply, messages = _ask_new_service(scenario, "fresh-chat")
    assert [item["role"] for item in messages] == ["system", "user"]
    assert messages[1]["content"] == QUESTION and FACT not in messages[1]["content"]
    assert OLD_TURN not in json.dumps(messages)
    assert FACT in messages[0]["content"] and SOURCE in messages[0]["content"]
    assert FACT in reply.text and SOURCE in reply.text
    assert len(scenario.model.calls) == before_calls
    assert scenario.engine.initialization_status()["total"] == 1


def test_rescan_and_retry_do_not_reextract_rebill_or_duplicate_successful_increment(scenario):
    _add_increment(scenario)
    baseline = (len(scenario.model.calls), len(scenario.engine.backend.records),
                scenario.engine.store.rows("SELECT * FROM budgets ORDER BY key"))
    scenario.restart_engine()
    for _ in range(2):
        scenario.engine.scan()
        scenario.engine.store.retry()
        assert not scenario.engine.process_next()
    actual = (len(scenario.model.calls), len(scenario.engine.backend.records),
              scenario.engine.store.rows("SELECT * FROM budgets ORDER BY key"))
    assert actual == baseline
    reply, messages = _ask_new_service(scenario, "after-idempotent-scan")
    assert messages[0]["content"].count(FACT) == 1
    assert reply.text.count(FACT) == 1 and SOURCE in reply.text


@pytest.mark.parametrize("restriction", ["local", "none", "private", "delete", "revoke"])
def test_restricted_or_removed_source_is_absent_from_fresh_context_without_rescan(scenario, restriction):
    _add_increment(scenario)
    before, _ = _ask_new_service(scenario, "before-restriction")
    assert FACT in before.text
    previous_calls = len(scenario.model.calls)
    if restriction == "delete":
        scenario.incremental_path.unlink()
    elif restriction == "revoke":
        scenario.engine.privacy.revoke()
    else:
        permission = "private: true" if restriction == "private" else f"memory: {restriction}"
        scenario.incremental_path.write_text(f"---\n{permission}\n---\n" + scenario.incremental_path.read_text())
    # Cached provenance still exists. Current source checks must protect this
    # request immediately, before the next scheduled scan or cleanup.
    reply, messages = _ask_new_service(scenario, "after-restriction")
    assert [item["role"] for item in messages] == ["system", "user"]
    assert FACT not in json.dumps(messages, ensure_ascii=False) and SOURCE not in messages[0]["content"]
    assert FACT not in reply.text and SOURCE not in reply.text
    assert len(scenario.model.calls) == previous_calls


def test_approved_personas_share_diary_facts_but_not_private_observations_or_old_turns(scenario):
    _add_increment(scenario)
    _seed_previous_conversation(scenario)
    private = _record("private-gentle", PRIVATE_OBSERVATION, scope=MemoryScope.PERSONA, persona_id=GENTLE)
    scenario.engine.backend.records[private.id] = private
    gentle, gentle_messages = _ask_new_service(scenario, "fresh-gentle", persona=GENTLE)
    blunt, blunt_messages = _ask_new_service(scenario, "previous-chat", persona=BLUNT)
    assert FACT in gentle.text and SOURCE in gentle.text
    assert FACT in blunt.text and SOURCE in blunt.text
    assert PRIVATE_OBSERVATION in gentle_messages[0]["content"]
    assert PRIVATE_OBSERVATION not in json.dumps(blunt_messages)
    assert OLD_TURN not in json.dumps(gentle_messages + blunt_messages)
    assert [item["role"] for item in blunt_messages] == ["system", "user"]


def test_other_allowed_user_gets_only_own_facts_and_unapproved_mentor_gets_no_journal(scenario):
    _add_increment(scenario)
    own = _record("other-user-fact", OTHER_USER_FACT, user_id="u2")
    scenario.engine.backend.records[own.id] = replace(own, metadata=dict(own.metadata, source_id="conversation/900"))
    other, other_messages = _ask_new_service(scenario, "other-user-chat", user="u2")
    assert OTHER_USER_FACT in other_messages[0]["content"]
    assert FACT not in json.dumps(other_messages, ensure_ascii=False) and SOURCE not in other.text
    owner, owner_messages = _ask_new_service(scenario, "owner-chat")
    assert FACT in owner.text and OTHER_USER_FACT not in json.dumps(owner_messages)
    denied, denied_messages = _ask_new_service(scenario, "unapproved-mentor", persona="future_self")
    assert FACT not in json.dumps(denied_messages, ensure_ascii=False) and SOURCE not in denied.text
