"""Create empty member-only mobile rooms without model or personal-history access."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from riji_agent.mentors.models import AudienceGrant, ChatBinding, Conversation
from riji_agent.mentors.store import key
from riji_agent.mobile.store import MobileError, fingerprint

if TYPE_CHECKING:
    from riji_agent.mobile.roundtables import MobileRoundtables


def create_group(roundtables: MobileRoundtables, device: dict, body: dict) -> dict:
    from riji_agent.mobile.service import MENTORS
    selected = tuple(body.get('personas', ()))
    if (not 2 <= len(set(selected)) == len(selected) <= 4 or not set(selected).issubset(MENTORS)
            or body.get('text', '').strip()):
        raise MobileError('invalid_group', 422)
    roundtables.binding(device)
    principal = roundtables.mobile.principal(device)
    actors = tuple(actor for actor in MENTORS if actor in selected)
    title = '、'.join(roundtables.runtime.identity.personas.get(actor).name for actor in actors)
    current = Conversation(owner_id=principal.id, kind='roundtable', chat_kind='group', title=title,
        question=title, personas=actors, run_personas=(), mode='reference', rounds=1,
        source_scope='mobile_selected', run_kind='group_chat', run_number=0,
        status='completed', room_status='ready', created_at=roundtables.service.now(),
        updated_at=roundtables.service.now())
    applications = _applications(roundtables, actors)
    adapter = roundtables.service.policy.channel.adapters['mobile']
    snapshot = adapter.prepare_room(principal, applications, current.id)
    if (set(snapshot.application_ids) != set(applications) or not snapshot.complete or not snapshot.private
            or not snapshot.management_restricted or not snapshot.history_restricted
            or not snapshot.continuity_verified or set(snapshot.human_subjects) != {principal.account.subject}):
        raise MobileError('audience_not_authorized')
    grant = AudienceGrant(conversation_id=current.id, owner_id=principal.id, input_revision=1,
                          snapshot=snapshot, source_versions={})
    current = current.model_copy(update={'room_id': snapshot.room_id, 'grant_id': grant.id})
    _persist_group(roundtables, device, body, current, grant)
    return {'conversation_id': current.id}


def _persist_group(roundtables: MobileRoundtables, device: dict, body: dict,
                   current: Conversation, grant: AudienceGrant) -> None:
    operation = body['operation_id']
    expected = fingerprint({'kind': 'roundtable_create', 'body': dict(body, conversation_id='')})
    binding_id = roundtables.mobile.store.binding(device['id'], 'host')
    with roundtables.store.transaction() as db:
        db.execute('ATTACH DATABASE ? AS mobile_receipts', (str(roundtables.mobile.store.path),))
        modes = [db.execute('PRAGMA ' + name + '.journal_mode').fetchone()[0] for name in ('main', 'mobile_receipts')]
        sync = [db.execute('PRAGMA ' + name + '.synchronous').fetchone()[0] for name in ('main', 'mobile_receipts')]
        if any(mode not in {'delete', 'truncate', 'persist'} for mode in modes) or any(not level for level in sync):
            raise MobileError('mobile_storage_unavailable', 503)
        row = db.execute('SELECT state,fingerprint FROM mobile_receipts.operations WHERE device=? AND id=?',
                         (device['id'], operation)).fetchone()
        if row is None or row['state'] != 'pending' or row['fingerprint'] != expected or binding_id is None:
            raise MobileError('operation_reconciliation_required')
        roundtables.store.bind(db, 'origin', current.id, binding_id)
        roundtables.store.put(db, 'mobile_room', grant.snapshot, current.id, current.room_id)
        roundtables.store.put(db, 'grant', grant, current.owner_id)
        current = roundtables.service.summaries.refresh(db, current)
        roundtables.store.put(db, 'conversation', current, current.owner_id)
        roundtables.store.bind(db, 'mobile_current_roundtable', device['id'], current.id)
        proof = {'conversation_id': current.id, 'fingerprint': expected}
        roundtables.store.bind(db, 'mobile_group_creation', key(device['id'], operation), json.dumps(proof))
        db.execute("UPDATE mobile_receipts.operations SET state='completed',result=?,error=NULL WHERE device=? AND id=?",
                   (json.dumps({'conversation_id': current.id}), device['id'], operation))


def recover_group_creation(roundtables: MobileRoundtables, device: dict, operation: str, receipt: dict) -> dict:
    if receipt['kind'] != 'roundtable_create' or receipt['state'] not in {'pending', 'unknown'}:
        return receipt
    with roundtables.store.transaction() as db:
        raw = roundtables.store.lookup(db, 'mobile_group_creation', key(device['id'], operation))
    if not raw:
        return roundtables.mobile.store.fail_uncommitted_group(device['id'], operation)
    proof = json.loads(raw)
    if proof['fingerprint'] != roundtables.mobile.store.operation_fingerprint(device['id'], operation):
        raise MobileError('operation_conflict')
    current = roundtables.store.read('conversation', proof['conversation_id'], Conversation)
    principal = roundtables.mobile.principal(device)
    binding_id = roundtables.mobile.store.binding(device['id'], 'host')
    binding = roundtables.store.read('chat', binding_id, ChatBinding) if binding_id else None
    with roundtables.store.transaction() as db:
        origin = roundtables.store.lookup(db, 'origin', proof['conversation_id'])
    if (current is None or current.owner_id != principal.id or current.chat_kind != 'group'
            or binding is None or origin != binding.id):
        raise MobileError('operation_reconciliation_required')
    roundtables.mobile.check_conversation(current, binding)
    return roundtables.mobile.store.finish(device['id'], operation, 'completed',
        result={'conversation_id': current.id})


def _applications(roundtables: MobileRoundtables, actors: tuple[str, ...]) -> tuple[str, ...]:
    result = []
    for actor in actors:
        matches = [app for app in roundtables.service.policy.channel.applications.values()
                   if app.platform == 'mobile' and app.tenant == 'personal-mobile-v1' and app.persona_id == actor]
        if len(matches) != 1 or matches[0].role != 'mentor':
            raise MobileError('application_configuration_required')
        result.append(matches[0].id)
    return tuple(result)
