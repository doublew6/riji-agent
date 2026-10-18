"""Read-only, evidence-checked mobile projection of rebuildable organization reports."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from riji_agent.memory.capture import contains_credentials
from riji_agent.memory.journal_organization import JournalOrganization
from riji_agent.memory.models import MemoryStatus
from riji_agent.memory.organization import CATEGORIES, _parse_report, active_records, memory_version
from riji_agent.mobile.memory import memory_version as decorated_version, memory_view
from riji_agent.mobile.store import MobileError

THEMES = {key: value for key, value in CATEGORIES.items() if key != "observations"}
PERSONAS = {"gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"}
MAX_PAGE_BYTES = 1024 * 1024


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _date(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).isoformat()
    except ValueError:
        return None


def _safe_record(item: Any) -> bool:
    return (re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item.id) is not None
            and 0 < len(item.content) <= 2000 and not contains_credentials(item.content)
            and item.metadata.get("privacy", "cloud") == "cloud"
            and ((item.scope.value == "shared" and item.persona_id is None)
                 or (item.scope.value == "persona" and item.persona_id in PERSONAS)))


def _evidence(item: Any) -> dict:
    view = memory_view(item)
    view.update(updated_at=_date(item.updated_at), reviewed_at=_date(view["reviewed_at"]),
                observed_at=_date(item.metadata.get("source_created_at") or item.metadata.get("valid_from")),
                kind=item.metadata.get("journal_kind", "memory"), certainty=item.metadata.get("certainty", "unspecified"))
    if view["kind"] not in {"memory", "event", "fact", "preference", "goal", "plan", "state", "observation"}:
        view["kind"] = "memory"
    if view["certainty"] not in {"explicit", "inferred", "uncertain", "unspecified"}:
        view["certainty"] = "unspecified"
    if view["state"] not in {"current", "historical", "superseded", "disputed", "uncertain"}:
        view["state"] = "uncertain"
    safe = []
    for source in view["sources"]:
        if (all(isinstance(source[key], str) and len(source[key]) <= 512
                and not contains_credentials(source[key]) for key in ("id", "relative_path", "section", "version", "kind"))
                and type(source["line"]) is int and 0 < source["line"] < 1000000):
            safe.append(source)
    view["sources"] = safe
    return view


@dataclass
class Snapshot:
    records: dict
    eligible: dict
    permissions: dict
    report: dict | None
    latest: dict | None
    organized_at: str | None
    stopped: dict
    journal: bool
    epoch: str | None
    version: str


class MobileOverview:
    def __init__(self, memory) -> None:
        self.memory = memory

    @property
    def service(self):
        self.memory.available()
        return self.memory.service

    def _guard(self):
        guard = getattr(self.service.backend, "mutation_guard", None)
        if guard is None:
            raise MobileError("memory_mutation_guard_unavailable", 503)
        return guard()

    def _permissions(self, owner: str) -> dict:
        engine = self.memory._engine(owner, required=False)
        if engine is None:
            return {"binding": _hash([owner, "legacy-organization"]), "allowed": True,
                    "paused": False, "restoring": False}
        consent = engine.privacy.status()
        return {"binding": consent["binding"], "allowed": bool(engine.policy.enabled and consent["valid"]
                and consent["permissions"].get("organization")), "consent": consent,
                "paused": engine.store.get_control("paused") == "1",
                "restoring": bool(engine.store.get_control("restore_in_progress"))}

    def _snapshot(self, owner: str) -> Snapshot:
        service = self.service
        engine = self.memory._engine(owner, required=False)
        records = {item.id: item for item in service.list_memories(user_id=owner, include_archived=True)
                   if item.user_id == owner}
        permissions = self._permissions(owner)
        eligible = {item.id: item for item in active_records(tuple(records.values()), owner)
                    if _safe_record(item) and (engine.can_send(item, "organization") if engine is not None
                                               else not item.metadata.get("journal_managed"))}
        store = service.operations.organization
        latest, report_run = store.latest(owner), store.latest_report(owner)
        report = report_run["report"] if report_run else None
        epoch = engine.store.get_control("catalog_epoch") if engine else None
        stopped = {row["id"]: row["version"] for row in engine.store.rows(
            "SELECT id,version FROM initialization_seeds WHERE state IN ('failed','spent','retry_failed','retry_spent')")} if engine else {}
        version = _hash([owner, permissions, epoch, sorted((mid, decorated_version(item)) for mid, item in records.items()),
                         sorted(eligible), report, latest, stopped])
        return Snapshot(records, eligible, permissions, report, latest,
                        _date(report_run["updated_at"]) if report_run else None, stopped, engine is not None, epoch, version)

    def listing(self, owner: str, cursor: str = "") -> dict:
        with self._guard():
            snapshot = self._snapshot(owner)
            value = self._project(snapshot, owner, cursor)
            # Sources can change outside the cooperative lock (vault edits and consent).
            # A second decorated read must agree before any projected text leaves this boundary.
            if self._snapshot(owner).version != snapshot.version:
                raise MobileError("memory_overview_changed")
            return value

    def _groups(self, snapshot: Snapshot, owner: str) -> tuple[list, set]:
        report = snapshot.report
        if report is None:
            return [], set()
        if not isinstance(report, dict) or not isinstance(report.get("groups"), list):
            return [], set(snapshot.eligible)
        if snapshot.journal and report.get("catalog_epoch") != snapshot.epoch:
            return [], set(snapshot.eligible)
        if not snapshot.journal and not self._legacy_manifest_valid(report):
            return [], set(snapshot.eligible)
        valid, invalid = [], set()
        for group in report["groups"]:
            try:
                valid.append(self._group(group, snapshot, owner))
            except (ValueError, TypeError, KeyError):
                if isinstance(group, dict):
                    versions = group.get("versions", report.get("versions", {}))
                    if isinstance(versions, dict):
                        invalid.update(set(versions).intersection(snapshot.records))
                else:
                    invalid.update(snapshot.eligible)
        return valid, invalid

    @staticmethod
    def _legacy_manifest_valid(report: dict) -> bool:
        try:
            versions = report["versions"]
            assigned = [mid for group in report["groups"] for topic in group["topics"] for mid in topic["evidence_ids"]]
            return isinstance(versions, dict) and len(assigned) == len(set(assigned)) and set(assigned) == set(versions)
        except (TypeError, KeyError):
            return False

    def _group(self, group: dict, snapshot: Snapshot, owner: str) -> dict:
        if not isinstance(group, dict):
            raise ValueError()
        versions = group.get("versions")
        if versions is None:
            if snapshot.journal:
                raise ValueError()
            ids = {mid for topic in group["topics"] for mid in topic["evidence_ids"]}
            versions = {mid: snapshot.report["versions"][mid] for mid in ids}
        if not isinstance(versions, dict) or not 0 < len(versions) <= 20:
            raise ValueError()
        records = []
        for mid, version in versions.items():
            item = snapshot.eligible.get(mid)
            if (item is None or item.user_id != owner or item.scope.value != group.get("scope")
                    or item.persona_id != group.get("persona_id") or memory_version(item) != version):
                raise ValueError()
            records.append(item)
        payload = {key: group[key] for key in ("topics", "comparisons", "time_bound_ids")}
        parsed = _parse_report(json.dumps(payload, ensure_ascii=False), set(versions))
        observations = group.get("observations", [])
        if observations:
            engine = self.memory._engine(owner, required=False)
            if engine is None:
                raise ValueError()
            validator = JournalOrganization(self.service.backend, self.service.operations.organization, None)
            validator._validate_observations(observations, records)
        for entry in observations:
            if len(entry["support_ids"]) + len(entry["counter_ids"]) > 20:
                raise ValueError()
        return dict(parsed, observations=observations, versions=versions,
                    scope=group["scope"], persona_id=group.get("persona_id"))

    def _entries(self, groups: list, snapshot: Snapshot) -> dict:
        result = {"summaries": [], "observations": [], "comparisons": []}
        for group in groups:
            boundary = {"scope": group["scope"], "persona_id": group["persona_id"]}
            for topic in group["topics"]:
                states = sorted({_evidence(snapshot.eligible[mid])["state"] for mid in topic["evidence_ids"]})
                if any(snapshot.eligible[mid].metadata.get("journal_kind") in {"goal", "plan"} for mid in topic["evidence_ids"]):
                    states.append("plan")
                if set(topic["evidence_ids"]).intersection(group["time_bound_ids"]):
                    states.append("time_bound")
                if boundary["scope"] == "persona" or topic["category"] == "observations":
                    states.append("observation")
                value = {key: topic[key] for key in ("category", "title", "summary", "evidence_ids")}
                result["summaries"].append(dict(value, **boundary, states=states, updated_at=snapshot.organized_at,
                                                 id=_hash(["summary", boundary, value])))
            for observation in group["observations"]:
                result["observations"].append(dict(observation, **boundary, id=_hash(["observation", boundary, observation])))
            for pair in group["comparisons"]:
                value = {key: pair[key] for key in ("kind", "reason", "evidence_ids")}
                result["comparisons"].append(dict(value, **boundary, id=_hash(["comparison", boundary, value])))
        return {kind: list({item["id"]: item for item in values}.values()) for kind, values in result.items()}

    @staticmethod
    def _coverage(snapshot: Snapshot, groups: list, invalid: set) -> dict:
        eligible = set(snapshot.eligible)
        valid = {mid for group in groups for mid in group["versions"]}
        report = snapshot.report if isinstance(snapshot.report, dict) else {}
        covered = report.get("covered_seeds")
        if isinstance(covered, dict):
            valid &= {mid for mid, version in covered.items()
                      if mid in snapshot.eligible and memory_version(snapshot.eligible[mid]) == version}
        stopped = snapshot.stopped
        stopped = {mid for mid, version in stopped.items()
                   if mid in snapshot.eligible and memory_version(snapshot.eligible[mid]) == version} - valid
        invalid |= {mid for mid, item in snapshot.records.items() if item.status is MemoryStatus.ACTIVE
                    and item.metadata.get("journal_state") in {"source_invalid", "deleted", "pending"}}
        return {"eligible": len(eligible), "organized": len(valid), "unorganized": len(eligible - valid - stopped),
                "stopped": len(stopped), "invalid": len(invalid - valid)}

    @staticmethod
    def _status(snapshot: Snapshot, coverage: dict) -> tuple[str, str | None]:
        permissions, latest = snapshot.permissions, snapshot.latest or {}
        reason = ("restoring" if permissions["restoring"] else "permission_denied" if not permissions["allowed"]
                  else "paused" if permissions["paused"] else "already_pending" if latest.get("status") in {"pending", "processing"}
                  else "no_eligible_work" if not coverage["unorganized"] else None)
        if not permissions["allowed"]:
            state = "permission_denied"
        elif permissions["restoring"]:
            state = "invalid"
        elif latest.get("status") == "failed":
            state = "failed"
        elif coverage["organized"]:
            state = "partial" if coverage["unorganized"] or coverage["stopped"] or coverage["invalid"] else "ready"
        elif coverage["invalid"]:
            state = "invalid"
        elif coverage["eligible"]:
            state = "unorganized"
        elif any(item.status is MemoryStatus.ACTIVE for item in snapshot.records.values()):
            state = "permission_denied"
        else:
            state = "empty"
        return state, reason

    def _project(self, snapshot: Snapshot, owner: str, cursor: str) -> dict:
        groups, invalid = self._groups(snapshot, owner)
        if not snapshot.permissions["allowed"] or snapshot.permissions["restoring"]:
            groups = []
        entries = self._entries(groups, snapshot)
        coverage = self._coverage(snapshot, groups, invalid)
        state, reason = self._status(snapshot, coverage)
        now = datetime.now(timezone.utc)
        page = {"version": snapshot.version, "updated_at": now.isoformat(), "organized_at": snapshot.organized_at,
                "cache_expires_at": now.timestamp() + 900, "state": state,
                "job_status": (snapshot.latest or {}).get("status", "idle"), "coverage": coverage,
                "categories": [{"id": key, "title": title, "summary_count": sum(
                    item["category"] == key and item["scope"] == "shared" and "observation" not in item["states"]
                    for item in entries["summaries"])} for key, title in THEMES.items()],
                "can_organize": reason is None, "organize_binding": snapshot.permissions["binding"],
                "organize_reason": reason, "next_cursor": None}
        return self._page(page, entries, snapshot, cursor)

    @staticmethod
    def _page(page: dict, entries: dict, snapshot: Snapshot, cursor: str) -> dict:
        combined = [(kind, item) for kind, values in entries.items() for item in values]
        offset = 0
        if cursor:
            try:
                version, offset = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                if version != snapshot.version or type(offset) is not int or not 0 <= offset < len(combined):
                    raise ValueError()
            except Exception:
                raise MobileError("memory_overview_changed") from None
        page.update(summaries=[], observations=[], comparisons=[], evidence=[])
        refs, count = set(), 0
        for kind, item in combined[offset:offset + 20]:
            candidate = dict(page, **{kind: page[kind] + [item]})
            selected = refs | set(item.get("evidence_ids", item.get("support_ids", []) + item.get("counter_ids", [])))
            candidate["evidence"] = [_evidence(snapshot.eligible[mid]) for mid in sorted(selected)]
            end = offset + count + 1
            candidate["next_cursor"] = base64.urlsafe_b64encode(json.dumps([snapshot.version, end]).encode()).decode().rstrip("=") if end < len(combined) else None
            if len(json.dumps(candidate, ensure_ascii=False).encode()) > MAX_PAGE_BYTES:
                if count == 0:
                    raise MobileError("memory_overview_too_large", 503)
                break
            page, refs, count = candidate, selected, count + 1
        return page

    def organize(self, owner: str, body: dict) -> dict:
        with self._guard():
            snapshot = self._snapshot(owner)
            if body["expected_version"] != snapshot.version:
                raise MobileError("memory_overview_changed")
            if body["binding"] != snapshot.permissions["binding"] or body.get("acknowledged") is not True:
                raise MobileError("privacy_scope_changed_or_invalid")
            page = self._project(snapshot, owner, "")
            if page["organize_reason"] not in {None, "already_pending"}:
                raise MobileError("memory_organization_" + page["organize_reason"])
            if self._snapshot(owner).version != snapshot.version:
                raise MobileError("memory_overview_changed")
            run = self.service.operations.organization.request_if_idle(owner)
            return {"accepted": True, "run_id": run, "state": "pending" if run is not None else "already_pending"}
