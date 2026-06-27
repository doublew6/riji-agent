from __future__ import annotations

import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FEISHU_AVATAR = ROOT / "assets" / "integrations" / "feishu" / "riji-bot-avatar.png"


def test_feishu_bot_avatar_is_square_png_asset() -> None:
    data = FEISHU_AVATAR.read_bytes()

    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (512, 512)
    assert FEISHU_AVATAR.stat().st_size < 500_000
