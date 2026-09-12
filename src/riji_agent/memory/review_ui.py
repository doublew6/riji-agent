"""Escaped server-rendered markup for the local Memory Review UI."""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from riji_agent.config import Settings
from riji_agent.memory.backend import MemoryBackendError
from riji_agent.memory.models import LongTermMemory, MemoryScope, MemoryStatus
from riji_agent.memory.service import MemoryService
from riji_agent.memory.journal_ui import render_journal_evidence, render_journal_progress
from riji_agent.memory.privacy_ui import privacy_banner, privacy_panel, memory_permission, PRIVACY_CSS
from riji_agent.memory.journal_forget import render_forget_plan
from riji_agent.memory.review_organization_ui import (
    ORGANIZATION_CSS, OrganizationPage, navigation, render_organization,
)


@dataclass(frozen=True)
class ReviewPageData:
    service: MemoryService
    settings: Settings
    user_id: str
    records: Sequence[LongTermMemory]
    csrf: str
    backend_ok: bool
    query: str
    scope: str
    status: str
    selected: Optional[str]
    view: str = "overview"


def render_login() -> str:
    return _document(
        "Memory Review 登录",
        """<body class="login-shell"><main class="login-sheet">
<p class="eyebrow">RIJI · PRIVATE ARCHIVE</p><h1>打开记忆档案</h1>
<p class="lede">输入本地管理令牌。令牌只用于建立 HttpOnly 会话。</p>
<form id="login-form"><label for="token">管理令牌</label>
<input id="token" name="token" type="password" autocomplete="current-password" required>
<p id="login-error" class="error" role="alert"></p><button type="submit">进入 Memory Review</button>
</form></main><script nonce="__NONCE__">""" + _LOGIN_SCRIPT + "</script></body>",
    )


def render_review(page: ReviewPageData) -> str:
    filtered = _filter_records(
        page.records, query=page.query, scope=page.scope, status=page.status
    )
    changes = page.service.operations.list_changes(user_id=page.user_id, limit=80)
    jobs = page.service.operations.list_jobs(limit=30, user_id=page.user_id)
    snapshot_state = page.service.operations.snapshot_state()
    selected_history = _selected_history(
        page.service, page.selected, page.user_id
    )
    view = page.view if page.view in {"overview", "compare", "lifecycle", "facts", "sources", "privacy"} else "overview"
    body = _header(page.settings) + privacy_banner(page) + navigation(page.user_id, view) + '<main class="review-shell">'
    body += _filters(page, view)
    if view == "privacy":
        body += privacy_panel(page)
    elif view == "sources":
        body += render_journal_progress(page.service.journal, page.user_id, page.query)
    elif view == "facts":
        body += '<div class="layout"><section class="memory-column">'
        body += _memory_section(filtered, page.backend_ok, page.user_id, page.selected)
        body += '</section><aside class="audit-column">'
        body += _snapshot_panel(page.settings, snapshot_state) + _history_panel(selected_history)
        body += render_forget_plan(page.service, page.selected, page.user_id)
        body += _queue_panel(jobs) + _timeline_panel(changes) + '</aside></div>'
    else:
        body += render_organization(OrganizationPage(
            page.user_id, page.records, {item.id for item in filtered},
            page.service.operations.organization, page.backend_ok,
        ), view)
        if view == "lifecycle":
            body += '<div class="trace-grid">' + _timeline_panel(changes) + _queue_panel(jobs) + '</div>'
    body += '</main><div id="toast" role="status" aria-live="polite"></div>'
    body += f'<meta name="csrf-token" content="{html.escape(page.csrf, quote=True)}">'
    body += f'<script nonce="__NONCE__">{_REVIEW_SCRIPT}</script></body>'
    return _document("Memory Review", body)


