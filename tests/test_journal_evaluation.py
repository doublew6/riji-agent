"""Offline checks for explicit synthetic evaluation and truthful result boundaries."""

from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from riji_agent.memory.journal_types import JournalMemoryError
from riji_agent.models.types import AssistantTurn, LLMError


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/evaluate_journal_memory.py"


@pytest.fixture
def evaluation():
    spec = importlib.util.spec_from_file_location("journal_evaluation_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SyntheticProvider:
    provider_name = "codex"
    model_name = "synthetic-configured-model"

    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []
        self.scopes = 0
        self.active_scope = False

    @contextmanager
    def request_scope(self):
        self.scopes += 1
        self.active_scope = True
        try:
            yield
        finally:
            self.active_scope = False

    def complete(self, messages, tools):
        assert self.active_scope
        assert tools == []
        self.calls.append(messages)
        value = next(self.replies)
        if isinstance(value, Exception):
            raise value
        return AssistantTurn(json.dumps(value, ensure_ascii=False))


def sample_case():
    return {"id": "synthetic-evaluation-case", "observed_at": "2026-01-01",
            "source_kind": "weekly", "text": "这周我修好了书架。", "review": "Check the quoted event.",
            "existing": [{"id": "fixture-existing", "content": "修好了书架。", "kind": "event",
                          "valid_from": "2026-01-01"}]}


def extraction():
    return {"complete": True, "memories": [{"content": "修好了书架。", "kind": "event",
            "certainty": "explicit", "valid_from": "2026-01-01", "quotes": ["我修好了书架"]}]}


@pytest.mark.parametrize("args,count", [([], 10), (["--provider", "codex", "--suite", "acceptance"], 8)])
def test_plan_never_loads_settings_or_constructs_model(evaluation, monkeypatch, capsys, args, count):
    def forbidden(*args):
        pytest.fail("plan must not read deployment settings or construct providers")
    monkeypatch.setattr(evaluation, "load_settings", forbidden)
    monkeypatch.setattr(evaluation, "build_memory_model_provider", forbidden)
    assert evaluation.main(args) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["mode"] == "plan" and plan["cloud_calls"] == 0
    assert len(plan["cases"]) == count and len(plan["corpus_sha256"]) == 64


def test_case_filter_cannot_silently_create_empty_success(evaluation, monkeypatch):
    monkeypatch.setattr(evaluation, "_build_provider", lambda name: pytest.fail("must not construct"))
    with pytest.raises(SystemExit) as error:
        evaluation.main(["--case", "not-a-bundled-case", "--run-model"])
    assert error.value.code == 2


def test_missing_output_fails_before_provider_construction(evaluation, monkeypatch):
    monkeypatch.setattr(evaluation, "_build_provider", lambda name: pytest.fail("must not construct"))
    with pytest.raises(SystemExit) as error:
        evaluation.main(["--run-model"])
    assert error.value.code == 2


@pytest.mark.parametrize("name", ["deepseek", "codex"])
def test_provider_factory_changes_only_memory_selection(evaluation, monkeypatch, tmp_path, name):
    observed = []
    selected = SimpleNamespace(memory_model_provider=name)
    original = SimpleNamespace(journal_root=tmp_path, memory_model_provider="original",
                               model_copy=lambda **kwargs: observed.append(kwargs) or selected)
    monkeypatch.setattr(evaluation, "load_settings", lambda: original)
    model = object()
    monkeypatch.setattr(evaluation, "build_memory_model_provider", lambda value: model if value is selected else None)
    actual, root = evaluation._build_provider(name)
    assert actual is model and root == tmp_path
    assert observed == [{"update": {"memory_model_provider": name}}]
    assert original.memory_model_provider == "original"
    assert list(tmp_path.iterdir()) == []


def test_real_adapter_construction_preserves_isolation_without_initializing_data(evaluation, monkeypatch, tmp_path):
    from riji_agent.config import Settings
    from riji_agent.models.codex import CodexProvider
    from riji_agent.models.deepseek import DeepSeekProvider
    journal = tmp_path / "synthetic-vault"
    journal.mkdir()
    data = tmp_path / "not-created-data"
    settings = Settings(_env_file=None, RIJI_JOURNAL_ROOT=journal, RIJI_DATA_DIR=data,
                        RIJI_MODEL_PROVIDER="codex", RIJI_MEMORY_MODEL_PROVIDER="codex",
                        RIJI_MEMORY_CODEX_MODEL="synthetic-configured-memory-model",
                        RIJI_ALLOWED_FEISHU_USER_IDS="test-user",
                        DEEPSEEK_API_KEY="test-deepseek-key", HERMES_SHARED_SECRET="test-shared-secret")
    monkeypatch.setattr(evaluation, "load_settings", lambda: settings)
    codex, _ = evaluation._build_provider("codex")
    assert isinstance(codex, CodexProvider) and codex.purpose == "memory"
    assert codex.home == data / "codex" and codex.model_name == "synthetic-configured-memory-model"
    deepseek, _ = evaluation._build_provider("deepseek")
    try:
        assert isinstance(deepseek, DeepSeekProvider) and deepseek.model_name == "deepseek-chat"
    finally:
        deepseek._client.close()
    assert not data.exists() and list(journal.iterdir()) == []


def test_guarded_stage_accounting_and_source_kind(evaluation):
    model = SyntheticProvider([extraction(), {"decisions": [{"index": 0, "action": "duplicate",
                              "target_id": "fixture-existing", "reason": "Same synthetic event."}]}])
    result = evaluation.evaluate_case(sample_case(), model)
    assert result["status"] == "valid_contract" and model.scopes == 1
    assert result["requested_model"] == model.model_name and result["requested_provider"] == "codex"
    assert result["guarded_send_attempts"] == 2
    assert result["request_chars"] == sum(len(message["content"]) for call in model.calls for message in call)
    assert sum(stage["request_chars"] for stage in result["stages"].values()) == result["request_chars"]
    assert json.loads(model.calls[0][1]["content"])["kind"] == "weekly"
    assert result["semantic_review"] == {"status": "unreviewed", "verdict": None}


def test_empty_extraction_does_not_make_relation_call(evaluation):
    model = SyntheticProvider([{"complete": True, "memories": []}])
    result = evaluation.evaluate_case(sample_case(), model)
    assert result["status"] == "valid_contract" and result["decisions"] == []
    assert len(model.calls) == result["guarded_send_attempts"] == 1
    assert set(result["stages"]) == {"extract"}


def test_structured_provider_counts_the_transmitted_business_schema(evaluation):
    class Structured(SyntheticProvider):
        def complete_json_with_guard(self, messages, schema, before_send):
            self.schema = schema
            before_send()
            return self.complete(messages, [])
    model = Structured([{"complete": True, "memories": []}])
    result = evaluation.evaluate_case(sample_case(), model)
    messages = sum(len(message["content"]) for message in model.calls[0])
    assert result["status"] == "valid_contract" and result["guarded_send_attempts"] == 1
    assert result["request_chars"] == messages + len(json.dumps(model.schema, ensure_ascii=False))


@pytest.mark.parametrize("error,expected", [
    (RuntimeError("sensitive arbitrary upstream detail"), "model_probe_failed"),
    (JournalMemoryError("journal_incomplete_extraction"), "journal_incomplete_extraction"),
    (JournalMemoryError("journal_private_arbitrary_detail"), "model_probe_failed"),
    (LLMError("codex_quota_exhausted"), "codex_quota_exhausted"),
    (LLMError("codex_login_required with arbitrary upstream detail"), "model_probe_failed"),
])
def test_failure_report_never_contains_arbitrary_error_text(evaluation, error, expected):
    model = SyntheticProvider([error])
    result = evaluation.evaluate_case(sample_case(), model)
    assert result["status"] == "failed" and result["error"] == expected
    assert result["guarded_send_attempts"] == 1 and result["stages"]["extract"]["request_chars"] > 0
    assert "arbitrary" not in json.dumps(result)


def test_codex_failure_before_send_is_not_counted_as_sent_request(evaluation):
    class Unavailable(SyntheticProvider):
        def complete_with_guard(self, messages, tools, before_send):
            raise RuntimeError("synthetic login unavailable before send")
    model = Unavailable([])
    result = evaluation.evaluate_case(sample_case(), model)
    assert result["status"] == "failed"
    assert result["guarded_send_attempts"] == result["request_chars"] == 0 and not model.calls


@pytest.mark.parametrize("provider", ["deepseek", "codex"])
def test_run_reports_selected_identity_and_preserves_legacy_fields(evaluation, monkeypatch, tmp_path, provider):
    model = SyntheticProvider([{"complete": True, "memories": []}])
    model.provider_name = provider
    model.model_name = "deepseek-chat" if provider == "deepseek" else "synthetic-memory-model"
    seen = []
    monkeypatch.setattr(evaluation, "_build_provider", lambda name: (seen.append(name) or model, tmp_path / "vault"))
    output = tmp_path / "report.json"
    args = ["--run-model", "--case", "no_durable_memory", "--output", str(output)]
    if provider == "codex":
        args += ["--provider", "codex"]
    assert evaluation.main(args) == 0 and seen == [provider]
    result = json.loads(output.read_text())
    assert result["requested_provider"] == provider and result["requested_model"] == model.model_name
    assert result["corpus"] == "synthetic-v1" and result["results"][0]["status"] == "valid_contract"
    assert result["billing_tokens"] is None and result["monetary_cost"] is None
    assert result["semantic_quality"]["score"] is None and result["coverage"]["semantic_retrieval"] == "not_run"
    assert output.stat().st_mode & 0o777 == 0o600


def test_failed_case_keeps_partial_report_and_exits_nonzero(evaluation, monkeypatch, tmp_path):
    model = SyntheticProvider([{"complete": False, "memories": []}])
    monkeypatch.setattr(evaluation, "_build_provider", lambda name: (model, tmp_path / "vault"))
    output = tmp_path / "report.json"
    assert evaluation.main(["--run-model", "--case", "no_durable_memory", "--output", str(output)]) == 1
    assert json.loads(output.read_text())["results"][0]["error"] == "journal_incomplete_extraction"


def test_output_rejects_vault_sources_symlinks_and_non_json(evaluation, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    source = tmp_path / "source.json"
    source.write_text("do not overwrite")
    alias = tmp_path / "alias.json"
    alias.symlink_to(source)
    for path in (vault, vault / "report.json", alias, evaluation.CASES, evaluation.ACCEPTANCE, tmp_path / ".env"):
        with pytest.raises(ValueError):
            evaluation._validate_output(path, vault)
    assert source.read_text() == "do not overwrite"


def test_report_setup_failure_prevents_model_calls_and_sanitizes_errors(evaluation, monkeypatch, tmp_path, capsys):
    model = SyntheticProvider([])
    monkeypatch.setattr(evaluation, "_build_provider", lambda name: (model, tmp_path / "vault"))
    monkeypatch.setattr(evaluation, "_save", lambda *args: (_ for _ in ()).throw(OSError("private output detail")))
    with pytest.raises(SystemExit):
        evaluation.main(["--run-model", "--output", str(tmp_path / "report.json")])
    assert model.calls == [] and "private output detail" not in capsys.readouterr().err


def test_acceptance_is_separate_synthetic_unreviewed_material(evaluation):
    old, _ = evaluation._load_cases("development", [])
    new, metadata = evaluation._load_cases("acceptance", [])
    assert not {c["id"] for c in old}.intersection(c["id"] for c in new)
    assert not {c["text"] for c in old}.intersection(c["text"] for c in new)
    assert metadata["annotation"] == {"origin": "assistant_authored_synthetic", "review_status": "unreviewed",
                                      "reviewer": None, "user_ground_truth": False, "blind_holdout": False}
    assert all(c["question_date"] and c["proposed_checks"] and len(c["text"]) <= 900 for c in new)
    template = json.loads((evaluation.ACCEPTANCE.parent / "review-template.json").read_text())
    assert template["labels"] == [] and template["reviewer"] is None
    assert not template["thresholds_frozen_before_holdout"]
