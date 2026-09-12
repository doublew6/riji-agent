"""Replaceable model, source and delivery boundaries owned by the application."""

from __future__ import annotations

from typing import Callable, Protocol

from riji_agent.mentors.models import (
    Conversation, Delivery, Generation, GenerationRequest, Principal,
    RoomSnapshot, Source, TransportResult,
)


class GenerationPort(Protocol):
    def generate(self, request: GenerationRequest, before_send: Callable[[], None]) -> Generation:
        ...


class SourcePort(Protocol):
    def background(self, principal: Principal, conversation: Conversation) -> tuple[Source, ...]:
        ...

    def validate(self, principal: Principal, source: Source) -> bool:
        ...


class ChannelPort(Protocol):
    def create_room(self, principal: Principal, applications: tuple[str, ...], operation_id: str) -> str:
        ...

    def inspect_room(self, room_id: str) -> RoomSnapshot:
        ...

    def send(self, delivery: Delivery, text: str) -> TransportResult:
        ...


class NoSources:
    """A new user can discuss their current question without journal access."""

    def background(self, principal: Principal, conversation: Conversation) -> tuple[Source, ...]:
        return ()

    def validate(self, principal: Principal, source: Source) -> bool:
        return False
