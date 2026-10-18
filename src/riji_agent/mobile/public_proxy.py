"""Independently bounded public ingress; enrollment never reaches the backend."""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from riji_agent.mobile.proxy import MAX_BODY, admitted, error, project_response
from riji_agent.mobile.store import MobileError, digest

MAX_HEADER_BYTES = 16384


class PublicHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if sum(len(key) + len(value) + 4 for key, value in scope["headers"]) > MAX_HEADER_BYTES:
            await error("mobile_headers_too_large", 431)(scope, receive, send)
            return

        async def no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(key, value) for key, value in message.get("headers", []) if key.lower() != b"cache-control"]
                message["headers"] = headers + [(b"cache-control", b"no-store")]
            await send(message)
        await self.app(scope, receive, no_store)


@dataclass(frozen=True)
class PublicLimits:
    concurrency: int = 8
    body_seconds: float = 5
    upstream_seconds: float = 30
    requests_per_minute: int = 120
    global_per_minute: int = 600
    failed_auth_per_minute: int = 600
    rate_entries: int = 1024


class Admission:
    def __init__(self, path: Path, limits: PublicLimits) -> None:
        self.path, self.limits = path, limits
        self.active = 0
        self.rates: dict[str, tuple[float, int]] = {}

    def rate(self, key: str, maximum: int) -> bool:
        now = time.monotonic()
        self.rates = {key: value for key, value in self.rates.items() if now - value[0] < 60}
        start, count = self.rates.get(key, (now, 0))
        if count >= maximum or (key not in self.rates and len(self.rates) >= self.limits.rate_entries):
            return False
        self.rates[key] = (start, count + 1)
        return True

    def authenticate(self, authorization: str) -> str | None:
        if not re.fullmatch(r"Bearer [A-Za-z0-9_-]{32,128}", authorization):
            return None
        if self.path.is_symlink():
            raise MobileError("mobile_authentication_unavailable", 503)
        # Never initialize, migrate, lock for writing, or cache a revocation decision.
        try:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=0.05)) as db:
                row = db.execute("SELECT id FROM devices WHERE token_hash=? AND revoked=0",
                                 (digest(authorization[7:]),)).fetchone()
            return row[0] if row else None
        except (OSError, sqlite3.Error):
            raise MobileError("mobile_authentication_unavailable", 503) from None

    async def authenticate_async(self, authorization: str) -> str | None:
        lookup = asyncio.create_task(asyncio.to_thread(self.authenticate, authorization))
        try:
            return await asyncio.shield(lookup)
        except asyncio.CancelledError:
            # A cancelled request must not release admission while its bounded lookup is running.
            try:
                await asyncio.shield(lookup)
            finally:
                raise


async def read_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY:
            raise OverflowError
        body.extend(chunk)
    return bytes(body)


async def forward_upstream(request: Request, body: bytes, transport):
    suffix = request.scope["raw_path"].decode("ascii")
    query = request.scope.get("query_string", b"")
    if query:
        suffix += "?" + query.decode("ascii")
    headers = {"Content-Type": "application/json", "Authorization": request.headers["authorization"]}
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, transport=transport,
                                 timeout=httpx.Timeout(25, connect=3)) as client:
        async with client.stream(request.method, "http://127.0.0.1:8765" + suffix,
                                 headers=headers, content=body) as upstream:
            return await project_response(upstream)


async def bounded_request(request: Request, transport, limits: PublicLimits):
    try:
        length = request.headers.get("content-length")
        if length is not None and (not length.isdecimal() or int(length) > MAX_BODY):
            return error("mobile_request_too_large", 413)
        body = await asyncio.wait_for(read_body(request), limits.body_seconds)
    except OverflowError:
        return error("mobile_request_too_large", 413)
    except asyncio.TimeoutError:
        return error("mobile_request_timeout", 408)
    except Exception:
        return error("mobile_request_invalid", 400)
    try:
        return await asyncio.wait_for(forward_upstream(request, body, transport), limits.upstream_seconds)
    except Exception:
        return error("mobile_upstream_unavailable", 503)


async def authenticated_request(request: Request, admission: Admission, transport):
    authorization = request.headers.getlist("authorization")
    try:
        device = await admission.authenticate_async(authorization[0]) if len(authorization) == 1 else None
    except MobileError:
        return error("mobile_authentication_unavailable", 503)
    if device is None:
        if not admission.rate("failed-auth", admission.limits.failed_auth_per_minute):
            return error("mobile_rate_limited", 429)
        return error("device_authentication_required", 401)
    if not admission.rate("authorized", admission.limits.global_per_minute) or not admission.rate(device, admission.limits.requests_per_minute):
        return error("mobile_rate_limited", 429)
    return await bounded_request(request, transport, admission.limits)


def create_public_proxy_app(path: Path, *, transport: httpx.AsyncBaseTransport | None = None,
                            limits: PublicLimits = PublicLimits()) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    admission = Admission(path.absolute(), limits)
    app.add_middleware(PublicHeadersMiddleware)

    @app.exception_handler(HTTPException)
    async def route_error(request: Request, exc: HTTPException):
        return error("mobile_route_not_found", 404)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
    async def forward(request: Request, path: str):
        raw = request.scope.get("raw_path", b"")
        if raw == b"/mobile/v1/pair" or not admitted(request.method, raw, request.scope.get("query_string", b"")):
            return error("mobile_route_not_found", 404)
        if admission.active >= limits.concurrency:
            return error("mobile_busy", 503)
        admission.active += 1
        try:
            return await authenticated_request(request, admission, transport)
        finally:
            admission.active -= 1
    return app
