from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

from riji_agent.chat import run_local_chat
from riji_agent.config import Settings
from riji_agent.models.types import AssistantTurn, ToolCall


class StubProvider:
    """Drives the loop: first a search_journal tool call, then a final answer."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self,
        messages: Sequence[Dict[str, Any]],
        tools: Sequence[Dict[str, Any]],
    ) -> AssistantTurn:
        self.calls += 1
        if self.calls == 1:
            return AssistantTurn(
                content=None,
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="search_journal",
                        arguments=json.dumps({"query": "launch"}),
                    ),
                ),
            )
        return AssistantTurn(content="Launch facts: [[riji/daily/2026-01-05]]")


def _vault(tmp_path: Path) -> Path:
    journal = tmp_path / "journal"
    (journal / "daily").mkdir(parents=True)
    (journal / "daily" / "2026-01-05.md").write_text(
        "---\ndate: 2026-01-05\ntags:\n  - launch\n---\n# 2026-01-05\n\n"
        "## Evening\n\n- Reviewed the launch planning checklist.\n",
        encoding="utf-8",
    )
    # A private note that also mentions launch must never reach the model.
    (journal / "daily" / "2026-01-06.md").write_text(
        "---\ndate: 2026-01-06\nprivate: true\n---\n# 2026-01-06\n\n"
        "## Evening\n\n- Secret launch budget details.\n",
        encoding="utf-8",
    )
    return journal


def _settings(tmp_path: Path) -> Settings:
    journal = _vault(tmp_path)
    return Settings(
        _env_file=None,
        RIJI_JOURNAL_ROOT=str(journal),
        RIJI_DATA_DIR=str(tmp_path / "data"),
        DEEPSEEK_API_KEY="secret",
        RIJI_ALLOWED_FEISHU_USER_IDS="ou_one",
        HERMES_SHARED_SECRET="another-secret",
    )


def test_local_chat_runs_real_loop_with_injected_provider(tmp_path: Path) -> None:
    stub = StubProvider()
    output = run_local_chat(_settings(tmp_path), "launch planning", provider=stub)

    assert stub.calls >= 2  # at least one tool round, then the final answer
    assert "Launch facts" in output
    assert "[[riji/daily/2026-01-05]]" in output


def test_local_chat_excludes_private_notes(tmp_path: Path) -> None:
    output = run_local_chat(_settings(tmp_path), "launch planning", provider=StubProvider())

    assert "riji/daily/2026-01-06" not in output


def test_local_chat_writes_audit(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_local_chat(settings, "launch planning", provider=StubProvider())

    assert (settings.data_dir / "audit.sqlite3").exists()
