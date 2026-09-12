"""Opt-in composition root; existing gateway, model provider and memory remain owners."""

from __future__ import annotations

from types import SimpleNamespace

from riji_agent.mentors.configuration import MentorConfig, credential, load_credentials
from riji_agent.mentors.delivery import OutboxDispatcher
from riji_agent.mentors.feishu import FeishuChannel, build_client
from riji_agent.mentors.generation import ModelGeneration
from riji_agent.mentors.handoff import JournalHandoff
from riji_agent.mentors.history import DiscussionHistory
from riji_agent.mentors.host_bridge import HostBridgeDispatcher, HostGroupBridge, member_resolver, register_host_owners
from riji_agent.mentors.identity import IdentityService
from riji_agent.mentors.identity_links import IdentityLinks
from riji_agent.mentors.ingress import DiscussionIngress
from riji_agent.mentors.langgraph_adapter import LangGraphDriver
from riji_agent.mentors.local_channel import ChannelRouter, LocalChannel
from riji_agent.mentors.models import Application, Envelope, MentorError
from riji_agent.mentors.policy import DiscussionPolicy
from riji_agent.mentors.service import DiscussionService
from riji_agent.mentors.sources import MemorySources
from riji_agent.mentors.store import MentorStore
from riji_agent.mentors.worker import DiscussionWorker
from riji_agent.mentors.transfer import DiscussionTransfer, TransferSources
from riji_agent.personas.registry import PersonaRegistry


def build_runtime(settings, *, model, memory_service, drafts):
    if not settings.mentors_enabled:
        return None
    if settings.mentors_config_path is None or settings.port != 8765:
        raise MentorError("mentor_loopback_configuration_required")
    config = MentorConfig.load(settings.mentors_config_path, settings.journal_root)
    credentials = load_credentials(config, settings.journal_root)
    store = MentorStore(settings.data_dir / "mentors.sqlite3")
    identity = IdentityService(store, PersonaRegistry())
    applications, clients, transport_tokens, user_tokens = {}, {}, {}, {}
    legacy_host = None
    for item in config.applications:
        if item.receiver == "hermes" and item.external_id != settings.feishu_app_id:
            raise MentorError("hermes_host_application_mismatch")
        if item.platform == "feishu" and item.external_id == settings.feishu_app_id and item.receiver != "hermes":
            raise MentorError("legacy_feishu_receiver_ownership_conflict")
        app = identity.register_application(Application(platform=item.platform, tenant=item.tenant,
              external_id=item.external_id, persona_id=item.persona_id, role="host" if item.persona_id == "host" else "mentor"))
        applications[app.id] = app
        if item.receiver == "hermes":
            legacy_host = app
        else:
            token = credential(item.transport_token_env, credentials).get_secret_value()
            if token in transport_tokens:
                raise MentorError("duplicate_mentor_credential")
            transport_tokens[token] = app.id
        if item.platform == "feishu":
            app_secret = (getattr(settings, "feishu_app_secret", None) if item.receiver == "hermes"
                          else credential(item.secret_env, credentials))
            if app_secret is None or not app_secret.get_secret_value():
                raise MentorError("hermes_host_secret_required")
            clients[app.id] = build_client(item.external_id, app_secret.get_secret_value())
    for item in config.users:
        principal = identity.register_principal(item.account, item.legacy_owner_key)
        token = credential(item.review_token_env, credentials).get_secret_value()
        if token in user_tokens or token in transport_tokens:
            raise MentorError("duplicate_mentor_credential")
        user_tokens[token] = principal.id
        if item.account.platform == "local":
            _local_bindings(identity, principal, applications)
    channels = ChannelRouter(applications, {"local": LocalChannel(store), "feishu": FeishuChannel(
        applications, clients, member_resolver=member_resolver(store))})
    sources = TransferSources(store, MemorySources(memory_service, identity.personas))
    policy = DiscussionPolicy(store, sources, channels)
    service = DiscussionService(store, identity, policy)
    graph = LangGraphDriver(store, settings.data_dir / "mentor-checkpoints.sqlite3")
    worker = DiscussionWorker(service, ModelGeneration(model, identity.personas))
    worker.execution_driver = graph
    worker.dispatcher = OutboxDispatcher(service)
    history = DiscussionHistory(service, graph)
    handoffs = JournalHandoff(history, drafts)
    transfers = DiscussionTransfer(history)
    ingress = DiscussionIngress(service, worker, history, handoffs)
    links = IdentityLinks(identity, set(applications))
    ingress.identity_links = links
    runtime = SimpleNamespace(store=store, identity=identity, service=service, worker=worker, graph=graph,
        history=history, handoffs=handoffs, transfers=transfers, ingress=ingress, identity_links=links,
        user_tokens=user_tokens, transport_tokens=transport_tokens, legacy_host=legacy_host, host_bridge=None)
    secret = getattr(settings, "hermes_shared_secret", None)
    if legacy_host is not None and secret is not None:
        allowed = frozenset(getattr(settings, "allowed_feishu_user_ids", ()))
        register_host_owners(runtime, allowed)
        runtime.host_bridge = HostGroupBridge(runtime, secret.get_secret_value(), allowed)
        worker.dispatcher = HostBridgeDispatcher(runtime.host_bridge, worker.dispatcher)
    return runtime


def _local_bindings(identity, principal, applications):
    for app in applications.values():
        if (app.platform, app.tenant) != (principal.account.platform, principal.account.tenant):
            continue
        identity.resolve(app.id, Envelope(delivery_id="local-enrollment", message_id="local-enrollment",
            external_user_id=principal.account.subject, subject=principal.account.subject,
            external_chat_id="local-private-" + principal.id + "/" + app.id, chat_type="p2p", text=""))
