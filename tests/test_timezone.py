from __future__ import annotations

from zoneinfo import ZoneInfoNotFoundError

import riji_agent.timezone as timezone_module


def test_local_journal_timezone_falls_back_to_utc_plus_8(monkeypatch) -> None:
    def missing_zone(_key: str):
        raise ZoneInfoNotFoundError

    monkeypatch.setattr(timezone_module, "ZoneInfo", missing_zone)

    tz = timezone_module.local_journal_timezone()

    assert tz.utcoffset(None) == timezone_module.timedelta(hours=8)
    assert tz.tzname(None) == "Asia/Shanghai"
