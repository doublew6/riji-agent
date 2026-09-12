"""One bounded rendering shared by context accounting and prompt assembly."""

from __future__ import annotations

from riji_agent.memory.models import LongTermMemory


def render_memory_fact(item: LongTermMemory) -> str:
    identifiers = item.metadata.get("source_ids") or [item.metadata.get("source_id")]
    if not isinstance(identifiers, (tuple, list)):
        identifiers = [item.metadata.get("source_id")]
    sources = "、".join(f"[[{value}]]" for value in identifiers[:4] if isinstance(value, str) and len(value) <= 300)
    observed = item.metadata.get("source_created_at")
    source = f"（来源时间 {observed or '未知'}，{sources}）" if sources else ""
    state = {"current": "当前", "historical": "历史阶段", "conflict": "待复核冲突"}.get(
        item.metadata.get("journal_state"), "")
    kind = "待验证观察" if item.metadata.get("journal_kind") == "observation" else ""
    effective = item.metadata.get("valid_from")
    corrected = "用户纠正" if item.metadata.get("manually_corrected") else ""
    labels = " · ".join(value for value in (state, kind, corrected, f"生效 {effective}" if effective else "") if value)
    return f"- {'[' + labels + '] ' if labels else ''}{item.content}{source}"
