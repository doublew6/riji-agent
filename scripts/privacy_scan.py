#!/usr/bin/env python3
"""Fail fast when tracked or staged files contain private local details."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


TEXT_EXTENSIONS = {
    ".cfg",
    ".css",
    ".env",
    ".example",
    ".html",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

ALLOWED_BINARY_FILES = {
    "assets/integrations/feishu/riji-bot-avatar.png",
}

FORBIDDEN_PATH_PARTS = (
    "/.env",
    ".sqlite3",
    ".db",
    ".pem",
    ".key",
)

PRIVATE_EMAIL_TOKENS = (
    "5300" + "wc501",
    "ocean" + "_net",
    "seatothe" + "moon18",
)

FORBIDDEN_CONTENT = (
    re.compile("/" + r"Users/(?!example(?:/|\b))[^\s'\"`]+"),
    re.compile("Mobile " + "Documents"),
    re.compile("iCloud" + "~md~obsidian"),
    re.compile(r"\b(?:" + "|".join(PRIVATE_EMAIL_TOKENS) + r")@?[A-Za-z0-9._-]*\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:cli|ou|oc)_[A-Za-z0-9]{12,}\b"),
    re.compile("Chat" + r"GPT.*(?:20|100).*(?:美|会员)"),
    re.compile("Resource " + "deadlock avoided"),
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout


def _repo_root() -> Path:
    return Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").strip())


def _split_nul(output: bytes) -> list[str]:
    return [item.decode("utf-8") for item in output.split(b"\0") if item]


def _staged_files(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return _split_nul(result.stdout)


def _tracked_files(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return _split_nul(result.stdout)


def _is_text_file(path: Path) -> bool:
    if path.suffix in TEXT_EXTENSIONS:
        return True
    try:
        sample = path.read_bytes()[:4096]
    except OSError:
        return False
    return b"\0" not in sample


def _check_path(rel_path: str) -> list[str]:
    if rel_path in ALLOWED_BINARY_FILES:
        return []

    lowered = rel_path.lower()
    errors: list[str] = []
    if lowered == ".env" or lowered.endswith(FORBIDDEN_PATH_PARTS):
        errors.append("private file type should not be tracked or committed")
    if lowered.startswith(("data/", "riji/", "journals/")):
        errors.append("private data directory should not be tracked or committed")
    return errors


def _line_number(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _check_content(path: Path) -> list[str]:
    if not path.exists() or not _is_text_file(path):
        return []

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []

    errors: list[str] = []
    for pattern in FORBIDDEN_CONTENT:
        for match in pattern.finditer(text):
            line = _line_number(text, match.start())
            errors.append(f"line {line}: matched {pattern.pattern!r}")
    return errors


def scan(repo: Path, files: list[str]) -> list[str]:
    failures: list[str] = []
    for rel_path in sorted(set(files)):
        path = repo / rel_path
        for error in _check_path(rel_path):
            failures.append(f"{rel_path}: {error}")
        for error in _check_content(path):
            failures.append(f"{rel_path}: {error}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="scan staged files")
    mode.add_argument("--tracked", action="store_true", help="scan tracked files")
    args = parser.parse_args()

    repo = _repo_root()
    files = _staged_files(repo) if args.staged else _tracked_files(repo)
    failures = scan(repo, files)
    if failures:
        print("Privacy scan failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    scope = "staged files" if args.staged else "tracked files"
    print(f"Privacy scan passed for {len(files)} {scope}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
