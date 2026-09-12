"""Authenticated Hermes group ingress, with source-free receipt diagnostics."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from types import SimpleNamespace
from typing import Callable, Literal

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from riji_agent.mentors.feishu import normalize_host_group
from riji_agent.mentors.group_dialogue import is_stop_intent
from riji_agent.mentors.models import Application, AudienceGrant, Conversation, MentorError, Principal, Record
from riji_agent.mentors.store import key

DIAGNOSTIC_COMMAND = "/主持接入检查"


class HostEvent(Record):
    raw_event: dict


class DiagnosticRequest(Record):
    expected_chat_id: str = Field(min_length=1, max_length=300)


class AdoptionRequest(DiagnosticRequest):
    replace_conversation_id: str = Field(default="", max_length=300)


class HostReceipt(Record):
    id: str
    owner_id: str
    fingerprint: str
    status: Literal["accepted", "pending", "rejected"] = "pending"
    code: str = "host_event_reconciliation_required"
    received_at: float


class HostDiagnostic(Record):
    id: str
    owner_id: str
    application_id: str
    expected_chat_id: str
    expires_at: float
    status: Literal["pending", "received", "expired"] = "pending"
    received_at: float = 0
    message_hash: str = ""


class HostNotice(Record):
    id: str
    owner_id: str
    conversation_id: str
    chat_id: str
    input_revision: int
    cancel_epoch: int
    text: str = Field(max_length=12000)
    created_at: float
    status: Literal["pending", "sending", "sent", "unknown", "cancelled"] = "pending"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def register_host_owners(runtime, allowed_users: frozenset[str]) -> None:
    """Use the original app's allowlist to link existing legacy owners only."""
    host = runtime.legacy_host
    if host is None:
        return
    with runtime.store.transaction() as db:
        for external_id in allowed_users:
            owner = runtime.store.lookup(db, "legacy_owner", external_id)
            if not owner or runtime.store.get(db, "principal", owner, Principal) is None:
                continue
            external = key(host.platform, host.tenant, host.id, external_id)
            previous = runtime.store.lookup(db, "external_user", external)
            if previous and previous != owner:
                raise MentorError("host_owner_mapping_conflict")
            runtime.store.bind(db, "external_user", external, owner)


def member_resolver(store):
    """Resolve app-scoped members without enrolling identities from a group."""
    def resolve(app: Application, external_id: str) -> Principal | None:
        with store.transaction() as db:
            owner = store.lookup(db, "external_user", key(app.platform, app.tenant, app.id, external_id))
            return store.get(db, "principal", owner, Principal) if owner else None
    return resolve


