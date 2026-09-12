"""Deterministic stage selection; independent opinions never see peer opinions."""

from __future__ import annotations

from dataclasses import dataclass

from riji_agent.mentors.models import Artifact, Conversation


@dataclass(frozen=True)
class Stage:
    kind: str
    actor: str
    round_index: int = 0


def next_stage(conversation: Conversation, artifacts: tuple[Artifact, ...]) -> Stage | None:
    current = tuple(item for item in artifacts if item.input_revision == conversation.input_revision and item.kind != "user"
                    and (not item.run_id or item.run_id == conversation.run_id))
    if conversation.run_kind == "followup" or conversation.kind == "private":
        return Stage("followup", conversation.followup_actor or conversation.personas[0])
    if conversation.summarize_requested:
        return None if any(item.kind == "synthesis" for item in current) else Stage("synthesis", "host")
    for persona in conversation.run_personas or conversation.personas:
        if not any(item.kind == "opinion" and item.actor == persona for item in current):
            return Stage("opinion", persona)
    if not any(item.kind == "comparison" for item in current):
        return Stage("comparison", "host")
    if conversation.mode == "reference":
        return None
    if any(item.kind == "comparison" and item.debate_needed is False for item in current):
        return None if any(item.kind == "synthesis" for item in current) else Stage("synthesis", "host")
    for number in range(1, conversation.rounds + 1):
        for persona in conversation.run_personas or conversation.personas:
            if not any(item.kind == "debate" and item.actor == persona and item.round_index == number for item in current):
                return Stage("debate", persona, number)
    return None if any(item.kind == "synthesis" for item in current) else Stage("synthesis", "host")


def visible_history(stage: Stage, artifacts: tuple[Artifact, ...], conversation: Conversation) -> tuple[Artifact, ...]:
    if stage.kind == "opinion":
        return ()
    current = tuple(item for item in artifacts if item.input_revision == conversation.input_revision
                    and (not item.run_id or item.run_id == conversation.run_id))
    return current[-24:]
