#!/usr/bin/env python3
"""Check publication inputs without printing the private values they contain."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from privacy_guard_core import (  # noqa: E402
    Finding, GuardError, MAX_BYTES, load_config, report, scan_bytes, scan_path_name, scan_text,
)


def _git(repo: Path, *args: str) -> bytes:
    command_line_git = Path("/Library/Developer/CommandLineTools/usr/bin/git")
    executable = str(command_line_git) if command_line_git.is_file() else "git"
    result = subprocess.run(
        [executable, *args], cwd=repo, capture_output=True, check=False,
    )
    if result.returncode:
        raise GuardError("git_read_failed")
    return result.stdout


def _names(raw: bytes) -> list[str]:
    return [item.decode("utf-8") for item in raw.split(b"\0") if item]


def _inspect(raw: bytes, name: str, config: dict[str, Any]) -> list[Finding]:
    if len(raw) > MAX_BYTES:
        raise GuardError("scan_input_too_large")
    return scan_path_name(name, name, config) + scan_bytes(raw, name, config)


def _working_files(repo: Path, files: list[str], config: dict[str, Any]) -> list[Finding]:
    findings = []
    for name in sorted(set(files)):
        path = repo / name
        if path.is_symlink():
            raise GuardError("publication_symlink_requires_review")
        if not path.exists():
            continue
        findings.extend(_inspect(path.read_bytes(), name, config))
    return findings


def scan(repo: Path, files: list[str]) -> list[str]:
    """Keep the existing working-file API for local callers and tests."""
    return [f"{item.source}:{item.line}: {item.category}" for item in
            _working_files(repo, files, load_config())]


def staged_findings(repo: Path, config: dict[str, Any]) -> list[Finding]:
    names = _names(_git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"))
    findings = []
    for name in names:
        mode = _git(repo, "ls-files", "--stage", "--", name).split(b" ", 1)[0]
        if mode in (b"120000", b"160000"):
            raise GuardError("publication_link_requires_review")
        findings.extend(_inspect(_git(repo, "show", ":" + name), name, config))
    return findings


def event_findings(path: Path, config: dict[str, Any]) -> list[Finding]:
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise GuardError("event_input_unavailable")
    event = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(event, dict):
        raise GuardError("event_input_invalid")
    findings = []
    for subject in ("issue", "pull_request", "comment", "review"):
        item = event.get(subject)
        if not isinstance(item, dict):
            continue
        for field in ("title", "body"):
            value = item.get(field)
            if isinstance(value, str):
                findings.extend(scan_text(value, f"event.{subject}.{field}", config))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--tracked", action="store_true")
    mode.add_argument("--event-file", type=Path)
    mode.add_argument("--all-files", action="store_true",
                      help="Audit tracked and non-ignored untracked working files, read-only.")
    mode.add_argument("--revision", help="Audit one Git commit tree and its message, read-only.")
    mode.add_argument("--history", action="store_true",
                      help="Audit all ancestors of an explicit --head, read-only.")
    parser.add_argument("--head", help="Exact ref/commit to freeze before a history audit.")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--report", type=Path,
                        help="Save a safe JSON audit report; only valid for audit modes.")
    args = parser.parse_args()
    try:
        if bool(args.history) != bool(args.head):
            raise GuardError("history_requires_explicit_head")
        if args.report and not (args.all_files or args.revision or args.history):
            raise GuardError("report_requires_audit_mode")
        config = load_config(args.config)
        if args.event_file:
            return report(event_findings(args.event_file, config))
        repo = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
        if args.all_files or args.revision or args.history:
            from privacy_audit import audit_sources

            selected = "all-files" if args.all_files else "history" if args.history else "revision"
            result = audit_sources(repo, selected, config, _git, args.head or args.revision)
            if args.report:
                if args.report.is_symlink():
                    raise GuardError("report_symlink_not_allowed")
                args.report.write_text(json.dumps(result, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
                print(json.dumps({key: value for key, value in result.items()
                                  if key not in ("findings", "manual_review", "identity_metadata")},
                                 ensure_ascii=True))
            else:
                print(json.dumps(result, ensure_ascii=True))
            return 0 if result["ok"] else 2
        if args.staged:
            return report(staged_findings(repo, config))
        return report(_working_files(repo, _names(_git(repo, "ls-files", "-z")), config))
    except GuardError as exc:
        return report([], str(exc))
    except (OSError, ValueError, UnicodeError):
        return report([], "scan_input_invalid")


if __name__ == "__main__":
    raise SystemExit(main())
