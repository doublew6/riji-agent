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


def test_polish_collapses_incidental_numbered_outline_into_paragraph() -> None:
    content = (
        "1. 下午5点参加的面试：是一个自营资金纯交易的小公司，连私募也算不上。\n"
        "2. 我和慧中说了一下：我觉得这公司不太好。\n"
        "3. 我的想法：\n"
        "a. 我还是想找到一个更符合我职业发展的想法、更合适的工作。\n"
        "b. 这样对长期的发展更有利。\n"
        "4. 慧中的意思：她希望我能够尽快地找到一个工作"
    )

    assert polish_draft_content(content) == (
        "下午5点参加的面试：是一个自营资金纯交易的小公司，连私募也算不上。"
        "我和慧中说了一下：我觉得这公司不太好。"
        "我的想法：我还是想找到一个更符合我职业发展的想法、更合适的工作。"
        "这样对长期的发展更有利。"
        "慧中的意思：她希望我能够尽快地找到一个工作"
    )


def test_polish_keeps_outline_when_user_requests_list_format() -> None:
    content = "请分条记录：\n1. 上午跑步。\n2. 晚上复盘。"

    assert polish_draft_content(content) == content


def test_polish_collapses_plain_multiline_note_into_paragraph() -> None:
    content = "今天下午参加了面试。\n我觉得这家公司不太适合。"

    assert (
        polish_draft_content(content) == "今天下午参加了面试。我觉得这家公司不太适合。"
    )
