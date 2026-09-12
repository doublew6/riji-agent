"""Synthetic structured extraction checks; no account or real journal access."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer

import pytest

from riji_agent.memory.journal_extract import (
    JournalMemoryExtractor, extraction_schema, parse_candidate, relation_schema,
)
from riji_agent.memory.journal_types import JournalEvidence, JournalMemoryError
from riji_agent.memory.model_call import complete_json_guarded
from riji_agent.models.types import AssistantTurn, LLMError, ToolCall
from test_codex_provider import fake_cli
from test_journal_memory import JournalModel, runtime, write_note
from test_mem0_long_term_memory import _record


EVIDENCE = JournalEvidence(
    "synthetic-evidence", "synthetic-source", "daily/2026-01-01.md", "version-1",
    "daily", "Notes", 1, "2026-01-01", "I prefer writing a short plan before beginning a project.",
)
CANDIDATE = {"content": "Prefers planning before starting a project.", "kind": "preference",
             "certainty": "explicit", "valid_from": None, "quotes": [EVIDENCE.text]}


class StructuredModel:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete_json_with_guard(self, messages, schema, before_send):
        self.calls.append((messages, schema))
        before_send()
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def complete(self, messages, tools):
        pytest.fail("A structured request must not fall back or resend.")


def test_extraction_uses_business_schema_and_charges_once():
    model = StructuredModel(AssistantTurn(json.dumps({"complete": True, "memories": [CANDIDATE]})))
    charged = []
    result = JournalMemoryExtractor(model, charge=charged.append).extract(EVIDENCE)
    assert result == (parse_candidate(CANDIDATE, EVIDENCE),)
    messages, schema = model.calls[0]
    assert schema == extraction_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"complete", "memories"}
    assert len(model.calls) == 1
    assert charged == [sum(len(item["content"]) for item in messages)
                       + len(json.dumps(schema, ensure_ascii=False))]


@pytest.mark.parametrize("response,code", [
    (AssistantTurn('```json\n{"complete":true,"memories":[]}\n```'), "journal_invalid_model_json"),
    (AssistantTurn('{"complete":true,"memories":['), "journal_invalid_model_json"),
    (AssistantTurn('{"complete":false,"memories":[]}'), "journal_incomplete_extraction"),
    (AssistantTurn('{"complete":true,"memories":[],"extra":true}'), "journal_incomplete_extraction"),
    (AssistantTurn("{}", (ToolCall("t", "arbitrary_tool", "{}"),)), "journal_invalid_model_output"),
    (AssistantTurn(42), "journal_invalid_model_output"),
])
def test_bad_structured_output_is_not_repaired_or_retried(response, code):
    model = StructuredModel(response)
    with pytest.raises(JournalMemoryError, match=f"^{code}$"):
        JournalMemoryExtractor(model, charge=lambda size: None).extract(EVIDENCE)
    assert len(model.calls) == 1


@pytest.mark.parametrize("change,code", [
    ({"quotes": ["A fabricated quote not in the source."]}, "journal_invalid_evidence_quote"),
    ({"kind": "fact", "certainty": "inferred"}, "journal_inference_must_be_observation"),
    ({"valid_from": "2026-99-99"}, "journal_invalid_fact_date"),
])
def test_schema_does_not_replace_evidence_and_semantic_validation(change, code):
    candidate = dict(CANDIDATE, **change)
    model = StructuredModel(AssistantTurn(json.dumps({"complete": True, "memories": [candidate]})))
    with pytest.raises(JournalMemoryError, match=f"^{code}$"):
        JournalMemoryExtractor(model, charge=lambda size: None).extract(EVIDENCE)


def test_relation_schema_separates_new_from_exact_current_target_ids():
    known_ids = {"allowed-a", "allowed-b"}
    schema = relation_schema(2, known_ids)
    decisions = schema["properties"]["decisions"]
    assert decisions["minItems"] == decisions["maxItems"] == 2
    new, related = decisions["items"]["anyOf"]
    assert new["properties"]["action"]["enum"] == ["new"]
    assert new["properties"]["target_id"] == {"type": "null"}
    assert set(related["properties"]["target_id"]["enum"]) == known_ids
    assert "new" not in related["properties"]["action"]["enum"]
    assert related["properties"]["index"]["enum"] == [0, 1]
    assert new["additionalProperties"] is related["additionalProperties"] is False


@pytest.mark.parametrize("action,target", [("new", "allowed-a"), ("related", "not-provided"),
                                          ("duplicate", None), ("enrich", ["allowed-a"])])
def test_invalid_target_is_never_downgraded_to_new(action, target):
    response = {"decisions": [{"index": 0, "action": action, "target_id": target,
                               "reason": "Synthetic relationship."}]}
    model = StructuredModel(AssistantTurn(json.dumps(response)))
    candidate = parse_candidate(CANDIDATE, EVIDENCE)
    with pytest.raises(JournalMemoryError, match="^journal_invalid_relation_target$"):
        JournalMemoryExtractor(model, charge=lambda size: None).relate(
            [candidate], [_record("allowed-a", "Synthetic existing preference.")])
    assert len(model.calls) == 1


def test_structured_send_keeps_target_guard_before_charge():
    model = StructuredModel(AssistantTurn("{}"))
    def revoked():
        raise JournalMemoryError("journal_target_changed")
    with pytest.raises(JournalMemoryError, match="^journal_target_changed$"):
        JournalMemoryExtractor(model, charge=lambda size: pytest.fail("Must not charge.")).relate(
            [parse_candidate(CANDIDATE, EVIDENCE)], [_record("allowed-a", "Synthetic existing fact.")],
            before_send=revoked,
        )


def test_unsupported_provider_retains_single_guarded_legacy_call():
    calls = []
    class LegacyModel:
        def complete(self, messages, tools):
            calls.append("complete")
            assert tools == []
            return AssistantTurn('{"complete":true,"memories":[]}')
    result = complete_json_guarded(LegacyModel(), [], extraction_schema(), lambda: calls.append("guard"))
    assert result.content == '{"complete":true,"memories":[]}'
    assert calls == ["guard", "complete"]


def test_structured_provider_failure_never_uses_legacy_fallback():
    model = StructuredModel(LLMError("codex_quota_exhausted"))
    with pytest.raises(LLMError, match="^codex_quota_exhausted$"):
        complete_json_guarded(model, [], extraction_schema(), lambda: None)
    assert len(model.calls) == 1


def test_codex_exec_receives_business_schema_without_json_string_envelope(tmp_path, monkeypatch):
    content = json.dumps({"complete": True, "memories": [CANDIDATE]})
    provider = fake_cli(tmp_path, events=[
        {"type": "item.completed", "item": {"type": "agent_message", "text": content}},
        {"type": "turn.completed"},
    ])
    original = provider._execute
    seen = []
    def inspect(workdir, schema_path, payload, deadline):
        seen.append((schema_path, json.loads(schema_path.read_text())))
        assert '"application_tools": []' in payload
        assert "an additional content/tool_calls envelope" in payload
        return original(workdir, schema_path, payload, deadline)
    monkeypatch.setattr(provider, "_execute", inspect)
    candidates = JournalMemoryExtractor(provider, charge=lambda size: None).extract(EVIDENCE)
    assert candidates == (parse_candidate(CANDIDATE, EVIDENCE),)
    assert seen[0][1] == extraction_schema()
    assert not seen[0][0].exists()


def test_codex_structured_request_still_rejects_native_tools(tmp_path):
    provider = fake_cli(tmp_path, events=[
        {"type": "item.started", "item": {"type": "command_execution"}},
    ])
    with pytest.raises(LLMError, match="^codex_unexpected_tool_activity$"):
        complete_json_guarded(provider, [], extraction_schema(), lambda: None)


def test_codex_structured_guard_failure_never_starts_model(tmp_path, monkeypatch):
    provider = fake_cli(tmp_path)
    monkeypatch.setattr(provider, "_execute", lambda *args: pytest.fail("Must not send."))
    def revoked():
        raise JournalMemoryError("journal_source_changed")
    with pytest.raises(JournalMemoryError, match="^journal_source_changed$"):
        complete_json_guarded(provider, [], extraction_schema(), revoked)


def _replace_synthetic_wire_text(value, content):
    if isinstance(value, dict):
        return {key: _replace_synthetic_wire_text(item, content) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_synthetic_wire_text(item, content) for item in value]
    return content if value == '{"content":"Synthetic completion","tool_calls":[]}' else value


def _business_wire_server(monkeypatch, schema, value):
    from test_codex_isolation import WireEvidence, _handler, _response_events
    import test_codex_isolation as wire

    content = json.dumps(value)
    body = "\n".join("data: " + json.dumps(_replace_synthetic_wire_text(json.loads(line[6:]), content))
                     if line.startswith("data: ") else line
                     for line in _response_events().decode().split("\n")).encode()
    monkeypatch.setattr(wire, "_response_events", lambda: body)
    observed = []
    class BusinessEvidence(WireEvidence):
        def observe(self, payload):
            super().observe(payload)
            actual = payload.get("text", {}).get("format", {})
            observed.append(actual.get("schema") == schema and actual.get("strict") is True)
    evidence = BusinessEvidence(())
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(evidence))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return server, worker, evidence, observed


@pytest.mark.skipif(not os.environ.get("RIJI_TEST_CODEX_BIN"),
                    reason="Set RIJI_TEST_CODEX_BIN for the offline business-schema wire check")
@pytest.mark.parametrize("phase", ["extraction", "relation"])
def test_official_codex_sends_business_schema_with_no_tools(tmp_path, monkeypatch, phase):
    from riji_agent.models import codex
    from test_codex_isolation import _synthetic_provider_args

    schema = extraction_schema() if phase == "extraction" else relation_schema(1, {"synthetic-memory"})
    value = {"complete": True, "memories": []} if phase == "extraction" else {"decisions": [
        {"index": 0, "action": "new", "target_id": None, "reason": "Synthetic independent fact."}]}
    server, worker, evidence, observed = _business_wire_server(monkeypatch, schema, value)
    original_command = codex.command
    monkeypatch.setattr(codex, "command", lambda *args: original_command(*args) + _synthetic_provider_args(server.server_port))
    monkeypatch.setattr(codex, "check_login", lambda *args, **kwargs: None)
    for key in os.environ:
        if "proxy" in key.lower():
            monkeypatch.delenv(key)
    home = tmp_path / "synthetic-codex-home"
    home.mkdir(mode=0o700)
    provider = codex.CodexProvider(os.environ["RIJI_TEST_CODEX_BIN"], "gpt-5.6-luna", home=home, timeout_seconds=30)
    guards = []
    try:
        result = complete_json_guarded(provider, [{"role": "user", "content": "Synthetic fixture."}],
                                       schema, lambda: guards.append(True))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    assert json.loads(result.content) == value and not result.tool_calls
    assert guards == [True] and observed and all(observed)
    assert evidence.invalid_requests == 0
    assert all(item["tool_count"] == 0 and item["store"] is False for item in evidence.requests)


class StructuredJournalModel(JournalModel):
    def __init__(self):
        super().__init__()
        self.invalid_relation = False

    def complete_json_with_guard(self, messages, schema, before_send):
        before_send()
        response = JournalModel.complete(self, messages, [])
        if self.invalid_relation and "decisions" in json.loads(response.content):
            return AssistantTurn('{"decisions":[{"index":0,"action":"related",'
                                 '"target_id":"not-provided","reason":"Synthetic invalid target."}]}')
        return response

    def complete(self, messages, tools):
        pytest.fail("Must use structured capability.")


def test_failed_relation_preserves_cache_and_requires_explicit_bounded_retry(tmp_path: Path):
    model = StructuredJournalModel()
    engine, _ = runtime(tmp_path, model=model, max_attempts=1)
    write_note(engine.policy.root, text="Synthetic first preference for planning work.")
    engine.scan()
    assert engine.process_next()
    model.invalid_relation = True
    write_note(engine.policy.root, "daily/2026-09-01.md", "Synthetic second preference for tracking progress.")
    engine.scan()
    assert engine.process_next()
    failed = engine.store.rows("SELECT * FROM evidence WHERE status='failed'")[0]
    assert failed["error"] == "journal_invalid_relation_target"
    assert failed["extracted"] is not None and failed["decisions"] is None
    assert len(engine.backend.records) == 1
    assert not engine.store.rows("SELECT * FROM operations WHERE evidence_id=?", (failed["id"],))
    assert not engine.process_next()
    count_before_retry = len(model.calls)
    model.invalid_relation = False
    engine.store.retry()
    assert engine.process_next()
    assert len(model.calls) == count_before_retry + 1
    assert "existing" in json.loads(model.calls[-1][-1]["content"])
    assert engine.store.evidence(failed["id"])["status"] == "succeeded"
    assert len(engine.backend.records) == 2
    assert not engine.process_next()