def _header(settings: Settings) -> str:
    dashboard = html.escape(settings.mem0_dashboard_url, quote=True)
    return f"""<body><header class="masthead"><div><p class="eyebrow">RIJI · MEMORY REVIEW</p>
<h1>Agent 对你的长期认识</h1><p class="lede">看见记忆如何积累、如何改变，也保留重新理解的余地。</p>
</div><nav><a href="{dashboard}" target="_blank" rel="noreferrer">Mem0 Dashboard ↗</a>
<button class="text-button" data-action="logout">退出</button></nav></header>"""


def _filters(page: ReviewPageData, view: str) -> str:
    users = "".join(
        _option(item, item, item == page.user_id)
        for item in sorted(page.settings.allowed_feishu_user_ids)
    )
    form = f"""<form class="filters" method="get"><input type="hidden" name="view" value="{view}"><label>用户<select name="user_id">{users}</select></label>
<label class="search">搜索<input name="q" value="{html.escape(page.query, quote=True)}" placeholder="偏好、目标或事件"></label>
<label>范围<select name="scope">{_select_options(page.scope, ('all','shared','persona'))}</select></label>
<label>状态<select name="status">{_select_options(page.status, ('all','active','archived'))}</select></label>
<button type="submit">筛选</button></form>"""
    if view == "facts":
        return form
    opened = " open" if page.query or page.scope != "all" or page.status != "active" else ""
    return f'<details class="filter-details"{opened}><summary>搜索与筛选</summary>{form}</details>'


def _memory_section(
    records: Sequence[LongTermMemory],
    backend_ok: bool,
    user_id: str,
    selected: Optional[str],
) -> str:
    label = "在线" if backend_ok else "不可用，聊天将降级"
    cards = "".join(_memory_card(item, user_id, selected) for item in records)
    if not cards:
        cards = '<div class="empty"><span>∅</span><h2>没有匹配的记忆</h2><p>调整筛选条件，或等待下一次对话捕获。</p></div>'
    return f"""<div class="section-heading"><div><p class="eyebrow">CURRENT MEMORY</p>
<h2>{len(records)} 条当前结果</h2></div><span class="health {'ok' if backend_ok else 'bad'}">Mem0 {label}</span></div>
<div class="memory-list">{cards}</div>"""


def _memory_card(item: LongTermMemory, user_id: str, selected: Optional[str]) -> str:
    scope = "共享事实" if item.scope is MemoryScope.SHARED else "导师观察"
    persona = f" · {html.escape(item.persona_id or '')}" if item.persona_id else ""
    source = html.escape(str(item.metadata.get("source_type", "来源未知")))
    source_id = html.escape(str(item.metadata.get("source_id", "未知")))
    observed = html.escape(str(item.metadata.get("source_created_at", "未知")))
    states = {"current": "当前认识", "historical": "历史阶段", "conflict": "待复核冲突", "source_invalid": "依据失效"}
    state = states.get(item.metadata.get("journal_state"), "")
    effective = html.escape(str(item.metadata.get("valid_from") or "未知"))
    memory_id = html.escape(item.id, quote=True)
    content = html.escape(item.content)
    opened = " open" if selected == item.id else ""
    archived = item.status is MemoryStatus.ARCHIVED
    mutation, label = ("restore", "恢复") if archived else ("archive", "归档")
    return f"""<article class="memory-card {'archived' if archived else ''}">
<div class="memory-meta"><span>{scope}{persona} · {source}</span><time>{html.escape(item.updated_at or item.created_at or '时间未知')}</time></div>
<p class="memory-text">{content}</p>{memory_permission(item, user_id)}<p class="meta">来源：{observed} · {source_id} · {state} · 生效日期 {effective}</p><details{opened}><summary>依据、校勘与操作</summary>{render_journal_evidence(item)}
<form class="edit-form" data-memory="{memory_id}" data-user="{html.escape(user_id, quote=True)}">
<label>记忆内容<textarea name="content" rows="3">{content}</textarea></label><div class="actions">
<button type="submit">保存纠正</button><button type="button" class="quiet" data-mutation="{mutation}">{label}</button>
<button type="button" class="danger" data-mutation="delete">永久删除</button>
<a class="detail-link" href="?user_id={html.escape(user_id, quote=True)}&selected={memory_id}">查看历史</a></div></form>
<p class="memory-id">ID {memory_id}</p></details></article>"""


