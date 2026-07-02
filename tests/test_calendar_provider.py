from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import SecretStr

from riji_agent.calendar.models import CalendarEventDraft
from riji_agent.calendar.providers import CalendarProviderError, FeishuCalendarProvider

TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def test_feishu_provider_gets_token_and_creates_event() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(200, json={"tenant_access_token": "tenant-token"})
        if request.url.path.endswith("/calendar/v4/calendars/primary/events"):
            assert request.headers["Authorization"] == "Bearer tenant-token"
            payload = request.read().decode()
            assert "项目复盘" in payload
            assert "secret" not in payload
            return httpx.Response(200, json={"data": {"event": {"event_id": "evt_1"}}})
        return httpx.Response(404)

    provider = FeishuCalendarProvider(
        app_id="cli_fake",
        app_secret=SecretStr("secret"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = provider.create_event(
        CalendarEventDraft(
            title="项目复盘",
            start_at=datetime(2026, 7, 3, 15, 0, tzinfo=TZ),
            end_at=datetime(2026, 7, 3, 16, 0, tzinfo=TZ),
            timezone="Asia/Shanghai",
            reminder_minutes=10,
            description="由 riji-agent 创建。",
        )
    )

    assert result.event_id == "evt_1"
    assert len(requests) == 2


def test_feishu_provider_raises_safe_error_on_auth_failure() -> None:
    provider = FeishuCalendarProvider(
        app_id="cli_fake",
        app_secret=SecretStr("secret"),
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
    )

    with pytest.raises(CalendarProviderError) as err:
        provider.create_event(
            CalendarEventDraft(
                title="项目复盘",
                start_at=datetime(2026, 7, 3, 15, 0, tzinfo=TZ),
                end_at=datetime(2026, 7, 3, 16, 0, tzinfo=TZ),
                timezone="Asia/Shanghai",
            )
        )

    assert err.value.code == "provider_auth_failed"
    assert "secret" not in str(err.value)


def test_feishu_provider_maps_permission_denied_to_safe_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(200, json={"tenant_access_token": "tenant-token"})
        if request.url.path.endswith("/calendar/v4/calendars/primary/events"):
            return httpx.Response(
                400,
                json={
                    "code": 99991672,
                    "msg": "Access denied. One of the following scopes is required.",
                },
            )
        return httpx.Response(404)

    provider = FeishuCalendarProvider(
        app_id="cli_fake",
        app_secret=SecretStr("secret"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(CalendarProviderError) as err:
        provider.create_event(
            CalendarEventDraft(
                title="项目复盘",
                start_at=datetime(2026, 7, 3, 15, 0, tzinfo=TZ),
                end_at=datetime(2026, 7, 3, 16, 0, tzinfo=TZ),
                timezone="Asia/Shanghai",
            )
        )

    assert err.value.code == "provider_permission_denied"
    assert "Access denied" not in str(err.value)
