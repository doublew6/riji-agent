"""Verified channel mappings preserve legacy memory owners and fixed personas."""

from __future__ import annotations

from riji_agent.mentors.models import Account, Application, ChatBinding, Envelope, MentorError, Principal
from riji_agent.mentors.store import MentorStore, key
from riji_agent.personas.registry import PersonaRegistry


class IdentityService:
    def __init__(self, store: MentorStore, personas: PersonaRegistry) -> None:
        self.store = store
        self.personas = personas

    def register_principal(self, account: Account, legacy_owner_key: str) -> Principal:
        """Operator-only enrollment from a previously verified account."""
        if not all((account.platform, account.tenant, account.subject, legacy_owner_key)):
            raise MentorError("invalid_identity")
        identity = key(account.platform, account.tenant, account.subject)
        with self.store.transaction() as db:
            old_id = self.store.lookup(db, "principal", identity)
            if old_id:
                old = self.store.get(db, "principal", old_id, Principal)
                if old.legacy_owner_key != legacy_owner_key:
                    raise MentorError("identity_conflict")
                return old
            alias = self.store.lookup(db, "legacy_owner", legacy_owner_key)
            if alias:
                raise MentorError("identity_link_required")
            principal = Principal(account=account, legacy_owner_key=legacy_owner_key)
            self.store.put(db, "principal", principal)
            self.store.bind(db, "principal", identity, principal.id)
            self.store.bind(db, "legacy_owner", legacy_owner_key, principal.id)
            return principal

    def register_application(self, application: Application) -> Application:
        if application.role == "mentor":
            self.personas.get(application.persona_id)
        elif application.persona_id != "host":
            raise MentorError("invalid_host_role")
        identity = key(application.platform, application.tenant, application.external_id)
        with self.store.transaction() as db:
            old_id = self.store.lookup(db, "application", identity)
            if old_id:
                old = self.store.get(db, "application", old_id, Application)
                if (old.persona_id, old.role) != (application.persona_id, application.role):
                    raise MentorError("application_role_conflict")
                return old
            self.store.put(db, "application", application)
            self.store.bind(db, "application", identity, application.id)
            return application

    def resolve(self, application_id: str, message: Envelope) -> tuple[Principal, Application, ChatBinding]:
        """Only a trusted authenticated transport supplies the application id."""
        with self.store.transaction() as db:
            app = self.store.get(db, "application", application_id, Application)
            if app is None or message.sender_kind != "user":
                raise MentorError("sender_not_allowed")
            identity = key(app.platform, app.tenant, message.subject)
            external = key(app.platform, app.tenant, app.id, message.external_user_id)
            principal_id = self.store.lookup(db, "external_user", external) if app.platform == "feishu" else (
                self.store.lookup(db, "principal", identity) if message.subject else None)
            if principal_id is None:
                raise MentorError("identity_verification_required")
            principal = self.store.get(db, "principal", principal_id, Principal)
            if principal is None:
                raise MentorError("identity_verification_required")
            if app.platform == "feishu":
                self._verify_subject(db, app, message, principal_id)
            self._bind_external_user(db, app, message, principal_id)
            binding = self._chat_binding(db, app, message, principal_id)
            return principal, app, binding

    def link_verified_account(self, db, app: Application, link) -> None:
        """Called only after owner authentication and target DM proof, atomically."""
        principal = self.store.get(db, "principal", link.owner_id, Principal)
        if principal is None or app.id != link.application_id or app.platform != "feishu":
            raise MentorError("identity_link_owner_unavailable")
        message = Envelope(delivery_id="identity-link", message_id="identity-link",
            external_user_id=link.external_user_id, subject=link.subject,
            external_chat_id=link.chat_id, chat_type="p2p", text="")
        self._verify_subject(db, app, message, principal.id)
        self._bind_external_user(db, app, message, principal.id)
        self._chat_binding(db, app, message, principal.id)

    def _verify_subject(self, db, app: Application, message: Envelope, principal_id: str) -> None:
        external = key(app.platform, app.tenant, app.id, message.external_user_id)
        previous = self.store.lookup(db, "external_subject", external)
        if not message.subject:
            return
        subject = key(app.platform, app.tenant, message.subject)
        owner = self.store.lookup(db, "principal", subject)
        if (previous and previous != message.subject) or (owner and owner != principal_id):
            raise MentorError("identity_conflict")
        self.store.bind(db, "principal", subject, principal_id)
        self.store.bind(db, "external_subject", external, message.subject)

    def _bind_external_user(self, db, app: Application, message: Envelope, principal_id: str) -> None:
        identity = key(app.platform, app.tenant, app.id, message.external_user_id)
        old = self.store.lookup(db, "external_user", identity)
        if old and old != principal_id:
            raise MentorError("identity_conflict")
        self.store.bind(db, "external_user", identity, principal_id)

    def _chat_binding(self, db, app: Application, message: Envelope, principal_id: str) -> ChatBinding:
        identity = key(app.platform, app.tenant, app.id, message.external_chat_id)
        binding_id = self.store.lookup(db, "chat", identity)
        if binding_id:
            binding = self.store.get(db, "chat", binding_id, ChatBinding)
            if binding.principal_id != principal_id or binding.chat_type != message.chat_type:
                raise MentorError("chat_binding_conflict")
            return binding
        binding = ChatBinding(principal_id=principal_id, application_id=app.id,
                              external_chat_id=message.external_chat_id, chat_type=message.chat_type)
        self.store.put(db, "chat", binding, principal_id)
        self.store.bind(db, "chat", identity, binding.id)
        return binding

    def select_conversation(self, binding: ChatBinding, persona_id: str, conversation_id: str) -> None:
        from riji_agent.mentors.models import Conversation
        with self.store.transaction() as db:
            conversation = self.store.get(db, "conversation", conversation_id, Conversation)
            if (conversation is None or conversation.owner_id != binding.principal_id
                    or conversation.kind != "private" or conversation.personas != (persona_id,)):
                raise MentorError("conversation_not_allowed")
            self.store.bind(db, "active", key(binding.principal_id, binding.id, persona_id), conversation_id)

    def current_conversation(self, binding: ChatBinding, persona_id: str) -> str | None:
        with self.store.transaction() as db:
            return self.store.lookup(db, "active", key(binding.principal_id, binding.id, persona_id))
