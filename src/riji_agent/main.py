"""FastAPI application entrypoint and local CLI for the riji-agent boundary."""

from __future__ import annotations

import argparse
import sys
import time
from contextlib import asynccontextmanager
from typing import Optional, Sequence

import uvicorn
from fastapi import FastAPI

from riji_agent.config import ConfigurationError, Settings, load_settings
from riji_agent.hermes.gateway import HermesGateway
from riji_agent.hermes.api import build_hermes_router
from riji_agent.journal.index import JournalIndex
from riji_agent.wiring import build_journal_index, build_production_gateway


def create_app(
    settings: Optional[Settings] = None,
    *,
    gateway: Optional[HermesGateway] = None,
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
        app.include_router(build_hermes_router(gateway))

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
        # Prewarm in the background, waiting only up to the startup timeout, then
        # let the periodic scheduler keep the index fresh. Stopped on shutdown.
        scheduler.prewarm(runtime_settings.index_startup_timeout_seconds)
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
            stats = index.build_index(rebuild=rebuild)
            duration = round(time.monotonic() - started, 3)
            action = "rebuilt" if rebuild else "indexed"
            print(
                f"{action}: added={stats.added} updated={stats.updated} "
                f"unchanged={stats.unchanged} deleted={stats.deleted} duration_s={duration}"
            )
    finally:
        index.close()
    return 0


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
    index_cmd = sub.add_parser("index", help="Build or inspect the local journal index.")
    index_cmd.add_argument(
        "--rebuild", action="store_true", help="Clear and rebuild the index from scratch."
    )
    index_cmd.add_argument(
        "--status", action="store_true", help="Print index metadata only; make no changes."
    )
    args = parser.parse_args(argv)

    if args.command == "index":
        raise SystemExit(_run_index_command(rebuild=args.rebuild, status=args.status))

    _serve()
