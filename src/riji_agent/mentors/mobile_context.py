"""Explicit mobile material scope, including transitive AI input lineage."""
from riji_agent.mentors.models import Artifact, Conversation


def selected_artifacts(conversation: Conversation, artifacts: tuple[Artifact, ...],
                       *, delivered: set[str] | None = None) -> tuple[Artifact, ...]:
    selected = set(conversation.selected_artifact_ids)
    sources = set(conversation.source_ids)
    return tuple(item for item in artifacts if
                 (item.id in selected or ((item.run_id == conversation.run_id or item.id in (delivered or set())) and item.kind != "user"
                                              and item.input_revision > conversation.material_selection_revision))
                 and set(item.context_artifact_ids).issubset(selected)
                 and set(item.dependencies).issubset(sources))
