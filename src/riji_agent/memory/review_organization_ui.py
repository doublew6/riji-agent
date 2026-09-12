"""Topic atlas, evidence comparisons and explicit memory review policy."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any, Sequence
from urllib.parse import urlencode

from riji_agent.memory.models import LongTermMemory
from riji_agent.memory.organization import (
    CATEGORIES, REVIEW_AFTER_DAYS, active_records, memory_fingerprint,
    memory_version, overdue_ids, review_age,
)
from riji_agent.memory.organization_store import OrganizationStore


@dataclass(frozen=True)
class OrganizationPage:
    user_id: str
    records: Sequence[LongTermMemory]
    visible_ids: set[str]
    store: OrganizationStore
    backend_ok: bool


def escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def navigation(user_id: str, view: str) -> str:
    items = (("overview", "认识地图"), ("compare", "比较与复核"),
             ("lifecycle", "处理轨迹"), ("facts", "原始记忆"), ("sources", "日记来源"))
    return '<nav class="memory-nav" aria-label="记忆视图">' + "".join(
        f'<a href="?{escape(urlencode(dict(user_id=user_id, view=key)))}" '
        f'{"aria-current=page" if key == view else ""}>{title}</a>' for key, title in items
    ) + '</nav>'


def render_organization(page: OrganizationPage, view: str) -> str:
    latest = page.store.latest(page.user_id)
    ready = page.store.latest(page.user_id, ready_only=True)
    report = ready["report"] if ready else None
    active = active_records(page.records, page.user_id)
    stale = bool(report and report["fingerprint"] != memory_fingerprint(active))
    state = _state_panel(page, latest, ready, stale)
    if not page.backend_ok:
        return state + '<div class="empty"><h2>记忆暂时不可读</h2><p>恢复连接后再展示依据与整理结果。</p></div>'
    valid = {item.id: item for item in active if report and
             report["versions"].get(item.id) == memory_version(item)}
    if view == "compare":
        content = _comparisons(page, report, valid) + _review_panel(page, report, active)
    elif view == "lifecycle":
        content = _lifecycle(ready, report)
    else:
        content = _atlas(page, report, valid, active)
    return state + content


def _state_panel(page: OrganizationPage, latest: Any, ready: Any, stale: bool) -> str:
    labels = {"pending": "等待后台整理", "processing": "正在整理",
              "ready": "整理已完成", "failed": "整理失败，可重新尝试"}
    label = labels.get(latest["status"], "尚未整理") if latest else "尚未整理"
    if stale:
        label += " · 部分记忆已变化"
    report = ready["report"] if ready else None
    detail = (f'已整理 {report["processed"]} / {report["total"]} 条 · '
              f'{len(report["groups"])} 个有界比较批次 · {escape(ready["updated_at"])}') if report else (
        "点击整理现有记忆。新事实与人工修改会自动触发后台整理。"
    )
    pending = bool(latest and latest["status"] in {"pending", "processing"})
    return f'''<section class="organization-state"><div><p class="eyebrow">MEMORY ATLAS</p>
<h2>{label}</h2><p class="meta">{detail}</p></div><div class="organization-actions">
<button data-action="organize" data-user="{escape(page.user_id)}" {"disabled" if pending or not page.backend_ok else ""}>
{"已加入队列" if pending else "整理现有记忆"}</button>
<a href="" class="refresh-link">刷新结果</a></div></section>'''


def _atlas(page: OrganizationPage, report: Any, valid: dict, active: Sequence) -> str:
    cards = []
    if report:
        for group in report["groups"]:
            for topic in group["topics"]:
                ids = topic["evidence_ids"]
                if all(mid in valid for mid in ids) and page.visible_ids.intersection(ids):
                    cards.append(_topic_card(topic, group, valid, page.user_id))
    covered = {mid for group in (report or {}).get("groups", []) for topic in group["topics"]
               if all(mid in valid for mid in topic["evidence_ids"]) for mid in topic["evidence_ids"]}
    remaining = [item for item in active if item.id not in covered and item.id in page.visible_ids]
    body = '<div class="atlas-intro"><h2>从事实到认识</h2><p>摘要由模型归纳。每个判断都保留依据；共享事实与各导师观察分别整理。</p></div>'
    body += '<div class="topic-list">' + "".join(cards) + '</div>'
    if remaining:
        body += f'<section class="unorganized"><h3>待整理的记忆 <span>{len(remaining)}</span></h3><p class="meta">尚未整理或内容已变化；不会沿用旧摘要。</p>'
        body += "".join(_evidence(item, page.user_id) for item in remaining) + '</section>'
    if not cards and not remaining:
        body += '<div class="empty"><h2>当前筛选下没有主题</h2><p>调整搜索或范围后再查看。</p></div>'
    return body


def _topic_card(topic: dict, group: dict, records: dict, user_id: str) -> str:
    ids = topic["evidence_ids"]
    scope = "共享事实" if group["scope"] == "shared" else f'导师观察 · {group["persona_id"]}'
    evidence = "".join(_evidence(records[mid], user_id) for mid in ids)
    return f'''<article class="topic-card"><div class="topic-index">{CATEGORIES[topic["category"]]}<small>{escape(scope)}</small></div>
<div class="topic-body"><h3>{escape(topic["title"])}</h3><p class="topic-summary">{escape(topic["summary"])}</p>
{_observations(group, records, user_id)}<details><summary>展开 {len(ids)} 条依据</summary>{evidence}</details></div></article>'''


def _observations(group: dict, records: dict, user_id: str) -> str:
    if not set(group.get("versions", {})) <= set(records):
        return ""
    rows = []
    for observation in group.get("observations", []):
        ids = observation["support_ids"] + observation["counter_ids"]
        if not all(mid in records for mid in ids):
            continue
        support = "".join(_evidence(records[mid], user_id) for mid in observation["support_ids"])
        counter = "".join(_evidence(records[mid], user_id) for mid in observation["counter_ids"])
        rows.append(f'<aside class="paper-panel"><p class="decision-label">待验证观察 · 供相关回答使用</p>'
                    f'<p>{escape(observation["summary"])}</p><p>{escape(observation["limitation"])}</p>'
                    f'<details><summary>支持证据</summary>{support}</details>'
                    f'<details><summary>反例</summary>{counter or "尚无已识别反例；不代表不存在反例。"}</details></aside>')
    return "".join(rows)


def _evidence(item: LongTermMemory, user_id: str, *, controls: bool = False) -> str:
    url = "?" + urlencode(dict(user_id=user_id, view="facts", selected=item.id))
    observed = item.metadata.get("source_created_at") or "观察时间未知"
    source = item.metadata.get("source_id") or "来源未标注"
    actions = ""
    if controls:
        actions = f'''<form class="edit-form review-actions" data-memory="{escape(item.id)}" data-user="{escape(user_id)}">
<button type="button" class="quiet" data-mutation="reconfirm">仍然有效</button>
<button type="button" class="quiet" data-mutation="archive">归档，可恢复</button></form>'''
    return f'''<blockquote class="evidence"><p>{escape(item.content)}</p><footer>
<span>{escape(observed)} · {escape(source)}</span><a href="{escape(url)}">校勘与历史 ↗</a></footer>{actions}</blockquote>'''


def _comparisons(page: OrganizationPage, report: Any, valid: dict) -> str:
    labels = {"duplicate": "疑似重复", "conflict": "有待澄清", "update": "状态变化", "related": "相关，建议分别保留"}
    cards = []
    for group in (report or {}).get("groups", []):
        for pair in group["comparisons"]:
            ids = pair["evidence_ids"]
            if not all(mid in valid for mid in ids) or not page.visible_ids.intersection(ids):
                continue
            evidence = "".join(_evidence(valid[mid], page.user_id, controls=True) for mid in ids)
            cards.append(f'''<article class="comparison"><header><span class="decision-label">{labels[pair["kind"]]}</span>
<span class="meta">模型建议 · 尚未合并或覆盖</span></header><div class="evidence-pair">{evidence}</div>
<p class="decision-reason"><b>判断依据</b> {escape(pair["reason"])}</p></article>''')
    empty = '<div class="empty compact"><h3>当前没有可展示的比较建议</h3><p>尚未整理、依据已变化或模型未发现关系，都可能出现此状态；不代表已证明没有冲突。</p></div>'
    return '<section class="comparison-section"><h2>把新旧认识放在一起看</h2><p class="meta">比较保持同一共享范围或同一导师；启用日记记忆后通过有界检索补入相关经历。核对依据后，可校勘正文或归档其中一条。</p>' + ("".join(cards) or empty) + '</section>'


def _review_panel(page: OrganizationPage, report: Any, active: Sequence) -> str:
    due = overdue_ids(report, active)
    records = [item for item in active if item.id in due and item.id in page.visible_ids]
    rows = "".join(f'<div class="review-due"><p class="decision-label">{review_age(item)} 天未复核 · 召回排序后移</p>'
                   + _evidence(item, page.user_id, controls=True) + '</div>' for item in records)
    return f'''<section class="review-policy"><div><p class="eyebrow">FORGETTING, WITH A WAY BACK</p>
<h2>哪些认识需要重新确认？</h2></div><p>阶段性计划、状态与待验证观察，超过 {REVIEW_AFTER_DAYS} 天未复核后进入这里。
排序后移仍保留检索机会；点击“仍然有效”重新计时，归档后退出召回。</p>
<p class="meta">稳定偏好、身份与历史事实不按年龄失效。观察时间未知时不推算到期日。此列表按当前日期计算，系统不自动删除。</p>
{rows or '<p class="policy-empty">当前没有已识别的到期条目。</p>'}</section>'''


def _lifecycle(ready: Any, report: Any) -> str:
    batches = len(report["groups"]) if report else 0
    steps = (
        ("01", "捕获", "只从用户自述提取；无长期价值的消息会记录为跳过。"),
        ("02", "精确去重", "同范围内规范化文本完全相同时跳过写入；保留匹配对象与来源。"),
        ("03", "主题整理", f"本次完成 {batches} 个独立批次；摘要引用原始记忆 ID。"),
        ("04", "语义比较", "重复、矛盾、变化与相关项生成建议，不自动覆盖事实。"),
        ("05", "复核与遗忘", f"阶段性信息 {REVIEW_AFTER_DAYS} 天未复核则排序后移；归档和删除由用户操作。"),
    )
    rows = "".join(f'<li><span class="step-number">{n}</span><div><h3>{title}</h3><p>{detail}</p></div></li>' for n, title, detail in steps)
    return f'''<section class="lifecycle"><h2>每次认识变化，留下来龙去脉</h2><p class="meta">下面是处理规则。实际发生的动作见下方时间线；旧数据没有记录的步骤不补造历史。</p>
<ol class="lifecycle-steps">{rows}</ol></section>'''


ORGANIZATION_CSS = """
.masthead{padding-top:32px;padding-bottom:24px}.filter-details{margin-bottom:12px}.filter-details>summary{color:var(--pencil);font-size:13px;min-height:44px;padding:12px 0;cursor:pointer}.filter-details .filters{margin:8px 0 24px}
.memory-nav,.review-shell{max-width:1480px;margin:auto;padding:0 40px}.memory-nav{display:flex;gap:32px;border-bottom:1px solid var(--rule);overflow:auto}.memory-nav a{padding:20px 0 16px;color:var(--pencil);text-decoration:none;white-space:nowrap;border-bottom:3px solid transparent}.memory-nav a[aria-current]{color:var(--ink);border-color:var(--vermillion);font-weight:650}.review-shell{padding-top:24px;padding-bottom:64px}.review-shell>.filters{margin-bottom:32px}.organization-state{display:flex;justify-content:space-between;gap:24px;align-items:center;padding:24px 0 32px;border-bottom:1px solid var(--rule)}.organization-actions{display:flex;align-items:center;gap:16px;flex-shrink:0}.refresh-link{display:inline-flex;align-items:center;font-size:13px}button:disabled{opacity:.4;cursor:default;transform:none}.atlas-intro{display:flex;align-items:baseline;gap:32px;margin:40px 0 24px}.atlas-intro p,.review-policy>p,.lifecycle p{color:var(--pencil);font-size:14px;line-height:1.8}.topic-card{display:grid;grid-template-columns:180px minmax(0,1fr);padding:28px 0;border-top:1px solid var(--rule);gap:32px}.topic-index{color:var(--vermillion);font-size:13px;padding-top:4px}.topic-index small{display:block;color:var(--pencil);font-size:11px;margin-top:12px;overflow-wrap:anywhere}.topic-body h3{font:600 24px/1.4 "Songti SC","STSong",serif;margin:0}.topic-summary{font:18px/1.9 "Songti SC","STSong",serif;margin:12px 0 16px;max-width:880px}.topic-body summary{font-size:13px;color:var(--pencil);cursor:pointer;padding:12px 0;min-height:44px}.evidence{margin:12px 0 0;padding:16px 20px;background:var(--paper);border-left:2px solid var(--rule);overflow-wrap:anywhere}.evidence p{margin:0 0 12px;font-size:15px;line-height:1.8}.evidence footer{display:flex;justify-content:space-between;gap:12px;align-items:baseline;font-size:11px;color:var(--pencil)}.evidence a{white-space:nowrap;display:inline-flex;align-items:center}.unorganized{margin-top:40px}.unorganized h3{font-size:18px}.unorganized h3 span{font-variant-numeric:tabular-nums;color:var(--pencil);margin-left:8px}.comparison-section,.review-policy,.lifecycle{padding-top:40px}.comparison{margin-top:24px;padding:24px;background:rgba(251,248,240,.5);border:1px solid var(--rule);border-radius:8px}.comparison header{display:flex;justify-content:space-between;gap:16px}.decision-label{color:var(--vermillion);font-size:13px;font-weight:600}.evidence-pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}.decision-reason{margin:20px 0 0;color:var(--pencil);font-size:14px;line-height:1.8}.decision-reason b{color:var(--ink);margin-right:8px}.review-actions{display:flex;flex-wrap:wrap;gap:8px}.review-actions button{font-size:12px}.review-policy{margin-top:40px;border-top:1px solid var(--rule)}.review-due{margin-top:24px}.policy-empty{padding:24px;background:var(--inset)}.compact{padding:32px}.compact p{font-size:13px;line-height:1.8}.lifecycle-steps{list-style:none;padding:0;display:grid;grid-template-columns:repeat(5,1fr);gap:24px;margin:32px 0}.lifecycle-steps li{border-top:2px solid var(--rule);padding-top:16px}.step-number{font:32px "Songti SC",serif;color:var(--vermillion)}.lifecycle h3{font-size:16px;margin:12px 0 0}.lifecycle-steps p{font-size:13px}.trace-grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(280px,1fr);gap:32px;margin-top:32px}.timeline span{float:none;display:block;margin-top:6px;overflow-wrap:anywhere}.timeline .change-diff{display:grid;grid-template-columns:1fr 1fr;gap:12px}.change-diff>p{padding:12px;background:var(--inset)}.change-diff b{display:block;margin-bottom:8px}.timeline .source-ref{font-size:11px;color:var(--pencil)}
@media(max-width:1100px){.filters{grid-template-columns:140px minmax(120px,1fr) 100px 100px auto}.lifecycle-steps{grid-template-columns:1fr}.lifecycle-steps li{display:flex;gap:24px}.lifecycle h3{margin-top:0}}
@media(max-width:700px){.memory-nav,.review-shell{padding-left:20px;padding-right:20px}.memory-nav{gap:24px}.organization-state,.atlas-intro{align-items:flex-start;flex-direction:column}.topic-card{grid-template-columns:1fr;gap:16px}.topic-index small{display:inline;margin-left:12px}.evidence-pair,.trace-grid,.timeline .change-diff{grid-template-columns:1fr}.comparison{padding:16px}.comparison header{flex-direction:column;gap:8px}.filters{grid-template-columns:1fr 1fr}.evidence footer{align-items:flex-start;flex-direction:column;gap:4px}.organization-actions{flex-wrap:wrap}.topic-summary{font-size:17px}}
"""
