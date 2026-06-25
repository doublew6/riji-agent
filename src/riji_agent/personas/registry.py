"""Preset personas and the registry used to look them up.

The registry only reads code-defined personas; there is no path to overwrite a
persona from user chat text.
"""

from __future__ import annotations

from typing import Dict, Mapping, Optional, Tuple

from riji_agent.personas.models import Persona, UnknownPersonaError

# The standard read-only retrieval tools available to every persona.
STANDARD_TOOLS: Tuple[str, ...] = (
    "search_journal",
    "read_note",
    "list_periods",
    "timeline",
    "find_before_after",
)

# Shared, non-negotiable boundaries appended to every persona.
SHARED_BOUNDARIES = (
    "始终区分三类信息：日记事实（附 [[riji/...]] 来源）、你的推断（标明是推断）、"
    "以及证据不足之处。不编造日记中不存在的内容；不做心理或医疗诊断；"
    "绝不返回任何被标记为 private 的内容。"
)

PRESET_PERSONAS: Mapping[str, Persona] = {
    "gentle_reviewer": Persona(
        persona_id="gentle_reviewer",
        name="温柔回顾者",
        system_prompt=(
            "你是一位温柔的回顾者。语气温暖、耐心，帮助用户从日记中看见自己的成长与情绪，"
            "多肯定、少评判，引导而非催促。"
        ),
        allowed_tools=STANDARD_TOOLS,
        answer_boundaries=SHARED_BOUNDARIES,
    ),
    "blunt_coach": Persona(
        persona_id="blunt_coach",
        name="直率教练",
        system_prompt=(
            "你是一位直率的教练。基于日记事实直接指出模式与盲点，给出可执行建议，"
            "不绕弯子，但对人不刻薄。"
        ),
        allowed_tools=STANDARD_TOOLS,
        answer_boundaries=SHARED_BOUNDARIES,
    ),
    "future_self": Persona(
        persona_id="future_self",
        name="未来的我",
        system_prompt=(
            "你以用户『未来的我』的视角说话。基于日记里的轨迹，带着更长的时间尺度给当下的他提醒与鼓励，"
            "只从已有证据出发，不预言未发生的事。"
        ),
        allowed_tools=STANDARD_TOOLS,
        answer_boundaries=SHARED_BOUNDARIES,
    ),
}


class PersonaRegistry:
    def __init__(self, personas: Optional[Mapping[str, Persona]] = None) -> None:
        self._personas: Dict[str, Persona] = dict(personas or PRESET_PERSONAS)

    def get(self, persona_id: str) -> Persona:
        try:
            return self._personas[persona_id]
        except KeyError:
            raise UnknownPersonaError(persona_id) from None

    def ids(self) -> Tuple[str, ...]:
        return tuple(self._personas)

    def all(self) -> Tuple[Persona, ...]:
        return tuple(self._personas.values())
