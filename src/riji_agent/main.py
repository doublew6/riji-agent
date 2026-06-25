"""FastAPI application entrypoint for the local riji-agent boundary."""

from __future__ import annotations

import sys
from typing import Optional

import uvicorn
from fastapi import FastAPI

from riji_agent.config import ConfigurationError, Settings, load_settings
from riji_agent.hermes.gateway import HermesGateway
from riji_agent.hermes.api import build_hermes_router
from riji_agent.wiring import build_production_gateway


def create_app(
    settings: Optional[Settings] = None, *, gateway: Optional[HermesGateway] = None
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
    )
    app.state.settings = runtime_settings

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"service": "riji-agent", "status": "ok"}

    if gateway is not None:
        app.include_router(build_hermes_router(gateway))

    return app


def create_production_app(settings: Optional[Settings] = None) -> FastAPI:
    """Create the fully wired app: health check plus the Hermes message route.

    This is the real deployment entrypoint — unlike a bare ``create_app()``, it
    assembles the gateway so ``/hermes/messages`` is mounted and the model,
    retrieval, drafts and audit are all live.
    """
    runtime_settings = settings or load_settings()
    gateway = build_production_gateway(runtime_settings)
    return create_app(runtime_settings, gateway=gateway)


def main() -> None:
    """Run only on loopback; use a private network proxy for later remote access."""
    try:
        app = create_production_app()
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)

    uvicorn.run(app, host="127.0.0.1", port=app.state.settings.port)
