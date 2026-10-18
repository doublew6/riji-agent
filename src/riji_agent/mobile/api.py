"""Versioned device-authenticated JSON routes; no administrator credentials."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from riji_agent.mobile.memory import memory_view
from riji_agent.mobile.store import MobileError
from riji_agent.mentors.models import MentorError

ID_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"
MAX_REQUEST_BYTES = 65536
Identifier = Annotated[str, Path(pattern=ID_PATTERN)]


class MobileRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > MAX_REQUEST_BYTES:
                    return JSONResponse({"detail": "mobile_request_too_large"}, status_code=413,
                                        headers={"Cache-Control": "no-store"})
            request._body = bytes(data)
            try:
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "mobile_request_invalid"}, status_code=422)
            except MentorError as exc:
                response = JSONResponse({"detail": exc.code}, status_code=409)
            except MobileError as exc:
                response = JSONResponse({"detail": exc.code}, status_code=exc.status)
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            except Exception:
                response = JSONResponse({"detail": "mobile_service_unavailable"}, status_code=503)
            response.headers["Cache-Control"] = "no-store"
            return response
        return handle


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PairInput(Input):
    code: str = Field(min_length=30, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    device_name: str = Field(min_length=1, max_length=80)


class OperationInput(Input):
    operation_id: str = Field(pattern=ID_PATTERN)


class MessageInput(OperationInput):
    text: str = Field(min_length=1, max_length=10000)


class ControlInput(OperationInput):
    action: Literal["stop", "resume"]
    expected_revision: int = Field(ge=1)


class MemoryInput(OperationInput):
    expected_version: str = Field(min_length=64, max_length=64)
    action: Literal["update", "reconfirm", "archive", "restore", "delete", "permission"]
    content: str = Field(default="", max_length=2000)
    confirmation: str = Field(default="", max_length=20)
    permission: Literal["cloud", "local", "none"] | None = None


class PrivacyInput(OperationInput):
    action: Literal["grant", "revoke", "pause", "resume", "scan"]
    binding: str = Field(min_length=64, max_length=64)
    acknowledged: StrictBool = False
    permissions: dict[str, StrictBool] = Field(default_factory=dict, max_length=4)


class OrganizeInput(OperationInput):
    action: Literal["organize"]
    expected_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    binding: str = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledged: StrictBool


def build_router(service) -> APIRouter:
    router = APIRouter(prefix="/mobile/v1", include_in_schema=False, route_class=MobileRoute)

    def device(request: Request) -> dict:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or len(header) > 200:
            raise MobileError("device_authentication_required", 401)
        return service.authenticate(header[7:])

    @router.post("/pair")
    def pair(body: PairInput):
        return service.pair(body.code, body.device_name)

    @router.get("/capabilities")
    def capabilities(request: Request):
        return service.capabilities(device(request))

    @router.post("/device/revoke")
    def revoke(body: OperationInput, request: Request):
        current = device(request)
        return service.operate(current, "device_revoke", body.model_dump(), lambda: service.revoke(current))

    @router.get("/operations/{operation_id}")
    def operation(operation_id: Identifier, request: Request):
        return service.operation_receipt(device(request), operation_id)

    attach_mentor_routes(router, service, device)
    attach_memory_routes(router, service, device)
    from riji_agent.mobile.roundtable_api import attach_roundtable_routes
    attach_roundtable_routes(router, service, device)
    return router


def attach_mentor_routes(router, service, device) -> None:
    @router.get("/mentors/{persona}/conversation")
    def conversation(persona: str, request: Request, cursor: str = Query(default="", max_length=512)):
        return service.conversation(device(request), persona, cursor)

    @router.post("/mentors/{persona}/messages")
    def message(persona: str, body: MessageInput, request: Request):
        current = device(request)
        service._persona(persona)
        value = body.model_dump()
        # Persona belongs to the fingerprint, preventing cross-mentor ID reuse.
        return service.operate(current, "mentor_message", dict(value, persona=persona),
                               lambda: service.send(current, persona, value))

    @router.post("/mentors/{persona}/control")
    def control(persona: str, body: ControlInput, request: Request):
        current = device(request)
        service._persona(persona)
        value = body.model_dump()
        return service.operate(current, "mentor_control", dict(value, persona=persona),
                               lambda: service.control(current, persona, value))


def attach_memory_routes(router, service, device) -> None:
    @router.get("/memory-overview")
    def overview(request: Request, cursor: str = Query(default="", max_length=512)):
        current = device(request)
        owner = service.principal(current).legacy_owner_key
        result = service.overview.listing(owner, cursor)
        service.principal(current)
        return result

    @router.post("/memory-overview/actions")
    def organize(body: OrganizeInput, request: Request):
        current, value = device(request), body.model_dump()
        owner = service.principal(current).legacy_owner_key
        return service.operate(current, "memory_organize", value, lambda: service.overview.organize(owner, value))

    @router.get("/memories")
    def memories(request: Request, cursor: str = Query(default="", max_length=512)):
        owner = service.principal(device(request)).legacy_owner_key
        return service.memory.listing(owner, cursor)

    @router.get("/memories/{identifier}")
    def memory(identifier: Identifier, request: Request):
        owner = service.principal(device(request)).legacy_owner_key
        return memory_view(service.memory.item(owner, identifier))

    @router.post("/memories/{identifier}/actions")
    def mutate(identifier: Identifier, body: MemoryInput, request: Request):
        current, value = device(request), body.model_dump()
        owner = service.principal(current).legacy_owner_key
        return service.operate(current, "memory_action", dict(value, memory_id=identifier),
                               lambda: service.memory.mutate(owner, identifier, value))

    @router.get("/privacy")
    def privacy(request: Request):
        return service.memory.privacy(service.principal(device(request)).legacy_owner_key)

    @router.post("/privacy/actions")
    def privacy_action(body: PrivacyInput, request: Request):
        current, value = device(request), body.model_dump()
        owner = service.principal(current).legacy_owner_key
        return service.operate(current, "privacy_action", value, lambda: service.memory.control(owner, value))
