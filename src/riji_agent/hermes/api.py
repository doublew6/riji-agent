"""FastAPI router exposing the Hermes gateway over loopback HTTP."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from riji_agent.hermes.errors import AuthError, AuthErrorCode
from riji_agent.hermes.gateway import HermesGateway
from riji_agent.im.feishu import FeishuIncomingMessage
from riji_agent.media.models import MediaError, MediaErrorCode


class MessageBody(BaseModel):
    event_id: str
    feishu_user_id: str
    chat_id: str
    chat_type: str
    text: str
    message_type: str = "text"
    attachment_ids: list[str] = Field(default_factory=list)
    reply_to_message_id: str = ""


def build_hermes_router(gateway: HermesGateway) -> APIRouter:
    router = APIRouter()

    @router.put("/hermes/attachments/{event_id}/{part_index}", include_in_schema=False)
    async def upload_attachment(
        event_id: str,
        part_index: int,
        request: Request,
        x_hermes_secret: str = Header(default=""),
    ):
        try:
            attachment = gateway.stage_attachment(
                x_hermes_secret,
                event_id,
                part_index,
                await request.body(),
            )
        except AuthError as exc:
            raise HTTPException(status_code=401, detail={"error": exc.code.value})
        except MediaError as exc:
            status = 413 if exc.code is MediaErrorCode.TOO_LARGE else 400
            raise HTTPException(status_code=status, detail={"error": exc.code.value})
        return {
            "attachment_id": attachment.attachment_id,
            "media_type": attachment.media_type,
            "size_bytes": attachment.size_bytes,
        }

    @router.post("/hermes/messages", include_in_schema=False)
    def handle_message(body: MessageBody, x_hermes_secret: str = Header(default="")):
        message = FeishuIncomingMessage(
            event_id=body.event_id,
            feishu_user_id=body.feishu_user_id,
            chat_id=body.chat_id,
            chat_type=body.chat_type,
            text=body.text,
            message_type=body.message_type,
            attachment_ids=tuple(body.attachment_ids),
            reply_to_message_id=body.reply_to_message_id,
        ).to_chat_message()
        try:
            reply = gateway.handle(x_hermes_secret, message)
        except AuthError as exc:
            status = 401 if exc.code is AuthErrorCode.UNAUTHENTICATED else 403
            raise HTTPException(status_code=status, detail={"error": exc.code.value})
        except MediaError as exc:
            raise HTTPException(status_code=400, detail={"error": exc.code.value})
        payload = {
            "request_id": reply.request_id,
            "persona_id": reply.persona_id,
            "reply": reply.text,
            "deduplicated": reply.deduplicated,
        }
        if reply.audio is not None:
            payload["audio"] = {
                "path": reply.audio.path,
                "mime_type": reply.audio.mime_type,
            }
        return payload

    return router
