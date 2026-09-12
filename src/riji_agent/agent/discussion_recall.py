"""A user's explicit request, rather than a model argument, enables AI sources."""

import re


def requests_discussion_recall(question: str) -> bool:
    return bool(re.match(
        r"^(?:请)?(?:参考|回顾|查看|查找|检索)"
        r"(?:以前|之前|历史|上次|过去|已保存)?(?:的)?"
        r"(?:AI\s*导师讨论结果|AI\s*讨论(?:结果)?|导师讨论(?:结果)?|历史讨论)"
        r"(?:[，,:：。？?\s]|$)", question.strip(),
    ))
