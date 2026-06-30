from __future__ import annotations

import re

import pytest

from riji_agent.packs import PackNotFoundError, get_pack, list_packs


def test_personal_growth_pack_is_discoverable() -> None:
    packs = list_packs()

    assert "personal-growth" in packs
    assert packs == tuple(sorted(packs))


def test_personal_growth_pack_describes_journal_capabilities() -> None:
    pack = get_pack("personal-growth")

    assert pack.id == "personal-growth"
    assert pack.name == "Personal Growth Journal"
    assert {"daily", "weekly", "monthly"} <= {template.id for template in pack.templates}

    skill_ids = {skill.id for skill in pack.skills}
    assert {
        "weekly-review-from-daily",
        "monthly-review-from-weekly",
        "daily-weather",
        "apple-health-sleep",
        "ticktick-tasks",
        "toggl-deepwork",
    } <= skill_ids

    automation_ids = {automation.id for automation in pack.automations}
    assert {
        "create-daily-journal",
        "create-previous-weekly-review",
        "create-previous-monthly-review",
        "fill-daily-weather",
    } <= automation_ids


def test_personal_growth_pack_keeps_write_boundary_explicit() -> None:
    pack = get_pack("personal-growth")
    rendered = "\n".join(pack.privacy_notes)

    assert "draft" in rendered
    assert "controlled writer" in rendered
    assert "complete vault" in rendered
    assert "raw Markdown files" in rendered
    assert "API keys" in rendered


def test_unknown_pack_raises_safe_error() -> None:
    with pytest.raises(PackNotFoundError, match="unknown pack"):
        get_pack("missing-pack")


def test_builtin_pack_metadata_has_no_personal_absolute_paths() -> None:
    pack = get_pack("personal-growth")
    rendered = pack.to_text()

    forbidden = (
        re.compile("/" + r"Users/(?!example(?:/|\b))[^\s'\"`]+"),
        re.compile("Mobile " + "Documents"),
        re.compile("iCloud" + "~md~obsidian"),
        re.compile(r"open\.larksuite\.com/open-apis/bot/v2/hook/[A-Za-z0-9-]+"),
    )
    for pattern in forbidden:
        assert not pattern.search(rendered)
