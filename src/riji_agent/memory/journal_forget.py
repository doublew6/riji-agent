"""Reviewable, explicitly selected scopes for broader memory forgetting."""

from __future__ import annotations

import html
import json
from typing import Any

from riji_agent.memory.backend import MemoryBackendError
from riji_agent.memory.journal_types import fingerprint, utc_now
from riji_agent.memory.organization import memory_version
from riji_agent.memory.service import MemoryService


def forget_plan(service: MemoryService, memory_id: str, user_id: str) -> dict[str, Any]:
    base = service._owned(memory_id, user_id)
    engine = service.journal
    if engine is None or user_id != engine.policy.user_id:
        raise ValueError("journal_memory_unavailable")
    refs = engine.store.rows("SELECT e.id,e.payload FROM evidence e JOIN supports s ON s.evidence_id=e.id WHERE s.memory_id=?", (memory_id,))
    identifiers = {memory_id}
    evidence = []
    for ref in refs:
        payload = json.loads(ref["payload"])
        ids = [row["memory_id"] for row in engine.store.rows("SELECT memory_id FROM supports WHERE evidence_id=?", (ref["id"],))]
        identifiers.update(ids)
        evidence.append({"id": ref["id"], "path": payload["path"], "section": payload["section"], "line": payload["line"], "memory_ids": ids})
    for row in engine.store.rows("SELECT source_id,target_id FROM relations WHERE source_id=? OR target_id=?", (memory_id, memory_id)):
        identifiers.update((row["source_id"], row["target_id"]))
    memories = []
    if len(identifiers) > 100 or len(evidence) > 100:
        raise ValueError("forget_scope_too_large_select_individually")
    for mid in sorted(identifiers):
        try:
            item = service._owned(mid, user_id)
        except MemoryBackendError as exc:
            if exc.code == "memory_not_found":
                continue
            raise
        if item.scope is base.scope and item.persona_id == base.persona_id:
            memories.append({"id": item.id, "content": item.content, "version": memory_version(item)})
    allowed = {item["id"] for item in memories}
    for ref in evidence:
        ref["memory_ids"] = [mid for mid in ref["memory_ids"] if mid in allowed]
    result = {"base_id": memory_id, "user_id": user_id, "memories": memories, "evidence": evidence}
    return dict(result, plan_hash=fingerprint(json.dumps(result, sort_keys=True, ensure_ascii=False)))


def apply_forget_plan(service: MemoryService, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("confirmation") != "DELETE":
        raise ValueError("delete_confirmation_required")
    if not all(isinstance(payload.get(key), str) for key in ("base_id", "user_id", "plan_hash")):
        raise ValueError("invalid_forget_scope")
    plan = forget_plan(service, payload["base_id"], payload["user_id"])
    if payload["plan_hash"] != plan["plan_hash"]:
        raise ValueError("forget_scope_changed_reload")
    selected = _selection(payload.get("memory_ids"), {row["id"] for row in plan["memories"]})
    evidence_ids = _selection(payload.get("evidence_ids"), {row["id"] for row in plan["evidence"]})
    for ref in plan["evidence"]:
        if ref["id"] in evidence_ids:
            selected.update(ref["memory_ids"])
    if not selected:
        raise ValueError("empty_forget_scope")
    for eid in evidence_ids:
        _suppress_evidence(service, eid)
    pending = []
    for mid in sorted(selected):
        try:
            service.delete_memory(mid, user_id=plan["user_id"])
        except MemoryBackendError as exc:
            if exc.code != "memory_cleanup_pending":
                raise
            pending.append(mid)
    return {"ok": True, "selected_count": len(selected), "cleanup_pending": pending}


def _selection(value: Any, allowed: set[str]) -> set[str]:
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(item, str) or item not in allowed for item in value):
        raise ValueError("invalid_forget_scope")
    return set(value)


def _suppress_evidence(service: MemoryService, evidence_id: str) -> None:
    service.journal.store.execute("INSERT OR IGNORE INTO suppression VALUES ('evidence',?,?)", (evidence_id, utc_now()))
    service.journal.store.execute("UPDATE evidence SET status='suppressed',token=NULL,extracted=NULL,decisions=NULL,"
                                  "payload=json_remove(payload,'$.text') WHERE id=?", (evidence_id,))


def render_forget_plan(service: MemoryService, memory_id: str | None, user_id: str) -> str:
    if not memory_id or service.journal is None or user_id != service.journal.policy.user_id:
        return ""
    try:
        plan = forget_plan(service, memory_id, user_id)
    except (MemoryBackendError, ValueError):
        return '<section class="paper-panel"><p>该记忆的遗忘范围暂不可读，可通过单条管理操作处理。</p></section>'
    escape = lambda value: html.escape(str(value), quote=True)
    memories = "".join(f'<label><input type="checkbox" name="memory_ids" value="{escape(row["id"])}" '
                       f'{"checked" if row["id"] == memory_id else ""}> {escape(row["content"])}</label>' for row in plan["memories"])
    evidence = "".join(f'<label><input type="checkbox" name="evidence_ids" value="{ref["id"]}"> '
                       f'{escape(ref["path"])} · {escape(ref["section"])} · 第 {ref["line"]} 行（{len(ref["memory_ids"])} 条记忆）</label>'
                       for ref in plan["evidence"])
    return f'''<section class="paper-panel"><h2>选择遗忘范围</h2><p class="meta">只处理勾选项。选片段会删除该片段当前派生的全部记忆，并抑制这一版本再次提取；日记原文与其他备份保留。</p>
<form class="forget-form" data-base="{escape(memory_id)}" data-user="{escape(user_id)}" data-plan="{plan["plan_hash"]}">
<div class="forget-choices">{memories}<p>可选的来源片段</p>{evidence}</div><button class="danger" type="submit">按选择永久遗忘</button></form></section>'''
