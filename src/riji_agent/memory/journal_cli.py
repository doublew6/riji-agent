"""Explicit bounded journal initialization and resumable local controls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from riji_agent.memory.service import MemoryService


def register_journal_command(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("journal", help="Manage local journal-derived long-term memory.")
    parser.add_argument("journal_action", choices=("plan", "initialize", "run", "status", "pause", "resume", "retry", "export", "restore"))
    parser.add_argument("--max-jobs", type=int, default=100, help="Maximum fragments processed in this invocation.")
    parser.add_argument("--output", type=Path, help="Local export bundle outside the journal root.")
    parser.add_argument("--input", type=Path, help="Bundle to validate or restore into an empty backend.")
    parser.add_argument("--apply", action="store_true", help="Apply a validated restore; otherwise only validate.")
    parser.add_argument("--evidence-ref", nargs=2, action="append", metavar=("ID", "VERSION"),
                        help="With retry, select a failed fragment and its exact source version; repeat as needed.")


def run_journal_command(args: argparse.Namespace, service: MemoryService) -> int:
    engine = service.journal
    if engine is None:
        print("Journal memory is disabled; configure its allowed user and sections first.", file=sys.stderr)
        return 2
    if not 1 <= args.max_jobs <= 10000:
        print("max-jobs must be between 1 and 10000.", file=sys.stderr)
        return 2
    action = args.journal_action
    refs = getattr(args, "evidence_ref", None)
    if refs and action != "retry":
        print("evidence-ref is only supported with retry.", file=sys.stderr)
        return 2
    if action in {"export", "restore"}:
        return _transfer(args, service)
    if action in {"plan", "initialize", "run"}:
        engine.scan()
    if action in {"pause", "resume"}:
        if action == "resume" and not engine.privacy.status()["valid"]:
            print("Authorize the current scope in Memory Review before resuming.", file=sys.stderr)
            return 1
        if action == "resume" and engine.store.get_control("restore_in_progress"):
            print("Complete the pending restore before resuming.", file=sys.stderr)
            return 1
        engine.store.set_control("paused", "1" if action == "pause" else "0")
    if action == "retry":
        if refs:
            engine.store.retry_failed_evidence([tuple(ref) for ref in refs])
        else:
            engine.store.retry()
        engine.notify_change()
    processed = 0
    if action in {"initialize", "run"}:
        while processed < args.max_jobs and engine.process_next():
            processed += 1
    progress = engine.store.progress()
    print(json.dumps(dict(progress, privacy=engine.privacy.status(), processed_this_run=processed), ensure_ascii=False, indent=2))
    return 1 if progress["error"] else 0


def _transfer(args: argparse.Namespace, service: MemoryService) -> int:
    from riji_agent.memory.journal_transfer import JournalMemoryTransfer
    try:
        transfer = JournalMemoryTransfer(service.backend)
        path = args.output if args.journal_action == "export" else args.input
        if path is None:
            print("Specify --output for export or --input for restore.", file=sys.stderr)
            return 2
        result = transfer.export(path.expanduser()) if args.journal_action == "export" else transfer.restore(
            path.expanduser(), apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(getattr(exc, "code", "memory_transfer_failed"), file=sys.stderr)
        return 1
