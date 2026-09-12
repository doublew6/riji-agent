"""Issue #45: one-request output contracts preserve strict validation and guards."""

from __future__ import annotations

from dataclasses import replace
import json
from typing import Any

import httpx
import pytest

from riji_agent.memory.journal_extract import (
    JournalMemoryExtractor, extraction_schema, parse_candidate, relation_schema,
)
from riji_agent.memory.journal_types import JournalEvidence, JournalMemoryError
from riji_agent.memory.models import LongTermMemory, MemoryScope, MemoryStatus
from riji_agent.models.deepseek import DeepSeekProvider
from sse_fixtures import completion_response
from riji_agent.models.types import AssistantTurn, LLMError


EVIDENCE = JournalEvidence(
    "synthetic-evidence", "synthetic-source", "daily/synthetic.md", "version-1",
    "daily", "Notes", 1, "2026-09-03",
    "2026-09-03，我独自完成了虚构的青杉项目演示，现场有六名同事。",
)
CANDIDATE = {
    "content": "2026-09-03，用户独自完成了虚构的青杉项目演示，现场有六名同事。",
    "kind": "event", "certainty": "explicit", "valid_from": "2026-09-03",
    "quotes": [EVIDENCE.text],
}


class LegacyModel:
    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result
        self.calls: list[tuple[Any, Any]] = []

    def complete(self, messages: Any, tools: Any) -> AssistantTurn:
        self.calls.append((messages, tools))
        if isinstance(self.result, Exception):
            raise self.result
        return AssistantTurn(json.dumps(self.result, ensure_ascii=False))


class NativeModel(LegacyModel):
    def complete_json_with_guard(self, messages: Any, schema: Any,
                                 before_send: Any) -> AssistantTurn:
        before_send()
        return super().complete(messages, schema)

    def complete(self, messages: Any, tools: Any) -> AssistantTurn:
        pytest.fail("A native structured call must not fall back or resend.")


def _existing() -> LongTermMemory:
    return LongTermMemory(
        "allowed-memory", "User completed a synthetic project demo.", "synthetic-user",
        MemoryScope.SHARED, None, MemoryStatus.ACTIVE, None, None,
        {"journal_kind": "event", "valid_from": "2026-09-03"},
    )


def _extraction(candidate: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"complete": True, "memories": [CANDIDATE if candidate is None else candidate]}


