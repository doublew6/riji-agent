"""FastAPI router exposing the Hermes gateway over loopback HTTP."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from riji_agent.hermes.errors import AuthError, AuthErrorCode
from riji_agent.hermes.gateway import HermesGateway
from riji_agent.im.feishu import FeishuIncomingMessage


class MessageBody(BaseModel):
    event_id: str
    feishu_user_id: str
    chat_id: str
    chat_type: str
    text: str


def build_hermes_router(gateway: HermesGateway) -> APIRouter:
    router = APIRouter()

    @router.post("/hermes/messages", include_in_schema=False)
    def handle_message(body: MessageBody, x_hermes_secret: str = Header(default="")):
        message = FeishuIncomingMessage(
            event_id=body.event_id,
            feishu_user_id=body.feishu_user_id,
            chat_id=body.chat_id,
            chat_type=body.chat_type,
            text=body.text,
        ).to_chat_message()
        try:
            reply = gateway.handle(x_hermes_secret, message)
        except AuthError as exc:
            status = 401 if exc.code is AuthErrorCode.UNAUTHENTICATED else 403
            raise HTTPException(status_code=status, detail={"error": exc.code.value})
        return {
            "request_id": reply.request_id,
            "persona_id": reply.persona_id,
            "reply": reply.text,
            "deduplicated": reply.deduplicated,
        }

    return router
