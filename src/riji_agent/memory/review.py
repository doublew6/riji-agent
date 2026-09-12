"""Local-only, authenticated Memory Review interface."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from riji_agent.config import Settings
from riji_agent.memory.backend import MemoryBackendError
from riji_agent.memory.capture import contains_credentials
from riji_agent.memory.review_ui import ReviewPageData, render_login, render_review
from riji_agent.memory.service import MemoryService
from riji_agent.memory.journal_types import JournalMemoryError
from riji_agent.journal.privacy import PERMISSIONS

_COOKIE = "riji_memory_admin"
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


@dataclass(frozen=True)
class _ReviewSecurity:
    token: str
    session: str
    csrf: str


@dataclass(frozen=True)
class _ReviewQuery:
    user_id: Optional[str] = None
    q: str = ""
    scope: str = "all"
    status: str = "active"
    selected: Optional[str] = None
    view: str = "overview"


def build_memory_review_router(service: MemoryService, settings: Settings) -> APIRouter:
    router = APIRouter()
    token = settings.memory_review_token.get_secret_value()  # type: ignore[union-attr]
    security = _ReviewSecurity(token, _digest(token, "session"), _digest(token, "csrf"))
    _register_auth_routes(router, security)
    _register_review_page(router, service, settings, security)
    _register_snapshot_routes(router, service, settings, security)
    _register_job_route(router, service, security)
    _register_mutation_route(router, service, settings, security)
    _register_organization_route(router, service, settings, security)
    _register_journal_routes(router, service, security)
    _register_forgetting_route(router, service, security)
    _register_export_route(router, service, settings, security)
    return router


def _register_export_route(router: APIRouter, service: MemoryService, settings: Settings, security: _ReviewSecurity) -> None:
    @router.get("/admin/memory/export", include_in_schema=False)
    def export_bundle(request: Request, user_id: str) -> JSONResponse:
        from riji_agent.memory.journal_transfer import JournalMemoryTransfer
        _authorize(request, security.session)
        if service.journal is None or user_id != service.journal.policy.user_id:
            return _json({"error": "journal_memory_unavailable"}, status=400)
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=settings.data_dir, suffix=".json") as handle:
            path = Path(handle.name)
            try:
                JournalMemoryTransfer(service.backend).export(path)
                import json
                response = _json(json.loads(path.read_text()))
            except Exception:
                return _json({"error": "memory_export_failed"}, status=503)
        response.headers["Content-Disposition"] = 'attachment; filename="journal-memory.json"'
        return response


def _register_forgetting_route(router: APIRouter, service: MemoryService, security: _ReviewSecurity) -> None:
    @router.post("/admin/memory/api/forget", include_in_schema=False)
    async def forget(request: Request) -> JSONResponse:
        from riji_agent.memory.journal_forget import apply_forget_plan
        _authorize(request, security.session)
        _verify_csrf(request, security.csrf)
        payload = await _json_body(request)
        try:
            return _json(apply_forget_plan(service, payload))
        except ValueError as exc:
            return _json({"error": str(exc)}, status=400)
        except MemoryBackendError as exc:
            return _json({"error": exc.code}, status=503)


def _register_auth_routes(router: APIRouter, security: _ReviewSecurity) -> None:

    @router.get("/admin/memory/login", include_in_schema=False)
    def login_page(request: Request):
        _require_local(request)
        return _html(render_login())

    @router.post("/admin/memory/login", include_in_schema=False)
    async def login(request: Request):
        _require_local(request)
        payload = await _json_body(request)
        if not hmac.compare_digest(str(payload.get("token", "")), security.token):
            return _json({"error": "invalid_token"}, status=401)
        response = _json({"ok": True})
        response.set_cookie(
            _COOKIE,
            security.session,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/admin/memory",
        )
        return response

    @router.post("/admin/memory/logout", include_in_schema=False)
    async def logout(request: Request):
        _authorize(request, security.session)
        _verify_csrf(request, security.csrf)
        response = _json({"ok": True})
        response.delete_cookie(_COOKIE, path="/admin/memory")
        return response


def _register_journal_routes(router: APIRouter, service: MemoryService, security: _ReviewSecurity) -> None:
    @router.post("/admin/memory/api/journal/{action}", include_in_schema=False)
    async def control_journal(action: str, request: Request) -> JSONResponse:
        _authorize(request, security.session)
        _verify_csrf(request, security.csrf)
        payload = await _json_body(request)
        engine = service.journal
        if engine is None or payload.get("user_id") != engine.policy.user_id:
            return _json({"error": "journal_memory_unavailable"}, status=400)
        if engine.store.get_control("restore_in_progress"):
            return _json({"error": "memory_restore_in_progress"}, status=409)
        if action == "scan":
            engine.store.set_control("scan_requested", "1")
        elif action == "authorize":
            if payload.get("acknowledged") is not True:
                return _json({"error": "privacy_acknowledgment_required"}, status=400)
            try:
                engine.privacy.grant(payload.get("binding", ""), payload.get("permissions", {}))
                engine.store.set_control("paused", "1")
            except (JournalMemoryError, TypeError) as exc:
                return _json({"error": getattr(exc, "code", "invalid_privacy_permissions")}, status=409)
        elif action == "revoke":
            engine.privacy.revoke()
        elif action == "permission":
            return _set_memory_permission(service, payload)
        elif action in {"pause", "resume"}:
            if action == "resume" and not engine.privacy.status()["valid"]:
                return _json({"error": "journal_consent_required"}, status=409)
            engine.store.set_control("paused", "1" if action == "pause" else "0")
        elif action == "retry":
            engine.store.retry()
        else:
            return _json({"error": "unsupported_action"}, status=400)
        return _json({"ok": True, "progress": engine.store.progress()})


def _set_memory_permission(service: MemoryService, payload: dict) -> JSONResponse:
    permission = payload.get("permission")
    if not isinstance(permission, str) or permission not in PERMISSIONS:
        return _json({"error": "invalid_memory_permission"}, status=400)
    try:
        item = service.backend.get(payload.get("memory_id", ""))
        if item.user_id != payload.get("user_id"):
            return _json({"error": "memory_not_found"}, status=404)
        service.backend.update(item.id, metadata=dict(item.metadata, privacy=permission))
        service.journal.notify_change()
    except MemoryBackendError:
        return _json({"error": "memory_not_found"}, status=404)
    return _json({"ok": True})


def _register_review_page(
    router: APIRouter,
    service: MemoryService,
    settings: Settings,
    security: _ReviewSecurity,
) -> None:
    @router.get("/admin/memory", include_in_schema=False)
    def review_page(
        request: Request,
        query: _ReviewQuery = Depends(),
    ) -> Response:
        if not _is_authorized(request, security.session):
            return RedirectResponse("/admin/memory/login", status_code=303)
        selected_user = _select_user(settings, query.user_id)
        if service.journal is not None and (query.view == "facts" or query.selected):
            service.request_snapshot()
        try:
            records = service.list_memories(user_id=selected_user, include_archived=True)
            backend_ok = service.backend.health()
        except MemoryBackendError:
            records, backend_ok = (), False
        page = render_review(
            ReviewPageData(
                service=service,
                settings=settings,
                user_id=selected_user,
                records=tuple(records),
                csrf=security.csrf,
                backend_ok=backend_ok,
                query=query.q,
                scope=query.scope,
                status=query.status,
                selected=query.selected,
                view="facts" if query.selected else query.view,
            )
        )
        return _html(page)


def _register_organization_route(
    router: APIRouter, service: MemoryService, settings: Settings, security: _ReviewSecurity
) -> None:
    @router.post("/admin/memory/api/organize", include_in_schema=False)
    async def organize(request: Request):
        _authorize(request, security.session)
        _verify_csrf(request, security.csrf)
        payload = await _json_body(request)
        user_id = payload.get("user_id")
        if not isinstance(user_id, str) or user_id not in settings.allowed_feishu_user_ids:
            return _json({"error": "invalid_user"}, status=400)
        run_id = service.operations.organization.request(user_id)
        return _json({"ok": True, "run_id": run_id}, status=202)


def _register_snapshot_routes(
    router: APIRouter,
    service: MemoryService,
    settings: Settings,
    security: _ReviewSecurity,
) -> None:
    @router.get("/admin/memory/snapshot", include_in_schema=False)
    def download_snapshot(request: Request) -> Response:
        _authorize(request, security.session)
        if service.journal is not None:
            try:
                service.refresh_snapshot()
            except Exception:
                if service.snapshot:
                    service.snapshot.invalidate()
                return _json({"error": "snapshot_refresh_failed"}, status=503)
        path = Path(settings.memory_snapshot_path)  # type: ignore[arg-type]
        if not path.is_file():
            raise HTTPException(status_code=404, detail="snapshot_not_found")
        response = PlainTextResponse(path.read_text(encoding="utf-8"))
        response.headers["Content-Disposition"] = 'attachment; filename="MEMORY.md"'
        _secure_headers(response)
        return response

    @router.post("/admin/memory/api/snapshot", include_in_schema=False)
    def regenerate_snapshot(request: Request):
        _authorize(request, security.session)
        _verify_csrf(request, security.csrf)
        try:
            generated_at, count = service.refresh_snapshot()
        except (MemoryBackendError, OSError):
            return _json({"error": "snapshot_refresh_failed"}, status=503)
        return _json({"ok": True, "generated_at": generated_at, "count": count})


def _register_job_route(
    router: APIRouter, service: MemoryService, security: _ReviewSecurity
) -> None:
    @router.post("/admin/memory/api/jobs/{job_id}/retry", include_in_schema=False)
    def retry_job(job_id: int, request: Request):
        _authorize(request, security.session)
        _verify_csrf(request, security.csrf)
        try:
            service.operations.get_job(job_id)
            service.operations.retry(job_id)
        except KeyError:
            return _json({"error": "job_not_found"}, status=404)
        return _json({"ok": True})


def _register_mutation_route(
    router: APIRouter,
    service: MemoryService,
    settings: Settings,
    security: _ReviewSecurity,
) -> None:
    @router.post("/admin/memory/api/memories/{memory_id}/{action}", include_in_schema=False)
    async def mutate_memory(memory_id: str, action: str, request: Request):
        _authorize(request, security.session)
        _verify_csrf(request, security.csrf)
        payload = await _json_body(request)
        user_id = _select_user(settings, str(payload.get("user_id", "")))
        try:
            _apply_action(service, memory_id, action, user_id, payload)
        except MemoryBackendError as exc:
            status = 404 if exc.code == "memory_not_found" else 503
            return _json({"error": exc.code}, status=status)
        except ValueError as exc:
            return _json({"error": str(exc)}, status=400)
        return _json({"ok": True})


def _apply_action(
    service: MemoryService,
    memory_id: str,
    action: str,
    user_id: str,
    payload: dict[str, Any],
) -> None:
    if action == "update":
        content = str(payload.get("content", "")).strip()
        if not content or len(content) > 2000 or contains_credentials(content):
            raise ValueError("invalid_content")
        service.update_memory(memory_id, user_id=user_id, content=content)
    elif action == "archive":
        service.archive_memory(memory_id, user_id=user_id)
    elif action == "restore":
        service.restore_memory(memory_id, user_id=user_id)
    elif action == "reconfirm":
        service.reconfirm_memory(memory_id, user_id=user_id)
    elif action == "delete":
        if payload.get("confirmation") != "DELETE":
            raise ValueError("delete_confirmation_required")
        service.delete_memory(memory_id, user_id=user_id)
    else:
        raise ValueError("unsupported_action")


def _select_user(settings: Settings, candidate: Optional[str]) -> str:
    users = sorted(settings.allowed_feishu_user_ids)
    if candidate and candidate in settings.allowed_feishu_user_ids:
        return candidate
    return users[0]


def _digest(token: str, purpose: str) -> str:
    return hmac.new(token.encode(), purpose.encode(), hashlib.sha256).hexdigest()


def _require_local(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in _LOCAL_HOSTS:
        raise HTTPException(status_code=403, detail="local_only")


def _is_authorized(request: Request, session_value: str) -> bool:
    _require_local(request)
    return hmac.compare_digest(request.cookies.get(_COOKIE, ""), session_value)


def _authorize(request: Request, session_value: str) -> None:
    if not _is_authorized(request, session_value):
        raise HTTPException(status_code=401, detail="authentication_required")


def _verify_csrf(request: Request, csrf_value: str) -> None:
    supplied = request.headers.get("X-CSRF-Token", "")
    if not hmac.compare_digest(supplied, csrf_value):
        raise HTTPException(status_code=403, detail="csrf_failed")


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_json") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid_json")
    return payload


def _html(body: str) -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    response = HTMLResponse(body.replace("__NONCE__", nonce))
    _secure_headers(response, nonce=nonce)
    return response


def _json(payload: dict[str, Any], *, status: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status)
    _secure_headers(response)
    return response


def _secure_headers(response, *, nonce: Optional[str] = None) -> None:
    sources = f" 'nonce-{nonce}'" if nonce else " 'none'"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src" + sources + "; script-src" + sources
        + "; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
