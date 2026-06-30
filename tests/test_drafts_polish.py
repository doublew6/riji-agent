from riji_agent.drafts.polish import polish_draft_content


def test_polish_removes_command_residue_and_leading_filler() -> None:
    content = "一下，今天我去三里屯理发了，今天的费用还是120，明天之后就会涨到140"

    assert polish_draft_content(content) == (
        "今天我去三里屯理发了，今天的费用还是120，明天之后就会涨到140"
    )


def test_polish_keeps_numbers_locations_and_meaning() -> None:
    content = "帮我记录一下：今天我去三里屯理发了，费用还是120，之后涨到140"

    polished = polish_draft_content(content)

    assert polished == "今天我去三里屯理发了，费用还是120，之后涨到140"
    assert "三里屯" in polished
    assert "120" in polished
    assert "140" in polished


def test_polish_does_not_remove_filler_inside_sentence() -> None:
    content = "我想确认一下今天写入是否成功"

    assert polish_draft_content(content) == content
