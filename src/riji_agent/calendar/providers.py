"""Calendar provider interfaces and Feishu implementation."""

from __future__ import annotations

from typing import Optional, Protocol

import httpx
from pydantic import SecretStr

from riji_agent.calendar.models import CalendarEventDraft, CalendarEventResult


class CalendarProvider(Protocol):
    provider_id: str

    def create_event(
        self, event: CalendarEventDraft, *, user_id: Optional[str] = None
    ) -> CalendarEventResult:
        """Create one calendar event and return provider metadata."""


class CalendarProviderError(RuntimeError):
    """Provider failure with a user-safe code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FeishuCalendarProvider:
    """Create events through Feishu's calendar API."""

    provider_id = "feishu"

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: SecretStr,
        calendar_id: str = "primary",
        base_url: str = "https://open.feishu.cn",
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._calendar_id = calendar_id
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30, trust_env=False)

    def create_event(
        self, event: CalendarEventDraft, *, user_id: Optional[str] = None
    ) -> CalendarEventResult:
        token = self._tenant_access_token()
        url = f"{self._base_url}/open-apis/calendar/v4/calendars/{self._calendar_id}/events"
        payload = {
            "summary": event.title,
            "description": event.description,
            "start_time": {
                "timestamp": str(int(event.start_at.timestamp())),
                "timezone": event.timezone,
            },
            "end_time": {
                "timestamp": str(int(event.end_at.timestamp())),
                "timezone": event.timezone,
            },
        }
        if event.reminder_minutes is not None:
            payload["reminders"] = [{"minutes": event.reminder_minutes}]
        try:
            response = self._client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            data = response.json()
        except Exception as exc:
            raise CalendarProviderError("provider_create_failed") from exc

        if response.status_code >= 400 or _feishu_code(data) not in (None, 0):
            raise CalendarProviderError(_safe_feishu_error(data, fallback="provider_create_failed"))

        event_data = data.get("data", {}).get("event", {}) if isinstance(data, dict) else {}
        event_id = event_data.get("event_id") or event_data.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise CalendarProviderError("provider_missing_event_id")
        if user_id:
            self._add_user_attendee(token, event_id=event_id, user_id=user_id)
        return CalendarEventResult(
            event_id=event_id,
            title=event.title,
            start_at=event.start_at,
            end_at=event.end_at,
            calendar_url=event_data.get("app_link") if isinstance(event_data.get("app_link"), str) else None,
        )

    def _tenant_access_token(self) -> str:
        try:
            response = self._client.post(
                f"{self._base_url}/open-apis/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": self._app_id,
                    "app_secret": self._app_secret.get_secret_value(),
                },
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise CalendarProviderError("provider_auth_failed") from exc
        token = data.get("tenant_access_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise CalendarProviderError("provider_auth_failed")
        return token

    def _add_user_attendee(self, token: str, *, event_id: str, user_id: str) -> None:
        url = (
            f"{self._base_url}/open-apis/calendar/v4/calendars/{self._calendar_id}"
            f"/events/{event_id}/attendees"
        )
        try:
            response = self._client.post(
                url,
                params={"user_id_type": "open_id", "need_notification": "false"},
                headers={"Authorization": f"Bearer {token}"},
                json={"attendees": [{"type": "user", "user_id": user_id}]},
            )
            data = response.json()
        except Exception as exc:
            raise CalendarProviderError("provider_attendee_failed") from exc
        if response.status_code >= 400 or _feishu_code(data) not in (None, 0):
            raise CalendarProviderError(_safe_feishu_error(data, fallback="provider_attendee_failed"))


def _feishu_code(data: object) -> Optional[int]:
    if not isinstance(data, dict):
        return None
    code = data.get("code")
    return code if isinstance(code, int) else None


def _safe_feishu_error(data: object, *, fallback: str) -> str:
    if not isinstance(data, dict):
        return fallback
    code = data.get("code")
    message = data.get("msg")
    if code == 99991672 or (
        isinstance(message, str)
        and ("Access denied" in message or "应用尚未开通所需的应用身份权限" in message)
    ):
        return "provider_permission_denied"
    return fallback
