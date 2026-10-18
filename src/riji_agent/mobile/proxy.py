"""Fixed loopback mobile-only proxy for a private HTTPS ingress."""

from __future__ import annotations

import json
import re

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

MAX_BODY = 65536
MAX_RESPONSE = 4 * 1024 * 1024
IDENTIFIER = r"[A-Za-z0-9_-]{1,128}"
ROUTES = {
    "GET": (r"capabilities", r"operations/" + IDENTIFIER, r"mentors/" + IDENTIFIER + r"/conversation",
            r"memories", r"memories/" + IDENTIFIER, r"memory-overview", r"privacy", r"roundtables", r"roundtables/" + IDENTIFIER),
    "POST": (r"pair", r"device/revoke", r"mentors/" + IDENTIFIER + r"/(messages|control)",
             r"memories/" + IDENTIFIER + r"/actions", r"memory-overview/actions", r"privacy/actions", r"roundtables",
             r"roundtables/" + IDENTIFIER + r"/(messages|commands|materials/preview|result-preview|result-validation)"),
}


def admitted(method: str, raw_path: bytes, query: bytes) -> bool:
    try:
        path, args = raw_path.decode("ascii"), query.decode("ascii")
    except UnicodeDecodeError:
        return False
    if not any(re.fullmatch(r"/mobile/v1/" + pattern, path) for pattern in ROUTES.get(method, ())):
        return False
    pageable = path in {"/mobile/v1/memories", "/mobile/v1/memory-overview", "/mobile/v1/roundtables"} or re.fullmatch(
        r"/mobile/v1/mentors/" + IDENTIFIER + r"/conversation", path) is not None
    pageable = pageable or re.fullmatch(r"/mobile/v1/roundtables/" + IDENTIFIER, path) is not None
    return not args or (method == "GET" and pageable
                        and re.fullmatch(r"cursor=[A-Za-z0-9_-]{1,512}", args) is not None)


def error(code: str, status: int) -> JSONResponse:
    return JSONResponse({"detail": code}, status_code=status, headers={"Cache-Control": "no-store"})


def create_proxy_app(*, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
    async def forward(request: Request, path: str):
        raw_path = request.scope.get("raw_path", b"")
        query = request.scope.get("query_string", b"")
        if not admitted(request.method, raw_path, query):
            return error("mobile_route_not_found", 404)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                return error("mobile_request_too_large", 413)
        authorization = request.headers.get("authorization", "")
        if len(authorization) > 200:
            return error("device_authentication_required", 401)
        headers = {"Content-Type": "application/json", "Authorization": authorization}
        suffix = raw_path.decode() + ("?" + query.decode() if query else "")
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, transport=transport,
                                         timeout=httpx.Timeout(30, connect=5)) as client:
                async with client.stream(request.method, "http://127.0.0.1:8765" + suffix,
                                         headers=headers, content=bytes(body)) as upstream:
                    return await project_response(upstream)
        except Exception:
            return error("mobile_upstream_unavailable", 503)
    return app


async def project_response(upstream: httpx.Response) -> JSONResponse:
    if upstream.is_redirect or "application/json" not in upstream.headers.get("content-type", ""):
        return error("mobile_upstream_invalid", 502)
    payload = bytearray()
    async for chunk in upstream.aiter_bytes():
        payload.extend(chunk)
        if len(payload) > MAX_RESPONSE:
            return error("mobile_response_too_large", 503)
    try:
        value = json.loads(payload)
    except ValueError:
        return error("mobile_upstream_invalid", 502)
    return JSONResponse(value, status_code=upstream.status_code, headers={"Cache-Control": "no-store"})
