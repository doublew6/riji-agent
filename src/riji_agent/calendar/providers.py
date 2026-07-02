"""Calendar provider interfaces and Feishu implementation."""

from __future__ import annotations

from typing import Optional, Protocol

import httpx
from pydantic import SecretStr

from riji_agent.calendar.models import CalendarEventDraft, CalendarEventResult


class CalendarProvider(Protocol):
    provider_id: str

    def create_event(self, event: CalendarEventDraft) -> CalendarEventResult:
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

    def create_event(self, event: CalendarEventDraft) -> CalendarEventResult:
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
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise CalendarProviderError("provider_create_failed") from exc

        event_data = data.get("data", {}).get("event", {}) if isinstance(data, dict) else {}
        event_id = event_data.get("event_id") or event_data.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise CalendarProviderError("provider_missing_event_id")
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
