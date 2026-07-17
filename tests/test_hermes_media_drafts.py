from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from riji_agent.drafts.models import DraftStatus
from riji_agent.drafts.service import DraftService
from riji_agent.drafts.store import DraftStore
from riji_agent.hermes.errors import AuthError, AuthErrorCode
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.gateway import HermesGateway
from riji_agent.hermes.models import IncomingMessage
from riji_agent.journal.index import JournalIndex
from riji_agent.media.service import MediaService
from riji_agent.memory.models import session_key
from riji_agent.memory.store import MemoryStore
from riji_agent.personas.registry import PersonaRegistry

SECRET = "secret"
PNG = b"\x89PNG\r\n\x1a\n" + b"journal-image"
JPG = b"\xff\xd8\xff" + b"second-image"
TEMPLATE = "# {{date}}\n\n## 🧠 Notes\n"


class ExplodingResponder:
    def respond(self, *args, **kwargs):
        raise AssertionError("media draft flow must not call the model")


def _build(tmp_path: Path, clock: list[datetime]):
    root = tmp_path / "riji"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "daily.md").write_text(TEMPLATE, encoding="utf-8")
    index = JournalIndex(tmp_path / "state" / "index.sqlite3", root)
    drafts = DraftService(
        DraftStore(tmp_path / "state" / "drafts.sqlite3"),
        root,
        index,
        now=lambda: clock[0],
    )
    media = MediaService(
        tmp_path / "state" / "media.sqlite3",
        tmp_path / "state" / "media" / "staging",
        now=lambda: clock[0],
    )
    gateway = HermesGateway(
        hermes_secret=SECRET,
        allowed_user_ids={"ou_1"},
        registry=PersonaRegistry(),
        store=MemoryStore(tmp_path / "state" / "memory.sqlite3"),
        events=EventLog(tmp_path / "state" / "events.sqlite3"),
        responder=ExplodingResponder(),
        draft_service=drafts,
        media_service=media,
    )
    return gateway, drafts, media, root, index


def _message(
    text: str,
    event_id: str,
    *,
    attachment_ids=(),
    message_type: str = "text",
    chat_type: str = "p2p",
) -> IncomingMessage:
    return IncomingMessage(
        event_id=event_id,
        feishu_user_id="ou_1",
        chat_id="c1",
        chat_type=chat_type,
        text=text,
        message_type=message_type,
        attachment_ids=tuple(attachment_ids),
    )


def test_image_first_then_text_commits_original_bytes_and_embed(tmp_path: Path) -> None:
    clock = [datetime(2026, 7, 11, 8, 0, tzinfo=timezone.utc)]
    gateway, _drafts, media, root, index = _build(tmp_path, clock)
    image = media.stage("image-1", 0, PNG)

    received = gateway.handle(
        SECRET, _message("", "image-1", attachment_ids=[image.attachment_id], message_type="photo")
    )
    assert "2 分钟内" in received.text
    preview = gateway.handle(SECRET, _message("帮我记录：今天看到了新的展览", "text-1"))
    assert "图片：1 张（PNG）" in preview.text
    assert not (root / "assets").exists()

    confirmed = gateway.handle(SECRET, _message("确认保存", "confirm-1"))
    assert "已写入" in confirmed.text
    asset = root / "assets" / f"{image.sha256}.png"
    assert asset.read_bytes() == PNG
    note = (root / "daily" / "2026-07-11.md").read_text(encoding="utf-8")
    assert "- 今天看到了新的展览\n  ![[" in note
    assert f"![[{image.sha256}.png]]" in note
    index.close()


def test_text_first_then_images_replaces_old_confirmation(tmp_path: Path) -> None:
    clock = [datetime(2026, 7, 11, 8, 0, tzinfo=timezone.utc)]
    gateway, drafts, media, root, index = _build(tmp_path, clock)
    first = gateway.handle(SECRET, _message("帮我记录：今天完成了图文测试", "text-1"))
    old = drafts.get_latest_awaiting_for_session(
        session_key("ou_1", "gentle_reviewer", "c1")
    )
    assert old is not None and "图片：" not in first.text
    image = media.stage("image-1", 0, JPG)

    revised = gateway.handle(
        SECRET, _message("", "image-1", attachment_ids=[image.attachment_id], message_type="photo")
    )
    assert "已把图片加入草稿" in revised.text
    assert "图片：1 张（JPEG）" in revised.text
    assert drafts.get_draft(old.draft_id).status is DraftStatus.CANCELLED
    stale = gateway.handle(SECRET, _message(f"确认保存 {old.draft_id}", "stale-confirm"))
    assert "已处理过" in stale.text
    gateway.handle(SECRET, _message("确认保存", "confirm"))
    assert (root / "assets" / f"{image.sha256}.jpg").exists()
    index.close()


