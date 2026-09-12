"""Owner-scoped records and portable, inert exports; no active grants leave storage."""

from __future__ import annotations

import hashlib
import json

from riji_agent.mentors.models import (
    Artifact, Command, Conversation, DiscussionRun, MentorError, Principal, Source,
    WorkingSummary, new_id,
)
from riji_agent.mentors.service import DiscussionService
from riji_agent.mentors.store import key


def digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class DiscussionHistory:
    def __init__(self, service: DiscussionService, checkpoints=None) -> None:
        self.service, self.store, self.checkpoints = service, service.store, checkpoints
        self.drafts = None

    def list(self, principal_id: str) -> tuple[dict, ...]:
        return tuple({"id": item.id, "kind": item.kind, "personas": item.personas, "question": item.question,
                      "status": item.status, "source_scope": item.source_scope, "updated_at": item.updated_at}
                     for item in self.store.list("conversation", principal_id, Conversation) if item.status != "deleted")

    def read(self, conversation_id: str, principal_id: str) -> dict:
        conversation = self.service.get(conversation_id, principal_id)
        principal = self.store.read("principal", principal_id, Principal)
        valid, artifacts = {}, []
        with self.store.transaction() as db:
            superseded = self.service.summaries.superseded(db, conversation)
        for item in self.store.list("artifact", conversation_id, Artifact):
            if conversation.source_scope == "group_only" and (item.dependencies or
                    (conversation.room_id and item.origin_room_id != conversation.room_id)):
                artifacts.append({"id": item.id, "kind": item.kind, "actor": item.actor,
                                  "unavailable": "group_only_source_violation"})
                continue
            for dependency in item.dependencies:
                if dependency not in valid:
                    source = self.store.read("source", dependency, Source)
                    valid[dependency] = source is not None and self.service.policy.sources.validate(principal, source)
            if all(valid[dependency] for dependency in item.dependencies):
                artifacts.append({**item.model_dump(), "superseded": item.id in superseded})
            else:
                artifacts.append({"id": item.id, "kind": item.kind, "actor": item.actor,
                                  "unavailable": "source_changed_or_revoked"})
        with self.store.transaction() as db:
            notice = self.store.lookup(db, "blocked", conversation_id) or self.store.lookup(db, "capture_notice", conversation_id)
        summaries = [self._summary_view(item, principal, superseded, group_only=conversation.source_scope == "group_only") for item in
                     self.store.list("summary", conversation_id, WorkingSummary)]
        runs = [item.model_dump() for item in self.store.list("run", conversation_id, DiscussionRun)]
        return {"conversation": conversation.model_dump(), "artifacts": artifacts, "notice": notice,
                "runs": runs, "summaries": summaries,
                "current_summary": next((item for item in summaries if item["id"] == conversation.summary_id), None),
                "budget": self.service.budgets.status(conversation)}

    def _summary_view(self, summary: WorkingSummary, principal: Principal, superseded: set[str], *, group_only: bool = False) -> dict:
        value = summary.model_dump()
        for item in value["items"]:
            if group_only and item["dependencies"]:
                item.update(text="", unavailable="group_only_source_violation", dependencies=(), source_refs=())
                continue
            sources = [self.store.read("source", identifier, Source) for identifier in item["dependencies"]]
            if any(source is None or not self.service.policy.sources.validate(principal, source) for source in sources):
                item["text"] = ""
                item["unavailable"] = "source_changed_or_revoked"
            item["superseded"] = bool(set(item["artifact_ids"]) & superseded)
        return value

    def export(self, conversation_id: str, principal_id: str) -> dict:
        view = self.read(conversation_id, principal_id)
        conversation = view["conversation"]
        safe = {name: conversation[name] for name in ("id", "kind", "personas", "question", "mode", "input_revision", "created_at",
                                                     "run_id", "run_personas", "run_number", "summary_id", "summary_version", "correction_version", "source_scope")}
        artifacts = view["artifacts"]
        for item in artifacts:
            item.pop("origin_room_id", None)
            dependencies = item.pop("dependencies", ())
            refs = [self.store.read("source", identifier, Source) for identifier in dependencies]
            item["dependency_refs"] = [{"id": ref.id, "version": ref.version} for ref in refs if ref is not None]
        principal = self.store.read("principal", principal_id, Principal)
        summaries = self._export_summaries(view["summaries"])
        payload = {"format": "riji-mentor-history", "version": 2, "conversation": safe,
                   "artifacts": artifacts, "runs": view["runs"], "summaries": summaries,
                   "owner_account": principal.account.model_dump(), "restored_state": "stopped"}
        return {"payload": payload, "sha256": digest(payload)}

    def _export_summaries(self, summaries: list[dict]) -> list[dict]:
        for summary in summaries:
            for item in summary["items"]:
                dependencies = item.pop("dependencies", ())
                refs = [self.store.read("source", identifier, Source) for identifier in dependencies]
                item["dependency_refs"] = [{"id": ref.id, "version": ref.version} for ref in refs if ref is not None]
        return summaries

    def restore(self, package: dict, principal_id: str, *, apply: bool = False) -> dict:
        if set(package) != {"payload", "sha256"} or digest(package["payload"]) != package["sha256"]:
            raise MentorError("export_checksum_invalid")
        payload = package["payload"]
        if payload.get("format") != "riji-mentor-history" or payload.get("version") not in {1, 2}:
            raise MentorError("export_format_invalid")
        source = payload["conversation"]
        principal = self.store.read("principal", principal_id, Principal)
        if principal is None or payload.get("owner_account") != principal.account.model_dump():
            raise MentorError("export_owner_mismatch")
        identifier = source["id"]
        if len(json.dumps(payload)) > 2_000_000:
            raise MentorError("export_too_large")
        with self.store.transaction() as db:
            if db.execute("SELECT 1 FROM mentor_deleted WHERE conversation_id=?", (identifier,)).fetchone():
                raise MentorError("deleted_discussion_cannot_restore")
            if self.store.get(db, "conversation", identifier, Conversation):
                raise MentorError("discussion_already_exists")
            current = Conversation(**source, owner_id=principal_id, status="stopped", room_status="sealed",
                                   updated_at=self.service.now())
            artifacts = self._restored_artifacts(payload["artifacts"], principal_id)
            if any(item.conversation_id != identifier for item in artifacts):
                raise MentorError("export_scope_invalid")
            if apply:
                # Imported history is an inert archive, never an execution input.
                self.store.put(db, "conversation", current, principal_id)
                remap = {item.id: new_id() for item in artifacts}
                superseded = {item["id"] for item in payload["artifacts"] if item.get("superseded")}
                for item in artifacts:
                    restored = item.model_copy(update={"id": remap[item.id],
                        "responds_to": tuple(remap[ref] for ref in item.responds_to if ref in remap),
                        "source_refs": tuple(remap.get(ref, ref) for ref in item.source_refs),
                        "comparison_findings": tuple(finding.model_copy(update={
                            "stances": tuple(stance.model_copy(update={
                                "artifact_id": remap.get(stance.artifact_id, stance.artifact_id)
                            }) for stance in finding.stances)
                        }) for finding in item.comparison_findings)})
                    self.store.put(db, "artifact", restored, identifier)
                    if item.id in superseded:
                        self.store.bind(db, "superseded", restored.id, identifier)
                self._restore_context(db, payload, current, principal_id, remap)
                self.store.bind(db, "archive_only", identifier, "1")
        return {"conversation_id": identifier, "artifacts": len(artifacts), "applied": apply, "status": "stopped"}

    @staticmethod
    def _restored_artifacts(items: list[dict], principal_id: str) -> tuple[Artifact, ...]:
        restored = []
        for original in items:
            if "unavailable" in original:
                continue
            data = dict(original)
            data.pop("superseded", None)
            refs = data.pop("dependency_refs", ())
            if "dependencies" in data:
                raise MentorError("export_scope_invalid")
            data["dependencies"] = tuple(key(principal_id, ref["id"], ref["version"]) for ref in refs)
            restored.append(Artifact.model_validate(data))
        return tuple(restored)

    def _restore_context(self, db, payload: dict, conversation: Conversation, principal_id: str, remap: dict) -> None:
        for original in payload.get("runs", ()):
            run = DiscussionRun.model_validate(original)
            if run.conversation_id != conversation.id or self.store.get(db, "run", run.id, DiscussionRun):
                raise MentorError("export_scope_invalid")
            self.store.put(db, "run", run, conversation.id)
        for original in payload.get("summaries", ()):
            data, items = dict(original), []
            for entry in data["items"]:
                item = dict(entry)
                if item.pop("unavailable", None):
                    continue
                item.pop("superseded", None)
                refs = item.pop("dependency_refs", ())
                if "dependencies" in item:
                    raise MentorError("export_scope_invalid")
                item["dependencies"] = tuple(key(principal_id, ref["id"], ref["version"]) for ref in refs)
                item["artifact_ids"] = tuple(remap[ref] for ref in item["artifact_ids"] if ref in remap)
                item["source_refs"] = tuple(remap.get(ref, ref) for ref in item["source_refs"])
                items.append(item)
            data["items"] = items
            for field in ("covered_artifact_ids", "pending_artifact_ids"):
                data[field] = tuple(remap[ref] for ref in data[field] if ref in remap)
            summary = WorkingSummary.model_validate(data)
            if summary.conversation_id != conversation.id or self.store.get(db, "summary", summary.id, WorkingSummary):
                raise MentorError("export_scope_invalid")
            self.store.put(db, "summary", summary, conversation.id)

    def delete(self, conversation_id: str, principal_id: str, command_id: str) -> dict:
        from riji_agent.mentors.handoff import Handoff
        current = self.store.read("conversation", conversation_id, Conversation)
        if current is None or current.owner_id != principal_id:
            raise MentorError("discussion_not_found")
        handoffs = self.store.list("handoff", conversation_id, Handoff)
        receipt = self.service.apply(Command(id=command_id, principal_id=principal_id,
                                             conversation_id=conversation_id, kind="delete", expected_revision=0))
        if self.drafts is not None:
            principal = self.store.read("principal", principal_id, Principal)
            for handoff in handoffs:
                if handoff.draft_id:
                    self.drafts.purge_uncommitted_draft(handoff.draft_id, user_id=principal.legacy_owner_key)
        if self.checkpoints is not None:
            self.checkpoints.forget(conversation_id)
        return receipt.model_dump()