class HostGroupBridge:
    def __init__(self, runtime, shared_secret: str, allowed_users: frozenset[str],
                 now: Callable[[], float] = time.time) -> None:
        self.runtime, self.store, self.host = runtime, runtime.store, runtime.legacy_host
        self._secret, self.allowed, self.now = shared_secret, frozenset(allowed_users), now
        self._lock = threading.RLock()
        self.runtime.service.policy.group_owner_authorizer = self._principal
        from riji_agent.mentors.group_adoption import GroupAdoptions
        self.adoptions = GroupAdoptions(self)
        from riji_agent.mentors.host_lifecycle import HostLifecycle
        self.lifecycle = HostLifecycle(self)
        with self.store.transaction() as db:
            for notice in self._notices(db):
                if notice.status == "sending":
                    self._save_notice(db, notice, "unknown")

    def authenticate(self, supplied: str) -> None:
        if not self._secret or not supplied or not hmac.compare_digest(supplied.encode(), self._secret.encode()):
            raise MentorError("host_authentication_required")

    def receive(self, raw: dict) -> dict:
        message = self._normalize(raw)
        principal = self._principal(message.external_user_id)
        identifier = _digest(key(self.host.id, message.external_chat_id, message.message_id))
        fingerprint = _digest(message.model_dump_json(exclude={"delivery_id"}))
        if is_stop_intent(message.text) or message.text.lstrip().startswith(("/停止 ", "/删除 ")):
            return self._urgent(message, principal.id, identifier, fingerprint)
        with self._lock:
            diagnostic = self._contains_diagnostic(message.text)
            saved = self._begin(identifier, principal.id, fingerprint, requires_notice=not diagnostic)
            if saved is not None:
                return self._response(saved, duplicate=True)
            notice = None
            try:
                if diagnostic:
                    self._claim_diagnostic(message, principal.id)
                    status, code = "accepted", "host_diagnostic_received"
                else:
                    if self.adoptions.pending(message.external_chat_id, principal.id):
                        conversation = self.adoptions.consume(message, principal.id,
                            int(raw["event"]["message"]["create_time"]) / 1000)
                    else:
                        conversation = self._managed(message, principal.id)
                    result = self.runtime.ingress.receive(self.host.id, message)
                    if isinstance(result.get("text"), str) and result["text"]:
                        notice = (conversation, result)
                    status, code = "accepted", "host_event_accepted"
            except MentorError as exc:
                status = "pending" if exc.code == "incoming_reconciliation_required" else "rejected"
                code = exc.code
            except Exception:
                status, code = "pending", "host_event_reconciliation_required"
            receipt = self._finish(identifier, status, code, notice)
        return self._response(receipt)

    def _urgent(self, message, owner_id: str, identifier: str, fingerprint: str) -> dict:
        """Use existing durable ingress receipts without the bridge queue lock."""
        conversation = self._managed(message, owner_id)
        receipt = HostReceipt(id=identifier, owner_id=owner_id, fingerprint=fingerprint,
                              received_at=self.now())
        try:
            result = self.runtime.ingress.receive(self.host.id, message)
            if isinstance(result.get("text"), str) and result["text"]:
                with self.store.transaction() as db:
                    self._enqueue_notice(db, receipt, conversation, result)
            receipt = receipt.model_copy(update={"status": "accepted", "code": "host_event_accepted"})
            return self._response(receipt, duplicate=result.get("status") == "duplicate")
        except MentorError as exc:
            pending = exc.code == "incoming_reconciliation_required"
            receipt = receipt.model_copy(update={"status": "pending" if pending else "rejected", "code": exc.code})
        except Exception:
            pass
        return self._response(receipt)

    def _contains_diagnostic(self, text: str) -> bool:
        if DIAGNOSTIC_COMMAND in text:
            return True
        candidates = re.findall(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])", text)
        with self.store.transaction() as db:
            return any(self.store.get(db, "host_diagnostic", _digest(value), HostDiagnostic)
                       is not None for value in candidates)

    def _normalize(self, raw: dict):
        if self.host is None:
            raise MentorError("host_bridge_unconfigured")
        try:
            from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
            if (raw.get("schema") != "2.0" or not isinstance(raw.get("header"), dict)
                    or not isinstance(raw.get("event"), dict)):
                raise ValueError
            created = raw["event"].get("message", {}).get("create_time")
            if (not isinstance(created, str) or not created.isascii() or not created.isdigit()
                    or len(created) > 16 or not self.now() - 86400 <= int(created) / 1000 <= self.now() + 300):
                raise ValueError
            if (raw["header"].get("event_type") != "im.message.receive_v1"
                    or raw["header"].get("tenant_key") != self.host.tenant):
                raise ValueError
            message = normalize_host_group(P2ImMessageReceiveV1(raw), self.host)
            if message.action_id or not message.text:
                raise ValueError
            return message
        except Exception:
            raise MentorError("host_group_event_invalid") from None

    def _principal(self, external_id: str) -> Principal:
        if external_id not in self.allowed:
            raise MentorError("host_sender_not_allowed")
        with self.store.transaction() as db:
            owner = self.store.lookup(db, "legacy_owner", external_id)
            mapped = self.store.lookup(db, "external_user",
                key(self.host.platform, self.host.tenant, self.host.id, external_id))
            principal = self.store.get(db, "principal", owner, Principal) if owner else None
        if principal is None or mapped != principal.id:
            raise MentorError("host_owner_unmapped")
        return principal

    def _managed(self, message, owner_id: str) -> Conversation:
        with self.store.transaction() as db:
            identifier = self.store.lookup(db, "room",
                key(self.host.platform, self.host.tenant, message.external_chat_id))
            conversation = self.store.get(db, "conversation", identifier, Conversation) if identifier else None
        if (conversation is None or conversation.owner_id != owner_id
                or conversation.kind != "roundtable" or conversation.status == "deleted"):
            raise MentorError("unmanaged_group")
        return conversation

    def _begin(self, identifier: str, owner_id: str, fingerprint: str, *, requires_notice: bool) -> HostReceipt | None:
        with self.store.transaction() as db:
            previous = self.store.get(db, "host_receipt", identifier, HostReceipt)
            if previous:
                if previous.owner_id != owner_id or previous.fingerprint != fingerprint:
                    raise MentorError("host_event_conflict")
                return previous
            self._prune_receipts(db)
            outstanding = self._prune_notices(db)
            if requires_notice and outstanding >= 200:
                raise MentorError("host_control_outbox_full")
            self.store.put(db, "host_receipt", HostReceipt(id=identifier, owner_id=owner_id,
                fingerprint=fingerprint, received_at=self.now()), owner_id)
        return None

    def _prune_receipts(self, db) -> None:
        rows = db.execute("SELECT value FROM mentor_records WHERE kind='host_receipt' ORDER BY rowid DESC").fetchall()
        retained, pending = 0, 0
        for row in rows:
            record = HostReceipt.model_validate_json(row[0])
            if record.status == "pending":
                pending += 1
            elif record.received_at < self.now() - 7 * 86400 or retained >= 2000:
                db.execute("DELETE FROM mentor_records WHERE kind='host_receipt' AND id=?", (record.id,))
                notice = self.store.get(db, "host_notice", record.id, HostNotice)
                if notice is not None and notice.status not in {"pending", "sending"}:
                    db.execute("DELETE FROM mentor_records WHERE kind='host_notice' AND id=?", (record.id,))
            else:
                retained += 1
        if pending >= 200:
            raise MentorError("host_receipt_capacity_reached")

    def _prune_notices(self, db) -> int:
        pending, retained = 0, 0
        for notice in reversed(self._notices(db)):
            if notice.status == "pending" and notice.created_at < self.now() - 86400:
                self._save_notice(db, notice, "cancelled")
                notice = notice.model_copy(update={"status": "cancelled", "text": ""})
            if notice.status in {"pending", "sending"}:
                pending += 1
            elif notice.created_at < self.now() - 7 * 86400 or retained >= 2000:
                db.execute("DELETE FROM mentor_records WHERE kind='host_notice' AND id=?", (notice.id,))
            else:
                retained += 1
        return pending

    def _finish(self, identifier: str, status: str, code: str, notice=None) -> HostReceipt:
        with self.store.transaction() as db:
            receipt = self.store.get(db, "host_receipt", identifier, HostReceipt)
            receipt = receipt.model_copy(update={"status": status, "code": code})
            self.store.put(db, "host_receipt", receipt, receipt.owner_id)
            if status == "accepted" and notice is not None:
                self._enqueue_notice(db, receipt, *notice)
            return receipt

    def _enqueue_notice(self, db, receipt: HostReceipt, previous: Conversation, result: dict) -> None:
        if self._prune_notices(db) >= 200:
            return
        current = self.store.get(db, "conversation", previous.id, Conversation)
        revision = result.get("input_revision", previous.input_revision)
        if current is None or current.status == "deleted" or current.input_revision != revision:
            return
        notice = HostNotice(id=receipt.id, owner_id=receipt.owner_id, conversation_id=current.id,
            chat_id=current.room_id, input_revision=revision, cancel_epoch=current.cancel_epoch,
            text=result["text"], created_at=self.now())
        self.store.put(db, "host_notice", notice, receipt.owner_id)

    def dispatch_notice(self, conversation_id: str = "") -> bool:
        notice = self._claim_notice(conversation_id)
        if notice is None:
            return False
        status = "unknown"
        try:
            current = self.store.read("conversation", notice.conversation_id, Conversation)
            if not self._notice_current(notice, current):
                raise MentorError("host_notice_stale")
            self._control_audience(current)
            current = self.store.read("conversation", notice.conversation_id, Conversation)
            if not self._notice_current(notice, current):
                raise MentorError("host_notice_stale")
            delivery = SimpleNamespace(application_id=self.host.id, chat_id=notice.chat_id,
                                       uuid=notice.id[:32])
            result = self.runtime.service.policy.channel.send(delivery, notice.text)
            status = "sent" if result.status == "sent" and result.message_id else "unknown"
        except MentorError:
            status = "cancelled"
        except Exception:
            pass
        with self.store.transaction() as db:
            self._save_notice(db, notice, status)
        return True

    def _control_audience(self, current: Conversation) -> None:
        """A failed control receipt must not turn an owner's stop into a run."""
        owner = self.store.read("principal", current.owner_id, Principal)
        if owner is None or self._principal(owner.legacy_owner_key).id != current.owner_id:
            raise MentorError("host_notice_owner_unavailable")
        grant = self.store.read("grant", current.grant_id, AudienceGrant)
        if (grant is None or not grant.active or grant.owner_id != current.owner_id
                or grant.input_revision != current.input_revision or current.room_status not in {"ready", "ended"}):
            raise MentorError("host_notice_audience_unverified")
        try:
            policy = self.runtime.service.policy
            if current.source_scope == "group_only":
                policy.group_content(current)
            snapshot = (policy.group_snapshot(current) if current.source_scope == "group_only"
                        else policy.channel.inspect_room(current.room_id))
            valid = (snapshot.complete and snapshot.private and snapshot.management_restricted
                     and (current.source_scope == "group_only" or (snapshot.history_restricted and snapshot.continuity_verified))
                     and snapshot == grant.snapshot and self.host.id in snapshot.application_ids)
        except Exception:
            valid = False
        if not valid:
            raise MentorError("host_notice_audience_unverified")

    def _claim_notice(self, conversation_id: str) -> HostNotice | None:
        with self.store.transaction() as db:
            for notice in self._notices(db):
                if notice.status != "pending" or (conversation_id and notice.conversation_id != conversation_id):
                    continue
                current = self.store.get(db, "conversation", notice.conversation_id, Conversation)
                if not self._notice_current(notice, current) or notice.created_at < self.now() - 86400:
                    self._save_notice(db, notice, "cancelled")
                    continue
                updated = notice.model_copy(update={"status": "sending"})
                self.store.put(db, "host_notice", updated, notice.owner_id)
                return updated
        return None

    @staticmethod
    def _notice_current(notice: HostNotice, current: Conversation | None) -> bool:
        return (current is not None and current.status != "deleted" and current.owner_id == notice.owner_id
                and current.room_id == notice.chat_id and current.input_revision == notice.input_revision
                and current.cancel_epoch == notice.cancel_epoch)

    @staticmethod
    def _notices(db) -> list[HostNotice]:
        rows = db.execute("SELECT value FROM mentor_records WHERE kind='host_notice' ORDER BY rowid").fetchall()
        return [HostNotice.model_validate_json(row[0]) for row in rows]

    def _save_notice(self, db, notice: HostNotice, status: str) -> None:
        self.store.put(db, "host_notice", notice.model_copy(update={"status": status, "text": ""}), notice.owner_id)

    @staticmethod
    def _response(receipt: HostReceipt, duplicate: bool = False) -> dict:
        return {"status": receipt.status, "code": receipt.code, "receipt_id": receipt.id,
                "duplicate": duplicate, "delivery": "backend_only", "hermes_reply": False}

    def create_diagnostic(self, owner_id: str, expected_chat_id: str) -> dict:
        self._diagnostic_room(owner_id, expected_chat_id)
        token = secrets.token_urlsafe(32)
        record = HostDiagnostic(id=_digest(token), owner_id=owner_id, application_id=self.host.id,
                                expected_chat_id=expected_chat_id, expires_at=self.now() + 600)
        with self.store.transaction() as db:
            for previous in self.store.records(db, "host_diagnostic", owner_id, HostDiagnostic):
                if previous.status == "pending":
                    self.store.put(db, "host_diagnostic", previous.model_copy(update={"status": "expired"}), owner_id)
            self.store.put(db, "host_diagnostic", record, owner_id)
        return {"id": record.id, "command": DIAGNOSTIC_COMMAND + " " + token,
                "expires_at": record.expires_at, "purpose": "receipt_only", "discussions_enabled": False}

    def _diagnostic_room(self, owner_id: str, expected_chat_id: str) -> None:
        if self.host is None:
            raise MentorError("host_bridge_unconfigured")
        channel = self.runtime.service.policy.channel.adapters.get("feishu")
        if channel is None:
            raise MentorError("host_diagnostic_room_unverified")
        try:
            evidence = channel.inspect_room_evidence(expected_chat_id)
            owners = {self._principal(external_id).id for external_id in evidence.human_open_ids}
            valid = (evidence.snapshot.private and evidence.human_pages_complete
                     and evidence.known_bot_count_matches and evidence.settings_stable
                     and owners == {owner_id} and len(evidence.human_open_ids) == 1
                     and self.host.id in evidence.known_application_ids)
        except Exception:
            valid = False
        if not valid:
            raise MentorError("host_diagnostic_room_unverified")

    def _claim_diagnostic(self, message, owner_id: str) -> None:
        parts = message.text.strip().split()
        if len(parts) != 2 or parts[0] != DIAGNOSTIC_COMMAND or len(parts[1]) != 43:
            raise MentorError("host_diagnostic_invalid")
        with self.store.transaction() as db:
            record = self.store.get(db, "host_diagnostic", _digest(parts[1]), HostDiagnostic)
            if (record is None or record.owner_id != owner_id or record.application_id != self.host.id
                    or record.expected_chat_id != message.external_chat_id or record.status != "pending"
                    or record.expires_at <= self.now()):
                raise MentorError("host_diagnostic_invalid")
            updated = record.model_copy(update={"status": "received", "received_at": self.now(),
                                                "message_hash": _digest(message.message_id)})
            self.store.put(db, "host_diagnostic", updated, owner_id)

    def diagnostic(self, owner_id: str, identifier: str) -> dict:
        record = self.store.read("host_diagnostic", identifier, HostDiagnostic)
        if record is None or record.owner_id != owner_id:
            raise MentorError("host_diagnostic_unavailable")
        status = "expired" if record.status == "pending" and record.expires_at <= self.now() else record.status
        return {"id": record.id, "status": status, "expires_at": record.expires_at,
                "received_at": record.received_at, "purpose": "receipt_only", "discussions_enabled": False}

    def status(self, owner_id: str) -> dict:
        receipts = self.store.list("host_receipt", owner_id, HostReceipt)
        notices = self.store.list("host_notice", owner_id, HostNotice)
        counts = {state: sum(item.status == state for item in receipts)
                  for state in ("accepted", "pending", "rejected")}
        return {"configured": self.host is not None, "receiver": "hermes", "delivery": "backend_only",
                "receipt_counts": counts, "last_received_at": max((item.received_at for item in receipts), default=0),
                "control_outbox_counts": {state: sum(item.status == state for item in notices)
                    for state in ("pending", "sending", "sent", "unknown", "cancelled")},
                "group_only_available": True, "personal_sources_enabled": False,
                "discussions_enabled": any(item.source_scope == "group_only" and item.room_status in {"ready", "ended"}
                    and item.status not in {"deleted", "archived"} for item in self.store.list("conversation", owner_id, Conversation))}


