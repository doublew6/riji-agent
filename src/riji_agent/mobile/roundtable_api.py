"""Strict roundtable inputs; result revalidation is deliberately never receipted."""
from typing import Annotated, Literal
from fastapi import Query, Request
from pydantic import Field
from riji_agent.mobile.api import Input, MessageInput, OperationInput, Identifier, ID_PATTERN
from riji_agent.mobile.store import MobileError
from riji_agent.mentors.models import MentorError

ID = Annotated[str, Field(pattern=ID_PATTERN)]


class RoundtableCreate(OperationInput):
    text: str = Field(default='', max_length=10000)
    chat_kind: Literal['legacy', 'group'] = 'legacy'
    personas: list[ID] = Field(default_factory=list, max_length=4)
    statement_kind: Literal['user_statement', 'user_plan', 'user_feedback'] = 'user_statement'


class RoundtableMessage(MessageInput):
    expected_revision: int = Field(ge=1)
    reply_personas: list[ID] = Field(default_factory=list, max_length=4)
    statement_kind: Literal['user_statement', 'user_plan', 'user_feedback'] = 'user_statement'


class RoundtableCommand(OperationInput):
    action: Literal['start_run', 'stop', 'summarize', 'resume', 'continue', 'correct', 'archive', 'restore', 'rename', 'end_turn']
    expected_revision: int = Field(ge=1)
    text: str = Field(default='', max_length=10000)
    personas: list[ID] = Field(default_factory=list, max_length=4)
    mode: Literal['reference', 'debate'] = 'reference'
    rounds: int = Field(default=1, ge=1, le=10)
    supersedes: list[ID] = Field(default_factory=list, max_length=48)
    preview_hash: str = Field(default='', max_length=64)
    statement_kind: Literal['user_statement', 'user_plan', 'user_feedback'] = 'user_statement'


class SelectedMemory(Input):
    id: ID
    expected_version: str = Field(pattern=r'^[a-f0-9]{64}$')


class MaterialsInput(Input):
    expected_revision: int = Field(ge=1)
    artifact_ids: list[ID] = Field(min_length=1, max_length=48)
    memories: list[SelectedMemory] = Field(default_factory=list, max_length=10)
    personas: list[ID] = Field(min_length=2, max_length=4)
    mode: Literal['reference', 'debate'] = 'reference'
    rounds: int = Field(default=1, ge=1, le=10)


class ResultInput(OperationInput):
    artifact_ids: list[ID] = Field(min_length=1, max_length=5)


class ResultValidation(Input):
    result_id: ID


def attach_roundtable_routes(router, service, device):
    rt = service.roundtables

    def mutate(current, kind, identifier, value, effect):
        def checked():
            try:
                return effect()
            except MentorError as exc:
                raise MobileError(exc.code) from None
        return service.operate(current, kind, dict(value, conversation_id=identifier), checked)

    @router.get('/roundtables')
    def listing(request: Request, cursor: str = Query(default='', max_length=512)):
        return rt.listing(device(request), cursor)

    @router.post('/roundtables')
    def create(body: RoundtableCreate, request: Request):
        current, value = device(request), body.model_dump()
        if value['chat_kind'] == 'legacy' and not value['personas']:
            value.pop('chat_kind')
            value.pop('personas')
        return mutate(current, 'roundtable_create', '', value, lambda: rt.create(current, value))

    @router.get('/roundtables/{identifier}')
    def detail(identifier: Identifier, request: Request, cursor: str = Query(default='', max_length=512)):
        return rt.detail(device(request), identifier, cursor)

    @router.post('/roundtables/{identifier}/messages')
    def message(identifier: Identifier, body: RoundtableMessage, request: Request):
        current, value = device(request), body.model_dump()
        if not value['reply_personas']:
            value.pop('reply_personas')
        return mutate(current, 'roundtable_message', identifier, value, lambda: rt.message(current, identifier, value))

    @router.post('/roundtables/{identifier}/commands')
    def command(identifier: Identifier, body: RoundtableCommand, request: Request):
        current, value = device(request), body.model_dump()
        return mutate(current, 'roundtable_command', identifier, value, lambda: rt.command(current, identifier, value))

    @router.post('/roundtables/{identifier}/materials/preview')
    def materials(identifier: Identifier, body: MaterialsInput, request: Request):
        return rt.materials(device(request), identifier, body.model_dump())

    @router.post('/roundtables/{identifier}/result-preview')
    def result(identifier: Identifier, body: ResultInput, request: Request):
        current, value = device(request), body.model_dump()
        return mutate(current, 'roundtable_result', identifier, value, lambda: rt.result_preview(current, identifier, value))

    @router.post('/roundtables/{identifier}/result-validation')
    def validation(identifier: Identifier, body: ResultValidation, request: Request):
        return rt.result_validation(device(request), identifier, body.model_dump())
