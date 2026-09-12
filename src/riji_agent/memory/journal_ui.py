"""Local journal coverage and persistent processing controls."""

from __future__ import annotations

import html
from urllib.parse import quote
from typing import Any, Optional

from riji_agent.memory.journal_engine import JournalMemoryEngine
from riji_agent.memory.models import LongTermMemory
from riji_agent.memory.privacy_ui import budget_summary


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def render_journal_progress(engine: Optional[JournalMemoryEngine], user_id: str, query: str = "") -> str:
    if engine is None or engine.policy.user_id != user_id:
        return '<section class="paper-panel"><h2>日记来源</h2><p>该用户尚未启用日记记忆。配置用户、允许的区块和处理预算后启用。</p></section>'
    progress = engine.store.progress()
    state = "已停用" if not engine.policy.enabled else "自动处理已暂停" if progress["paused"] else "当前来源提取已结束" if progress["initialized"] else "提取待处理或有失败"
    buttons = (("scan", "重新发现文件"), ("resume" if progress["paused"] else "pause",
               "继续自动处理" if progress["paused"] else "暂停自动处理"), ("retry", "重试失败片段"))
    actions = "".join(f'<button data-journal-action="{key}" data-user="{_escape(user_id)}">{label}</button>'
                      for key, label in buttons)
    sources = [row for row in progress["sources"] if query.casefold() in row["path"].casefold()]
    rows = "".join(_source_row(row) for row in sources[:200])
    scope = "、".join(engine.policy.sections)
    dates = f'{engine.policy.date_from or "不限起始"} → {engine.policy.date_to or "不限结束"}'
    budgets = progress["budgets"]
    budget = f'{budgets[0]["key"]}：{budgets[0]["chars"]} 字符' if budgets else "尚无模型调用"
    return f'''<section class="paper-panel"><p class="eyebrow">JOURNAL MEMORY</p><h2>日记来源 · {state}</h2>
<p>发现 {progress["discovered"]} 个文件，完成 {progress["completed"]} 个，待处理或失败 {progress["pending"]} 个。</p>
<p class="meta">本地目录：{_escape(engine.policy.root)}<br>授权区块：{_escape(scope)} · 日期：{_escape(dates)}<br>
最近扫描：{_escape(progress["last_scan"] or "尚未扫描")} · {_escape(progress["error"] or "无扫描错误")}<br>
{_escape(budget)} · 等待后端清理 {progress["cleanup_pending"]} 条<br>
<strong>{_escape(budget_summary(engine))}</strong></p><div class="actions">{actions}
<a href="/admin/memory/export?user_id={quote(user_id, safe='')}">导出记忆与来源关系</a></div>
<p class="meta">文件扫描完成不代表提取完成，来源提取结束也不代表后续整理全部成功。暂停自动处理会停止日记提取与整理的新模型调用；本地扫描继续检查来源变化。
修改区块范围或提高预算需要修改本地配置并重启服务。永久删除会阻止相关来源片段再次自动提取。</p>
<div class="source-table"><table><thead><tr><th>文件</th><th>类型 / 日期</th><th>处理状态</th></tr></thead>
<tbody>{rows or '<tr><td colspan="3">没有匹配的文件。</td></tr>'}</tbody></table></div>
<p class="meta">显示 {min(200, len(sources))} / {len(sources)} 个匹配文件；可按相对路径搜索，完整状态可从命令行导出。</p></section>'''


def _source_row(row: dict[str, Any]) -> str:
    labels = {"completed": "完成", "pending": "待处理", "failed": "读取失败", "excluded": "范围排除",
              "empty": "无可提取片段", "deleted": "来源已删除"}
    jobs = "；".join(f'{job["status"]} × {job["count"]} {job["error"] or ""}' for job in row["jobs"])
    detail = "没有适合的长期记忆" if row.get("outcome") == "no_durable_memory" else row["reason"] or jobs
    return (f'<tr><td>{_escape(row["path"])}</td><td>{_escape(row["kind"])} / '
            f'{_escape(row["observed_at"] or "日期未知")}</td><td>{_escape(labels.get(row["status"], row["status"]))}'
            f'<br><span class="meta">{_escape(detail)}</span></td></tr>')


def render_journal_evidence(item: LongTermMemory) -> str:
    if not item.metadata.get("journal_managed"):
        return ""
    evidence = item.metadata.get("journal_evidence", [])
    rows = "".join(f'<li>{_escape(ref["source_id"])} · {_escape(ref["section"])} · 第 {ref["line"]} 行'
                   f' · 版本 {_escape(ref["version"][:12])}</li>' for ref in evidence[:8])
    native = item.metadata.get("native_evidence", [])
    rows += "".join(f'<li>{_escape(ref["source_id"])} · 原生对话 · {_escape(ref["observed_at"] or "时间未知")}</li>'
                    for ref in native[:8])
    action = {"new": "新增", "duplicate": "补充依据", "enrich": "补充细节", "state_change": "阶段变化",
              "conflict": "待复核冲突", "related": "关联不同事实"}.get(item.metadata.get("relation_action"), "历史记录")
    reason = item.metadata.get("relation_reason") or "未记录关系理由"
    target = item.metadata.get("relation_target") or "无"
    kind = item.metadata.get("journal_kind") or "未知"
    return (f'<div class="meta"><p>类型 {_escape(kind)} · {_escape(action)} · 关联记忆 {_escape(target)}</p>'
            f'<p>{_escape(reason)}</p><ul>{rows or "<li>没有可验证的日记依据；人工纠正可能提供独立依据。</li>"}</ul>'
            f'<p>有效日记依据 {len(evidence)} 处，原生对话依据 {len(native)} 处；同一事件的多种记录不自动算作多次经历。</p></div>')
