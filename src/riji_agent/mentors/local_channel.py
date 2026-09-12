"""Loopback presentation adapter: a private local view with named AI actors."""

from __future__ import annotations

from riji_agent.mentors.models import MentorError, RoomSnapshot, TransportResult
from riji_agent.mentors.store import MentorStore


class LocalChannel:
    def __init__(self, store: MentorStore | None = None) -> None:
        self.rooms: dict[str, RoomSnapshot] = {}
        self.store = store

    def create_room(self, principal, applications, operation_id) -> str:
        identifier = "local-" + operation_id
        self.rooms[identifier] = RoomSnapshot(room_id=identifier, human_subjects=(principal.account.subject,),
            application_ids=applications, private=True, complete=True, management_restricted=True,
            history_restricted=True, configuration_version=operation_id)
        if self.store is not None:
            with self.store.transaction() as db:
                self.store.put(db, "local_room", self.rooms[identifier], operation_id, identifier)
        return identifier

    def inspect_room(self, room_id) -> RoomSnapshot:
        if self.store is not None:
            snapshot = self.store.read("local_room", room_id, RoomSnapshot)
            if snapshot is None:
                raise MentorError("local_room_unavailable")
            return snapshot
        if room_id not in self.rooms:
            raise MentorError("local_room_unavailable_after_restart")
        return self.rooms[room_id]

    def send(self, delivery, text) -> TransportResult:
        # The authenticated local history view renders the authoritative artifact.
        return TransportResult(status="sent", message_id="local-" + delivery.uuid)


class ChannelRouter:
    def __init__(self, applications, adapters) -> None:
        self.applications, self.adapters = applications, adapters

    def create_room(self, principal, applications, operation_id):
        scopes = {(self.applications[identifier].platform, self.applications[identifier].tenant)
                  for identifier in applications}
        if len(scopes) != 1:
            raise MentorError("room_application_scope_invalid")
        platform, _ = next(iter(scopes))
        return self.adapters[platform].create_room(principal, applications, operation_id)

    def inspect_room(self, room_id):
        platform = "local" if room_id.startswith("local-") else "feishu"
        return self.adapters[platform].inspect_room(room_id)

    def send(self, delivery, text):
        return self.adapters[self.applications[delivery.application_id].platform].send(delivery, text)
