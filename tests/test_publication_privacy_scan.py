"""Exercise publication inputs rather than scanner implementation details."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publication_scan", ROOT / "scripts/privacy_scan.py")
assert spec and spec.loader
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)
CONFIG = {"version": 1, "private_tokens": [], "allow_values": [], "private_path_globs": []}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_staged_blob_is_checked_when_worktree_was_sanitized(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    file = tmp_path / "issue.md"
    file.write_text("Host directory: /" + "Users/synthetic-owner/private-work")
    _git(tmp_path, "add", "issue.md")
    file.write_text("Host directory: <private-directory>")
    assert scanner._working_files(tmp_path, ["issue.md"], CONFIG) == []
    assert any(f.category == "personal_path" for f in scanner.staged_findings(tmp_path, CONFIG))


def test_unstaged_private_edit_is_not_confused_with_publish_content(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    file = tmp_path / "issue.md"
    file.write_text("Host directory: <private-directory>")
    _git(tmp_path, "add", "issue.md")
    file.write_text("Host directory: /" + "Users/synthetic-owner/private-work")
    assert scanner.staged_findings(tmp_path, CONFIG) == []


def test_event_text_is_scanned_as_data_and_never_echoed(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    canary = "sk-" + "SYNTHETIC" * 5
    event = tmp_path / "event.json"
    event.write_text(json.dumps({
        "issue": {"title": "Deployment token " + canary,
                  "body": f"$(touch {marker})"},
        "comment": {"body": "Host /" + "Users/synthetic-owner/private-work"},
    }))
    result = subprocess.run([sys.executable, str(ROOT / "scripts/privacy_scan.py"),
                             "--event-file", str(event)], capture_output=True, text=True)
    assert result.returncode == 2
    assert canary not in result.stdout + result.stderr
    assert not marker.exists()
    payload = json.loads(result.stdout)
    assert {f["source"] for f in payload["findings"]} >= {"event.issue.title", "event.comment.body"}


def test_public_event_placeholder_and_loopback_are_allowed(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"title": "Keep the API local",
        "body": "Use 127.0.0.1, /Users/example/app and test@example.com."}}))
    assert scanner.event_findings(event, CONFIG) == []
