from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from riji_agent.media.models import MediaError, MediaErrorCode
from riji_agent.media.service import MAX_IMAGE_BYTES, MediaService

PNG = b"\x89PNG\r\n\x1a\n" + b"image-data"


def _service(tmp_path: Path, clock: list[datetime]) -> MediaService:
    return MediaService(
        tmp_path / "media.sqlite3",
        tmp_path / "staging",
        now=lambda: clock[0],
    )


def test_stage_is_idempotent_and_private(tmp_path: Path) -> None:
    clock = [datetime(2026, 7, 11, tzinfo=timezone.utc)]
    service = _service(tmp_path, clock)
    first = service.stage("event-1", 0, PNG)
    second = service.stage("event-1", 0, PNG)

    assert first == second
    path = Path(first.staged_path)
    assert path.read_bytes() == PNG
    assert path.stat().st_mode & 0o777 == 0o600


def test_stage_rejects_changed_unsupported_and_oversized_parts(tmp_path: Path) -> None:
    clock = [datetime(2026, 7, 11, tzinfo=timezone.utc)]
    service = _service(tmp_path, clock)
    service.stage("event-1", 0, PNG)

    with pytest.raises(MediaError) as changed:
        service.stage("event-1", 0, PNG + b"changed")
    assert changed.value.code is MediaErrorCode.CONTENT_MISMATCH
    with pytest.raises(MediaError) as unsupported:
        service.stage("event-2", 0, b"not-an-image")
    assert unsupported.value.code is MediaErrorCode.UNSUPPORTED_TYPE
    with pytest.raises(MediaError) as oversized:
        service.stage("event-3", 0, b"\x89PNG\r\n\x1a\n" + b"x" * MAX_IMAGE_BYTES)
    assert oversized.value.code is MediaErrorCode.TOO_LARGE


def test_capture_window_expires_and_removes_unbound_image(tmp_path: Path) -> None:
    clock = [datetime(2026, 7, 11, tzinfo=timezone.utc)]
    service = _service(tmp_path, clock)
    image = service.stage("event-1", 0, PNG)
    service.add_to_capture("session", "user", [image])
    assert service.active_capture("session") == (image,)

    clock[0] += timedelta(seconds=121)
    assert service.active_capture("session") == ()
    assert not Path(image.staged_path).exists()


def test_capture_rejects_more_than_six_images(tmp_path: Path) -> None:
    clock = [datetime(2026, 7, 11, tzinfo=timezone.utc)]
    service = _service(tmp_path, clock)
    images = [service.stage(f"event-{idx}", 0, PNG + bytes([idx])) for idx in range(7)]

    service.add_to_capture("session", "user", images[:6])
    with pytest.raises(MediaError) as error:
        service.add_to_capture("session", "user", images[6:])
    assert error.value.code is MediaErrorCode.INVALID_PART