class HostBridgeDispatcher:
    """One backend dispatcher owns both control notices and model deliveries."""

    def __init__(self, bridge: HostGroupBridge, downstream) -> None:
        self.bridge, self.downstream = bridge, downstream

    def dispatch_one(self, conversation_id: str = "") -> bool:
        return self.bridge.dispatch_notice(conversation_id) or self.downstream.dispatch_one(conversation_id)

    def retry_known_unsent(self, delivery_id: str, principal_id: str) -> None:
        self.downstream.retry_known_unsent(delivery_id, principal_id)


def attach_host_routes(router, runtime, owned, safe) -> None:
    def bridge():
        value = getattr(runtime, "host_bridge", None)
        if value is None:
            raise HTTPException(503, "host_bridge_unconfigured")
        return value

    @router.post("/host-events")
    def incoming(body: HostEvent, request: Request):
        current = bridge()
        try:
            current.authenticate(request.headers.get("x-hermes-secret", ""))
        except MentorError:
            raise HTTPException(401, "host_authentication_required") from None
        try:
            return current.receive(body.raw_event)
        except MentorError as exc:
            return JSONResponse(status_code=409, content={"status": "rejected", "code": exc.code,
                "delivery": "backend_only", "hermes_reply": False})
        except Exception:
            return JSONResponse(status_code=503, content={"status": "pending",
                "code": "host_event_reconciliation_required", "delivery": "backend_only", "hermes_reply": False})

    @router.post("/host-lifecycle")
    def lifecycle(body: HostEvent, request: Request):
        current = bridge()
        try:
            current.authenticate(request.headers.get("x-hermes-secret", ""))
        except MentorError:
            raise HTTPException(401, "host_authentication_required") from None
        return safe(lambda: current.lifecycle.receive(body.raw_event))

    @router.post("/host-groups/adoptions")
    def adopt(body: AdoptionRequest, request: Request):
        owner = owned(request)
        return safe(lambda: bridge().adoptions.prepare(owner, body.expected_chat_id, body.replace_conversation_id))

    @router.post("/host-groups/{identifier}/revalidate")
    def revalidate(identifier: str, request: Request):
        owner = owned(request)
        return safe(lambda: bridge().adoptions.revalidate(owner, identifier))

    @router.post("/host-diagnostics")
    def create(body: DiagnosticRequest, request: Request):
        owner = owned(request)
        return safe(lambda: bridge().create_diagnostic(owner, body.expected_chat_id))

    @router.get("/host-diagnostics/{identifier}")
    def diagnostic(identifier: str, request: Request):
        owner = owned(request)
        return safe(lambda: bridge().diagnostic(owner, identifier))

    @router.get("/host-status")
    def status(request: Request):
        owner = owned(request)
        return safe(lambda: bridge().status(owner))
