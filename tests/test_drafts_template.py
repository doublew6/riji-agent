from datetime import date

import pytest

from riji_agent.drafts.errors import DraftError, DraftErrorCode
from riji_agent.drafts.template import append_to_section, instantiate_daily

TEMPLATE = "# {{date}}\n\n## 🌆 Evening\n- 既有条目\n\n## 🧠 Notes\n"


def test_instantiate_replaces_date() -> None:
    out = instantiate_daily(TEMPLATE, date(2026, 6, 25))
    assert out.startswith("# 2026-06-25")
    assert "{{date}}" not in out


def test_append_adds_bullet_at_end_of_section() -> None:
    out = append_to_section(TEMPLATE, "🌆 Evening", "新事件")
    lines = out.split("\n")
    # appended after the existing bullet, before the next heading
    evening = lines.index("## 🌆 Evening")
    notes = lines.index("## 🧠 Notes")
    section = lines[evening:notes]
    assert "- 既有条目" in section
    assert "- 新事件" in section
    assert section.index("- 新事件") > section.index("- 既有条目")


def test_append_leaves_other_sections_intact() -> None:
    out = append_to_section(TEMPLATE, "🧠 Notes", "随手记")
    assert "- 既有条目" in out  # Evening section untouched
    assert out.count("- 随手记") == 1


def test_append_matches_by_partial_section_name() -> None:
    out = append_to_section(TEMPLATE, "Evening", "用部分名匹配")
    assert "- 用部分名匹配" in out


def test_missing_section_raises() -> None:
    with pytest.raises(DraftError) as err:
        append_to_section(TEMPLATE, "不存在的区块", "x")
    assert err.value.code is DraftErrorCode.SECTION_NOT_FOUND
