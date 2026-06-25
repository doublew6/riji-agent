"""CLI: ``riji-agent index`` / ``--rebuild`` / ``--status`` over a temp vault.

``load_settings`` is patched so the CLI runs against a fixture vault rather than
the real .env or journal.
"""

from pathlib import Path

import pytest

from riji_agent import main as cli
from riji_agent.config import Settings

DUMMY_KEY = "cli-stub-key-not-real"
PRIVATE_BODY = "绝不应被任何命令打印的私密正文XYZ"


def _settings(tmp_path: Path) -> Settings:
    root = tmp_path / "riji"
    (root / "daily").mkdir(parents=True)
    (root / "daily" / "2026-06-24.md").write_text(
        "---\ndate: 2026-06-24\ntags: [ai]\n---\n# 2026-06-24\n项目进展评审通过。\n",
        encoding="utf-8",
    )
    (root / "daily" / "2026-06-25.md").write_text(
        f"---\ndate: 2026-06-25\nprivate: true\n---\n# 2026-06-25\n{PRIVATE_BODY}。\n",
        encoding="utf-8",
    )
    return Settings(
        _env_file=None,
        RIJI_JOURNAL_ROOT=str(root),
        RIJI_DATA_DIR=str(tmp_path / "state"),
        DEEPSEEK_API_KEY=DUMMY_KEY,
        RIJI_ALLOWED_FEISHU_USER_IDS="ou_1",
        HERMES_SHARED_SECRET="cli-secret",
    )


@pytest.fixture
def patched(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    return settings


def _run(argv) -> int:
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    return int(exc.value.code or 0)


def test_index_incremental_prints_safe_stats(patched, capsys) -> None:
    assert _run(["index"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("indexed:")
    assert "added=2" in out  # both notes newly indexed
    assert "duration_s=" in out
    assert DUMMY_KEY not in out
    assert PRIVATE_BODY not in out


def test_index_rebuild_prints_rebuilt(patched, capsys) -> None:
    assert _run(["index"]) == 0  # first build
    capsys.readouterr()
    assert _run(["index", "--rebuild"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("rebuilt:")
    assert "added=2" in out  # rebuild re-adds every note


def test_index_status_is_metadata_only(patched, capsys) -> None:
    _run(["index"])  # populate first
    capsys.readouterr()
    assert _run(["index", "--status"]) == 0
    out = capsys.readouterr().out

    assert "note_count: 2" in out
    assert "last_indexed_at:" in out
    assert "semantic_search: off" in out
    assert "schedule_enabled: True" in out
    # never leak the key or private note bodies
    assert DUMMY_KEY not in out
    assert PRIVATE_BODY not in out


def test_status_on_empty_index_reports_never(patched, capsys) -> None:
    # no prior build: status must still work and report an empty index
    assert _run(["index", "--status"]) == 0
    out = capsys.readouterr().out
    assert "note_count: 0" in out
    assert "last_indexed_at: (never)" in out
