"""FastAPI application entrypoint for the local riji-agent boundary."""

from __future__ import annotations

import sys
from typing import Optional

import uvicorn
from fastapi import FastAPI

from riji_agent.config import ConfigurationError, Settings, load_settings


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Create the application without exposing configuration through its API."""
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

    return app


def main() -> None:
    """Run only on loopback; use a private network proxy for later remote access."""
    try:
        app = create_app()
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)

    uvicorn.run(app, host="127.0.0.1", port=app.state.settings.port)
