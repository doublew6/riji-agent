"""Persistent privacy status and explicit, purpose-specific authorization controls."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from riji_agent.memory.journal_privacy import PURPOSES

LABELS = {"history": "历史日记初始化", "incremental": "新增和修改日记自动提取",
          "organization": "云端整理相关记忆", "recall": "导师回答使用日记记忆"}


def destination(value: str) -> str:
    parsed = urlsplit(value)
    return html.escape(f"{parsed.scheme}://{parsed.hostname or '未配置'}" + (f":{parsed.port}" if parsed.port else ""))


def processing_target(provider: str, endpoint: str, model: str) -> str:
    label = "OpenAI / Codex（ChatGPT 套餐）" if provider == "codex" else provider
    return f"{html.escape(label)} · {destination(endpoint)} / {html.escape(model)}"


def initialization_progress(initialization: dict[str, Any]) -> str:
    """Describe fixed-batch outcomes independently of the automatic-processing switch."""
    if not initialization["enabled"]:
        return ""
    total, completed = initialization["total"], initialization["completed"]
    pending, failed = initialization["pending"], initialization["failed"]
    excluded = initialization["excluded"]
    coverage = f"（{completed}/{total} 个片段）"
    if not total:
        extraction = "历史提取尚未开始"
    elif completed == total:
        extraction = "历史提取完成" + coverage
    else:
        extraction = "历史提取已结束" if not pending and not failed else "历史提取未完成"
        extraction += coverage + f"：待处理 {pending}、失败 {failed}、排除 {excluded}"
    seeds = initialization["organization"]
    failed = seeds.get("failed", 0) + seeds.get("retry_failed", 0)
    pending = seeds.get("pending", 0) + seeds.get("spent", 0)
    recovery = seeds.get("retry_pending", 0) + seeds.get("retry_spent", 0)
    if failed:
        organization = f"本批整理有 {failed} 项失败"
    elif pending or recovery:
        organization = "本批整理未完成"
    elif seeds.get("done", 0) + seeds.get("retry_done", 0):
        organization = "本批整理已结束"
    else:
        organization = "本批暂无待处理的整理任务"
    if pending or recovery:
        organization += f"（待处理 {pending}、恢复中 {recovery}）"
    elif failed:
        organization += "（无恢复任务，不会自动重试）"
    return f"{extraction} · {organization}"


def organization_wait_status(page: Any) -> str:
    run = page.service.operations.organization.latest(page.user_id)
    if run is None or run["status"] not in {"pending", "processing"}:
        return ""
    if run["status"] == "processing":
        return "后台整理正在处理"
    if run["error_code"] == "journal_daily_budget":
        return "后台整理等待日常额度（每日 00:00 UTC 重置）"
    return "后台整理待处理"


def budget_summary(engine: Any) -> str:
    initialization = engine.initialization_status()
    day = "day:" + datetime.now(timezone.utc).date().isoformat()
    rows = engine.store.rows("SELECT chars FROM budgets WHERE key=?", (day,))
    used = rows[0]["chars"] if rows else 0
    daily = f"日常处理每日上限 {engine.policy.daily_chars} 字符（UTC 日），当日已用 {used} 字符"
    if not initialization["enabled"]:
        return daily
    if initialization.get("recovery_active"):
        mode = "本次固定批次有限恢复不限每日总量（每个失败 ID / 版本仅一次）"
    elif initialization["active"]:
        mode = "本次初始化不限每日总量"
    elif initialization["state"] == "completed":
        mode = "本次初始化已结束，不限额处理已关闭"
    elif initialization["state"] == "blocked":
        mode = "本次初始化处理已结束，不限额处理已关闭"
    else:
        mode = "初始化不限额处理尚未生效或已停止"
    state = {"active": "初始处理", "blocked": "有失败待处理", "completed": "已结束",
             "cancelled": "已停止", "awaiting_scan": "等待扫描"}.get(initialization["state"], "未启用")
    counts = (f'原批次状态：{state}；固定批次 {initialization["total"]} 个片段：完成 {initialization["completed"]}、'
              f'待处理 {initialization["pending"]}、排除 {initialization["excluded"]}、失败 {initialization["failed"]}')
    seeds = initialization["organization"]
    organization = (f'记忆整理：完成 {seeds.get("done", 0) + seeds.get("retry_done", 0)}、'
                    f'待处理 {seeds.get("pending", 0) + seeds.get("spent", 0)}、'
                    f'失败 {seeds.get("failed", 0) + seeds.get("retry_failed", 0)}、'
                    f'恢复队列 {seeds.get("retry_pending", 0) + seeds.get("retry_spent", 0)}、'
                    f'排除 {seeds.get("excluded", 0)}')
    recovery = (f'其中适用初始化账本的有限恢复：待发送 {initialization.get("recovery_pending", 0)}、'
                f'已发送待结果 {initialization.get("recovery_spent", 0)}；'
                '恢复仍须满足当前范围和权限，普通整理计入日常上限')
    return f'{mode}；{counts}；{organization}；{recovery}；初始化累计已计量 {initialization["request_chars"]} 字符；{daily}'


def privacy_banner(page: Any) -> str:
    engine = page.service.journal
    if engine is None or engine.policy.user_id != page.user_id:
        return '<aside class="privacy-banner"><strong>隐私 · 日记长期记忆未启用</strong><span>本地保存；聊天与回复仍经过配置的模型和飞书。</span></aside>'
    status, policy = engine.privacy.status(), engine.policy
    paused = engine.store.get_control("paused") == "1"
    state = "待授权 / 授权范围已变化" if not status["valid"] else "自动处理已暂停" if paused else "自动处理已开启"
    flags = " · ".join(f'{LABELS[key]}：{"允许" if status["permissions"][key] else "关闭"}' for key in PURPOSES)
    user = html.escape(page.user_id, quote=True)
    action, label = ("resume", "继续自动处理") if paused else ("pause", "暂停自动处理")
    progress = initialization_progress(engine.initialization_status())
    waiting = organization_wait_status(page) if status["valid"] and not paused else ""
    return f'''<aside class="privacy-banner" aria-label="当前隐私权限">
<div><strong>隐私 · {state}</strong>
{f'<strong class="processing-progress">{html.escape(progress)}</strong>' if progress else ''}
{f'<span>{html.escape(waiting)}</span>' if waiting else ''}
<span>自动处理开关用于控制日记提取与整理；历史完成后，仍可处理已授权的新增和修改。</span>
<span>本地保存 · 授权片段与相关记忆仍会出云</span>
<span>提取与整理：{processing_target(policy.extraction_provider, policy.extraction_destination, policy.extraction_model)}</span>
<span>导师回答：{processing_target(policy.recall_provider, policy.recall_destination, policy.recall_model)}</span>
<span>{html.escape(budget_summary(engine))}</span>
<span>{html.escape('、'.join(policy.sections))} · {policy.date_from or '不限起始'} 至 {policy.date_to or '不限结束'} · {flags}</span></div>
<div class="actions"><a href="?view=privacy&amp;user_id={user}">查看与修改权限</a>
<button data-journal-action="{action}" data-user="{user}">{label}</button>
<button class="danger" data-journal-action="revoke" data-user="{user}">撤回日记授权</button></div></aside>'''


def privacy_panel(page: Any) -> str:
    engine = page.service.journal
    if engine is None or engine.policy.user_id != page.user_id:
        return ""
    status, policy = engine.privacy.status(), engine.policy
    checks = "".join(f'<label><input type="checkbox" name="{key}" {"checked" if status["permissions"][key] else ""}> {LABELS[key]}</label>' for key in PURPOSES)
    events = engine.store.rows("SELECT phase,request_chars,created_at,refs FROM egress_attempts ORDER BY id DESC LIMIT 10")
    rows = "".join(f'<tr><td>{html.escape(row["created_at"])}</td><td>{html.escape(row["phase"])}</td><td>{row["request_chars"]}</td><td>{html.escape("、".join(ref[:12] for ref in json.loads(row["refs"])))}</td></tr>' for row in events)
    return f'''<section class="paper-panel"><h2>日记记忆权限与数据流</h2>
<p>日记和记忆由你保存。云端提取会接收授权日记的原文片段；关系判断和主动整理还会接收相关记忆。</p>
<p>提取与整理：{processing_target(policy.extraction_provider, policy.extraction_destination, policy.extraction_model)}<br>
导师回答：{processing_target(policy.recall_provider, policy.recall_destination, policy.recall_model)}<br>
可使用日记记忆的导师：{html.escape('、'.join(policy.mentors) or '当前测试导师')}<br>
本地来源：{html.escape(str(policy.root))}<br>区块：{html.escape('、'.join(policy.sections))}；日期：{policy.date_from or '不限起始'} 至 {policy.date_to or '不限结束'}<br>
每片段 {policy.segment_chars} 字符，同一来源版本累计 {policy.source_chars} 字符。<br>
<strong>{html.escape(budget_summary(engine))}</strong></p>
<p>启用初始化不限额时，仅本次固定来源版本的提取、关系判断及必要整理使用初始化账本，仍逐次记录发送量。
本批失败整理按精确 ID / 版本显式登记的一次恢复也使用初始化账本，不重新开放原批次；恢复完成或失败后不能自动再试。
后续新增或修改的日记按日常预算处理；原批次及有限恢复达到终态后关闭相应不限额处理，旧账本不清零或追溯转移。
暂停、撤权、私密标记、单片段和来源版本限制始终生效。</p>
<p>这些额度控制发送量，不是脱敏保证。飞书会接触经它发送的消息和回答；你自己的 iCloud 等同步与备份也可能保存副本。</p>
<form class="privacy-form" data-user="{html.escape(page.user_id, quote=True)}" data-binding="{status['binding']}">
{checks}<label><input type="checkbox" name="acknowledged" required> 我理解上述内容会交给所列模型服务处理，并授权勾选的用途。</label>
<button type="submit">保存这份授权</button></form>
<p>保存授权后仍需点击“继续自动处理”。历史范围为保存授权时已发现的文件版本；其后的新增和修改由增量开关控制。
暂停只停止日记提取和整理，已有记忆仍按召回权限使用；撤回授权同时停止日记记忆的新云端使用。
已发送请求无法撤回，服务商的留存和训练规则需以实际使用的服务和账号数据设置为准。
Codex 使用 OpenAI 云端推理及 ChatGPT 套餐额度；临时会话只减少本地会话文件，不代表云端零留存。
服务使用独立的本地 Codex 登录目录，首次需要官方登录；之后由官方客户端保存凭据并续期，不复制个人 Codex 凭据文件。
额度不足或登录失效时保留任务、延迟重试并显示原因，不自动切到按量付费的模型。</p>
<h3>内容的三级权限</h3><p><code>memory: none</code> 不参与记忆；<code>memory: local</code> 仅本地；<code>memory: cloud</code> 在总授权内允许云端处理。
这些值写在日记开头的 YAML 属性中；未标注时沿用已授权范围。<code>private: true</code> 始终排除。</p>
<p>局部排除：用 <code>&lt;!-- riji-memory:local --&gt;</code> 或 <code>&lt;!-- riji-memory:none --&gt;</code> 开始，
用 <code>&lt;!-- /riji-memory --&gt;</code> 结束。当前没有本地生成模型，仅本地内容跳过云端提取；记忆条目也可单独设置权限。</p>
<p>原生聊天自动捕获：{"开启" if page.settings.memory_auto_capture else "关闭"}，由独立配置控制；本页撤回日记授权不会停止正常聊天、原生聊天捕获或原有受限日记问答。日记私密标记同样约束原有检索工具。</p>
<h3>最近日记模型请求</h3><p>只记录用途、来源标识、时间及字符量，不复制正文；记录代表请求尝试，不保证模型处理成功。</p>
<div class="source-table"><table><thead><tr><th>时间（UTC）</th><th>用途</th><th>请求字符</th><th>来源标识（缩略）</th></tr></thead><tbody>{rows or '<tr><td colspan="4">尚无请求</td></tr>'}</tbody></table></div>
<p>已删除的本地记忆不会自动撤回服务商收到的内容，也不会删除你独立保留的原日记和旧备份。</p></section>'''


def memory_permission(item: Any, user_id: str) -> str:
    current = item.metadata.get("privacy", "cloud")
    options = "".join(f'<option value="{value}" {"selected" if value == current else ""}>{label}</option>'
                      for value, label in (("cloud", "允许云端（仍受来源及总授权限制）"), ("local", "仅本地保存"), ("none", "不参与记忆使用")))
    return f'<label class="memory-permission">记忆权限<select data-memory-permission="{html.escape(item.id, quote=True)}" data-user="{html.escape(user_id, quote=True)}">{options}</select></label>'


PRIVACY_CSS = """
.privacy-banner{position:sticky;top:0;z-index:20;background:#fff3d6;border:1px solid #ba9454;border-left:5px solid #936018;padding:12px 20px;display:flex;gap:16px;align-items:center;justify-content:space-between;box-shadow:0 3px 12px #33221116}
.privacy-banner strong{display:block;font-size:16px}.privacy-banner span{display:block;font-size:12px;line-height:1.6;margin-top:3px}.privacy-banner .actions{flex-shrink:0;margin:0}.privacy-form{display:grid;gap:14px;padding:20px;background:var(--inset);border-radius:8px}.privacy-form input{width:auto}.privacy-form label{line-height:1.6}.memory-permission{display:block;font-size:12px;margin:12px 0}.memory-permission select{margin-top:6px;max-width:100%}
@media(max-width:900px){.privacy-banner{flex-direction:column;align-items:stretch;padding:10px 16px;gap:8px}.privacy-banner .actions{gap:8px}.privacy-banner span{font-size:11px}}
"""