def _snapshot_panel(settings: Settings, state: dict[str, object]) -> str:
    preview = _snapshot_preview(settings.memory_snapshot_path)
    status = html.escape(str(state.get("status")))
    generated = html.escape(str(state.get("generated_at") or "尚未生成"))
    return f"""<section class="paper-panel"><div class="panel-heading"><h2>MEMORY.md</h2>
<button data-action="snapshot">重新生成</button></div><p class="meta">状态 {status} · {generated}</p>
<pre>{html.escape(preview)}</pre><a href="/admin/memory/snapshot">下载当前快照</a></section>"""


def _history_panel(history) -> str:
    if history is None:
        return ""
    rows = "".join(
        f'<li><b>{html.escape(item.event)}</b><span>{html.escape(item.created_at or "时间未知")}</span>'
        f'<p>{html.escape(item.after or item.before or "无正文")}</p></li>'
        for item in history
    ) or '<li class="muted">Mem0 尚无该条目的历史记录。</li>'
    return f'<section class="paper-panel"><h2>选中记忆历史</h2><ol class="timeline">{rows}</ol></section>'


def _queue_panel(jobs) -> str:
    rows = "".join(_job_row(job) for job in jobs)
    content = rows or '<p class="muted">队列为空。</p>'
    return f'<section class="paper-panel"><h2>捕获队列</h2>{content}</section>'


def _job_row(job) -> str:
    retry = ""
    if job.status.value == "dead_letter":
        retry = f'<button class="quiet" data-retry="{job.id}">重试</button>'
    return (
        f'<div class="job"><span>#{job.id} · {html.escape(job.status.value)}</span>'
        f'<small>尝试 {job.attempts} 次 · {html.escape(job.error_code or "无错误")}</small>{retry}</div>'
    )


def _timeline_panel(changes) -> str:
    rows = "".join(_change_row(item) for item in changes) or '<li class="muted">尚无记忆变更。</li>'
    return f'<section class="paper-panel"><h2>校勘时间线</h2><p class="meta">最近 80 条实际动作。比较与归纳建议见“比较与复核”。</p><ol class="timeline">{rows}</ol></section>'


def _change_row(item) -> str:
    labels = {"ADD": "新增事实", "UPDATE": "人工纠正", "ARCHIVE": "归档，退出召回",
              "RESTORE": "恢复召回", "DELETE": "永久删除", "RECONFIRM": "确认仍然有效",
              "DEDUP_SKIP": "完全相同，跳过写入", "NO_DURABLE_FACT": "未提取到长期事实"}
    label = labels.get(item.action, item.action)
    text = f'<p>{html.escape(item.after or item.before or "未写入正文")}</p>'
    if item.before and item.after and item.action in {"UPDATE", "DEDUP_SKIP"}:
        text = (f'<div class="change-diff"><p><b>原有记忆</b>{html.escape(item.before)}</p>'
                f'<p><b>{"纠正后" if item.action == "UPDATE" else "本次候选"}</b>{html.escape(item.after)}</p></div>')
    source = html.escape(item.source_request_id or "用户校勘")
    return (f'<li><b>{label}</b><span>{html.escape(item.created_at)}</span>{text}'
            f'<p class="source-ref">来源 {source} · 记忆 {html.escape(item.memory_id or "无")}</p></li>')


def _selected_history(
    service: MemoryService, memory_id: Optional[str], user_id: str
):
    if not memory_id:
        return None
    try:
        if service.backend.get(memory_id).user_id != user_id:
            return ()
        return service.backend.history(memory_id)
    except MemoryBackendError:
        return ()


