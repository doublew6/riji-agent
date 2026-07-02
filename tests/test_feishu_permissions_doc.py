from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PERMISSIONS = ROOT / "docs" / "feishu-permissions.yaml"


def test_feishu_permissions_file_lists_required_bot_scopes() -> None:
    data = yaml.safe_load(PERMISSIONS.read_text(encoding="utf-8"))
    groups = data["permission_groups"]
    scopes = {
        permission["scope"]
        for group in groups.values()
        for permission in group["permissions"]
    }

    assert groups["core_private_chat"]["required"] is True
    assert "im:message.p2p_msg:readonly" in scopes
    assert "im:message:send_as_bot" in scopes
    assert "contact:user.id:readonly" in scopes
    assert "im:resource" in scopes
    assert "calendar:calendar.event:create" in scopes
    assert "calendar:calendar" in scopes


def test_feishu_permission_docs_point_to_single_source_of_truth() -> None:
    assert "feishu-permissions.yaml" in (ROOT / "docs" / "hermes-integration.md").read_text(
        encoding="utf-8"
    )
    assert "feishu-permissions.yaml" in (ROOT / "README.zh-CN.md").read_text(
        encoding="utf-8"
    )
