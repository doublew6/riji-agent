"""Sealed, subscription-authenticated official Codex CLI invocation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any

from riji_agent.models.types import LLMError

DISABLED_FEATURES = (
    "apps", "plugins", "remote_plugin", "recommended_plugins", "hooks", "shell_tool",
    "unified_exec", "shell_snapshot", "view_image", "browser_use", "browser_use_external",
    "browser_use_full_cdp_access", "computer_use", "image_generation",
    "code_mode_host", "skill_search", "skill_mcp_dependency_install", "tool_suggest",
    "multi_agent", "multi_agent_v2", "memories", "goals", "workspace_dependencies",
    "sleep_tool", "request_permissions_tool", "auth_elicitation", "tool_call_mcp_elicitation",
)
SUPPORTED_VERSIONS = {"codex-cli 0.153.4", "codex-cli 0.153.0-alpha.5"}


def child_environment(home: Path, proxy_url: str | None = None) -> dict[str, str]:
    allowed = {
        "HOME", "USER", "LOGNAME", "PATH", "TMPDIR", "LANG", "LC_ALL", "HTTPS_PROXY",
        "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "all_proxy",
        "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "CODEX_CA_CERTIFICATE",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env["CODEX_HOME"] = str(home)
    if proxy_url is not None:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env[key] = proxy_url
        # Explicit routing must not be bypassed by ambient NO_PROXY=* or a
        # remote hostname. Local service traffic remains exempt in this child.
        env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"
    return env


def check_home(home: Path) -> None:
    """Never borrow a personal profile or a copied/symlinked credential cache."""
    if (not home.is_absolute() or not home.is_dir() or home.is_symlink()
            or home.resolve() == (Path.home() / ".codex").resolve()):
        raise LLMError("codex_home_not_isolated")
    if any((home / name).exists() for name in (
            "AGENTS.md", "AGENTS.override.md", "config.toml", "skills", "plugins", "rules", "hooks.json")):
        raise LLMError("codex_home_not_isolated")
    if (home / "auth.json").is_symlink():
        raise LLMError("codex_home_not_isolated")
    if home.stat().st_mode & 0o077:
        raise LLMError("codex_home_permissions_invalid")


def check_login(binary: str, home: Path, timeout: float, proxy_url: str | None = None) -> None:
    try:
        version = subprocess.run(
            [binary, "--version"], env=child_environment(home, proxy_url), stdin=subprocess.DEVNULL,
            capture_output=True, timeout=max(0.1, min(timeout, 5)), check=False,
        )
        if version.returncode != 0 or version.stdout.decode(errors="replace").strip() not in SUPPORTED_VERSIONS:
            raise LLMError("codex_unsupported_version")
        result = subprocess.run(
            [binary, "login", "status"], env=child_environment(home, proxy_url), stdin=subprocess.DEVNULL,
            capture_output=True, timeout=max(0.1, min(timeout, 5)), check=False,
        )
    except subprocess.TimeoutExpired:
        raise LLMError("codex_timeout") from None
    except OSError:
        raise LLMError("codex_unavailable") from None
    output = result.stdout + result.stderr
    if result.returncode != 0 or b"Logged in using ChatGPT" not in output:
        raise LLMError("codex_login_required")


def command(binary: str, model: str, home: Path, workdir: Path, schema_path: Path) -> list[str]:
    args = [binary, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
            "--skip-git-repo-check", "--sandbox", "read-only", "--json", "--color", "never",
            "--output-schema", str(schema_path), "--cd", str(workdir), "--model", model]
    config: dict[str, Any] = {
        "model_provider": "openai", "chatgpt_base_url": "https://chatgpt.com/backend-api",
        "approval_policy": "never", "web_search": "disabled", "project_doc_max_bytes": 0,
        "project_doc_fallback_filenames": [], "developer_instructions": "", "notify": [],
        "skills.include_instructions": False, "skills.bundled.enabled": False,
        "apps._default.enabled": False, "agents.enabled": False,
        "tools.update_plan.enabled": False, "tools.experimental_request_user_input.enabled": False,
        "features.skip_host_skill_discovery": True, "analytics.enabled": False,
        "features.code_mode.enabled": False,
        "suppress_unstable_features_warning": True,
        "otel.exporter": "none", "otel.trace_exporter": "none", "otel.log_user_prompt": False,
        "history.persistence": "none", "sqlite_home": str(workdir / "state"),
        "log_dir": str(workdir / "logs"), "model_reasoning_effort": "low",
    }
    config.update({"features." + name: False for name in DISABLED_FEATURES})
    for key, value in config.items():
        args.extend(["-c", key + "=" + json.dumps(value)])
    return args + ["-"]


def safe_error(value: Any) -> LLMError:
    text = json.dumps(value).lower()
    if any(word in text for word in ("usage_limit", "rate_limit", "quota", "429", "usage limit")):
        return LLMError("codex_quota_exhausted")
    if any(word in text for word in ("unauthorized", "401", "refresh_token", "login", "authentication")):
        return LLMError("codex_login_required")
    return LLMError("codex_request_failed")
