"""Offline protocol, privacy and harness integration checks for EvalMesh."""

from __future__ import annotations

import importlib
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from riji_agent.models.types import AssistantTurn, LLMError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def modules(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    names = {"adapter": "evalmesh_adapter", "providers": "evalmesh_support.providers",
             "private": "evalmesh_support.private_io", "suite": "evalmesh_support.suite",
             "cli": "evaluate_agent_suite"}
    return {key: importlib.import_module(name) for key, name in names.items()}


def _envelope(**updates: Any) -> dict[str, Any]:
    return {"protocol": "evalmesh.case.v1", "case_id": "synthetic-01",
            "input": {"family": "memory", "data_class": "synthetic"}, **updates}


def _stdin(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data), encoding="utf-8"))


def test_adapter_reads_only_input_envelope(modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    value = _envelope()
    _stdin(monkeypatch, json.dumps(value).encode())
    assert modules["adapter"].read_case() == value


@pytest.mark.parametrize("value", [
    [], {}, _envelope(protocol="evalmesh.result.v1"), _envelope(case_id="../../outside"),
    _envelope(input={"family": "memory"}),
    _envelope(input={"family": "memory", "data_class": "production"}),
    _envelope(expected={"answer": "never send this"}),
    _envelope(input={"data_class": "synthetic", "expected": {}}),
    _envelope(input={"data_class": "synthetic", "rubric": {}}),
    _envelope(input={"data_class": "synthetic", "gold": {}}),
])
def test_adapter_rejects_malformed_or_answer_bearing_envelopes(
    value: Any, modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stdin(monkeypatch, json.dumps(value).encode())
    with pytest.raises(ValueError):
        modules["adapter"].read_case()


@pytest.mark.parametrize("raw", [b'{"x": NaN}', b'{} trailing', b'[' + b' ' * 1048576])
def test_adapter_rejects_nonfinite_invalid_and_oversized_json(
    raw: bytes, modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stdin(monkeypatch, raw)
    with pytest.raises(ValueError):
        modules["adapter"].read_case()


def test_adapter_rejects_ambiguous_duplicate_json_keys(
    modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = (b'{"protocol":"evalmesh.case.v1","case_id":"synthetic-01",'
           b'"input":{"data_class":"production","data_class":"synthetic"}}')
    _stdin(monkeypatch, raw)
    with pytest.raises(ValueError):
        modules["adapter"].read_case()


@pytest.mark.parametrize("metric", [True, float("nan"), float("inf"), "1", None])
def test_result_metrics_must_be_finite_numbers(metric: Any, modules: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        modules["adapter"].validate_result({"output": {}, "metrics": {"calls": metric}})


class _PlainProvider:
    provider_name = "controlled"
    model_name = "offline"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: Any, tools: Any) -> AssistantTurn:
        self.calls += 1
        return AssistantTurn("controlled")


class _GuardedProvider(_PlainProvider):
    def complete_with_guard(self, messages: Any, tools: Any, before_send: Any) -> AssistantTurn:
        before_send()
        return self.complete(messages, tools)

    def complete_json_with_guard(self, messages: Any, schema: Any, before_send: Any) -> AssistantTurn:
        before_send()
        return self.complete(messages, schema)


def test_counted_provider_preserves_absence_of_optional_methods(modules: dict[str, Any]) -> None:
    underlying = _PlainProvider()
    counted = modules["providers"].CountedProvider(underlying, max_calls=1)
    assert not hasattr(counted, "complete_json_with_guard")
    assert not hasattr(counted, "complete_with_guard")
    assert counted.provider_name == "controlled"
    counted.complete([{"role": "user", "content": "synthetic request"}], [])
    with pytest.raises(LLMError, match="evaluation_call_limit"):
        counted.complete([], [])
    assert underlying.calls == counted.calls == 1


def test_guarded_provider_preserves_send_guard_and_json_mode(modules: dict[str, Any]) -> None:
    underlying = _GuardedProvider()
    counted = modules["providers"].CountedProvider(underlying)
    guards = []
    counted.complete_with_guard([], [], before_send=lambda: guards.append("chat"))
    counted.complete_json_with_guard([], {"type": "object"}, before_send=lambda: guards.append("json"))
    assert guards == ["chat", "json"]
    assert counted.calls == underlying.calls == 2


@pytest.mark.parametrize("method,second", [("complete_with_guard", "tools"),
                                          ("complete_json_with_guard", "schema")])
def test_keyword_provider_calls_have_full_character_accounting(
    method: str, second: str, modules: dict[str, Any],
) -> None:
    counted = modules["providers"].CountedProvider(_GuardedProvider())
    messages = [{"role": "user", "content": "synthetic payload " * 20}]
    getattr(counted, method)(**{"messages": messages, second: [], "before_send": lambda: None})
    assert counted.request_chars >= len(messages[0]["content"])
    assert counted.calls == 1


def test_missing_credentials_do_not_load_project_dotenv(
    modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=synthetic-never-load\n")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="evaluation_credentials_missing"):
        modules["providers"].build_provider("deepseek", "deepseek-chat", 1)


def test_failed_provider_attempt_is_retained_and_error_text_redacted(
    modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "synthetic-error-payload-never-expose"

    class FailedProvider(_PlainProvider):
        def complete(self, messages: Any, tools: Any) -> AssistantTurn:
            raise LLMError(marker)

    counted = modules["providers"].CountedProvider(FailedProvider())
    monkeypatch.setattr(modules["adapter"], "build_provider", lambda *args: counted)
    args = SimpleNamespace(live=True, provider="deepseek", model="test-chat",
                           memory_model="test-memory", model_timeout=1)
    case = {"family": "memory", "data_class": "synthetic", "text": "今天整理了书桌。"}
    result = modules["adapter"].dispatch(case, args)
    assert result["output"]["observed"]["status"] == "failed"
    assert result["metrics"]["provider_attempts"] == 1
    assert result["metrics"]["application_request_chars"] > len(case["text"])
    assert marker not in json.dumps(result)


@pytest.mark.parametrize("message,category", [
    ("codex_login_required", "authentication"), ("codex_quota_exhausted", "quota"),
    ("codex_timeout", "timeout"), ("evaluation_call_limit", "evaluation_budget"),
    ("model_authentication_failed", "authentication"),
    ("model_rate_limited", "rate_limit"),
])
def test_provider_failure_codes_remain_distinct(
    message: str, category: str, modules: dict[str, Any],
) -> None:
    result = modules["providers"].failure_observation(LLMError(message))
    assert result["output"]["error_category"] == category
    assert result["output"]["observed"]["status"] == "failed"


def test_private_artifacts_are_owner_only_and_never_overwritten(
    modules: dict[str, Any], tmp_path: Path,
) -> None:
    directory = modules["private"].make_private_directory(tmp_path.resolve() / "private")
    artifact = directory / "attempt.json"
    modules["private"].write_private_json(artifact, {"synthetic": True})
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        modules["private"].write_private_json(artifact, {"overwritten": True})
    assert json.loads(artifact.read_text()) == {"synthetic": True}


@pytest.mark.parametrize("git_file", [False, True])
def test_private_artifact_cannot_be_inside_git_tree(
    git_file: bool, modules: dict[str, Any], tmp_path: Path,
) -> None:
    root = tmp_path.resolve() / "repository"
    root.mkdir()
    if git_file:
        (root / ".git").write_text("gitdir: elsewhere")
    else:
        (root / ".git").mkdir()
    with pytest.raises(ValueError, match="private_path_required"):
        modules["private"].make_private_directory(root / "private")


def test_private_artifact_rejects_symlinked_ancestor_and_leaf(
    modules: dict[str, Any], tmp_path: Path,
) -> None:
    directory = modules["private"].make_private_directory(tmp_path.resolve() / "private")
    alias = tmp_path.resolve() / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ValueError):
        modules["private"].write_private_json(alias / "attempt.json", {})
    (directory / "leaf.json").symlink_to(tmp_path / "outside.json")
    with pytest.raises(ValueError):
        modules["private"].write_private_json(directory / "leaf.json", {})


def test_private_artifact_rejects_public_parent_permissions(
    modules: dict[str, Any], tmp_path: Path,
) -> None:
    parent = tmp_path.resolve() / "public"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    with pytest.raises(ValueError, match="private_directory_permissions"):
        modules["private"].write_private_json(parent / "attempt.json", {})


@pytest.mark.parametrize("ancestor", [False, True])
def test_snapshot_rejects_symlinked_source_root(
    ancestor: bool, modules: dict[str, Any], tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "nested").mkdir()
    (source / "nested/module.py").write_text("SYNTHETIC = True\n")
    alias = tmp_path / "source-alias"
    alias.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="snapshot_symlink_rejected"):
        modules["suite"].copy_tree(alias / "nested" if ancestor else alias, tmp_path / "destination")


def _subject_fixture(root: Path) -> tuple[Path, Path]:
    subject, harness = root / "subject", root / "harness"
    (subject / "src").mkdir(parents=True)
    (subject / "src/module.py").write_text("SYNTHETIC = True\n")
    (subject / "scripts/evalmesh_support").mkdir(parents=True)
    (subject / "scripts/evalmesh_support/__init__.py").write_text("")
    (subject / "scripts/evalmesh_support/providers.py").write_bytes(
        (ROOT / "scripts/evalmesh_support/providers.py").read_bytes(),
    )
    for name in ("environment.py", "suite.py"):
        (subject / "scripts/evalmesh_support" / name).write_bytes(
            (ROOT / "scripts/evalmesh_support" / name).read_bytes(),
        )
    (subject / "scripts/evalmesh_adapter.py").write_text("pass\n")
    (subject / "scripts/evaluate_agent_suite.py").write_bytes(
        (ROOT / "scripts/evaluate_agent_suite.py").read_bytes(),
    )
    (subject / "pyproject.toml").write_text('[project]\nname="synthetic-subject"\n')
    (subject / "uv.lock").write_text("version = 1\n")
    (subject / "evals/agent-v1").mkdir(parents=True)
    (subject / "evals/agent-v1/memory-review.json").write_text(json.dumps({
        "status": "unreviewed", "gold": "synthetic annotation outside target fixture",
    }))
    for family, name in [("boundary", "boundaries"), ("memory", "memory"), ("mentor", "mentors")]:
        row = {"id": family + "-01", "input": {"family": family, "data_class": "synthetic"},
               "expected": {"observed": {"status": "completed"}}, "grader_ids": ["observed"],
               "tags": ["synthetic", "regression", "smoke"]}
        (subject / "evals/agent-v1" / (name + ".jsonl")).write_text(json.dumps(row) + "\n")
    (harness / "src").mkdir(parents=True)
    (harness / "src/module.py").write_text("SYNTHETIC_HARNESS = True\n")
    (harness / "pyproject.toml").write_text('[project]\nname="synthetic-harness"\n')
    return subject, harness


def _options(modules: dict[str, Any], tmp_path: Path, **updates: Any) -> Any:
    subject, harness = _subject_fixture(tmp_path)
    values = {"output": tmp_path.resolve() / "evaluation", "subject": subject, "harness": harness,
              "selection": "smoke", "provider": "deepseek", "model": "deepseek-reasoner",
              "memory_model": "deepseek-chat", "repetitions": 1, "live": False, "execute": False}
    return modules["suite"].SuiteOptions(**{**values, **updates})


def test_prepare_freezes_cases_outside_target_fixture_without_model(
    modules: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = _options(modules, tmp_path)

    def no_model(*args: Any, **kwargs: Any) -> None:
        pytest.fail("dry-run must not construct a model")

    monkeypatch.setattr(modules["providers"], "build_provider", no_model)
    cases = modules["suite"].prepare(options)
    assert len(cases) == 3
    fixture = options.output / "fixture"
    assert (fixture / "src/module.py").is_file()
    assert not (fixture / "cases.jsonl").exists()
    assert not (fixture / "evals").exists()
    assert not (fixture / "reviews").exists()
    assert (options.output / "cases.jsonl").is_file()
    review = options.output / "reviews/memory-review.json"
    assert json.loads(review.read_text())["status"] == "unreviewed"
    snapshot = json.loads((options.output / "snapshot.json").read_text())
    assert snapshot["files"]["reviews/memory-review.json"] == hashlib.sha256(review.read_bytes()).hexdigest()
    assert snapshot["files"]["harness-pyproject.toml"] == hashlib.sha256(
        (options.harness / "pyproject.toml").read_bytes(),
    ).hexdigest()
    assert snapshot["blind_holdout"] is False
    assert snapshot["attempt_count"] == 3


def test_quality_execution_requires_live_flag(modules: dict[str, Any], tmp_path: Path) -> None:
    options = _options(modules, tmp_path, execute=True)
    with pytest.raises(ValueError, match="live_execution_requires_explicit_flag"):
        modules["suite"].prepare(options)
    assert not options.output.exists()


def test_snapshot_rejects_symlinked_dependency_file(modules: dict[str, Any], tmp_path: Path) -> None:
    options = _options(modules, tmp_path)
    private_value = tmp_path / "outside.txt"
    private_value.write_text("synthetic-outside-content-must-not-be-copied")
    lock = options.subject / "uv.lock"
    lock.unlink()
    lock.symlink_to(private_value)
    with pytest.raises(ValueError, match="snapshot_symlink_rejected"):
        modules["suite"].prepare(options)


def test_cli_default_only_prepares_and_never_runs_harness(
    modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    calls = []
    monkeypatch.setattr(modules["cli"], "prepare", lambda options: calls.append(options) or [{}])
    monkeypatch.setattr(modules["cli"], "run_suite", lambda options: pytest.fail("unexpected execution"))
    monkeypatch.setattr(sys, "argv", ["evaluate_agent_suite.py", "--output", str(tmp_path / "out"),
                                    "--evalmesh-source", str(tmp_path / "harness")])
    assert modules["cli"].main() == 0
    assert calls[0].execute is calls[0].live is False


def test_actual_evalmesh_manifest_grades_observed_output_and_excludes_hmac(
    modules: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = ROOT.parent / "evalmesh" / "src"
    if not harness.is_dir():
        pytest.skip("local EvalMesh checkout unavailable for integration contract check")
    monkeypatch.syspath_prepend(str(harness))
    from evalmesh.graders import build_grader
    from evalmesh.manifest import load_suite
    from evalmesh.models import RawExecutionResult
    from evalmesh.ports import GradeContext

    options = _options(modules, tmp_path)
    modules["suite"].prepare(options)
    manifest, cases = load_suite(options.output / "evalmesh.toml")
    spec = next(item for item in manifest.graders if item.id == "observed")
    result = RawExecutionResult({"observed": {"status": "completed"}}, "", "", 0, 1)
    score = build_grader(spec).grade(GradeContext(cases[0], result))
    assert score.passed is True
    bad = RawExecutionResult({"observed": {"status": "failed"}}, "", "", 0, 1)
    assert build_grader(spec).grade(GradeContext(cases[0], bad)).passed is False
    assert "EVALMESH_HMAC_KEY" not in manifest.target.forward_env
    assert "DEEPSEEK_API_KEY" in manifest.target.forward_env
    assert manifest.target.workspace_mode == "copy"


def _sealed_options(modules: dict[str, Any], tmp_path: Path,
                    monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("EVALMESH_HMAC_KEY", "synthetic-snapshot-test-key-never-a-credential")
    options = _options(modules, tmp_path)
    modules["suite"].prepare(options)
    return options


def test_verify_snapshot_accepts_unchanged_sealed_fixture(
    modules: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = _sealed_options(modules, tmp_path, monkeypatch)
    modules["suite"].verify_snapshot(options.output)


@pytest.mark.parametrize("relative", [
    "fixture/src/module.py", "fixture/evalmesh_support/__init__.py",
    "fixture/evalmesh_support/providers.py",
    "fixture/evalmesh_adapter.py", "harness/src/module.py", "pyproject.toml",
    "reviews/memory-review.json", "harness/pyproject.toml",
])
def test_verify_snapshot_detects_source_dependency_and_annotation_mutation(
    relative: str, modules: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = _sealed_options(modules, tmp_path, monkeypatch)
    path = options.output / relative
    path.write_bytes(path.read_bytes() + b"\nchanged-after-freeze\n")
    with pytest.raises(ValueError, match="snapshot_content_changed"):
        modules["suite"].verify_snapshot(options.output)


def test_verify_snapshot_detects_case_mutation(
    modules: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = _sealed_options(modules, tmp_path, monkeypatch)
    path = options.output / "cases.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["expected"]["observed"]["status"] = "changed-after-freeze"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="snapshot_cases_changed"):
        modules["suite"].verify_snapshot(options.output)


@pytest.mark.parametrize("mutation", ["receipt", "missing_mac", "wrong_key"])
def test_verify_snapshot_rejects_invalid_seal(
    mutation: str, modules: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = _sealed_options(modules, tmp_path, monkeypatch)
    path = options.output / "snapshot.json"
    receipt = json.loads(path.read_text())
    if mutation == "receipt":
        receipt["case_count"] += 1
    elif mutation == "missing_mac":
        receipt.pop("mac")
    else:
        monkeypatch.setenv("EVALMESH_HMAC_KEY", "different-synthetic-snapshot-test-key")
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="snapshot_seal_invalid"):
        modules["suite"].verify_snapshot(options.output)


def test_dry_prepared_unsealed_snapshot_cannot_be_verified_for_execution(
    modules: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EVALMESH_HMAC_KEY", raising=False)
    options = _options(modules, tmp_path)
    modules["suite"].prepare(options)
    with pytest.raises(ValueError, match="snapshot_seal_invalid"):
        modules["suite"].verify_snapshot(options.output)


def test_verify_snapshot_detects_manifest_model_mutation(
    modules: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = _sealed_options(modules, tmp_path, monkeypatch)
    path = options.output / "evalmesh.toml"
    path.write_text(path.read_text().replace("deepseek-reasoner", "changed-model"))
    with pytest.raises(ValueError, match="snapshot_manifest_changed"):
        modules["suite"].verify_snapshot(options.output)


@pytest.mark.parametrize("relative", ["fixture/src/httpx.py", "fixture/httpx.py"])
def test_verify_snapshot_detects_added_import_shadowing_source(
    relative: str, modules: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = _sealed_options(modules, tmp_path, monkeypatch)
    (options.output / relative).write_text("SHADOWING_MODULE = True\n")
    with pytest.raises(ValueError, match="snapshot_"):
        modules["suite"].verify_snapshot(options.output)


def test_verify_snapshot_rejects_case_symlink_even_when_digest_matches(
    modules: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = _sealed_options(modules, tmp_path, monkeypatch)
    path = options.output / "cases.jsonl"
    outside = tmp_path / "same-cases.jsonl"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="snapshot_symlink_rejected"):
        modules["suite"].verify_snapshot(options.output)


def test_provider_trace_snapshots_messages_and_tools_at_each_attempt(modules: dict[str, Any]) -> None:
    counted = modules["providers"].CountedProvider(_PlainProvider())
    messages = [{"role": "user", "content": "first synthetic request"}]
    tools = [{"type": "function", "function": {"name": "search_journal"}}]
    counted.complete(messages, tools)
    original_bytes = counted.trace_bytes
    messages[0]["content"] = "later changed text"
    messages.append({"role": "assistant", "content": "later response"})
    tools[0]["function"]["name"] = "later_changed_tool"
    assert counted.trace[0]["messages"] == [{"role": "user", "content": "first synthetic request"}]
    assert counted.trace[0]["tools_or_schema"][0]["function"]["name"] == "search_journal"
    assert counted.trace_bytes == original_bytes


def test_oversized_provider_trace_is_explicitly_omitted(modules: dict[str, Any]) -> None:
    counted = modules["providers"].CountedProvider(_PlainProvider())
    messages = [{"role": "user", "content": "synthetic-large-payload" * 220000}]
    counted.complete(messages, [])
    assert counted.trace[0]["content_omitted"] is True
    assert "messages" not in counted.trace[0]
    assert "response" not in counted.trace[0]
    assert counted.trace_bytes <= 4 * 1024 * 1024


@pytest.mark.parametrize("status,exit_code", [("completed", 0), ("failed", 1)])
def test_execute_keeps_model_trace_only_in_owner_private_record(
    status: str, exit_code: int, modules: dict[str, Any], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    directory = modules["private"].make_private_directory(tmp_path.resolve() / "private-attempts")
    monkeypatch.setenv("RIJI_EVAL_OUTPUT_DIR", str(directory))
    _stdin(monkeypatch, json.dumps(_envelope()).encode())
    marker = "synthetic-private-model-trace-only"
    trace = [{"messages": [{"role": "user", "content": marker}],
              "response": {"content": "synthetic raw response", "tool_calls": []}}]
    result = {"output": {"observed": {"status": status}, "_private_model_trace": trace},
              "metrics": {"provider_attempts": 1}}
    monkeypatch.setattr(modules["adapter"], "dispatch", lambda *args: result)
    assert modules["adapter"].execute(SimpleNamespace()) == exit_code
    emitted = capsys.readouterr().out
    assert marker not in emitted
    envelope = json.loads(emitted)
    assert "_private_model_trace" not in envelope["output"]
    records = list(directory.glob("*.json"))
    assert len(records) == 1
    stored = json.loads(records[0].read_text())
    assert stored["model_trace"] == trace
    assert "_private_model_trace" not in stored["result"]["output"]
    assert stored["execution_id"] == envelope["output"]["execution_id"]
    assert stat.S_IMODE(records[0].stat().st_mode) == 0o600


@pytest.fixture
def synthetic_system_proxy(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Exercise HTTPX's real macOS-style discovery seam without any sockets."""
    import httpx._utils

    lookups: list[bool] = []

    def getproxies() -> dict[str, str]:
        lookups.append(True)
        return {"https": "http://127.0.0.1:1"}

    monkeypatch.setattr(httpx._utils, "getproxies", getproxies)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-route-key")
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.delenv(name, raising=False)
    return lookups


def test_evaluation_uses_direct_verified_tls_despite_system_proxy_and_environment_ca(
    modules: dict[str, Any], synthetic_system_proxy: list[bool],
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import httpx
    import ssl

    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "unused-private-ca.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "unused-private-ca-directory"))
    counted = modules["providers"].build_provider("deepseek", "synthetic-model", 17)
    client = counted.provider._client
    try:
        assert isinstance(client, httpx.Client)
        assert client._transport_for_url(httpx.URL("https://api.deepseek.com")) is client._transport
        assert synthetic_system_proxy == []
        assert client.timeout == httpx.Timeout(17)
        context = client._transport._pool._ssl_context
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
    finally:
        client.close()


@pytest.mark.parametrize("provider_name", ["deepseek", "openai"])
def test_production_provider_still_inherits_system_proxy(
    provider_name: str, synthetic_system_proxy: list[bool],
) -> None:
    import httpx
    from riji_agent.models.deepseek import DeepSeekProvider
    from riji_agent.models.openai_compatible import OpenAICompatibleProvider

    if provider_name == "deepseek":
        provider = DeepSeekProvider(api_key="example-model-key")
    else:
        provider = OpenAICompatibleProvider(api_key="example-model-key", model="synthetic-model",
                                            base_url="https://api.example.test")
    try:
        client = provider._client
        assert synthetic_system_proxy == [True]
        assert client._transport_for_url(httpx.URL(provider._url)) is not client._transport
    finally:
        provider._client.close()


@pytest.mark.parametrize("failure", ["ReadTimeout", "RemoteProtocolError"])
def test_explicit_route_failure_has_one_counted_attempt_and_no_fallback(
    failure: str, modules: dict[str, Any], synthetic_system_proxy: list[bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    from riji_agent.models.errors import UNCERTAIN_MODEL_OUTCOMES

    calls = []
    marker = "synthetic-route-error-body-do-not-expose"

    def fail(self: Any, request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise getattr(httpx, failure)(marker, request=request)

    # Patch only sending; retain the real client and proxy/SSL construction.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", fail)
    counted = modules["providers"].build_provider("deepseek", "synthetic-model", 1)
    counted.max_calls = 1
    code = "model_timeout" if failure == "ReadTimeout" else "model_transport_failed"
    try:
        with pytest.raises(LLMError, match="^" + code + "$"):
            counted.complete([], [])
        with pytest.raises(LLMError, match="evaluation_call_limit"):
            counted.complete([], [])
        assert len(calls) == counted.calls == 1
        assert calls[0].url == httpx.URL("https://api.deepseek.com/chat/completions")
        assert counted.failures[0]["error_code"] == code
        assert code in UNCERTAIN_MODEL_OUTCOMES
        assert marker not in json.dumps([counted.failures, counted.trace])
        assert synthetic_system_proxy == []
    finally:
        counted.provider._client.close()


def test_stream_keepalive_comments_are_accepted_without_retry(
    modules: dict[str, Any], synthetic_system_proxy: list[bool], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    calls = []

    def complete(self: Any, request: httpx.Request) -> httpx.Response:
        calls.append(request)
        from sse_fixtures import completion_bytes
        body = b": keepalive\r\n\r\n" + completion_bytes({"content": "synthetic answer"})
        return httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", complete)
    counted = modules["providers"].build_provider("deepseek", "synthetic-model", 1)
    try:
        assert counted.complete([], []).content == "synthetic answer"
        assert counted.calls == len(calls) == 1
        assert json.loads(calls[0].content)["stream"] is True
        assert synthetic_system_proxy == []
    finally:
        counted.provider._client.close()


@pytest.mark.parametrize("failed", [False, True])
def test_route_metadata_matches_sealed_snapshot_on_success_and_failure(
    failed: bool, modules: dict[str, Any], synthetic_system_proxy: list[bool],
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import httpx
    import evalmesh_support.memory as memory

    options = _sealed_options(modules, tmp_path, monkeypatch)
    policy = modules["providers"].provider_route_policy("deepseek")
    receipt_path = options.output / "snapshot.json"
    receipt = json.loads(receipt_path.read_text())
    assert receipt["provider_route_policy"] == policy
    clients = []

    def run(case: Any, counted: Any) -> dict[str, Any]:
        clients.append(counted.provider._client)
        turn = counted.complete([], [])
        return {"output": {"answer": turn.content}, "metrics": {}}

    def complete(self: Any, request: httpx.Request) -> httpx.Response:
        if failed:
            raise httpx.RemoteProtocolError("synthetic-private-route-detail", request=request)
        from sse_fixtures import completion_response
        return completion_response({"content": "synthetic"})

    monkeypatch.setattr(memory, "evaluate", run)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", complete)
    args = SimpleNamespace(live=True, provider="deepseek", model="synthetic-model",
                           memory_model="synthetic-model", model_timeout=1)
    try:
        result = modules["adapter"].dispatch({"family": "memory", "data_class": "synthetic"}, args)
        assert result["output"]["provider_route_policy"] == policy
        assert result["output"]["provider_response_mode"] == "sse"
        assert result["metrics"]["provider_attempts"] == 1
        if failed:
            assert result["output"]["error_code"] == "model_transport_failed"
        assert "127.0.0.1" not in json.dumps(policy)
        assert "synthetic-private-route-detail" not in json.dumps(result)
        modules["suite"].verify_snapshot(options.output)
        receipt["provider_route_policy"]["trust_env"] = True
        receipt_path.write_text(json.dumps(receipt))
        with pytest.raises(ValueError, match="snapshot_seal_invalid"):
            modules["suite"].verify_snapshot(options.output)
    finally:
        for client in clients:
            client.close()


def test_boundary_receipt_and_execution_never_construct_http_client(
    modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import httpx
    import evalmesh_support.boundaries as boundaries

    monkeypatch.setattr(httpx, "Client", lambda *a, **k: pytest.fail("unexpected live client"))
    monkeypatch.setattr(modules["adapter"], "build_provider",
                        lambda *a, **k: pytest.fail("unexpected model provider"))
    options = _options(modules, tmp_path, selection="boundary", live=True)
    modules["suite"].prepare(options)
    receipt = json.loads((options.output / "snapshot.json").read_text())
    assert receipt["provider_route_policy"] == modules["providers"].provider_route_policy("controlled")
    seen = []
    monkeypatch.setattr(boundaries, "evaluate", lambda case, provider: seen.append(provider) or
                        {"output": {"observed": {"status": "controlled"}}, "metrics": {}})
    result = modules["adapter"].dispatch({"family": "boundary"}, SimpleNamespace(live=True))
    assert result["output"]["observed"]["status"] == "controlled"
    assert "provider_route_policy" not in result["output"]
    assert seen == [None]


def test_codex_route_policy_does_not_claim_http_directness(modules: dict[str, Any]) -> None:
    assert modules["providers"].provider_route_policy("codex") == {
        "provider": "codex", "route": "runtime_managed",
    }


@pytest.mark.parametrize("old_contract", ["missing", "different"])
def test_prepare_rejects_subject_with_a_different_route_implementation(
    old_contract: str, modules: dict[str, Any], tmp_path: Path,
) -> None:
    options = _options(modules, tmp_path)
    path = options.subject / "scripts/evalmesh_support/providers.py"
    if old_contract == "missing":
        path.unlink()
    else:
        path.write_text("# Synthetic prior adapter that inherits the system route.\n")
    with pytest.raises(ValueError, match="snapshot_provider_route_contract_mismatch"):
        modules["suite"].prepare(options)
    assert not (options.output / "snapshot.json").exists()
