"""Evidence-scope guidance shared by default and persona-specific agent calls."""

from typing import Any


JOURNAL_EVIDENCE_BOUNDARIES = (
    "检索证据边界：只根据实际返回的内容陈述日记事实，并保留日期和来源；"
    "把事实、推断和本次检索尚未找到的证据分开。"
    "日期按工具的date_basis解释：date是日记元数据中的记录日期，query_anchor是检索中心；"
    "before/on/after与timeline时间桶按记录日期分组。先逐条写明记录日期与所述经历，再比较变化；"
    "同主题记录可以描述不同次事件，事件日期和事件之间的前后关系须由返回正文支持。"
    "例如虚构记录：林禾在2031-04-12写‘4月10日参加读书会’，4月14日写‘今天再次参加’。"
    "应区分12日记录的10日活动与14日的另一次活动，检索中心12日不使它们成为同一次活动的会前与会后。"
    "正文给出的事件日期仍可使用；优先写有依据的绝对日期，相对天数须核算日历差。"
    "关键词、主题、日期、标签筛选和结果数量上限只给出有限匹配，"
    "未命中、空时间桶、工具失败或次数耗尽，不代表当天没有日记、没有发生某事或没有其他记录。"
    "truncated=false只表示本次结果没有标记截断，不证明查全；返回一条也不证明它是唯一事件。"
    "read_note只支持对该来源实际返回正文的判断，截断或不可见部分未知；"
    "即使读完一篇日记，也不能据此断言当天的全部经历。list_periods只列可见的有限元数据。"
    "遵守工具观测中的evidence_scope：例如应说‘本次检索未找到该日期与此主题匹配的内容’，"
    "不能把它改称‘这天没有记录’。只有直接证据支持的范围才可作排除或唯一性判断，"
    "不要把检索缺口写成确定事实，也不要为消除不确定性突破已有工具或权限限制。"
)

_TOOL_SCOPES = {
    "search_journal": (
        "query_filtered_snippets",
        "Only returned matches for this query and filters; result and snippet limits apply. "
        "An empty result or truncated=false does not establish missing entries, absent events or exhaustive coverage.",
    ),
    "read_note": (
        "one_source_permitted_body",
        "Only the returned body of this cited source; respect truncation and visibility limits. "
        "A complete returned note is not a complete account of the day's events.",
    ),
    "list_periods": (
        "bounded_visible_metadata",
        "Only returned visible entry metadata within the requested filters and result limit. "
        "An omitted entry is not proof that no entry exists; metadata does not establish event content.",
    ),
    "timeline": (
        "topic_filtered_timeline",
        "Buckets contain only returned topic matches. empty_periods means no returned evidence "
        "for this topic in those buckets, not missing journal entries or absent events. Limits still apply.",
    ),
    "find_before_after": (
        "bounded_date_window",
        "Only returned entries in this date window, optionally filtered by topic. Empty before/on/after "
        "groups are retrieval gaps, not proof of missing entries or absent events. Empty snippets provide "
        "metadata only, not evidence about event content. before/on/after is relative to the query anchor, "
        "not a verified event date; prefer supported absolute dates. Limits still apply.",
    ),
}


def with_evidence_scope(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Annotate existing observations without reading or discovering more sources."""
    if name not in _TOOL_SCOPES:
        return payload
    selection, interpretation = _TOOL_SCOPES[name]
    return {"date_basis": {
        "date": "journal_note_metadata",
        "event_dates_and_relations": "require_returned_content_evidence",
    }, **payload, "evidence_scope": {
        "selection": selection,
        "journal_completeness": "not_established",
        "interpretation": interpretation,
    }}