def _filter_records(records: Sequence[LongTermMemory], *, query: str, scope: str, status: str) -> tuple[LongTermMemory, ...]:
    needle = query.strip().lower()
    return tuple(
        item for item in records
        if (not needle or needle in item.content.lower())
        and (scope == "all" or item.scope.value == scope)
        and (status == "all" or item.status.value == status
             and (status != "active" or item.metadata.get("journal_state") not in {"source_invalid", "pending", "deleted"}))
    )


def _select_options(selected: str, values: tuple[str, ...]) -> str:
    labels = {
        "all": "全部", "shared": "共享", "persona": "导师",
        "active": "有效", "archived": "归档",
    }
    return "".join(_option(value, labels[value], value == selected) for value in values)


def _option(value: str, label: str, selected: bool) -> str:
    return (
        f'<option value="{html.escape(value, quote=True)}" '
        f'{"selected" if selected else ""}>{html.escape(label)}</option>'
    )


def _snapshot_preview(path: Optional[Path]) -> str:
    if path is None or not path.is_file():
        return "MEMORY.md 尚未生成。"
    try:
        return path.read_text(encoding="utf-8")[:4000]
    except OSError:
        return "MEMORY.md 暂时不可读。"


def _document(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style nonce="__NONCE__">{_CSS}{ORGANIZATION_CSS}{PRIVACY_CSS}.review-shell .layout{{padding:0;max-width:none}}.review-shell .layout .filters{{display:none}}</style></head>{body}</html>"""


_LOGIN_SCRIPT = """
const form=document.querySelector('#login-form');
form.addEventListener('submit',async(event)=>{event.preventDefault();const error=document.querySelector('#login-error');error.textContent='';const response=await fetch('/admin/memory/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:form.token.value})});if(response.ok){location.href='/admin/memory'}else{error.textContent='令牌不正确。'}});
"""


_REVIEW_SCRIPT = """
const csrf=document.querySelector('meta[name="csrf-token"]').content;
const toast=document.querySelector('#toast');
function say(text){toast.textContent=text;toast.classList.add('show');setTimeout(()=>toast.classList.remove('show'),1800)}
async function post(url,payload={}){const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(payload)});if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.error||'request_failed')}return response.json()}
document.addEventListener('change',async(event)=>{const field=event.target;if(!field.dataset.memoryPermission)return;try{await post('/admin/memory/api/journal/permission',{user_id:field.dataset.user,memory_id:field.dataset.memoryPermission,permission:field.value});location.reload()}catch(error){say(error.message)}});
document.addEventListener('submit',async(event)=>{const form=event.target;if(form.matches('.privacy-form')){event.preventDefault();const permissions=Object.fromEntries(['history','incremental','organization','recall'].map(key=>[key,form.elements[key].checked]));try{await post('/admin/memory/api/journal/authorize',{user_id:form.dataset.user,binding:form.dataset.binding,permissions,acknowledged:form.elements.acknowledged.checked});location.reload()}catch(error){say(error.message)}return}if(form.matches('.forget-form')){event.preventDefault();if(prompt('将永久遗忘勾选范围。输入 DELETE 确认：')!=='DELETE')return;const selected=(name)=>Array.from(form.querySelectorAll('input[name='+name+']:checked')).map(input=>input.value);try{await post('/admin/memory/api/forget',{base_id:form.dataset.base,user_id:form.dataset.user,plan_hash:form.dataset.plan,memory_ids:selected('memory_ids'),evidence_ids:selected('evidence_ids'),confirmation:'DELETE'});location.href='?view=facts'}catch(error){say(error.message)}return}if(!form.matches('.edit-form'))return;event.preventDefault();try{await post(`/admin/memory/api/memories/${encodeURIComponent(form.dataset.memory)}/update`,{user_id:form.dataset.user,content:form.content.value});location.reload()}catch(error){say(`保存失败：${error.message}`)}});
document.addEventListener('click',async(event)=>{const button=event.target.closest('button');if(!button)return;try{if(button.dataset.journalAction){button.disabled=true;await post('/admin/memory/api/journal/'+encodeURIComponent(button.dataset.journalAction),{user_id:button.dataset.user});location.reload()}else if(button.dataset.action==='organize'){button.disabled=true;button.textContent='正在提交…';await post('/admin/memory/api/organize',{user_id:button.dataset.user});location.reload()}else if(button.dataset.action==='snapshot'){await post('/admin/memory/api/snapshot');location.reload()}else if(button.dataset.action==='logout'){await post('/admin/memory/logout');location.href='/admin/memory/login'}else if(button.dataset.retry){await post(`/admin/memory/api/jobs/${button.dataset.retry}/retry`);location.reload()}else if(button.dataset.mutation){const form=button.closest('.edit-form');let confirmation;if(button.dataset.mutation==='delete'){confirmation=prompt('永久删除不可恢复。请输入 DELETE 确认：');if(confirmation!=='DELETE')return}await post(`/admin/memory/api/memories/${encodeURIComponent(form.dataset.memory)}/${button.dataset.mutation}`,{user_id:form.dataset.user,confirmation});location.reload()}}catch(error){button.disabled=false;if(button.dataset.action==='organize')button.textContent='重新整理';say(`操作失败：${error.message}`)}});
"""


_CSS = """
:root{--parchment:#f3efe5;--paper:#fbf8f0;--ink:#26231f;--pencil:#6e675e;--faint:#a9a094;--rule:rgba(55,45,35,.14);--inset:#ece5d8;--vermillion:#a63d2f;--moss:#566c57;--shadow:0 0 0 1px rgba(55,45,35,.06),0 10px 30px rgba(55,45,35,.07);font-family:"Avenir Next","PingFang SC",sans-serif;color:var(--ink);background:var(--parchment)}
*{box-sizing:border-box}body{margin:0;min-height:100vh;-webkit-font-smoothing:antialiased}button,input,select,textarea{font:inherit}button,a{min-height:40px}button{border:0;border-radius:6px;background:var(--ink);color:var(--paper);padding:9px 14px;font-weight:600;cursor:pointer;transition:transform 120ms cubic-bezier(.23,1,.32,1),opacity 150ms}button:hover{opacity:.84}button:active{transform:scale(.97)}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid rgba(166,61,47,.26);outline-offset:2px}.text-button,.quiet{background:transparent;color:var(--ink);box-shadow:inset 0 0 0 1px var(--rule)}.danger{background:var(--vermillion)}a{color:var(--vermillion);text-underline-offset:3px}.masthead{max-width:1480px;margin:auto;padding:48px 40px 28px;display:flex;align-items:flex-end;justify-content:space-between;gap:32px;border-bottom:1px solid var(--rule)}.masthead nav{display:flex;align-items:center;gap:16px}.eyebrow{margin:0 0 8px;font-size:11px;font-weight:700;letter-spacing:.16em;color:var(--pencil)}h1,h2{font-family:"Songti SC","STSong",serif;font-weight:600;text-wrap:balance}h1{margin:0;font-size:38px;line-height:1.15;letter-spacing:-.025em}h2{margin:0;font-size:22px}.lede{max-width:680px;margin:12px 0 0;color:var(--pencil);line-height:1.65;text-wrap:pretty}.layout{max-width:1480px;margin:auto;padding:32px 40px 72px;display:grid;grid-template-columns:minmax(0,1.7fr) minmax(310px,.8fr);gap:40px}.filters{display:grid;grid-template-columns:160px minmax(220px,1fr) 120px 120px auto;gap:12px;align-items:end;padding:16px;background:rgba(255,255,255,.25);border:1px solid var(--rule);border-radius:10px}.filters label,.edit-form label,.login-sheet label{display:grid;gap:6px;font-size:12px;font-weight:650;color:var(--pencil)}input,select,textarea{width:100%;border:1px solid var(--rule);border-radius:6px;background:var(--inset);color:var(--ink);padding:10px 11px}.section-heading{display:flex;justify-content:space-between;align-items:end;margin:36px 0 16px}.health{font-size:12px;color:var(--pencil)}.health:before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--vermillion);margin-right:7px}.health.ok:before{background:var(--moss)}.memory-list{display:grid;gap:12px}.memory-card{background:var(--paper);padding:20px 22px;border-radius:8px;box-shadow:var(--shadow);border-left:3px solid transparent}.forget-choices label{display:block;margin:12px 0;font-size:13px;line-height:1.6}.forget-choices input{width:auto;margin-right:6px}.source-table{overflow:auto;margin-top:20px}.source-table table{border-collapse:collapse;width:100%;text-align:left;font-size:13px}.source-table th,.source-table td{padding:12px;border-bottom:1px solid var(--rule);overflow-wrap:anywhere}.memory-card.archived{opacity:.62;border-left-color:var(--faint)}.memory-meta{display:flex;justify-content:space-between;gap:16px;color:var(--pencil);font-size:11px}.memory-text{font-family:"Songti SC","STSong",serif;font-size:19px;line-height:1.7;margin:13px 0;text-wrap:pretty}.memory-card summary{cursor:pointer;color:var(--pencil);font-size:12px;min-height:40px;display:flex;align-items:center}.edit-form{border-top:1px solid var(--rule);padding-top:16px}.actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:12px}.detail-link{display:inline-flex;align-items:center;margin-left:auto}.memory-id{font:11px ui-monospace,SFMono-Regular,monospace;color:var(--faint);overflow-wrap:anywhere}.audit-column{display:grid;align-content:start;gap:20px}.paper-panel{background:rgba(251,248,240,.72);border:1px solid var(--rule);border-radius:10px;padding:20px}.panel-heading{display:flex;justify-content:space-between;align-items:center;gap:12px}.paper-panel pre{max-height:320px;overflow:auto;background:var(--inset);padding:14px;border-radius:6px;white-space:pre-wrap;font:11px/1.6 ui-monospace,SFMono-Regular,monospace}.meta,.muted{color:var(--pencil);font-size:12px}.timeline{list-style:none;padding:0;margin:16px 0 0}.timeline li{position:relative;padding:0 0 20px 22px;border-left:1px solid var(--rule)}.timeline li:before{content:"";position:absolute;left:-4px;top:3px;width:7px;height:7px;border-radius:50%;background:var(--pencil)}.timeline b{font-size:11px;letter-spacing:.08em}.timeline span{float:right;color:var(--faint);font-size:10px}.timeline p{margin:6px 0 0;color:var(--pencil);font-size:12px;line-height:1.55}.job{display:grid;grid-template-columns:1fr auto;gap:3px 8px;border-bottom:1px solid var(--rule);padding:10px 0;font-size:12px}.job small{color:var(--pencil)}.job button{grid-column:2;grid-row:1/3}.empty{text-align:center;padding:72px 24px;color:var(--pencil)}.empty span{font-family:serif;font-size:40px}.login-shell{display:grid;place-items:center;padding:24px}.login-sheet{width:min(480px,100%);background:var(--paper);padding:40px;border-radius:10px;box-shadow:var(--shadow)}.login-sheet form{display:grid;gap:16px;margin-top:28px}.error{min-height:20px;color:var(--vermillion);margin:0;font-size:12px}#toast{position:fixed;right:24px;bottom:24px;background:var(--ink);color:var(--paper);padding:12px 16px;border-radius:6px;opacity:0;transform:translateY(8px);pointer-events:none;transition:opacity 180ms,transform 180ms}#toast.show{opacity:1;transform:none}@media(max-width:900px){.masthead{padding:28px 20px;align-items:flex-start;flex-direction:column}.layout{padding:20px;grid-template-columns:1fr}.filters{grid-template-columns:1fr 1fr}.search{grid-column:1/-1}.memory-meta{flex-direction:column;gap:4px}h1{font-size:30px}}@media(prefers-reduced-motion:reduce){*{transition:none!important}}
"""