def test_rich_text_event_records_multiple_images_and_removes_placeholder(tmp_path: Path) -> None:
    clock = [datetime(2026, 7, 11, 8, 0, tzinfo=timezone.utc)]
    gateway, _drafts, media, root, index = _build(tmp_path, clock)
    first = media.stage("post-1", 0, PNG)
    second = media.stage("post-1", 1, JPG)
    preview = gateway.handle(
        SECRET,
        _message(
            "帮我记录：今天整理了照片\n[Image: first]\n[Image: second]",
            "post-1",
            attachment_ids=[first.attachment_id, second.attachment_id],
        ),
    )

    assert "图片：2 张（PNG, JPEG）" in preview.text
    assert "[Image:" not in preview.text
    gateway.handle(SECRET, _message("确认保存", "confirm"))
    note = (root / "daily" / "2026-07-11.md").read_text(encoding="utf-8")
    assert note.count("![[") == 2
    index.close()


def test_image_capture_expires_before_later_record_request(tmp_path: Path) -> None:
    clock = [datetime(2026, 7, 11, 8, 0, tzinfo=timezone.utc)]
    gateway, _drafts, media, _root, index = _build(tmp_path, clock)
    image = media.stage("image-1", 0, PNG)
    gateway.handle(
        SECRET, _message("", "image-1", attachment_ids=[image.attachment_id], message_type="photo")
    )
    clock[0] += timedelta(seconds=121)

    preview = gateway.handle(SECRET, _message("帮我记录：窗口已经过期", "text-1"))
    assert "图片：" not in preview.text
    index.close()


def test_cancel_discards_staging_without_touching_vault(tmp_path: Path) -> None:
    clock = [datetime(2026, 7, 11, 8, 0, tzinfo=timezone.utc)]
    gateway, _drafts, media, root, index = _build(tmp_path, clock)
    image = media.stage("post-1", 0, PNG)
    gateway.handle(
        SECRET,
        _message("帮我记录：取消测试", "post-1", attachment_ids=[image.attachment_id]),
    )
    cancelled = gateway.handle(SECRET, _message("取消记录", "cancel"))

    assert "已取消" in cancelled.text
    assert not Path(image.staged_path).exists()
    assert not (root / "daily").exists()
    assert not (root / "assets").exists()
    index.close()


def test_text_correction_preserves_images(tmp_path: Path) -> None:
    clock = [datetime(2026, 7, 11, 8, 0, tzinfo=timezone.utc)]
    gateway, _drafts, media, root, index = _build(tmp_path, clock)
    image = media.stage("post-1", 0, PNG)
    gateway.handle(
        SECRET,
        _message("帮我记录：今天去了地点甲", "post-1", attachment_ids=[image.attachment_id]),
    )
    revised = gateway.handle(
        SECRET, _message("不是地点甲，是地点乙", "correction")
    )
    assert "地点乙" in revised.text
    assert "图片：1 张" in revised.text
    gateway.handle(SECRET, _message("确认保存", "confirm"))
    note = (root / "daily" / "2026-07-11.md").read_text(encoding="utf-8")
    assert "地点乙" in note
    assert f"![[{image.sha256}.png]]" in note
    index.close()


def test_group_and_unsupported_media_are_rejected(tmp_path: Path) -> None:
    clock = [datetime(2026, 7, 11, 8, 0, tzinfo=timezone.utc)]
    gateway, _drafts, media, _root, index = _build(tmp_path, clock)
    image = media.stage("image-1", 0, PNG)
    with pytest.raises(AuthError) as denied:
        gateway.handle(
            SECRET,
            _message(
                "帮我记录：群聊",
                "image-1",
                attachment_ids=[image.attachment_id],
                chat_type="group",
            ),
        )
    assert denied.value.code is AuthErrorCode.GROUP_CHAT_DENIED
    unsupported = gateway.handle(
        SECRET, _message("", "file-1", message_type="document")
    )
    assert "不支持视频、文件或语音" in unsupported.text
    index.close()
