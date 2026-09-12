"""Authenticated loopback contracts for transports and the owner's local view."""

from __future__ import annotations

import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from riji_agent.mentors.models import Application, ChatBinding, Command, Envelope, MentorError, Record
from riji_agent.mentors.http_boundary import PrivateRoute
from riji_agent.mentors.identity_api import attach_identity_routes
from riji_agent.mentors.host_bridge import attach_host_routes


class NewDiscussion(Record):
    binding_id: str
    question: str = Field(min_length=1, max_length=10000)
    personas: tuple[str, ...]
    mode: str = "private"
    rounds: int = 2
    run_personas: tuple[str, ...] = ()


class UserCommand(Record):
    id: str = Field(min_length=1, max_length=300)
    kind: str
    expected_revision: int = 1
    text: str = Field(default="", max_length=10000)
    actor: str = ""
    preview_hash: str = ""
    personas: tuple[str, ...] = ()
    mode: Literal["reference", "debate"] = "reference"
    rounds: int = Field(default=2, ge=1, le=2)
    supersedes: tuple[str, ...] = ()
    statement_kind: Literal["user_statement", "user_plan", "user_feedback"] = "user_statement"
    replace_background: bool = False
    reanalyze: bool = False


class UserUtterance(Record):
    id: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=10000)


class HandoffSelection(Record):
    id: str = Field(min_length=1, max_length=300)
    artifact_ids: tuple[str, ...] = Field(min_length=1, max_length=5)


def authenticate(request: Request, tokens: dict) -> str:
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    for token, identifier in tokens.items():
        if secrets.compare_digest(supplied, token):
            return identifier
    raise HTTPException(status_code=401, detail="mentor_authentication_required")


def build_router(runtime) -> APIRouter:
    router = APIRouter(prefix="/api/mentors/v1", tags=[], include_in_schema=False, route_class=PrivateRoute)

    def owned(request: Request) -> str:
        return authenticate(request, runtime.user_tokens)

    def safe(call):
        try:
            return call()
        except MentorError as exc:
            raise HTTPException(status_code=409, detail=exc.code) from None
        except Exception:
            raise HTTPException(status_code=503, detail="mentor_operation_unavailable") from None

    attach_entry_routes(router, runtime, owned, safe)
    attach_history_routes(router, runtime, owned, safe)
    attach_transfer_routes(router, runtime, owned, safe)
    attach_identity_routes(router, runtime, owned, safe)
    attach_host_routes(router, runtime, owned, safe)
    return router


def attach_entry_routes(router, runtime, owned, safe) -> None:
    @router.post("/messages")
    def incoming(message: Envelope, request: Request):
        app_id = authenticate(request, runtime.transport_tokens)
        return safe(lambda: runtime.ingress.receive(app_id, message))

    @router.get("/me")
    def me(request: Request):
        principal = owned(request)
        chats = runtime.store.list("chat", principal, ChatBinding)
        app_ids = {item.application_id for item in chats}
        apps = runtime.store.list("application", "", Application)
        return {"principal_id": principal, "chats": [item.model_dump() for item in chats],
                "personas": list(runtime.identity.personas.ids()),
                "applications": [{"id": item.id, "persona_id": item.persona_id, "platform": item.platform}
                                 for item in apps if item.id in app_ids]}

    @router.get("/conversations")
    def conversations(request: Request):
        return {"items": runtime.history.list(owned(request))}

    @router.post("/conversations")
    def create(body: NewDiscussion, request: Request):
        principal = owned(request)
        binding = runtime.store.read("chat", body.binding_id, ChatBinding)
        if binding is None or binding.principal_id != principal:
            raise HTTPException(404, "chat_not_found")
        result = safe(lambda: runtime.service.create(binding, body.question, personas=body.personas,
                                                     mode=body.mode, rounds=body.rounds,
                                                     run_personas=body.run_personas).model_dump())
        runtime.worker.wake()
        return result



def attach_history_routes(router, runtime, owned, safe) -> None:
    @router.post("/conversations/{identifier}/handoffs")
    def select_handoff(identifier: str, body: HandoffSelection, request: Request):
        principal = owned(request)
        handoff = safe(lambda: runtime.handoffs.create(identifier, principal, body.artifact_ids,
                                                       operation_id="web:" + body.id))
        return {"handoff_id": handoff.id, "conversation_id": identifier,
                "text": "请到本人日记导师私聊发送以下命令，核对日期和内容后再确认：\n/接收转交 " + handoff.id}

    @router.post("/conversations/{identifier}/messages")
    def message(identifier: str, body: UserUtterance, request: Request):
        principal = owned(request)
        result = safe(lambda: runtime.ingress.receive_owner(principal, identifier, body.id, body.text))
        runtime.worker.wake()
        return result

    @router.get("/conversations/{identifier}")
    def read(identifier: str, request: Request):
        principal = owned(request)
        return safe(lambda: runtime.history.read(identifier, principal))

    @router.get("/conversations/{identifier}/share-preview")
    def preview(identifier: str, request: Request):
        principal = owned(request)
        return safe(lambda: runtime.service.share_preview(identifier, principal))

    @router.post("/conversations/{identifier}/commands")
    def command(identifier: str, body: UserCommand, request: Request):
        principal = owned(request)
        cmd = Command(**body.model_dump(), principal_id=principal, conversation_id=identifier)
        result = safe(lambda: runtime.history.delete(identifier, principal, body.id) if body.kind == "delete"
                      else runtime.service.apply(cmd).model_dump())
        runtime.worker.wake()
        return result

    @router.get("/conversations/{identifier}/export")
    def export(identifier: str, request: Request):
        principal = owned(request)
        return safe(lambda: runtime.history.export(identifier, principal))

    @router.post("/restore")
    async def restore(request: Request, apply: bool = False):
        principal = owned(request)
        raw = await request.body()
        if len(raw) > 2_000_000:
            raise HTTPException(413, "export_too_large")
        import json
        try:
            package = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "export_format_invalid") from None
        return safe(lambda: runtime.history.restore(package, principal, apply=apply))



class TransferPreview(Record):
    origin_id: str
    artifact_ids: tuple[str, ...]
    personas: tuple[str, ...]


class TransferAccept(Record):
    fingerprint: str
    target_id: str


def attach_transfer_routes(router, runtime, owned, safe) -> None:
    @router.post("/transfers/preview")
    def preview(body: TransferPreview, request: Request):
        principal = owned(request)
        return safe(lambda: runtime.transfers.preview(body.origin_id, principal, body.artifact_ids, body.personas))

    @router.post("/transfers/{identifier}/accept")
    def accept(identifier: str, body: TransferAccept, request: Request):
        principal = owned(request)
        return safe(lambda: runtime.transfers.accept(identifier, principal, body.fingerprint, body.target_id))
