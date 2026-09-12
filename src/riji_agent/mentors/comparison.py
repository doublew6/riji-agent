"""Ground comparison attribution in current, independently authored positions.

Exact quotes establish attribution, not semantic incompatibility. Relationship,
conditions and rationale remain auditable model judgments for content review.
"""

from __future__ import annotations

from typing import Any

from riji_agent.mentors.models import Artifact, Generation, GenerationRequest, MentorError


COMPARISON_ERRORS = {
    "comparison_stage_required", "comparison_decision_required",
    "comparison_evidence_invalid", "comparison_quote_unsupported",
    "comparison_decision_inconsistent",
}

COMPARISON_QUOTE_DESCRIPTION = (
    "Copy one exact continuous substring from the text field of the previous opinion artifact "
    "identified by artifact_id, preserving relevant conditions, punctuation and whitespace. "
    "Do not copy from claims, next_steps or uncertainties, combine separate passages, or paraphrase."
)


def comparison_repair_instruction(code: str) -> str:
    """Explain quote recovery without replaying the rejected model output."""
    if code == "comparison_quote_unsupported":
        return (code + "：按每个stance.artifact_id定位previous中的对应首轮发言，"
                "重新从该artifact.text字段选择一段连续原文作为quote，保留相关条件、标点和空格。"
                "不要从claims、next_steps或uncertainties复制，不要拼接不同段落、改写或改换作者。"
                "提交前逐项核对quote确实是对应artifact.text的连续子串，"
                "再核对引文条件、relationship、rationale及debate_needed是否一致；不要为修复引文捏造共识或冲突。")
    return code


def current_opinions(request: GenerationRequest) -> dict[str, Artifact]:
    """Accept unscoped legacy opinions only within the original initial run."""
    current = request.conversation
    legacy_initial = (current.run_kind == "initial" and current.run_number == 1
                      and not current.reanalyze and not current.suspended_run_id)
    actors = current.run_personas or current.personas
    return {item.id: item for item in request.previous
            if item.kind == "opinion" and item.conversation_id == current.id
            and item.input_revision == current.input_revision and item.actor in actors
            and (item.run_id == current.run_id or (not item.run_id and legacy_initial))}


def scope_comparison_schema(schema: dict[str, Any], request: GenerationRequest) -> None:
    """Only the host comparison may decide whether substantive conflict exists."""
    if request.stage == "comparison":
        schema["properties"]["debate_needed"] = {"type": "boolean"}
        schema["required"] = list(dict.fromkeys([
            *schema["required"], "debate_needed", "comparison_findings",
        ]))
        allowed = sorted(current_opinions(request))
        field = schema["$defs"]["ComparisonStance"]["properties"]["artifact_id"]
        field["enum"] = allowed
        schema["$defs"]["ComparisonStance"]["properties"]["quote"]["description"] = COMPARISON_QUOTE_DESCRIPTION
    else:
        schema["properties"]["debate_needed"] = {"type": "null", "default": None}
        schema["properties"]["comparison_findings"] = {"type": "array", "maxItems": 0, "default": []}
        schema.pop("$defs", None)


def validate_comparison(result: Generation, request: GenerationRequest) -> None:
    if request.stage != "comparison":
        if result.debate_needed is not None or result.comparison_findings:
            raise MentorError("comparison_stage_required")
        return
    if request.actor != "host" or type(result.debate_needed) is not bool:
        raise MentorError("comparison_decision_required")
    opinions = current_opinions(request)
    for finding in result.comparison_findings:
        identifiers = [stance.artifact_id for stance in finding.stances]
        if (any(identifier not in opinions for identifier in identifiers)
                or not set(identifiers).issubset(result.source_refs)
                or len({opinions[identifier].actor for identifier in identifiers}) != 2):
            raise MentorError("comparison_evidence_invalid")
        for stance in finding.stances:
            if not stance.quote.strip() or stance.quote not in opinions[stance.artifact_id].text:
                raise MentorError("comparison_quote_unsupported")
        if not all(value.strip() for value in (
                finding.decision, finding.shared_condition, finding.rationale)):
            raise MentorError("comparison_evidence_invalid")
    conflict = any(item.relationship == "conflict" for item in result.comparison_findings)
    if result.debate_needed != conflict:
        raise MentorError("comparison_decision_inconsistent")
