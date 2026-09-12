"""Bounded cross-batch organization and evidence-checked observation recall."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Sequence

from riji_agent.memory.backend import MemoryBackendError
from riji_agent.memory.capture import contains_credentials
from riji_agent.memory.journal_types import JournalMemoryError, fingerprint
from riji_agent.memory.models import LongTermMemory, MemoryScope, MemoryStatus
from riji_agent.memory.model_call import complete_json_guarded, deferred_model_error
from riji_agent.memory.organization import CATEGORIES, _PROMPT, _parse_report, active_records, memory_fingerprint, memory_version
from riji_agent.memory.organization_store import ORGANIZATION_PROVIDER_ERROR_CODES, OrganizationStore
from riji_agent.models.types import LLMError, LLMProvider

_OBSERVATIONS = """
输入包含跨批次检索的相关事实，主动寻找反例，不把相似表达、同一事件的聊天/日记/周月汇总当多次经历。
额外输出 observations 数组（最多3条），每项为：
{"summary":"最多240字，明确是待验证观察","support_ids":["独立事件1","独立事件2"],
"counter_ids":["反例ID"],"limitation":"最多180字，说明适用条件及反例，不能留空"}。
只有至少两个独立、明确发生的经历才可归纳；输入有 independent_days 标注有效日记经历日期，
同日材料保守视为同一经历，汇总不计入独立次数。没有足够依据则 observations=[]。
support_ids 只能引用 independent_days 非空的记忆，且这些日期合起来至少有两个不同日期。
observations=[] 不影响主题整理；topics 仍须完整覆盖所有输入记忆，每个 ID 恰好出现一次。
不要把观察再次当作事实证据。检查整个输入，重要反例必须进入 counter_ids 和 limitation。
"""


_VALIDATION_ERROR_CODES = {
    "invalid_organization": "organization_invalid_report",
    "incomplete_organization": "organization_incomplete_report",
    "invalid_entry": "organization_invalid_entry",
    "invalid_evidence": "organization_invalid_evidence",
    "invalid_category": "organization_invalid_category",
    "invalid_comparison": "organization_invalid_comparison",
    "invalid_organization_text": "organization_invalid_text",
    "invalid_observations": "organization_invalid_observations",
    "invalid_observation_evidence": "organization_invalid_observation_evidence",
    "insufficient_independent_evidence": "organization_insufficient_independent_evidence",
}


def _organization_error_code(error: Exception) -> str:
    """Classify fixed diagnostics without serializing arbitrary exception values."""
    if isinstance(error, json.JSONDecodeError):
        return "organization_invalid_json"
    code = error.args[0] if len(error.args) == 1 and type(error.args[0]) is str else None
    if isinstance(error, LLMError) and code in ORGANIZATION_PROVIDER_ERROR_CODES:
        return code
    if isinstance(error, ValueError):
        return _VALIDATION_ERROR_CODES.get(code, "organization_failed")
    return "organization_failed"


def _eligible_seed(item: LongTermMemory) -> bool:
    return (item.metadata.get("journal_kind") != "observation"
            and 0 < len(item.content) <= 2000 and not contains_credentials(item.content))


def _organization_schema(ids: Sequence[str],
                         independent_days: dict[str, Sequence[str]] | None = None) -> dict[str, Any]:
    days = independent_days or {}
    supports = [mid for mid in ids if days.get(mid)]
    can_observe = len(supports) >= 2 and len({day for mid in supports for day in days[mid]}) >= 2

    def text(maximum: int) -> dict[str, Any]:
        return {"type": "string", "minLength": 1, "maxLength": maximum}

    def references(minimum: int = 0, maximum: int | None = None,
                   allowed: Sequence[str] | None = None) -> dict[str, Any]:
        choices = ids if allowed is None else allowed
        return {"type": "array", "items": {"type": "string", "enum": list(choices)},
                "minItems": minimum, "maxItems": len(choices) if maximum is None else maximum}

    def entry(properties: dict[str, Any]) -> dict[str, Any]:
        return {"type": "object", "properties": properties, "required": list(properties),
                "additionalProperties": False}

    return entry({
        "topics": {"type": "array", "maxItems": 20, "items": entry({
            "category": {"type": "string", "enum": sorted(CATEGORIES)},
            "title": text(40), "summary": text(300), "evidence_ids": references(1)})},
        "comparisons": {"type": "array", "maxItems": 30, "items": entry({
            "kind": {"type": "string", "enum": ["duplicate", "conflict", "update", "related"]},
            "evidence_ids": references(2, 2), "reason": text(240)})},
        "time_bound_ids": references(),
        "observations": {"type": "array", "maxItems": 3 if can_observe else 0, "items": entry({
            "summary": text(240), "support_ids": references(2, max(2, len(supports)), supports or ids),
            "counter_ids": references(), "limitation": text(180)})},
    })


class JournalOrganization:
    def __init__(self, backend: Any, store: OrganizationStore, provider: LLMProvider) -> None:
        self.backend, self.store, self.provider = backend, store, provider
        self.engine = backend.engine

    def process(self, run: dict[str, Any]) -> None:
        pending_before = self._pending_initialization()
        try:
            self._check_enabled()
            records = [item for item in active_records(self.backend.export_memories(user_id=run["user_id"]), run["user_id"])
                       if self.engine.can_send(item, "organization")]
            report = self._organize(records)
            self.store.finish(run["id"], report)
            if report["remaining"]:
                self.store.request_if_idle(run["user_id"])
        except JournalMemoryError as exc:
            delay = 3600 if exc.code == "journal_daily_budget" else 60
            self.store.defer(run["id"], delay, exc.code)
        except Exception as exc:
            deferred = deferred_model_error(exc)
            if deferred is not None:
                code, delay = deferred
                self.store.defer(run["id"], delay, code)
            else:
                self.store.finish(run["id"], None, error_code=_organization_error_code(exc))
                # Only durable progress permits automatic continuation. A failure
                # before sending must not create a tight backend-error retry loop.
                if pending_before - self._pending_initialization():
                    self.store.request_if_idle(run["user_id"])
        finally:
            self.engine.advance_initialization()

    def _pending_initialization(self) -> set[tuple[str, str]]:
        if self.engine.store.get_control("initialization_scope") != self.engine.policy.scope_id:
            return set()
        states = ("pending", "retry_pending") if self.engine.store.initialization_active(self.engine.policy) else ("retry_pending",)
        return {(row["id"], row["version"]) for row in self.engine.store.rows(
            "SELECT id,version,state FROM initialization_seeds") if row["state"] in states}

    def resume_initialization(self) -> bool:
        """Requeue fixed pending work, including explicitly authorized daily retries."""
        self._check_enabled()
        if not self._pending_initialization():
            return False
        return self.store.request_if_idle(self.engine.policy.user_id) is not None

    def _check_enabled(self) -> None:
        self.engine.privacy.check("organization")
        if not self.engine.policy.enabled or self.engine.store.get_control("paused") == "1":
            raise JournalMemoryError("journal_memory_paused")

    def _organize(self, records: Sequence[LongTermMemory]) -> dict[str, Any]:
        known = {item.id: item for item in records}
        self.engine.store.exclude_initialization_seeds({item.id: memory_version(item) for item in records if _eligible_seed(item)})
        latest = self.store.latest(self.engine.policy.user_id, ready_only=True)
        previous = latest["report"] if latest else {}
        covered = {mid: version for mid, version in (previous or {}).get("covered_seeds", {}).items()
                   if mid in known and memory_version(known[mid]) == version}
        covered.update({item.id: memory_version(item) for item in records if not _eligible_seed(item)})
        groups = [group for group in (previous or {}).get("groups", [])
                  if all(mid in known and memory_version(known[mid]) == version
                         for mid, version in group.get("versions", {}).items()) and group.get("versions")]
        for row in self.engine.store.rows("SELECT id,version,report_json FROM initialization_seeds WHERE state IN ('done','retry_done') AND report_json IS NOT NULL"):
            group = json.loads(row["report_json"])
            if (row["id"] in known and memory_version(known[row["id"]]) == row["version"]
                    and all(mid in known and memory_version(known[mid]) == version for mid, version in group["versions"].items())):
                groups = [old for old in groups if old["versions"] != group["versions"]] + [group]
                covered[row["id"]] = row["version"]
        self.engine.store.settle_initialization_coverage(covered)
        stopped = {row["id"]: row["version"] for row in self.engine.store.rows(
            "SELECT id,version FROM initialization_seeds WHERE state IN ('failed','spent','retry_failed','retry_spent')")
            if row["id"] in known and memory_version(known[row["id"]]) == row["version"]}
        candidates = sorted((item for item in records if item.id not in covered and item.id not in stopped
                             and _eligible_seed(item)), key=lambda item: item.id)
        pending = self._pending_initialization()
        initial = [item for item in candidates if self.engine.store.initialization_seed(
            self.engine.policy, item.id, memory_version(item))]
        recovering = [item for item in candidates if (item.id, memory_version(item)) in pending]
        seeds = (initial or recovering or candidates)[:5]
        for seed in seeds:
            batch = self._related(seed, known)
            group = self._call(batch, initialization_seed=(seed.id, memory_version(seed)))
            ids = {item.id for item in batch}
            groups = [old for old in groups if not set(old["versions"]).issubset(ids)] + [group]
            covered[seed.id] = memory_version(seed)
        return {"fingerprint": memory_fingerprint(records), "total": len(records), "processed": len(covered),
                "groups": groups, "covered_seeds": covered, "stopped_seeds": stopped,
                "remaining": sum(item.id not in covered for item in candidates),
                "versions": {mid: version for group in groups for mid, version in group["versions"].items()},
                "catalog_epoch": self.engine.store.get_control("catalog_epoch"), "review_after_days": 90}

    def _related(self, seed: LongTermMemory, known: dict[str, LongTermMemory]) -> list[LongTermMemory]:
        matches = self.backend.search(seed.content, user_id=seed.user_id, scope=seed.scope,
                                      persona_id=seed.persona_id, limit=12)
        batch, seen, chars = [], set(), 0
        for item in (seed, *matches):
            if len(batch) >= 12:
                break
            if (item.id in seen or item.id not in known or item.user_id != seed.user_id
                    or item.scope is not seed.scope or item.persona_id != seed.persona_id
                    or not _eligible_seed(item) or chars + len(item.content) > 4500):
                continue
            batch.append(item)
            seen.add(item.id)
            chars += len(item.content)
        return batch

    def _call(self, batch: list[LongTermMemory], *,
              initialization_seed: tuple[str, str] | None = None) -> dict[str, Any]:
        if not batch:
            return {"topics": [], "comparisons": [], "time_bound_ids": [], "observations": [], "versions": {}}
        self._check_enabled()
        self._check_versions(batch)
        payload = [{"id": item.id, "content": item.content, "observed_at": item.metadata.get("source_created_at"),
                    "valid_from": item.metadata.get("valid_from"), "state": item.metadata.get("journal_state"),
                    "kind": item.metadata.get("journal_kind"), "independent_days": sorted(self._days(item))} for item in batch]
        messages = [{"role": "system", "content": _PROMPT + _OBSERVATIONS},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        size = sum(len(message["content"]) for message in messages)
        schema = _organization_schema([item.id for item in batch],
            {item["id"]: item["independent_days"] for item in payload})
        if callable(getattr(self.provider, "complete_json_with_guard", None)):
            size += len(json.dumps(schema, ensure_ascii=False))
        sent = False
        def before_send() -> None:
            nonlocal sent
            self._before_send(batch, size, initialization_seed)
            sent = True
        try:
            turn = complete_json_guarded(self.provider, messages, schema, before_send)
            if turn.tool_calls or not turn.content or len(turn.content) > 30000:
                raise ValueError("invalid_organization")
            result = json.loads(turn.content)
            if not isinstance(result, dict):
                raise ValueError("invalid_organization")
            if "observations" not in result:
                raise ValueError("invalid_observations")
            observations = result.pop("observations")
            parsed = _parse_report(json.dumps(result), {item.id for item in batch})
            parsed["observations"] = self._validate_observations(observations, batch)
            self._check_enabled()
            self._check_versions(batch)
            parsed.update(scope=batch[0].scope.value, persona_id=batch[0].persona_id,
                          versions={item.id: memory_version(item) for item in batch})
        except Exception:
            if initialization_seed and sent:
                self.engine.store.finish_initialization_seed(*initialization_seed, "failed")
            raise
        if initialization_seed:
            self.engine.store.finish_initialization_seed(*initialization_seed, "done", parsed)
        return parsed

    def _before_send(self, batch: Sequence[LongTermMemory], size: int,
                     initialization_seed: tuple[str, str] | None = None) -> None:
        self._check_enabled()
        self._check_versions(batch)
        if initialization_seed and not any((item.id, memory_version(item)) == initialization_seed for item in batch):
            raise JournalMemoryError("journal_initialization_seed_changed")
        self.engine.store.reserve_daily(self.engine.policy, size, initialization_seed=initialization_seed)
        self.engine.store.log_call("organization", [item.id + ":" + memory_version(item) for item in batch], size)

    def _check_versions(self, records: Sequence[LongTermMemory]) -> None:
        for item in records:
            current = self.backend.get(item.id)
            if not self.engine.can_send(current, "organization") or memory_version(item) != memory_version(current):
                raise JournalMemoryError("journal_source_changed")

    def _days(self, item: LongTermMemory) -> set[str]:
        if item.metadata.get("journal_kind") != "event" or item.metadata.get("certainty") != "explicit":
            return set()
        days = set()
        for ref in self.engine.store.rows("SELECT evidence_id FROM supports WHERE memory_id=?", (item.id,)):
            try:
                evidence = self.engine.current_evidence(ref["evidence_id"])
                if evidence.kind == "daily" and evidence.observed_at:
                    days.add(evidence.observed_at)
            except JournalMemoryError:
                continue
        return days

    def _validate_observations(self, values: Any, records: Sequence[LongTermMemory]) -> list[dict[str, Any]]:
        if not isinstance(values, list) or len(values) > 3:
            raise ValueError("invalid_observations")
        known = {item.id: item for item in records}
        for value in values:
            if not isinstance(value, dict) or set(value) != {"summary", "support_ids", "counter_ids", "limitation"}:
                raise ValueError("invalid_observations")
            for key, maximum in (("summary", 240), ("limitation", 180)):
                if not isinstance(value[key], str) or not 1 <= len(value[key]) <= maximum or contains_credentials(value[key]):
                    raise ValueError("invalid_observations")
            support, counter = value["support_ids"], value["counter_ids"]
            if not all(isinstance(ids, list) and all(isinstance(mid, str) and mid in known for mid in ids)
                       and len(ids) == len(set(ids)) for ids in (support, counter)):
                raise ValueError("invalid_observation_evidence")
            if len(support) < 2 or set(support).intersection(counter):
                raise ValueError("insufficient_independent_evidence")
            days = [self._days(known[mid]) for mid in support]
            if any(not value for value in days) or len(set.union(*days)) < 2:
                raise ValueError("insufficient_independent_evidence")
        return values


def recall_observations(service: Any, records: Sequence[LongTermMemory], user_id: str) -> tuple[LongTermMemory, ...]:
    if service.journal is None or user_id != service.journal.policy.user_id or not records:
        return ()
    latest = service.operations.organization.latest(user_id, ready_only=True)
    report = latest["report"] if latest else None
    if not report or report.get("catalog_epoch") != service.journal.store.get_control("catalog_epoch"):
        return ()
    recalled, result = {item.id for item in records}, []
    for group in report["groups"]:
        if not group.get("observations") or not recalled.intersection(group.get("versions", {})):
            continue
        valid = _valid_group(service, group, records[0])
        if not valid:
            continue
        for observation in group["observations"]:
            evidence_ids = observation["support_ids"] + observation["counter_ids"]
            if not recalled.intersection(evidence_ids):
                continue
            identifier = "observation:" + fingerprint(json.dumps(observation, sort_keys=True, ensure_ascii=False))
            sources = sorted({str(valid[mid].metadata.get("source_id", "unknown")) for mid in evidence_ids})
            result.append(replace(valid[evidence_ids[0]], id=identifier,
                content=f'待验证观察：{observation["summary"]}；适用限制与反例：{observation["limitation"]}',
                metadata={"source_type": "derived-observation", "source_id": sources[0], "source_ids": sources,
                          "journal_kind": "observation", "certainty": "inferred", "evidence_ids": evidence_ids}))
    return tuple({item.id: item for item in result}.values())[:2]


def _valid_group(service: Any, group: dict[str, Any], selected: LongTermMemory) -> dict[str, LongTermMemory]:
    if group.get("scope") != selected.scope.value or group.get("persona_id") != selected.persona_id:
        return {}
    result = {}
    for mid, version in group["versions"].items():
        try:
            item = service.backend.get(mid)
        except MemoryBackendError:
            return {}
        if (item.user_id != selected.user_id or item.scope is not selected.scope or item.persona_id != selected.persona_id
                or item.status is not MemoryStatus.ACTIVE or not service.journal.can_send(item)
                or memory_version(item) != version):
            return {}
        result[mid] = item
    return result
