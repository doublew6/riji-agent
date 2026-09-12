"""Synthetic service-level boundary observations for the external EvalMesh grader.

This target never reads expected answers, application settings, or live data.
Controlled transport/model responses exercise real domain state transitions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from riji_agent.agent.tools import ToolRegistry
from riji_agent.drafts.errors import DraftError
from riji_agent.drafts.service import DraftService
from riji_agent.drafts.store import DraftStore
from riji_agent.journal.content import AI_RESULT, personal_body
from riji_agent.journal.index import JournalIndex
from riji_agent.journal.parser import parse_note
from riji_agent.memory.journal_sources import read_source
from riji_agent.memory.journal_types import JournalMemoryPolicy
from riji_agent.memory.store import MemoryStore
from riji_agent.mentors.delivery import OutboxDispatcher
from riji_agent.mentors.generation import ModelGeneration
from riji_agent.mentors.handoff import Handoff, JournalHandoff
from riji_agent.mentors.history import DiscussionHistory
from riji_agent.mentors.identity import IdentityService
from riji_agent.mentors.identity_links import IdentityLinks
from riji_agent.mentors.ingress import DiscussionIngress
from riji_agent.mentors.local_channel import LocalChannel
from riji_agent.mentors.models import (
    Account, Application, Artifact, ChatBinding, Command, Conversation,
    Delivery, Envelope, Generation, GenerationRequest, MentorError, Principal,
    RoomSnapshot, Source, TransportResult,
)
from riji_agent.mentors.policy import DiscussionPolicy
from riji_agent.mentors.ports import NoSources
from riji_agent.mentors.service import DiscussionService
from riji_agent.mentors.store import MentorStore
from riji_agent.mentors.worker import DiscussionWorker
from riji_agent.models.types import AssistantTurn, LLMError, LLMProvider, ToolCall
from riji_agent.personas.registry import PersonaRegistry
from riji_agent.retrieval.models import ToolContext
from riji_agent.retrieval.service import RetrievalService


_FIXED_NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


def _clock() -> float:
    return _FIXED_NOW.timestamp()


class ControlledChannel(LocalChannel):
    """Use the real local channel, with explicit injected transport outcomes."""

    def __init__(self, store: MentorStore) -> None:
        super().__init__(store)
        self.sent: list[str] = []
        self.outcome = "sent"
        self.incomplete = False

    def create_room(self, principal: Principal, applications: tuple[str, ...],
                    operation_id: str) -> str:
        room = super().create_room(principal, applications, operation_id)
        if self.incomplete:
            self.change_room(room, complete=False)
        return room

    def change_room(self, room: str, **changes: Any) -> None:
        current = self.inspect_room(room).model_copy(update=changes)
        with self.store.transaction() as db:
            self.store.put(db, "local_room", current, "", room)

    def send(self, delivery: Delivery, text: str) -> TransportResult:
        self.sent.append(delivery.id)
        if self.outcome != "sent":
            return TransportResult(status=self.outcome)
        return super().send(delivery, text)


class ControlledGenerator:
    def __init__(self) -> None:
        self.calls: list[GenerationRequest] = []
        self.behavior = "valid"
        self.after_send: Callable[[], None] = lambda: None

    def generate(self, request: GenerationRequest,
                 before_send: Callable[[], None]) -> Generation:
        before_send()
        self.calls.append(request)
        self.after_send()
        if self.behavior == "timeout":
            raise LLMError("codex_timeout")
        if self.behavior == "malformed":
            raise ValueError("synthetic_invalid_json")
        targets = tuple(item.id for item in request.previous
                        if item.actor != request.actor and item.kind == "opinion")[:1]
        refs = tuple(source.id for source in request.background)
        return Generation(
            text="SyntheticAdvice: consider a reversible practice session.",
            claims=("Synthetic conditional suggestion",),
            source_refs=("invented-source",) if self.behavior == "forged" else refs,
            responds_to=targets if request.stage == "debate" else (),
            uncertainties=("Practice schedule is not known",),
            next_steps=("Choose a ten-minute trial",),
            debate_needed=False if request.stage == "comparison" else None,
        )


class ControlledProvider:
    def __init__(self, behavior: str) -> None:
        self.behavior = behavior
        self.calls = 0

    def complete(self, messages: Any, tools: Any) -> AssistantTurn:
        self.calls += 1
        if self.behavior == "unexpected_tool":
            return AssistantTurn(None, (ToolCall("call", "commit_draft", "{}"),))
        codes = {"auth": "codex_login_required", "quota": "codex_quota_exhausted"}
        raise LLMError(codes[self.behavior])


class ControlledSources:
    def __init__(self, owner: str, personas: tuple[str, ...]) -> None:
        self.enabled = True
        self.source = Source(id="synthetic-source", owner_id=owner,
            version="synthetic-v1", text="SyntheticEvidence: user has ten minutes.",
            kind="memory", allowed_personas=personas)

    def background(self, principal: Principal,
                   conversation: Conversation) -> tuple[Source, ...]:
        return (self.source,)

    def validate(self, principal: Principal, source: Source) -> bool:
        return self.enabled and source.version == self.source.version


@dataclass
class Environment:
    path: Path
    store: MentorStore = field(init=False)
    identity: IdentityService = field(init=False)
    owner: Principal = field(init=False)
    apps: dict[str, Application] = field(init=False)
    binding: ChatBinding = field(init=False)
    channel: ControlledChannel = field(init=False)
    service: DiscussionService = field(init=False)
    generator: ControlledGenerator = field(init=False)
    worker: DiscussionWorker = field(init=False)
    dispatcher: OutboxDispatcher = field(init=False)

    def __post_init__(self) -> None:
        self.store = MentorStore(self.path / "mentors.sqlite3")
        self.identity = IdentityService(self.store, PersonaRegistry())
        self.owner = self.identity.register_principal(
            Account(platform="local", tenant="synthetic", subject="owner"), "owner")
        self.apps = {}
        for actor in ("host", "gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"):
            app = Application(platform="local", tenant="synthetic", external_id=actor,
                persona_id=actor, role="host" if actor == "host" else "mentor")
            self.apps[actor] = self.identity.register_application(app)
        self.binding = self.bind("host")
        self.channel = ControlledChannel(self.store)
        policy = DiscussionPolicy(self.store, NoSources(), self.channel, now=_clock)
        self.service = DiscussionService(self.store, self.identity, policy, now=_clock)
        self.generator = ControlledGenerator()
        self.worker = DiscussionWorker(self.service, self.generator, now=_clock)
        self.dispatcher = OutboxDispatcher(self.service, now=_clock)

    def bind(self, actor: str, **changes: Any) -> ChatBinding:
        return self.identity.resolve(self.apps[actor].id, self.message(actor, **changes))[2]

    def message(self, actor: str = "host", **changes: Any) -> Envelope:
        message = Envelope(delivery_id="synthetic-event", message_id="synthetic-message",
            external_user_id="open-" + actor, subject="owner",
            external_chat_id="private-" + actor, chat_type="p2p", text="Synthetic question")
        return message.model_copy(update=changes)

    def command(self, conversation: Conversation, kind: str, **changes: Any) -> Any:
        fields = dict(id=kind + "-event", principal_id=self.owner.id,
            conversation_id=conversation.id, kind=kind,
            expected_revision=conversation.input_revision)
        return self.service.apply(Command(**{**fields, **changes}))

    def prepare(self, mode: str = "reference") -> Conversation:
        conversation = self.service.create(self.binding, "Synthetic practice choice",
            personas=("gentle_reviewer", "blunt_coach"), mode=mode)
        preview = self.service.share_preview(conversation.id, self.owner.id)
        self.command(conversation, "share", preview_hash=preview["preview_hash"])
        return self.service.provision(conversation.id)

    def drain(self, identifier: str) -> Conversation:
        for _ in range(60):
            worked = self.worker.run_one(identifier)
            sent = self.dispatcher.dispatch_one(identifier)
            if not worked and not sent:
                return self.service.get(identifier, self.owner.id)
        raise RuntimeError("synthetic_discussion_did_not_terminate")

    def observations(self, conversation: Conversation) -> dict[str, Any]:
        current = self.service.get(conversation.id, self.owner.id)
        artifacts = self.store.list("artifact", current.id, Artifact)
        with self.store.transaction() as db:
            rows = db.execute("SELECT status,error FROM mentor_steps WHERE conversation_id=?",
                              (current.id,)).fetchall()
        return {"status": current.status, "room_status": current.room_status,
            "model_calls": len(self.generator.calls), "sent": len(self.channel.sent),
            "ai_artifacts": sum(item.kind != "user" for item in artifacts),
            "budget_requests": self.service.budgets.status(current)["total_requests"],
            "steps": [{"status": row[0], "error": row[1]} for row in rows]}


@dataclass
class Journal:
    path: Path
    root: Path = field(init=False)
    index: JournalIndex = field(init=False)
    store: DraftStore = field(init=False)
    drafts: DraftService = field(init=False)
    tools: ToolRegistry = field(init=False)

    def __post_init__(self) -> None:
        self.root = self.path / "synthetic-vault"
        (self.root / "templates").mkdir(parents=True)
        (self.root / "templates/daily.md").write_text("# {{date}}\n\n## 🧠 Notes\n")
        self.index = JournalIndex(self.path / "index.sqlite3", self.root)
        self.store = DraftStore(self.path / "drafts.sqlite3")
        self.drafts = DraftService(self.store, self.root, self.index, now=lambda: _FIXED_NOW)
        self.tools = ToolRegistry(RetrievalService(self.index), draft_service=self.drafts)

    def close(self) -> None:
        self.store.close()
        self.index.close()

    def files(self) -> list[Path]:
        return sorted(self.root.glob("daily/*.md"))

    def note(self, text: str, frontmatter: str = "") -> Path:
        path = self.root / "daily/2026-09-01.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(frontmatter + "# 2026-09-01\n\n## 🧠 Notes\n" + text + "\n")
        return path


def _error(action: Callable[[], Any]) -> str | None:
    try:
        action()
    except (MentorError, DraftError) as exc:
        return getattr(exc.code, "value", exc.code)
    return None


def _identity(s: Environment, scenario: str) -> dict[str, Any]:
    if scenario == "cross_user":
        conversation = s.service.create(s.binding, "Synthetic private topic",
            personas=("gentle_reviewer",))
        other = s.identity.register_principal(
            Account(platform="local", tenant="synthetic", subject="other"), "other")
        return {"read_error": _error(lambda: DiscussionHistory(s.service).read(conversation.id, other.id)),
            "command_error": _error(lambda: s.command(conversation, "stop", principal_id=other.id)),
            "owner_status": s.service.get(conversation.id, s.owner.id).status}
    if scenario == "fixed_persona":
        binding = s.bind("gentle_reviewer")
        error = _error(lambda: s.service.create(binding, "Synthetic question",
            personas=("blunt_coach",)))
        return {"error": error, "conversations": len(DiscussionHistory(s.service).list(s.owner.id))}
    if scenario == "chat_conflict":
        s.identity.register_principal(Account(platform="local", tenant="synthetic", subject="other"), "other")
        error = _error(lambda: s.bind("host", subject="other", external_user_id="other-open"))
        return {"error": error, "owner_unchanged": s.bind("host").principal_id == s.owner.id}
    ingress = DiscussionIngress(s.service, s.worker, DiscussionHistory(s.service))
    actor = "host" if scenario == "unmanaged_group" else "blunt_coach"
    error = _error(lambda: ingress.receive(s.apps[actor].id,
        s.message(actor, chat_type="group", external_chat_id="unmanaged-room")))
    return {"error": error, "model_calls": len(s.generator.calls),
        "conversations": len(DiscussionHistory(s.service).list(s.owner.id))}


def _session_isolation(s: Environment) -> dict[str, Any]:
    store = MemoryStore(s.path / "sessions.sqlite3")
    try:
        for owner, persona, chat, marker in (
            ("owner", "gentle_reviewer", "one", "current"),
            ("other", "gentle_reviewer", "one", "other-user"),
            ("owner", "blunt_coach", "one", "other-mentor"),
            ("owner", "gentle_reviewer", "two", "other-chat"),
        ):
            store.append_message(owner, persona, chat, "user", "SyntheticTopic " + marker)
        tools = ToolRegistry(None, memory_store=store)
        context = ToolContext("request", "owner:gentle_reviewer:one", "owner", "gentle_reviewer")
        result = tools.invoke(context, "session_search", '{"query":"SyntheticTopic"}')
        forged = replace(context, session_id="other:gentle_reviewer:one")
        return {"snippets": [item["snippet"] for item in result.payload["items"]],
            "forged_error": tools.invoke(forged, "session_search", '{"query":"SyntheticTopic"}').error}
    finally:
        store.close()


def _linking(s: Environment, scenario: str) -> dict[str, Any]:
    app = s.identity.register_application(Application(platform="feishu", tenant="synthetic",
        external_id="synthetic-feishu", persona_id="gentle_reviewer"))
    other = s.identity.register_application(Application(platform="feishu", tenant="synthetic",
        external_id="synthetic-other", persona_id="blunt_coach"))
    now = [1000.0]
    links = IdentityLinks(s.identity, {app.id, other.id}, lambda: now[0])
    ingress = DiscussionIngress(s.service, s.worker, DiscussionHistory(s.service))
    ingress.identity_links = links
    request = links.create(s.owner.id, app.id)
    message = s.message("gentle_reviewer", subject="feishu-subject", text=request["command"])
    if scenario == "link_wrong_app":
        return {"error": _error(lambda: links.claim(other.id, message)),
            "connected": sum(item["connected"] for item in links.connections(s.owner.id)["items"])}
    receipt = ingress.receive(app.id, message)
    proof = receipt["text"].splitlines()[0].removeprefix("核对码：")
    before = _error(lambda: s.identity.resolve(app.id, message))
    if scenario == "link_expiry":
        now[0] += 601
        return {"error": _error(lambda: links.confirm(request["id"], s.owner.id, proof)),
            "resolve_error": _error(lambda: s.identity.resolve(app.id, message))}
    links.confirm(request["id"], s.owner.id, proof)
    if scenario == "link_two_phase":
        restarted = IdentityService(MentorStore(s.store.path), PersonaRegistry())
        resolved = restarted.resolve(app.id, message)[0]
        return {"before_error": before, "same_owner_after_restart": resolved.id == s.owner.id,
            "other_app_error": _error(lambda: restarted.resolve(other.id, message))}
    with s.store.transaction() as db:
        dump = "\n".join(db.iterdump())
        histories = db.execute("SELECT count(*) FROM mentor_records WHERE kind IN ('conversation','artifact')").fetchone()[0]
    return {"raw_link_token_stored": request["command"].split()[1] in dump,
        "proof_label_stored": "核对码" in dump, "history_records": histories,
        "model_calls": len(s.generator.calls)}


def _audience(s: Environment, scenario: str) -> dict[str, Any]:
    if scenario == "incomplete_room":
        s.channel.incomplete = True
        return {"error": _error(s.prepare), "sent": len(s.channel.sent),
            "model_calls": len(s.generator.calls)}
    conversation = s.prepare()
    s.worker.run_one(conversation.id)
    s.channel.change_room(conversation.room_id, human_subjects=("owner", "stranger"))
    s.dispatcher.dispatch_one(conversation.id)
    return s.observations(conversation)


def _source_boundary(s: Environment, scenario: str) -> dict[str, Any]:
    sources = ControlledSources(s.owner.id, ("gentle_reviewer", "blunt_coach"))
    if scenario == "source_wrong_owner":
        sources.source = sources.source.model_copy(update={"owner_id": "other"})
    elif scenario == "source_wrong_persona":
        sources.source = sources.source.model_copy(update={"allowed_personas": ("gentle_reviewer",)})
    s.service.policy.sources = sources
    conversation = s.prepare()
    if scenario in {"source_wrong_owner", "source_wrong_persona"}:
        s.worker.run_one(conversation.id)
        return {"frozen_sources": len(conversation.source_ids),
            "model_background_sources": len(s.generator.calls[0].background)}
    if scenario == "revoke_before_model":
        sources.enabled = False
        s.worker.run_one(conversation.id)
        return s.observations(conversation)
    s.drain(conversation.id)
    sources.enabled = False
    view = DiscussionHistory(s.service).read(conversation.id, s.owner.id)
    hidden = [item for item in view["artifacts"] if item.get("unavailable")]
    summary_items = [item for summary in view["summaries"] for item in summary["items"]
                     if item.get("unavailable")]
    return {"hidden_artifacts": len(hidden), "hidden_artifact_texts": sum("text" in item for item in hidden),
        "hidden_summary_items": len(summary_items),
        "nonempty_hidden_summary_texts": sum(bool(item["text"]) for item in summary_items)}


def _tool_boundary(s: Environment, j: Journal, scenario: str) -> dict[str, Any]:
    context = ToolContext("request", "owner:gentle_reviewer:chat", "owner", "gentle_reviewer")
    if scenario in {"private_note", "memory_local", "memory_none"}:
        path = j.note("SyntheticSecret about piano practice")
        j.index.build_index()
        setting = {"private_note": "private: true", "memory_local": "memory: local",
                   "memory_none": "memory: none"}[scenario]
        original = j.note("SyntheticSecret about piano practice", "---\n" + setting + "\n---\n").read_bytes()
        stale = j.index.search("SyntheticSecret", include_private=True)
        result = j.tools.invoke(context, "search_journal", '{"query":"SyntheticSecret"}')
        return {"stale_index_hits": len(stale), "cloud_items": len(result.payload["items"]),
            "source_unchanged": path.read_bytes() == original}
    if scenario == "read_without_search":
        j.note("SyntheticSecret about piano practice")
        j.index.build_index()
        source_id = j.index.search("SyntheticSecret")[0].source_id
        result = j.tools.invoke(context, "read_note", json.dumps({"source_id": source_id}))
    elif scenario == "unknown_tool":
        result = j.tools.invoke(context, "shell", '{"command":"write synthetic marker"}')
    else:
        context = replace(context, chat_type="group", allowed_tools=("draft_daily_entry",),
                          purpose="roundtable", execution_guard=lambda: None)
        result = j.tools.invoke(context, "draft_daily_entry", json.dumps({
            "operations": [{"section": "🧠 Notes", "content": "Injected group write"}]}))
    return {"error": result.error, "source_ids": list(result.source_ids), "journal_files": len(j.files())}


def _generation_boundary(s: Environment, scenario: str) -> dict[str, Any]:
    conversation = s.prepare()
    if scenario == "forged_source":
        s.generator.behavior = "forged"
        s.worker.run_one(conversation.id)
        return s.observations(conversation)
    provider = ControlledProvider("unexpected_tool")
    s.worker.generator = ModelGeneration(provider, PersonaRegistry())
    s.worker.run_one(conversation.id)
    observed = s.observations(conversation)
    observed["model_calls"] = provider.calls
    return observed


def _handoff(s: Environment, j: Journal) -> tuple[JournalHandoff, Handoff, Conversation]:
    conversation = s.prepare()
    s.drain(conversation.id)
    artifact = next(item for item in s.store.list("artifact", conversation.id, Artifact)
                    if item.kind == "comparison")
    handoffs = JournalHandoff(DiscussionHistory(s.service), j.drafts)
    selected = handoffs.create(conversation.id, s.owner.id, (artifact.id,))
    return handoffs, selected, conversation


def _save_simple(s: Environment, j: Journal, scenario: str) -> dict[str, Any]:
    handoffs, selected, _ = _handoff(s, j)
    if scenario == "save_group":
        binding = s.binding.model_copy(update={"chat_type": "group"})
        error = _error(lambda: handoffs.confirm(selected.id, binding, "confirm-group"))
    elif scenario == "save_no_preview":
        error = _error(lambda: handoffs.confirm(selected.id, s.binding, "confirm-unseen"))
    else:
        handoffs.preview(selected.id, s.binding, "preview")
        saved = s.store.read("handoff", selected.id, Handoff)
        if scenario == "save_expiry":
            j.drafts._now = lambda: _FIXED_NOW + timedelta(hours=1)
            error = _error(lambda: handoffs.confirm(selected.id, s.binding, "expired"))
        else:
            draft = j.drafts.get_draft(saved.draft_id)
            error = _error(lambda: j.drafts.commit_draft(draft.draft_id,
                user_id="owner", token=draft.token))
    return {"error": error, "journal_files": len(j.files())}


def _save_revision(s: Environment, j: Journal) -> dict[str, Any]:
    handoffs, selected, _ = _handoff(s, j)
    handoffs.preview(selected.id, s.binding, "preview")
    revised = handoffs.revise(selected.id, s.binding, "edit", text="EditedSyntheticAdvice is a suggestion.",
                              target_date=date(2026, 8, 1))
    revised_id = revised.split("/确认转交 ")[-1]
    error = _error(lambda: handoffs.confirm(selected.id, s.binding, "old-confirm"))
    before = len(j.files())
    handoffs.confirm(revised_id, s.binding, "new-confirm")
    path = j.files()[0]
    spans = parse_note(path, j.root).content_spans
    provenance = next(span.provenance for span in spans if span.provenance)
    return {"old_error": error, "files_before_confirm": before, "saved_name": path.name,
        "edited_by_user": provenance.edited_by_user,
        "advice_in_personal_body": "EditedSyntheticAdvice" in personal_body(path.read_text())}


def _save_duplicate(s: Environment, j: Journal) -> dict[str, Any]:
    handoffs, selected, _ = _handoff(s, j)
    handoffs.preview(selected.id, s.binding, "preview")
    before = len(j.files())
    handoffs.confirm(selected.id, s.binding, "first-confirm")
    original = j.files()[0].read_bytes()
    handoffs.confirm(selected.id, s.binding, "second-confirm")
    return {"files_before_confirm": before, "journal_files": len(j.files()),
        "unchanged_after_duplicate": j.files()[0].read_bytes() == original,
        "ai_blocks": original.count(b"<!-- riji:ai-discussion-result ")}


def _save_revocation(s: Environment, j: Journal) -> dict[str, Any]:
    sources = ControlledSources(s.owner.id, ("gentle_reviewer", "blunt_coach"))
    s.service.policy.sources = sources
    handoffs, selected, _ = _handoff(s, j)
    handoffs.preview(selected.id, s.binding, "preview")
    sources.enabled = False
    error = _error(lambda: handoffs.confirm(selected.id, s.binding, "after-revocation"))
    return {"error": error, "journal_files": len(j.files())}


def _ai_lifecycle(s: Environment, j: Journal) -> dict[str, Any]:
    handoffs, selected, _ = _handoff(s, j)
    handoffs.preview(selected.id, s.binding, "preview")
    handoffs.confirm(selected.id, s.binding, "confirm")
    path = j.files()[0]
    path.write_text(path.read_text() + "\n- PersonalFact: practiced piano for ten minutes.\n")
    j.index.update_note(path)
    retrieval = RetrievalService(j.index)
    context = ToolContext("request", "session", "owner", "gentle_reviewer")
    normal = retrieval.search_journal(context, "SyntheticAdvice")
    ai = retrieval.search_journal(replace(context, include_ai_discussions=True), "SyntheticAdvice")
    evidence = read_source(path, JournalMemoryPolicy(j.root, "owner", ("🧠 Notes",),
        segment_chars=100, settle_seconds=0)).evidence
    return {"default_ai_hits": len(normal.items), "explicit_ai_hits": len(ai.items),
        "retrieved_content_type": ai.items[0].content_type if ai.items else None,
        "retrieved_provenance": bool(ai.items and ai.items[0].content_spans[0].provenance),
        "ai_in_extraction": any("SyntheticAdvice" in item.text for item in evidence),
        "personal_fact_in_extraction": any("PersonalFact" in item.text for item in evidence)}


def _failure(s: Environment, scenario: str) -> dict[str, Any]:
    conversation = s.prepare()
    provider = None
    if scenario in {"failure_auth", "failure_quota"}:
        provider = ControlledProvider(scenario.removeprefix("failure_"))
        s.worker.generator = ModelGeneration(provider, PersonaRegistry())
    else:
        s.generator.behavior = "timeout" if scenario == "failure_timeout" else "malformed"
    s.worker.run_one(conversation.id)
    replay = s.worker.run_one(conversation.id)
    observed = s.observations(conversation)
    observed["automatic_retry_progress"] = replay
    if provider is not None:
        observed["model_calls"] = provider.calls
    if scenario == "failure_timeout":
        observed["resume_error"] = _error(lambda: s.command(conversation, "resume"))
    return observed


def _unknown_delivery(s: Environment) -> dict[str, Any]:
    conversation = s.prepare()
    s.worker.run_one(conversation.id)
    s.worker.run_one(conversation.id)
    s.channel.outcome = "unknown"
    s.dispatcher.dispatch_one(conversation.id)
    following = s.dispatcher.dispatch_one(conversation.id)
    return {**s.observations(conversation), "following_dispatch": following,
        "delivery_states": [item.status for item in s.store.list("delivery", conversation.id, Delivery)]}


def _stop_race(s: Environment) -> dict[str, Any]:
    conversation = s.prepare()
    s.generator.after_send = lambda: s.command(conversation, "stop")
    s.worker.run_one(conversation.id)
    dispatch = s.dispatcher.dispatch_one(conversation.id)
    return {**s.observations(conversation), "dispatch_after_stop": dispatch}


def _restart(s: Environment) -> dict[str, Any]:
    conversation = s.prepare()
    s.worker.run_one(conversation.id)
    delivery = s.store.list("delivery", conversation.id, Delivery)[0]
    # A persisted sending state represents a crash between send and receipt.
    with s.store.transaction() as db:
        s.store.put(db, "delivery", delivery.model_copy(update={"status": "sending", "attempts": 1}), conversation.id)
    store = MentorStore(s.store.path)
    identity = IdentityService(store, PersonaRegistry())
    restored = DiscussionService(store, identity,
        DiscussionPolicy(store, NoSources(), ControlledChannel(store), now=_clock), now=_clock)
    recovered = restored.recover()
    repeat = restored.recover()
    current = restored.get(conversation.id, s.owner.id)
    return {"recovered": recovered, "repeat_recovered": repeat, "status": current.status,
        "delivery_states": [item.status for item in store.list("delivery", current.id, Delivery)],
        "budget_requests": restored.budgets.status(current)["total_requests"],
        "resume_error": _error(lambda: restored.apply(Command(id="resume", principal_id=s.owner.id,
            conversation_id=current.id, kind="resume", expected_revision=current.input_revision)))}


def _partial_budget(s: Environment) -> dict[str, Any]:
    conversation = s.prepare()
    s.drain(conversation.id)
    initial = s.service.budgets.status(conversation)["total_requests"]
    with s.store.transaction() as db:
        db.execute("UPDATE mentor_budgets SET requests=23 WHERE id=?", (conversation.id,))
    s.command(conversation, "debate")
    final = s.drain(conversation.id)
    return {"initial_requests": initial, "final_requests": s.service.budgets.status(final)["total_requests"],
        "status": final.status, "last_stage": s.generator.calls[-1].stage,
        "opinion_calls": sum(call.stage == "opinion" for call in s.generator.calls)}


_IDENTITY = {"cross_user", "fixed_persona", "chat_conflict", "unmanaged_group", "nonhost_group"}
_LINKS = {"link_two_phase", "link_credentials", "link_wrong_app", "link_expiry"}
_SOURCES = {"source_wrong_owner", "source_wrong_persona", "revoke_before_model", "revoked_history"}
_TOOLS = {"private_note", "memory_local", "memory_none", "read_without_search", "unknown_tool", "group_draft"}
_SAVE_SIMPLE = {"save_group", "save_no_preview", "save_expiry", "save_token_bypass"}
_FAILURES = {"failure_timeout", "failure_quota", "failure_auth", "failure_malformed"}


def _dispatch(s: Environment, j: Journal, scenario: str) -> dict[str, Any]:
    if scenario in _IDENTITY:
        return _identity(s, scenario)
    if scenario == "session_isolation":
        return _session_isolation(s)
    if scenario in _LINKS:
        return _linking(s, scenario)
    if scenario in {"member_change", "incomplete_room"}:
        return _audience(s, scenario)
    if scenario in _SOURCES:
        return _source_boundary(s, scenario)
    if scenario in _TOOLS:
        return _tool_boundary(s, j, scenario)
    if scenario in {"forged_source", "unexpected_tool"}:
        return _generation_boundary(s, scenario)
    if scenario in _SAVE_SIMPLE:
        return _save_simple(s, j, scenario)
    journal_handlers = {"save_revision": _save_revision, "save_duplicate": _save_duplicate,
        "save_revocation": _save_revocation, "ai_lifecycle": _ai_lifecycle}
    if scenario in journal_handlers:
        return journal_handlers[scenario](s, j)
    if scenario in _FAILURES:
        return _failure(s, scenario)
    handlers = {"unknown_delivery": _unknown_delivery, "stop_race": _stop_race,
        "restart": _restart, "partial_budget": _partial_budget}
    if scenario not in handlers:
        raise ValueError("unsupported_boundary_scenario")
    return handlers[scenario](s)


def evaluate(case: dict[str, Any], provider: LLMProvider | None = None) -> dict[str, Any]:
    """Return observed state only; provider is deliberately unused in this family."""
    if (case.get("family") != "boundary" or case.get("data_class") != "synthetic"
            or not isinstance(case.get("scenario"), str)):
        raise ValueError("invalid_boundary_input")
    with TemporaryDirectory(prefix="riji-eval-boundary-") as temporary:
        path = Path(temporary).resolve()
        system = Environment(path)
        journal = Journal(path)
        try:
            observed = _dispatch(system, journal, case["scenario"])
            return {"output": {"observed": observed, "execution_kind": "synthetic_service_regression"},
                "metrics": {"live_model_calls": 0, "scenario_count": 1}}
        finally:
            journal.close()
