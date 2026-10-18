"""Device-owned persistent host chat and explicit bounded mentor invitations."""
from __future__ import annotations

import base64
import json
from dataclasses import asdict
from functools import wraps
from contextlib import nullcontext

from riji_agent.mentors.handoff import Handoff, JournalHandoff
from riji_agent.mentors.history import digest, DiscussionHistory
from riji_agent.mentors.models import (
    Application, Artifact, AudienceGrant, ChatBinding, Command, Conversation,
    Delivery, MentorError, RoomSnapshot, Source, new_id,
)
from riji_agent.mentors.store import key
from riji_agent.mentors.sources import MemorySources
from riji_agent.mobile.memory import memory_view
from riji_agent.mobile.replies import last_reply_id
from riji_agent.mobile.store import MobileError

MAX_ROUNDTABLES_PER_PAGE = 100


def material_guard(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        memory = self.mobile.memory.service
        guard = getattr(memory.backend, 'mutation_guard', None) if memory is not None else None
        with guard() if guard else nullcontext():
            return method(self, *args, **kwargs)
    return guarded


class MobileRoundtables:
    def __init__(self, mobile):
        self.mobile, self.runtime = mobile, mobile.runtime
        self.service, self.store = self.runtime.service, self.runtime.store
        self.memory_sources = MemorySources(mobile.memory.service, self.runtime.identity.personas)
        self.handoff = JournalHandoff(DiscussionHistory(self.service), None)

    def binding(self, device):
        principal = self.mobile.principal(device)
        identifier = self.mobile.store.binding(device['id'], 'host')
        if identifier:
            binding = self.store.read('chat', identifier, ChatBinding)
            if binding is None or binding.principal_id != principal.id:
                raise MobileError('mobile_binding_unavailable')
            return binding
        from riji_agent.mobile.service import MENTORS
        for actor in ('host', *MENTORS):
            app = self.runtime.identity.register_application(Application(platform='mobile', tenant='personal-mobile-v1',
                external_id='riji-mobile-' + actor, persona_id=actor, role='host' if actor == 'host' else 'mentor'))
            self.service.policy.channel.applications[app.id] = app
            if actor == 'host':
                host = app
        binding = ChatBinding(principal_id=principal.id, application_id=host.id,
                              external_chat_id='mobile-' + device['id'] + '-host', chat_type='p2p')
        with self.store.transaction() as db:
            self.store.put(db, 'chat', binding, principal.id)
        self.mobile.store.bind(device['id'], 'host', binding.id)
        return binding

    def get(self, device, identifier):
        principal = self.mobile.principal(device)
        conversation = self.service.get(identifier, principal.id)
        with self.store.transaction() as db:
            origin = self.store.lookup(db, 'origin', identifier)
        binding = self.store.read('chat', origin, ChatBinding) if origin else None
        if (conversation.source_scope != 'mobile_selected' or binding is None
                or self.mobile.store.binding(device['id'], 'host') != binding.id):
            raise MobileError('roundtable_not_found', 404)
        self.mobile.check_conversation(conversation, binding)
        if conversation.chat_kind == 'group':
            self.service.policy._check_audience(conversation)
        return conversation, binding

    def create(self, device, body):
        from riji_agent.mobile.service import MENTORS
        if body.get('chat_kind') == 'group':
            from riji_agent.mobile.group_chats import create_group
            return create_group(self, device, body)
        if body.get('personas'):
            raise MobileError('invalid_roundtable', 422)
        if not body['text'].strip():
            raise MobileError('message_required', 422)
        binding = self.binding(device)
        conversation = Conversation(owner_id=binding.principal_id, kind='roundtable', personas=tuple(MENTORS),
            question=body['text'], mode='reference', source_scope='mobile_selected', run_kind='followup',
            followup_actor='host', room_status='ready', created_at=self.service.now(), updated_at=self.service.now())
        applications = tuple(app.id for app in self.service.policy.channel.applications.values()
            if app.platform == 'mobile' and app.tenant == 'personal-mobile-v1')
        principal = self.mobile.principal(device)
        room = self.service.policy.channel.adapters['mobile'].create_room(principal, applications, conversation.id)
        snapshot = self.service.policy.channel.inspect_room(room)
        grant = AudienceGrant(conversation_id=conversation.id, owner_id=principal.id,
                              input_revision=1, snapshot=snapshot, source_versions={})
        conversation = conversation.model_copy(update={'room_id': room, 'grant_id': grant.id})
        with self.store.transaction() as db:
            self.store.bind(db, 'origin', conversation.id, binding.id)
            self.store.put(db, 'grant', grant, principal.id)
            self.service._user_artifact(db, conversation, body['text'],
                                        statement_kind=body.get('statement_kind', 'user_statement'))
            conversation = self.service.summaries.refresh(db, conversation)
            self.store.put(db, 'conversation', conversation, principal.id)
            self.service.summaries.record_run(db, conversation)
            self.store.bind(db, 'followup_question', conversation.run_id, body['text'])
            self.store.bind(db, 'mobile_current_roundtable', device['id'], conversation.id)
            self.service.budgets.activate(db, conversation)
        self.runtime.worker.wake()
        return {'conversation_id': conversation.id}

    def _list_snapshot(self, device: dict) -> dict:
        principal = self.mobile.principal(device)
        binding = self.mobile.store.binding(device['id'], 'host')
        items = []
        with self.store.transaction() as db:
            for item in self.store.records(db, 'conversation', principal.id, Conversation):
                if (binding and item.kind == 'roundtable' and item.source_scope == 'mobile_selected'
                        and item.status != 'deleted' and self.store.lookup(db, 'origin', item.id) == binding):
                    items.append({'id': item.id, 'title': item.title or item.question[:80], 'status': item.status,
                                  'chat_kind': item.chat_kind, 'personas': item.personas,
                                  'last_reply_id': last_reply_id(self.store, item.id, item.room_id, db=db),
                                  'updated_at': item.updated_at, 'revision': item.input_revision})
            current = self.store.lookup(db, 'mobile_current_roundtable', device['id'])
        items.sort(key=lambda item: (item['updated_at'], item['id']), reverse=True)
        if current not in {item['id'] for item in items}:
            current = None
        version = digest([device['id'], principal.id, binding, items, current])
        return {'items': items, 'current_id': current, 'history_version': version}

    def listing(self, device: dict, cursor: str = '') -> dict:
        snapshot = self._list_snapshot(device)
        offset = 0
        if cursor:
            try:
                version, offset = json.loads(base64.b64decode(
                    cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True))
                if (version != snapshot['history_version'] or type(offset) is not int
                        or not 0 < offset < len(snapshot['items'])):
                    raise ValueError()
            except (ValueError, TypeError, UnicodeError):
                raise MobileError('roundtable_list_changed') from None
        end = offset + MAX_ROUNDTABLES_PER_PAGE
        more = end < len(snapshot['items'])
        next_cursor = base64.urlsafe_b64encode(json.dumps(
            [snapshot['history_version'], end]).encode()).decode().rstrip('=') if more else None
        if self._list_snapshot(device) != snapshot:
            raise MobileError('roundtable_list_changed')
        return dict(snapshot, items=snapshot['items'][offset:end], next_cursor=next_cursor, truncated=more)

    def _authorized_artifacts(self, device: dict, current: Conversation) -> dict:
        """Admission uses complete live history, independently of display pagination."""
        if self.get(device, current.id)[0] != current:
            raise MobileError('conversation_page_changed')
        view = self.runtime.history.read(current.id, current.owner_id)
        sent = self._delivered_artifacts(current)
        eligible = {item['id']: item for item in view['artifacts']
                    if not item.get('superseded') and not item.get('unavailable')
                    and (item['kind'] == 'user' or item['id'] in sent)}
        refreshed = self.runtime.history.read(current.id, current.owner_id)
        if (self.get(device, current.id)[0] != current or refreshed['artifacts'] != view['artifacts']
                or self._delivered_artifacts(current) != sent):
            raise MobileError('conversation_page_changed')
        return eligible

    def _delivered_artifacts(self, current: Conversation) -> set[str]:
        return {item.artifact_id for item in self.store.list('delivery', current.id, Delivery)
                if item.status == 'sent' and item.chat_id == current.room_id}

    @material_guard
    def detail(self, device, identifier, cursor=''):
        from riji_agent.mobile.service import history_page
        current, binding = self.get(device, identifier)
        view = self.runtime.history.read(identifier, current.owner_id)
        sent = self._delivered_artifacts(current)
        material_sources = self._material_sources(device, current)
        messages = []
        for item in view['artifacts']:
            if item['kind'] != 'user' and item['id'] not in sent:
                continue
            artifact = self.store.read('artifact', item['id'], Artifact)
            refs = [self.store.read('source', ref, Source) for ref in artifact.dependencies]
            messages.append({field: item.get(field, default) for field, default in (
                ('id', ''), ('actor', ''), ('kind', ''), ('text', ''), ('created_at', artifact.created_at),
                ('input_revision', artifact.input_revision), ('run_id', artifact.run_id), ('origin_kind', artifact.origin_kind),
                ('superseded', False), ('unavailable', None), ('uncertainties', ()), ('next_steps', ()))} |
                {'source_ids': [ref.id for ref in refs if ref is not None and not item.get('unavailable')]})
        fields = ('status', 'state_revision', 'summary_version', 'correction_version', 'run_id', 'run_kind',
                  'run_personas', 'mode', 'rounds', 'suspended_run_id')
        result = {name: getattr(current, name) for name in fields}
        summary = view['current_summary']
        visible_ids = {item['id'] for item in messages}
        if summary is not None:
            summary = dict(summary, items=[item for item in summary['items'] if set(item['artifact_ids']).issubset(visible_ids)])
        result.update(conversation_id=identifier, revision=current.input_revision, messages=messages,
                      current_summary=summary, runs=view['runs'][-30:], runs_truncated=len(view['runs']) > 30,
                      audience=list(current.personas) if current.chat_kind == 'group' else ['host', *current.personas],
                      chat_kind=current.chat_kind, title=current.title or current.question[:80],
                      personas=current.personas, notice=view['notice'],
                      last_reply_id=last_reply_id(self.store, identifier, current.room_id),
                      selected_artifact_ids=current.selected_artifact_ids,
                      material_sources=material_sources, progress=self._progress(current),
                      cache_expires_at=int(self.service.now()) + 900)
        with self.store.transaction() as db:
            result['turn_ended'] = (current.chat_kind == 'group'
                and self.store.lookup(db, 'ended_group_turn', current.run_id) == current.id)
        # Cache expiry is metadata, not part of the exact history snapshot.
        expiry = result.pop('cache_expires_at')
        page = history_page(result, (device['id'], identifier), cursor)
        fresh, _ = self.get(device, identifier)
        refreshed = self.runtime.history.read(identifier, current.owner_id)
        if (fresh != current or self._material_sources(device, current) != material_sources
                or any(refreshed[field] != view[field] for field in ('artifacts', 'current_summary', 'runs', 'notice'))):
            raise MobileError('conversation_page_changed')
        return dict(page, cache_expires_at=expiry)

    def _material_sources(self, device, current):
        principal = self.mobile.principal(device)
        result = []
        for identifier in current.source_ids:
            source = self.store.read('source', identifier, Source)
            usable = source is not None and self.service.policy._usable(source, principal, current)
            result.append({'id': identifier, 'kind': source.kind if source is not None else 'memory',
                           'text': source.text if usable else '',
                           'unavailable': None if usable else 'source_changed_or_revoked'})
        return result

    def _progress(self, current):
        from riji_agent.mentors.planner import next_stage
        if current.status not in {'queued', 'running', 'delivering'}:
            return None
        if current.status == 'delivering':
            pending = [item for item in self.store.list('delivery', current.id, Delivery)
                       if item.status != 'sent' and item.input_revision == current.input_revision]
            if pending:
                artifact = self.store.read('artifact', pending[0].artifact_id, Artifact)
                if artifact is not None:
                    return {'actor': artifact.actor, 'stage': 'delivery', 'round': artifact.round_index}
        stage = next_stage(current, self.store.list('artifact', current.id, Artifact))
        return {'actor': stage.actor, 'stage': stage.kind, 'round': stage.round_index} if stage else None

    def message(self, device, identifier, body):
        current, _ = self.get(device, identifier)
        if current.chat_kind == 'group':
            fields = dict(body, personas=body.get('reply_personas', []))
            command = self._command(device, current, fields, kind='group_message')
        else:
            if body.get('reply_personas'):
                raise MobileError('group_chat_required')
            command = self._command(device, current, body, kind='supplement', actor='host')
        self.service.apply(command)
        self.runtime.worker.wake()
        return {'conversation_id': identifier}

    @material_guard
    def command(self, device, identifier, body):
        current, _ = self.get(device, identifier)
        if current.chat_kind == 'group' and body['action'] not in {'rename', 'stop', 'resume', 'archive', 'restore', 'end_turn'}:
            raise MobileError('group_chat_action_not_available')
        if body['action'] == 'start_run' and body.get('text', '').strip():
            raise MobileError('material_preview_changed')
        selection = self._consume_preview(device, current, body) if body['action'] == 'start_run' else None
        command = self._command(device, current, body, kind=body['action'])
        self.service.apply(command, mobile_materials=selection)
        self.runtime.worker.wake()
        return {'conversation_id': identifier}

    @staticmethod
    def _command(device, current, body, **extra):
        fields = {name: body[name] for name in ('text', 'expected_revision', 'personas', 'mode', 'rounds',
                  'supersedes', 'statement_kind', 'preview_hash') if name in body}
        for name in ('personas', 'supersedes'):
            if name in fields:
                fields[name] = tuple(fields[name])
        return Command(id='mobile:' + key(device['id'], body['operation_id']), principal_id=current.owner_id,
                       conversation_id=current.id, **fields, **extra)

    @material_guard
    def materials(self, device, identifier, body):
        current, _ = self.get(device, identifier)
        if current.chat_kind == 'group':
            raise MobileError('group_chat_action_not_available')
        if current.input_revision != body['expected_revision']:
            raise MobileError('conversation_version_changed')
        selection = self._selection(device, current, body)
        fingerprint = digest({'device': device['id'], 'conversation': identifier,
                              'revision': current.input_revision, 'state_revision': current.state_revision, **selection})
        with self.store.transaction() as db:
            self.store.bind(db, 'mobile_material_preview', fingerprint,
                            __import__('json').dumps(dict(body, state_revision=current.state_revision)))
        if self._selection(device, current, body) != selection or self.get(device, identifier)[0] != current:
            raise MobileError('material_preview_changed')
        return {'preview_hash': fingerprint, 'artifact_ids': body['artifact_ids'], 'memories': selection['memory_views'],
                'audience': ['host', *current.personas], 'personas': body['personas'],
                'mode': body['mode'], 'rounds': body['rounds'], 'revision': current.input_revision}

    def _selection(self, device, current, body):
        from riji_agent.mentors.mobile_context import selected_artifacts
        from riji_agent.memory.models import MemoryScope, MemoryStatus
        principal = self.mobile.principal(device)
        actors = tuple(body['personas'])
        if not 2 <= len(set(actors)) == len(actors) <= 4 or not set(actors).issubset(current.personas):
            raise MobileError('invalid_roundtable', 422)
        sources, memory_views = [], []
        for selected in body['memories']:
            item = self.mobile.memory.item(principal.legacy_owner_key, selected['id'])
            view = memory_view(item)
            if view['version'] != selected['expected_version'] or item.scope != MemoryScope.SHARED or item.status != MemoryStatus.ACTIVE:
                raise MobileError('material_changed')
            source = self.memory_sources._source(principal, item)
            if not self.service.policy._usable(source, principal, current):
                raise MobileError('material_unavailable')
            sources.append(source)
            memory_views.append(view)
        if len({source.id for source in sources}) != len(sources) or sum(len(source.text) for source in sources) > 4000:
            raise MobileError('material_limit', 422)
        proposed = current.model_copy(update={'selected_artifact_ids': tuple(body['artifact_ids']),
                         'source_ids': tuple(key(principal.id, source.id, source.version) for source in sources)})
        eligible = self._authorized_artifacts(device, current)
        chosen = tuple(self.store.read('artifact', ref, Artifact) for ref in body['artifact_ids'])
        if (not chosen or len(set(body['artifact_ids'])) != len(chosen) or not set(body['artifact_ids']).issubset(eligible)
                or any(item is None or item.conversation_id != current.id for item in chosen)
                or len(selected_artifacts(proposed, chosen)) != len(chosen)):
            raise MobileError('material_selection_invalid')
        return {'artifact_ids': body['artifact_ids'], 'artifact_hashes': [digest(item.model_dump()) for item in chosen],
                'source_ids': list(proposed.source_ids), 'sources': [source.model_dump() for source in sources],
                'memory_views': memory_views, 'personas': body['personas'], 'mode': body['mode'], 'rounds': body['rounds']}

    def _consume_preview(self, device, current, body):
        import json
        with self.store.transaction() as db:
            raw = self.store.lookup(db, 'mobile_material_preview', body.get('preview_hash', ''))
        if not raw:
            raise MobileError('material_preview_required')
        preview = json.loads(raw)
        if any(body[name] != preview[name] for name in ('personas', 'mode', 'rounds', 'expected_revision')):
            raise MobileError('material_preview_changed')
        if current.state_revision != preview.pop('state_revision'):
            raise MobileError('material_preview_changed')
        selected = self._selection(device, current, preview)
        expected = digest({'device': device['id'], 'conversation': current.id,
                           'revision': current.input_revision, 'state_revision': current.state_revision, **selected})
        if expected != body['preview_hash']:
            raise MobileError('material_preview_changed')
        from riji_agent.mentors.service import MobileMaterials
        return MobileMaterials(current, tuple(selected['artifact_ids']),
                               tuple(Source.model_validate(item) for item in selected['sources']))

    @material_guard
    def result_preview(self, device, identifier, body):
        current, binding = self.get(device, identifier)
        valid = self._authorized_artifacts(device, current)
        if not set(body['artifact_ids']).issubset(valid):
            raise MobileError('handoff_selection_invalid')
        handoff = self.handoff.create(identifier, current.owner_id, tuple(body['artifact_ids']), operation_id=body['operation_id'])
        with self.store.transaction() as db:
            self.store.put(db, 'handoff', handoff.model_copy(update={'binding_id': binding.id}), identifier)
        self.handoff._check(handoff.id, binding)
        if self.get(device, identifier)[0] != current:
            raise MobileError('conversation_page_changed')
        return {'result_id': handoff.id}

    @material_guard
    def result_validation(self, device, identifier, body):
        current, binding = self.get(device, identifier)
        handoff = self.handoff._check(body['result_id'], binding)
        if handoff.conversation_id != identifier:
            raise MobileError('handoff_selection_invalid')
        fresh, _ = self.get(device, identifier)
        if fresh != current or self.handoff._check(body['result_id'], binding) != handoff:
            raise MobileError('handoff_source_unavailable')
        provenance = asdict(handoff.provenance)
        return {'result_id': handoff.id, 'text': handoff.text, 'provenance': provenance,
                'binding': digest({'device': device['id'], 'text': handoff.text, 'provenance': provenance}),
                'validated_at': self.service.now()}
