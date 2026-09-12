"""Observed failure categories survive safely without changing retry semantics."""

from pathlib import Path
import json
from typing import Any

import httpx
import pytest

from riji_agent.mentors.generation import ModelGeneration
from riji_agent.mentors.models import Artifact, Command, MentorError
from riji_agent.mentors.store import MentorStore
from riji_agent.models.errors import MODEL_ERROR_CATEGORIES, model_failure_code
from riji_agent.models.openai_compatible import OpenAICompatibleProvider
from riji_agent.models.types import LLMError
from riji_agent.personas.registry import PersonaRegistry
from test_mentor_discussions import prepare, system

CANARY = "synthetic-sensitive-response-do-not-store"


def provider(handler: Any) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        api_key=CANARY, base_url="https://api.example.test/v1", model="synthetic-model",
        provider_label=CANARY, client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize("status,code", [
    (400, "model_request_rejected"), (401, "model_authentication_failed"),
    (403, "model_permission_denied"), (408, "model_timeout"),
    (429, "model_rate_limited"), (500, "model_server_failed"), (503, "model_server_failed"),
])
def test_http_failure_has_safe_fixed_category(status: int, code: str) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json={"error": CANARY})

    with pytest.raises(LLMError) as caught:
        provider(handler).complete([{"role": "user", "content": CANARY}], [])
    assert str(caught.value) == code
    assert model_failure_code(caught.value) == code
    assert CANARY not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize("exception,code", [
    (httpx.ReadTimeout, "model_timeout"), (httpx.ConnectTimeout, "model_timeout"),
    (httpx.ConnectError, "model_connection_failed"), (httpx.ReadError, "model_transport_failed"),
    (httpx.RemoteProtocolError, "model_transport_failed"),
    (httpx.TooManyRedirects, "model_request_failed"),
])
def test_transport_error_text_is_not_diagnostic_content(exception: Any, code: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exception(CANARY, request=request)

    with pytest.raises(LLMError) as caught:
        provider(handler).complete([], [])
    assert str(caught.value) == code
    assert CANARY not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("body", [
    None, [], {}, {"choices": []}, {"choices": [{"message": None}]},
    {"choices": [{"message": []}]}, {"choices": [{"message": {"content": {"data": CANARY}}}]},
    {"choices": [{"message": {"content": None}}]},
    {"choices": [{"message": {"content": "ok", "tool_calls": "invalid"}}]},
    {"choices": [{"message": {"tool_calls": [None]}}]},
    {"choices": [{"message": {"tool_calls": [{"function": None}]}}]},
    {"choices": [{"message": {"tool_calls": [{"id": "id", "function": {
        "name": "tool", "arguments": {"secret": CANARY}}}]}}]},
])
def test_malformed_completion_shape_is_a_safe_provider_error(body: Any) -> None:
    with pytest.raises(LLMError, match="^model_output_invalid$"):
        provider(lambda request: httpx.Response(200, json=body)).complete([], [])


def test_non_json_response_is_a_safe_provider_error() -> None:
    with pytest.raises(LLMError, match="^model_output_invalid$"):
        provider(lambda request: httpx.Response(200, text=CANARY)).complete([], [])


@pytest.mark.parametrize("choice", [
    {"message": {"role": "assistant", "content": None, "refusal": CANARY}},
    {"message": {"role": "assistant", "refusal": CANARY}},
    {"message": {"role": "assistant", "content": None}, "finish_reason": "content_filter"},
])
def test_valid_refusal_is_classified_without_preserving_its_text(choice: dict[str, Any]) -> None:
    with pytest.raises(LLMError, match="^model_refused$"):
        provider(lambda request: httpx.Response(200, json={"choices": [choice]})).complete([], [])


def test_malformed_refusal_shape_remains_invalid() -> None:
    body = {"choices": [{"message": {"content": None, "refusal": {"text": CANARY}}}]}
    with pytest.raises(LLMError, match="^model_output_invalid$"):
        provider(lambda request: httpx.Response(200, json=body)).complete([], [])


def test_refusal_is_a_known_failed_step_without_automatic_retry(system: Any) -> None:
    service, worker, _, _, _, principal, *_ = system
    conversation = prepare(system)
    body = {"choices": [{"message": {"content": None, "refusal": CANARY}}]}
    worker.generator = ModelGeneration(provider(lambda request: httpx.Response(200, json=body)), PersonaRegistry())
    worker.run_one(conversation.id)
    assert not worker.run_one(conversation.id)
    with service.store.transaction() as db:
        row = db.execute("SELECT status,error FROM mentor_steps").fetchone()
        assert dict(row) == {"status": "failed", "error": "model_refused"}
    resumed = service.apply(Command(id="resume-refused", principal_id=principal.id,
                            conversation_id=conversation.id, kind="resume", expected_revision=1))
    assert resumed.status == "queued"
    assert service.budgets.status(conversation)["total_requests"] == 1


@pytest.mark.parametrize("message", [
    "codex_timeout " + CANARY, "401 authentication " + CANARY,
    "malformed quota 429 " + CANARY, CANARY,
])
def test_unrecognized_error_text_stays_unknown(message: str) -> None:
    assert model_failure_code(LLMError(message)) == "model_request_failed"


@pytest.mark.parametrize("code", list(MODEL_ERROR_CATEGORIES))
def test_evaluation_receives_same_allowlisted_category(code: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from evalmesh_support.providers import failure_observation

    result = failure_observation(LLMError(code))
    assert result["output"]["error_category"] == MODEL_ERROR_CATEGORIES[code]
    assert result["output"]["error_code"] == code


@pytest.mark.parametrize("status,code,step_status", [
    (401, "model_authentication_failed", "failed"),
    (429, "model_rate_limited", "failed"),
    (503, "model_server_failed", "unknown"),
    (408, "model_timeout", "unknown"),
])
def test_mentor_persists_category_and_never_automatically_retries(
    system: Any, status: int, code: str, step_status: str, tmp_path: Path,
) -> None:
    service, worker, dispatcher, channel, _, principal, *_ = system
    conversation = prepare(system)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json={"error": CANARY})

    worker.generator = ModelGeneration(provider(handler), PersonaRegistry())
    assert worker.run_one(conversation.id)
    assert not worker.run_one(conversation.id)
    assert not dispatcher.dispatch_one(conversation.id)
    assert len(calls) == 1 and not channel.sent
    assert service.budgets.status(conversation)["total_requests"] == 1
    assert all(item.kind == "user" for item in service.store.list("artifact", conversation.id, Artifact))
    reopened = MentorStore(tmp_path / "mentor.sqlite3")
    with reopened.transaction() as db:
        row = db.execute("SELECT status,error FROM mentor_steps").fetchone()
        assert dict(row) == {"status": step_status, "error": code}
        assert reopened.lookup(db, "blocked", conversation.id) == code
        assert CANARY not in json.dumps([dict(x) for x in db.execute("SELECT * FROM mentor_keys")])
    if step_status == "unknown":
        with pytest.raises(MentorError, match="step_reconciliation_required"):
            service.apply(Command(id="explicit-resume", principal_id=principal.id,
                          conversation_id=conversation.id, kind="resume", expected_revision=1))


def test_unknown_historical_step_is_not_reclassified(system: Any) -> None:
    service, worker, _, _, generator, *_ = system
    conversation = prepare(system)

    def unknown(request: Any, before_send: Any) -> None:
        before_send()
        raise RuntimeError(CANARY)

    generator.generate = unknown
    worker.run_one(conversation.id)
    service.recover()
    with service.store.transaction() as db:
        row = db.execute("SELECT status,error FROM mentor_steps").fetchone()
        assert dict(row) == {"status": "unknown", "error": "generation_unknown"}
