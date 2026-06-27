"""FastAPI application entrypoint and local CLI for the riji-agent boundary."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Sequence

import uvicorn
from fastapi import FastAPI

from riji_agent.agent.hermes import HermesAgentRuntime, build_hermes_runtime_router
from riji_agent.config import ConfigurationError, Settings, load_settings
from riji_agent.config_cli import DEFAULT_PRESET, run_doctor, write_init_env
from riji_agent.demo import copy_sample_vault, run_demo_chat
from riji_agent.integrations.hermes_installer import (
    HermesBridgeInstallError,
    install as install_hermes_bridge,
    status as hermes_bridge_status,
    uninstall as uninstall_hermes_bridge,
)
from riji_agent.journal.index import JournalIndex
from riji_agent.wiring import build_journal_index, build_production_gateway


def create_app(
    settings: Optional[Settings] = None,
    *,
    gateway: Optional[HermesAgentRuntime] = None,
    lifespan=None,
) -> FastAPI:
    """Create the application without exposing configuration through its API.

    When a gateway is supplied the Hermes message route is mounted; otherwise the
    app only serves health checks (e.g. before the model wiring is configured).
    """
    runtime_settings = settings or load_settings()
    app = FastAPI(
        title="riji-agent",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = runtime_settings

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"service": "riji-agent", "status": "ok"}

    if gateway is not None:
        app.include_router(build_hermes_runtime_router(gateway))

    return app


def create_production_app(settings: Optional[Settings] = None) -> FastAPI:
    """Create the fully wired app and start background index maintenance.

    Startup never blocks unboundedly on a cold vault: the initial index runs in
    the background and we wait only up to ``index_startup_timeout_seconds`` for
    it, then the periodic scheduler keeps the index fresh.
    """
    runtime_settings = settings or load_settings()
    gateway = build_production_gateway(runtime_settings)
    scheduler = gateway.index_scheduler

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        # Bound the initial prewarm WITHOUT blocking the event loop and WITHOUT
        # a non-daemon worker. The build runs in the scheduler's own daemon
        # thread; we poll its done-event up to the startup timeout, then proceed
        # and let it finish in the background. Because the worker is a daemon, a
        # build stuck on a cold iCloud read can never keep the process alive
        # after shutdown (issue #43); startup stays non-blocking (issue #39).
        timeout = runtime_settings.index_startup_timeout_seconds
        done = scheduler.begin_prewarm()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not done.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                logging.getLogger("riji_agent.index").info(
                    "index prewarm exceeded %.3gs startup budget; continuing in background",
                    timeout,
                )
                break
            await asyncio.sleep(min(0.05, remaining))
        scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()

    app = create_app(runtime_settings, gateway=gateway, lifespan=_lifespan)
    app.state.index_scheduler = scheduler
    return app


# --------------------------------------------------------------------- CLI


def _run_index_command(*, rebuild: bool, status: bool) -> int:
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    index = build_journal_index(settings)
    try:
        if status:
            _print_index_status(settings, index)
        else:
            started = time.monotonic()
            stats = index.build_index(rebuild=rebuild, progress=_cli_progress)
            duration = round(time.monotonic() - started, 3)
            action = "rebuilt" if rebuild else "indexed"
            print(
                f"{action}: added={stats.added} updated={stats.updated} "
                f"unchanged={stats.unchanged} deleted={stats.deleted} "
                f"skipped={stats.skipped} duration_s={duration}"
            )
            if stats.skipped_sources:
                # Sanitized wikilink ids only — never absolute paths or content.
                print("skipped (slow/unreadable):", file=sys.stderr)
                for source_id in stats.skipped_sources:
                    print(f"  - {source_id}", file=sys.stderr)
    finally:
        index.close()
    return 0


def _run_init_command(args) -> int:
    try:
        write_init_env(
            Path(args.env_file).expanduser(),
            preset=args.preset,
            journal_root=Path(args.journal_root).expanduser() if args.journal_root else None,
            data_dir=Path(args.data_dir).expanduser() if args.data_dir else None,
            deepseek_api_key=args.deepseek_api_key,
            feishu_user_ids=tuple(args.feishu_user_id),
            hermes_shared_secret=args.hermes_shared_secret,
            force=args.force,
        )
    except FileExistsError:
        print("env file already exists; use --force to overwrite", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("created env file")
    return 0


def _run_doctor_command(env_file: str) -> int:
    result = run_doctor(env_file=Path(env_file).expanduser())
    for message in result.messages:
        print(message)
    return 0 if result.ok else 2


def _run_demo_command(args) -> int:
    if args.action == "init":
        try:
            path = copy_sample_vault(Path(args.target).expanduser(), force=args.force)
        except FileExistsError:
            print("sample vault target already exists; use --force to overwrite", file=sys.stderr)
            return 2
        print(f"sample vault: {path}")
        return 0
    print(f"unknown demo action: {args.action}", file=sys.stderr)
    return 2


def _run_chat_command(args) -> int:
    if not args.demo:
        print("only --demo chat is available in this release", file=sys.stderr)
        return 2
    print(run_demo_chat(args.question, data_dir=Path(args.data_dir).expanduser()))
    return 0


def _run_hermes_bridge_command(action: str, gateway_run: Optional[str], no_backup: bool) -> int:
    path = Path(gateway_run).expanduser() if gateway_run else None
    try:
        if action == "status":
            result = hermes_bridge_status(path) if path else hermes_bridge_status()
        elif action == "install":
            result = (
                install_hermes_bridge(path, backup=not no_backup)
                if path
                else install_hermes_bridge(backup=not no_backup)
            )
        elif action == "uninstall":
            result = (
                uninstall_hermes_bridge(path, backup=not no_backup)
                if path
                else uninstall_hermes_bridge(backup=not no_backup)
            )
        else:
            raise HermesBridgeInstallError(f"unknown hermes bridge action: {action}")
    except HermesBridgeInstallError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"gateway_run: {result.gateway_run}")
    print(f"state: {result.state}")
    return 0 if result.installed or action != "status" else 1


def _cli_progress(done: int, total: int, action: str, source_id: str) -> None:
    # Progress goes to stderr so the final stdout summary stays a single clean
    # line; only the wikilink id is shown, never the file path or contents.
    print(f"[{done}/{total}] {action} {source_id}", file=sys.stderr)


def _print_index_status(settings: Settings, index: JournalIndex) -> None:
    # Metadata only: never note bodies or credentials.
    print(f"note_count: {index.count()}")
    print(f"last_indexed_at: {index.last_indexed_at() or '(never)'}")
    print(f"database_path: {settings.resolved_database_path}")
    print(f"semantic_search: {'on' if settings.semantic_search_enabled else 'off'}")
    print(f"schedule_enabled: {settings.index_schedule_enabled}")
    print(f"interval_seconds: {settings.index_interval_seconds}")


def _serve() -> None:
    """Run only on loopback; use a private network proxy for later remote access."""
    try:
        app = create_production_app()
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)

    uvicorn.run(app, host="127.0.0.1", port=app.state.settings.port)


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="riji-agent", description="Local journal agent boundary."
    )
    sub = parser.add_subparsers(dest="command")
    init_cmd = sub.add_parser("init", help="Create a local .env for the default stack.")
    init_cmd.add_argument("--preset", default=DEFAULT_PRESET, choices=(DEFAULT_PRESET,))
    init_cmd.add_argument("--env-file", default=".env")
    init_cmd.add_argument("--journal-root")
    init_cmd.add_argument("--data-dir")
    init_cmd.add_argument("--deepseek-api-key", default="replace-me")
    init_cmd.add_argument("--feishu-user-id", action="append", default=["ou_replace_me"])
    init_cmd.add_argument("--hermes-shared-secret")
    init_cmd.add_argument("--force", action="store_true")
    doctor_cmd = sub.add_parser("doctor", help="Validate local configuration safely.")
    doctor_cmd.add_argument("--env-file", default=".env")
    sub.add_parser("serve", help="Run the local riji-agent service.")
    demo_cmd = sub.add_parser("demo", help="Create or inspect demo assets.")
    demo_cmd.add_argument("action", choices=("init",))
    demo_cmd.add_argument("--target", default="sample-riji")
    demo_cmd.add_argument("--force", action="store_true")
    chat_cmd = sub.add_parser("chat", help="Run a local demo chat.")
    chat_cmd.add_argument("--demo", action="store_true")
    chat_cmd.add_argument("--question", default="launch planning")
    chat_cmd.add_argument("--data-dir", default=".riji-demo")
    index_cmd = sub.add_parser("index", help="Build or inspect the local journal index.")
    index_cmd.add_argument(
        "--rebuild", action="store_true", help="Clear and rebuild the index from scratch."
    )
    index_cmd.add_argument(
        "--status", action="store_true", help="Print index metadata only; make no changes."
    )
    bridge_cmd = sub.add_parser(
        "hermes-bridge",
        help="Install, inspect, or remove the Hermes Feishu -> riji-agent bridge hook.",
    )
    bridge_cmd.add_argument("action", choices=("status", "install", "uninstall"))
    bridge_cmd.add_argument(
        "--gateway-run",
        help="Path to Hermes gateway/run.py; defaults to ~/.hermes/hermes-agent/gateway/run.py.",
    )
    bridge_cmd.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create a .riji-agent.bak copy before modifying gateway/run.py.",
    )
    args = parser.parse_args(argv)

    if args.command == "index":
        raise SystemExit(_run_index_command(rebuild=args.rebuild, status=args.status))
    if args.command == "init":
        raise SystemExit(_run_init_command(args))
    if args.command == "doctor":
        raise SystemExit(_run_doctor_command(args.env_file))
    if args.command == "serve":
        _serve()
        return
    if args.command == "demo":
        raise SystemExit(_run_demo_command(args))
    if args.command == "chat":
        raise SystemExit(_run_chat_command(args))
    if args.command == "hermes-bridge":
        raise SystemExit(
            _run_hermes_bridge_command(args.action, args.gateway_run, args.no_backup)
        )

    _serve()
