from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from riji_agent.config import Settings
from riji_agent.config_cli import _model_checks
from riji_agent.memory.journal_engine import JournalMemoryEngine
from riji_agent.memory.privacy_ui import privacy_banner, privacy_panel
from riji_agent.models.deepseek import DeepSeekProvider
from riji_agent.models.registry import build_model_provider, build_memory_model_provider
from riji_agent.wiring import build_memory_runtime
from test_journal_memory import runtime
from test_mem0_long_term_memory import FakeBackend


def settings_for(tmp_path: Path, **overrides) -> Settings:
    journal = tmp_path / "journal"
    journal.mkdir(exist_ok=True)
    values = dict(
        _env_file=None, RIJI_JOURNAL_ROOT=journal, RIJI_DATA_DIR=tmp_path / "data",
        DEEPSEEK_API_KEY=None, RIJI_MODEL_PROVIDER="codex",
        RIJI_MEMORY_MODEL_PROVIDER="codex", RIJI_ALLOWED_FEISHU_USER_IDS="u1",
        HERMES_SHARED_SECRET="test-shared-secret",
    )
    values.update(overrides)
    return Settings(**values)


def test_codex_only_does_not_require_unused_api_credentials(tmp_path):
    settings = settings_for(tmp_path, RIJI_MEMORY_PROVIDER="mem0", RIJI_MEM0_API_KEY="test-local-key")
    assert settings.deepseek_api_key is None and settings.model_api_key is None
    assert settings.codex_model == "gpt-5.6-terra"
    assert settings.memory_codex_model == "gpt-5.6-luna"
    assert settings.codex_home == tmp_path / "data" / "codex"
    assert settings.codex_proxy_url is None


@pytest.mark.parametrize("overrides", [
    {"RIJI_MODEL_PROVIDER": "deepseek"},
    {"RIJI_MEMORY_PROVIDER": "mem0", "RIJI_MEM0_API_KEY": "test-local-key",
     "RIJI_MEMORY_MODEL_PROVIDER": "deepseek"},
])
def test_active_deepseek_role_still_requires_its_key(tmp_path, overrides):
    with pytest.raises(ValueError, match="DeepSeek API key is required"):
        settings_for(tmp_path, **overrides)


@pytest.mark.parametrize("overrides", [
    {"RIJI_MEMORY_MODEL_PROVIDER": "openai"},
    {"RIJI_CODEX_BIN": "\n"},
    {"RIJI_CODEX_MODEL": ""},
    {"RIJI_MEMORY_CODEX_MODEL": "model\nunsafe"},
    {"RIJI_CODEX_TIMEOUT_SECONDS": 0},
    {"RIJI_CODEX_TIMEOUT_SECONDS": float("inf")},
    {"RIJI_CODEX_HOME": "relative/codex"},
])
def test_codex_config_rejects_invalid_values(tmp_path, overrides):
    with pytest.raises(ValueError):
        settings_for(tmp_path, **overrides)


def test_codex_home_cannot_be_inside_the_journal_root(tmp_path):
    with pytest.raises(ValueError, match="outside the journal root"):
        settings_for(tmp_path, RIJI_CODEX_HOME=tmp_path / "journal" / "codex")


@pytest.mark.parametrize("proxy", [
    "http://127.0.0.1:15236", "https://localhost:443/", "http://[::1]:8080",
])
def test_codex_proxy_accepts_only_explicit_local_transport_and_masks_settings(tmp_path, proxy):
    settings = settings_for(tmp_path, RIJI_CODEX_PROXY_URL=proxy)
    assert settings.codex_proxy_url.get_secret_value() == proxy
    assert proxy not in repr(settings) and proxy not in settings.model_dump_json()


@pytest.mark.parametrize("proxy", [
    "https://proxy.example:443", "http://127.0.0.1", "http://127.0.0.1:0",
    "http://127.0.0.1:65536", "http://127.0.0.1:invalid", "socks5://127.0.0.1:1080",
    "http://user:secret@127.0.0.1:8080", "http://@127.0.0.1:8080",
    "http://127.0.0.1:8080/path", "http://127.0.0.1:8080?secret=value",
    "http://127.0.0.1:8080#secret", "http://127.0.0.1:8080?", "http://127.0.0.1:8080#",
    " http://127.0.0.1:8080", "http://127.0.0.1:8080\n", "http://127.0.0.1:8080\x00",
    "http://[::1", "http://127.0.0.1:8080/;secret", "http://127.0.0.1:8080\\",
])
def test_codex_proxy_rejects_unsupported_or_ambiguous_routes(tmp_path, proxy):
    with pytest.raises(ValueError, match="Codex proxy"):
        settings_for(tmp_path, RIJI_CODEX_PROXY_URL=proxy)


def test_empty_codex_proxy_keeps_existing_environment_behavior(tmp_path):
    assert settings_for(tmp_path, RIJI_CODEX_PROXY_URL="").codex_proxy_url is None


