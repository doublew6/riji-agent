"""Audit frozen synthetic Git history without publishing or changing its index."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from privacy_audit import audit_sources  # noqa: E402

SPEC = importlib.util.spec_from_file_location("audit_scan", ROOT / "scripts/privacy_scan.py")
assert SPEC and SPEC.loader
scanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)
CONFIG = {"version": 1, "private_tokens": [], "allow_values": [], "private_path_globs": []}


def git(repo: Path, *args: str) -> bytes:
    environment = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(["git", *args], cwd=repo, env=environment, check=True,
                          capture_output=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Synthetic Author")
    git(tmp_path, "config", "user.email", "author@example.com")
    (tmp_path / "public.md").write_text("Public text\n")
    git(tmp_path, "add", "public.md")
    git(tmp_path, "commit", "-m", "Synthetic initial commit")
    return tmp_path


def test_history_checks_removed_blob_and_message_once(repo: Path) -> None:
    secret = "sk-" + "SYNTHETIC" * 5
    (repo / "secret.md").write_text(secret)
    git(repo, "add", "secret.md")
    git(repo, "commit", "-m", "Synthetic example " + secret)
    git(repo, "rm", "secret.md")
    git(repo, "commit", "-m", "Remove synthetic sensitive content")
    result = audit_sources(repo, "history", CONFIG, git, "HEAD")
    assert result["counts"]["commits"] == 3
    assert result["counts"]["unique_blobs"] == 2
    assert len([f for f in result["findings"] if f["category"] == "api_token"]) == 2
    assert secret not in json.dumps(result)
    latest = audit_sources(repo, "revision", CONFIG, git, "HEAD")
    assert not latest["findings"]


def test_all_files_includes_untracked_but_not_ignored(repo: Path) -> None:
    (repo / ".gitignore").write_text("local/\n")
    (repo / "local").mkdir()
    (repo / "local" / "secret.md").write_text("/" + "Users/synthetic-owner/secret")
    (repo / "new.md").write_text("/" + "Users/synthetic-owner/work")
    result = audit_sources(repo, "all-files", CONFIG, git)
    assert result["counts"]["paths"] == 3
    assert len(result["findings"]) == 1
    assert result["findings"][0]["source"] == "new.md"


def test_images_and_binary_are_review_items_with_exact_digest(repo: Path) -> None:
    image = b"\x89PNG\r\n\x1a\n" + b"synthetic pixels"
    (repo / "picture.png").write_bytes(image)
    (repo / "archive.bin").write_bytes(b"\xff\0\xee")
    result = audit_sources(repo, "all-files", CONFIG, git)
    assert not result["ok"]
    assert not result["automated_scan_complete"]
    assert {r["category"] for r in result["manual_review"]} == {
        "image_pixels_and_metadata", "unknown_binary"}
    assert any(r["sha256"] == hashlib.sha256(image).hexdigest()
               for r in result["manual_review"])


def test_svg_requires_visual_review_and_also_scans_visible_text(repo: Path) -> None:
    secret = "sk-" + "SYNTHETIC" * 5
    (repo / "diagram.svg").write_text("<svg><text>" + secret + "</text></svg>")
    result = audit_sources(repo, "all-files", CONFIG, git)
    assert result["manual_review"][0]["category"] == "image_vector_and_metadata"
    assert result["findings"][0]["category"] == "api_token"
    assert secret not in json.dumps(result)


def test_paths_scanned_even_when_blob_shared_and_private_name_redacted(repo: Path) -> None:
    secret = "sk-" + "SYNTHETIC" * 5
    (repo / secret).write_text("Public text\n")
    (repo / "auth.json").write_text("Public text\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Synthetic path example")
    result = audit_sources(repo, "revision", CONFIG, git, "HEAD")
    assert result["counts"]["unique_blobs"] == 1
    assert {f["category"] for f in result["findings"]} == {"api_token", "private_file"}
    assert secret not in json.dumps(result)


def test_identity_values_never_emitted_but_review_remains_required(repo: Path) -> None:
    git(repo, "config", "user.name", "Synthetic Private Author")
    git(repo, "config", "user.email", "hidden@private-company.invalid")
    git(repo, "commit", "--allow-empty", "-m", "Synthetic identity")
    config = dict(CONFIG, private_tokens=["Synthetic Private Author"])
    result = audit_sources(repo, "history", config, git, "HEAD")
    assert result["identity_metadata"]["review_required"]
    assert len(result["identity_metadata"]["identities"]) == 2
    assert "Synthetic Private Author" not in json.dumps(result)
    assert "hidden@private-company.invalid" not in json.dumps(result)
    assert any("personal_identifier" in i["categories"]
               for i in result["identity_metadata"]["identities"])


def test_audit_preserves_head_index_worktree_and_untracked_files(repo: Path) -> None:
    (repo / "public.md").write_text("Staged replacement\n")
    git(repo, "add", "public.md")
    (repo / "public.md").write_text("Unstaged replacement\n")
    (repo / "new.md").write_text("Untracked\n")
    before = (git(repo, "show-ref"), git(repo, "diff", "--cached"),
              git(repo, "diff"), git(repo, "status", "--porcelain=v1"))
    audit_sources(repo, "history", CONFIG, git, "HEAD")
    audit_sources(repo, "all-files", CONFIG, git)
    after = (git(repo, "show-ref"), git(repo, "diff", "--cached"),
             git(repo, "diff"), git(repo, "status", "--porcelain=v1"))
    assert before == after


def test_symlink_is_not_followed(repo: Path) -> None:
    (repo / "link.md").symlink_to(repo.parent / "outside-do-not-read")
    result = audit_sources(repo, "all-files", CONFIG, git)
    assert result["manual_review"] == [{"category": "symbolic_link", "source": "link.md"}]


def test_tracked_file_under_symlink_directory_is_not_followed(repo: Path) -> None:
    folder = repo / "folder"
    folder.mkdir()
    (folder / "note.md").write_text("Public\n")
    git(repo, "add", "folder/note.md")
    (folder / "note.md").unlink()
    folder.rmdir()
    outside = repo.parent / "external-data"
    outside.mkdir()
    (outside / "note.md").write_text("/" + "Users/synthetic-owner/secret")
    folder.symlink_to(outside, target_is_directory=True)
    result = audit_sources(repo, "all-files", CONFIG, git)
    assert not result["findings"]
    assert any(r["category"] == "symbolic_link" and r["source"] == "folder/note.md"
               for r in result["manual_review"])


def test_oversized_input_fails_closed_without_reading(repo: Path, monkeypatch) -> None:
    import privacy_audit
    monkeypatch.setattr(privacy_audit, "MAX_BYTES", 10)
    (repo / "huge.txt").write_text("x" * 11)
    result = audit_sources(repo, "all-files", CONFIG, git)
    assert not result["ok"]
    assert any(r["category"] == "oversized_input" for r in result["manual_review"])


def test_total_size_limit_is_explicit(repo: Path, monkeypatch) -> None:
    import privacy_audit
    monkeypatch.setattr(privacy_audit, "MAX_TOTAL_BYTES", 1)
    result = audit_sources(repo, "all-files", CONFIG, git)
    assert not result["ok"]
    assert result["manual_review"][0]["category"] == "total_size_limit"


def test_cli_requires_history_head_and_can_save_safe_report(repo: Path) -> None:
    config = repo / "private-config.json"
    config.write_text(json.dumps(CONFIG))
    config.chmod(0o600)
    command = [sys.executable, str(ROOT / "scripts/privacy_scan.py"), "--config", str(config)]
    missing = subprocess.run([*command, "--history"], cwd=repo, capture_output=True, text=True)
    assert missing.returncode == 2
    assert json.loads(missing.stdout)["error"] == "history_requires_explicit_head"
    report = repo / "audit-report.json"
    run = subprocess.run([*command, "--revision", "HEAD", "--report", str(report)],
                         cwd=repo, capture_output=True, text=True)
    assert run.returncode == 2  # Identity metadata still requires human review.
    assert json.loads(report.read_text())["head"] == git(repo, "rev-parse", "HEAD").decode().strip()
    assert "findings" not in json.loads(run.stdout)


def test_private_config_is_applied_to_file_content(repo: Path) -> None:
    (repo / "notes.md").write_text("SyntheticSensitiveMarker")
    config = dict(CONFIG, private_tokens=["SyntheticSensitiveMarker"])
    result = audit_sources(repo, "all-files", config, git)
    assert result["findings"][0]["category"] == "personal_identifier"
    assert "SyntheticSensitiveMarker" not in json.dumps(result)


def test_git_only_receives_read_commands(repo: Path) -> None:
    commands = []

    def record(path: Path, *args: str) -> bytes:
        commands.append(args[0])
        return git(path, *args)

    audit_sources(repo, "history", CONFIG, record, "HEAD")
    audit_sources(repo, "all-files", CONFIG, record)
    assert set(commands) <= {"rev-parse", "rev-list", "show", "ls-tree", "cat-file", "ls-files"}
