from __future__ import annotations

from pathlib import Path

from riji_agent.im.feishu import FEISHU_PLATFORM, FeishuIncomingMessage
from riji_agent.im.models import IncomingChatMessage, PRIVATE_CHAT_TYPE


def test_feishu_message_maps_to_neutral_chat_message() -> None:
    message = FeishuIncomingMessage(
        event_id="e1",
        feishu_user_id="ou_1",
        chat_id="c1",
        chat_type=PRIVATE_CHAT_TYPE,
        text="hello",
    ).to_chat_message()

    assert message == IncomingChatMessage(
        event_id="e1",
        user_id="ou_1",
        chat_id="c1",
        chat_type=PRIVATE_CHAT_TYPE,
        text="hello",
        platform=FEISHU_PLATFORM,
    )


def test_im_contract_does_not_depend_on_default_stack_names() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "riji_agent" / "im"
    text = (root / "models.py").read_text(encoding="utf-8")

    forbidden = ("Feishu", "Hermes", "DeepSeek", "feishu_user_id")
    for phrase in forbidden:
        assert phrase not in text