def test_chat_and_all_memory_paths_use_independent_providers(tmp_path, monkeypatch):
    class FakeCodex:
        def __init__(self, **kwargs):
            self.options = kwargs

    monkeypatch.setitem(sys.modules, "riji_agent.models.codex", SimpleNamespace(CodexProvider=FakeCodex))
    settings = settings_for(
        tmp_path, RIJI_MODEL_PROVIDER="deepseek", DEEPSEEK_API_KEY="test-deepseek-key",
        RIJI_MEMORY_PROVIDER="mem0", RIJI_MEM0_API_KEY="test-local-key",
        RIJI_JOURNAL_MEMORY_ENABLED=True, RIJI_JOURNAL_MEMORY_USER_ID="u1",
        RIJI_MEMORY_SNAPSHOT_ENABLED=False,
        RIJI_CODEX_PROXY_URL="http://127.0.0.1:15236",
    )
    settings.ensure_data_directory()
    assert isinstance(build_model_provider(settings), DeepSeekProvider)
    service, worker = build_memory_runtime(settings, backend=FakeBackend())
    provider = service.journal.provider
    assert isinstance(provider, FakeCodex)
    assert provider.options == dict(binary="codex", model="gpt-5.6-luna", timeout_seconds=90.0,
                                    purpose="memory", home=tmp_path / "data" / "codex",
                                    proxy_url="http://127.0.0.1:15236")
    assert provider is worker._processor._extractor._provider
    assert provider is worker._organizer._provider
    assert service.journal.policy.extraction_destination == "https://chatgpt.com"
    assert service.journal.policy.extraction_model == "gpt-5.6-luna"
    assert service.journal.policy.recall_destination == "https://api.deepseek.com"
    settings.model_provider = "codex"
    settings.memory_model_provider = "deepseek"
    assert build_model_provider(settings).options["purpose"] == "chat"
    assert build_model_provider(settings).options["proxy_url"] == "http://127.0.0.1:15236"
    assert isinstance(build_memory_model_provider(settings), DeepSeekProvider)


@pytest.mark.parametrize("field,value", [
    ("extraction_provider", "codex"), ("extraction_model", "different-model"),
    ("recall_provider", "codex"), ("recall_model", "different-model"),
])
def test_provider_or_model_change_invalidates_consent(tmp_path, field, value):
    engine, provider = runtime(tmp_path)
    changed = JournalMemoryEngine(replace(engine.policy, **{field: value}), engine.store, engine.backend, provider)
    assert not changed.privacy.status()["valid"]
    assert not changed.process_next()
    assert not provider.calls


def test_privacy_banner_displays_both_actual_recipients_and_models(tmp_path):
    engine, _ = runtime(
        tmp_path, extraction_provider="codex", extraction_destination="https://chatgpt.com",
        extraction_model="gpt-5.6-luna", recall_provider="codex",
        recall_destination="https://chatgpt.com", recall_model="gpt-5.6-terra",
    )
    from test_journal_lifecycle import attached_service
    page = SimpleNamespace(service=attached_service(tmp_path, engine), user_id="u1",
                           settings=SimpleNamespace(memory_auto_capture=True))
    for rendered in (privacy_banner(page), privacy_panel(page)):
        assert "OpenAI / Codex（ChatGPT 套餐）" in rendered
        assert "https://chatgpt.com" in rendered
        assert "gpt-5.6-luna" in rendered and "gpt-5.6-terra" in rendered
        assert "deepseek-chat" not in rendered


@pytest.mark.parametrize("stdout,stderr,returncode,expected", [
    ("", "Logged in using ChatGPT", 0, "chatgpt"),
    ("Logged in using an API key: secret-that-must-not-leak", "", 0, "api_key_rejected"),
    ("", "Not logged in /private/path", 1, "login_required"),
    ("Unexpected secret-that-must-not-leak", "", 0, "login_required"),
])
def test_doctor_only_reads_login_status_and_never_discloses_raw_output(
    tmp_path, monkeypatch, stdout, stderr, returncode, expected,
):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
    monkeypatch.setattr("riji_agent.config_cli.subprocess.run", run)
    ok, messages = _model_checks(settings_for(tmp_path, RIJI_CODEX_BIN="/synthetic/codex"))
    assert ok == (expected == "chatgpt")
    rendered = "\n".join(messages)
    assert expected in rendered
    assert "secret-that-must-not-leak" not in rendered and "/private/path" not in rendered
    command, kwargs = calls[0]
    assert command == ["/synthetic/codex", "login", "status"]
    assert kwargs["timeout"] == 5 and kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["env"]["CODEX_HOME"] == str(tmp_path / "data" / "codex")
    assert "OPENAI_API_KEY" not in kwargs["env"]
    assert len(calls) == 1


def test_doctor_handles_missing_binary_without_login(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise FileNotFoundError("/private/secret/path")
    monkeypatch.setattr("riji_agent.config_cli.subprocess.run", fail)
    ok, messages = _model_checks(settings_for(tmp_path))
    assert not ok and "codex_auth: unavailable" in messages
    assert "/private" not in "\n".join(messages)


def test_doctor_keeps_proxy_in_codex_child_and_does_not_claim_connectivity(tmp_path, monkeypatch):
    import os

    calls = []
    proxy = "http://127.0.0.1:15236"
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7777")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-private-key")
    before = dict(os.environ)
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="Logged in using ChatGPT", stderr="", returncode=0)
    monkeypatch.setattr("riji_agent.config_cli.subprocess.run", run)
    ok, messages = _model_checks(settings_for(tmp_path, RIJI_CODEX_PROXY_URL=proxy))
    assert ok and dict(os.environ) == before and len(calls) == 1
    child = calls[0][1]["env"]
    assert child["HTTP_PROXY"] == child["HTTPS_PROXY"] == proxy
    assert "OPENAI_API_KEY" not in child
    rendered = "\n".join(messages)
    assert "configured (Codex child only); connectivity not checked" in rendered
    assert proxy not in rendered and "synthetic-private-key" not in rendered


def test_inactive_memory_codex_does_not_trigger_auth_check(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unused Codex must not be invoked")
    monkeypatch.setattr("riji_agent.config_cli.subprocess.run", forbidden)
    ok, _ = _model_checks(settings_for(tmp_path, RIJI_MODEL_PROVIDER="deepseek", DEEPSEEK_API_KEY="test"))
    assert ok