def test_deepseek_wire_contains_contract_once_without_changing_endpoint_or_model() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return completion_response({"content": json.dumps(_extraction(), ensure_ascii=False),
                                    "reasoning_content": "Synthetic reasoning excluded from memory."})

    charged = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekProvider(api_key="example-model-key", model="deepseek-chat", client=client)
        candidates = JournalMemoryExtractor(provider, charge=charged.append).extract(EVIDENCE)
    assert candidates == (parse_candidate(CANDIDATE, EVIDENCE),)
    assert len(requests) == len(charged) == 1
    assert str(requests[0].url) == "https://api.deepseek.com/chat/completions"
    payload = json.loads(requests[0].content)
    assert set(payload) == {"model", "messages", "stream"}
    assert payload["model"] == "deepseek-chat"
    assert payload["stream"] is True
    messages = payload["messages"]
    schema_text = json.dumps(extraction_schema(), ensure_ascii=False)
    assert messages[0]["content"].count(schema_text) == 1
    assert "不得将 observed_at、section、text 等输入字段复制到候选中" in messages[0]["content"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert json.loads(messages[1]["content"])["text"] == EVIDENCE.text
    assert charged == [sum(len(message["content"]) for message in messages)]


@pytest.mark.parametrize("extra", [{"observed_at": "2026-09-03"}, {"unexpected": True}])
def test_diagnostic_extra_field_and_unknown_fields_remain_rejected(extra: dict[str, Any]) -> None:
    model = LegacyModel(_extraction(dict(CANDIDATE, **extra)))
    charged = []
    with pytest.raises(JournalMemoryError, match="^journal_invalid_candidate$"):
        JournalMemoryExtractor(model, charge=charged.append).extract(EVIDENCE)
    assert len(model.calls) == len(charged) == 1


def test_valid_counterpart_keeps_exact_fields_without_inventing_metadata() -> None:
    model = LegacyModel(_extraction())
    candidate, = JournalMemoryExtractor(model, charge=lambda size: None).extract(EVIDENCE)
    assert candidate.to_dict() == {**CANDIDATE, "quotes": tuple(CANDIDATE["quotes"])}
    assert set(candidate.to_dict()) == {"content", "kind", "certainty", "valid_from", "quotes"}


@pytest.mark.parametrize("observed_at", [None, "2026-09-03"])
def test_unknown_fact_date_is_not_filled_from_record_metadata(observed_at: str | None) -> None:
    evidence = replace(EVIDENCE, observed_at=observed_at,
                       text="某次旅途中，我在虚构的纸桥车站找回了遗失手账，记不清是哪一年。")
    candidate = dict(CANDIDATE, content="用户曾在纸桥车站找回遗失手账，时间未知。",
                     valid_from=None, quotes=[evidence.text])
    model = LegacyModel(_extraction(candidate))
    result, = JournalMemoryExtractor(model, charge=lambda size: None).extract(evidence)
    assert result.valid_from is None
    assert json.loads(model.calls[0][0][-1]["content"])["observed_at"] == observed_at


@pytest.mark.parametrize("changes,code", [
    ({"quotes": ["This quote was never in the evidence."]}, "journal_invalid_evidence_quote"),
    ({"certainty": "inferred", "kind": "fact"}, "journal_inference_must_be_observation"),
    ({"valid_from": "2026-99-99"}, "journal_invalid_fact_date"),
    ({"kind": "unregistered-kind"}, "journal_invalid_candidate"),
])
def test_prompt_schema_does_not_replace_fact_and_evidence_validation(
    changes: dict[str, Any], code: str,
) -> None:
    model = LegacyModel(_extraction(dict(CANDIDATE, **changes)))
    with pytest.raises(JournalMemoryError, match=f"^{code}$"):
        JournalMemoryExtractor(model, charge=lambda size: None).extract(EVIDENCE)
    assert len(model.calls) == 1


def test_legacy_relation_receives_exact_target_schema_and_retains_target_validation() -> None:
    model = LegacyModel({"decisions": [
        {"index": 0, "action": "duplicate", "target_id": "invented", "reason": "Same event"},
    ]})
    charged = []
    with pytest.raises(JournalMemoryError, match="^journal_invalid_relation_target$"):
        JournalMemoryExtractor(model, charge=charged.append).relate(
            [parse_candidate(CANDIDATE, EVIDENCE)], [_existing()],
        )
    messages, tools = model.calls[0]
    schema_text = json.dumps(relation_schema(1, {"allowed-memory"}), ensure_ascii=False)
    assert schema_text in messages[0]["content"] and tools == []
    assert charged == [sum(len(message["content"]) for message in messages)]
    assert len(model.calls) == 1


def test_native_provider_keeps_original_business_schema_and_single_charge() -> None:
    model = NativeModel(_extraction())
    charged = []
    JournalMemoryExtractor(model, charge=charged.append).extract(EVIDENCE)
    messages, schema = model.calls[0]
    schema_text = json.dumps(extraction_schema(), ensure_ascii=False)
    assert schema == extraction_schema()
    assert schema_text not in messages[0]["content"]
    assert charged == [sum(len(message["content"]) for message in messages) + len(schema_text)]
    assert len(model.calls) == 1


@pytest.mark.parametrize("model_type", [LegacyModel, NativeModel])
def test_relation_revocation_precedes_charge_and_send(model_type: type[LegacyModel]) -> None:
    model = model_type({"decisions": []})
    charged = []

    def revoked() -> None:
        raise JournalMemoryError("journal_target_changed")

    with pytest.raises(JournalMemoryError, match="^journal_target_changed$"):
        JournalMemoryExtractor(model, charge=charged.append).relate(
            [parse_candidate(CANDIDATE, EVIDENCE)], [_existing()], before_send=revoked,
        )
    assert charged == model.calls == []


def test_budget_rejection_prevents_legacy_http_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("Budget rejection must happen before the HTTP send.")

    def no_budget(size: int) -> None:
        raise JournalMemoryError("daily_budget")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekProvider(api_key="example-model-key", client=client)
        with pytest.raises(JournalMemoryError, match="^daily_budget$"):
            JournalMemoryExtractor(provider, charge=no_budget).extract(EVIDENCE)


@pytest.mark.parametrize("content_type", ["ai_discussion_result", "unknown_ai"])
def test_excluded_ai_source_does_not_reach_contract_or_model(content_type: str) -> None:
    model = LegacyModel(_extraction())
    charged = []
    result = JournalMemoryExtractor(model, charge=charged.append).extract(
        replace(EVIDENCE, content_type=content_type),
    )
    assert result == () and model.calls == charged == []


@pytest.mark.parametrize("model_type", [LegacyModel, NativeModel])
@pytest.mark.parametrize("code", ["codex_timeout", "codex_quota_exhausted"])
def test_provider_error_is_not_retried_or_changed_to_other_mode(
    model_type: type[LegacyModel], code: str,
) -> None:
    model = model_type(LLMError(code))
    charged = []
    with pytest.raises(LLMError, match=f"^{code}$"):
        JournalMemoryExtractor(model, charge=charged.append).extract(EVIDENCE)
    assert len(model.calls) == len(charged) == 1
