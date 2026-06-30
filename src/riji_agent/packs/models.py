"""Typed manifest models for optional journal capability packs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Tuple


@dataclass(frozen=True)
class PackTemplate:
    id: str
    name: str
    target: str
    description: str


@dataclass(frozen=True)
class PackSkill:
    id: str
    name: str
    source: str
    description: str
    requires_confirmation: bool


@dataclass(frozen=True)
class PackAutomation:
    id: str
    name: str
    schedule: str
    description: str
    status: str


@dataclass(frozen=True)
class PackManifest:
    id: str
    name: str
    version: str
    description: str
    templates: Tuple[PackTemplate, ...]
    skills: Tuple[PackSkill, ...]
    automations: Tuple[PackAutomation, ...]
    required_config: Tuple[str, ...]
    optional_config: Tuple[str, ...]
    privacy_notes: Tuple[str, ...]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PackManifest":
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            version=str(data["version"]),
            description=str(data["description"]),
            templates=tuple(_templates(data.get("templates", ()))),
            skills=tuple(_skills(data.get("skills", ()))),
            automations=tuple(_automations(data.get("automations", ()))),
            required_config=tuple(
                str(item) for item in data.get("required_config", ())
            ),
            optional_config=tuple(
                str(item) for item in data.get("optional_config", ())
            ),
            privacy_notes=tuple(str(item) for item in data.get("privacy_notes", ())),
        )

    def to_text(self) -> str:
        parts = [
            self.id,
            self.name,
            self.version,
            self.description,
            *self.required_config,
            *self.optional_config,
            *self.privacy_notes,
        ]
        for template in self.templates:
            parts.extend(
                (template.id, template.name, template.target, template.description)
            )
        for skill in self.skills:
            parts.extend((skill.id, skill.name, skill.source, skill.description))
        for automation in self.automations:
            parts.extend(
                (
                    automation.id,
                    automation.name,
                    automation.schedule,
                    automation.description,
                )
            )
        return "\n".join(parts)


def _templates(items: Any) -> tuple[PackTemplate, ...]:
    return tuple(
        PackTemplate(
            id=str(item["id"]),
            name=str(item["name"]),
            target=str(item["target"]),
            description=str(item["description"]),
        )
        for item in items
    )


def _skills(items: Any) -> tuple[PackSkill, ...]:
    return tuple(
        PackSkill(
            id=str(item["id"]),
            name=str(item["name"]),
            source=str(item["source"]),
            description=str(item["description"]),
            requires_confirmation=bool(item["requires_confirmation"]),
        )
        for item in items
    )


def _automations(items: Any) -> tuple[PackAutomation, ...]:
    return tuple(
        PackAutomation(
            id=str(item["id"]),
            name=str(item["name"]),
            schedule=str(item["schedule"]),
            description=str(item["description"]),
            status=str(item["status"]),
        )
        for item in items
    )
