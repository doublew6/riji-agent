"""Only an explicit owner request opens historical AI material, with typed output."""

from dataclasses import replace
import json

import pytest

from riji_agent.agent.discussion_recall import requests_discussion_recall
from riji_agent.agent.loop import AgentRunner
from riji_agent.agent.tools import ToolRegistry
from riji_agent.models.types import AssistantTurn
from riji_agent.retrieval.models import ToolContext
from riji_agent.retrieval.service import RetrievalService
from test_agent_loop import FakeProvider, _tool_turn
from test_ai_discussion_journal import _commit, journal, provenance  # noqa: F401


@pytest.mark.parametrize("text", [
    "参考历史讨论：上次的建议是什么？", "请参考之前的导师讨论结果，帮我回顾。",
    "回顾AI讨论结果", "查看上次的AI 导师讨论结果", "检索已保存的导师讨论结果。",
])
def test_owner_explicit_recall(text):
    assert requests_discussion_recall(text)


@pytest.mark.parametrize("text", [
    "不要参考历史讨论", "我今天真的完成了工作。", "他说‘参考历史讨论’", "参考历史讨论这几个字怎么写",
    "‘参考历史讨论：’是引语", "请勿查看AI讨论结果", "总结我这周的经历", "",
])
def test_ordinary_negative_quoted_or_ambiguous_request_does_not_opt_in(text):
    assert not requests_discussion_recall(text)


def context(**updates):
    return replace(ToolContext("recall-request", "session", "owner", "gentle_reviewer"), **updates)


def tool_messages(provider):
    return [json.loads(row["content"]) for row in provider.calls[-1]["messages"] if row["role"] == "tool"]


def test_tool_payload_keeps_ai_provenance_for_explicit_recall(journal, provenance):
    _commit(journal, provenance)
    provider = FakeProvider([_tool_turn("search_journal", {"query": "SyntheticAdvice"}), AssistantTurn(content="AI建议，未经执行验证。")])
    AgentRunner(provider, ToolRegistry(RetrievalService(journal[1]))).run(context(), "参考历史讨论：SyntheticAdvice")
    item = tool_messages(provider)[0]["items"][0]
    assert item["content_type"] == "ai_discussion_result"
    assert item["content_spans"][0]["provenance"]["discussion_id"] == "run"
    assert item["content_spans"][0]["provenance"]["artifact_ids"] == ["artifact"]


def test_model_arguments_cannot_enable_ai_recall(journal, provenance):
    _commit(journal, provenance)
    provider = FakeProvider([_tool_turn("search_journal", {"query": "SyntheticAdvice", "include_ai_discussions": True}), AssistantTurn(content="无本人事实。")])
    AgentRunner(provider, ToolRegistry(RetrievalService(journal[1]))).run(context(), "总结我这周的经历")
    assert not any(item.get("items") for item in tool_messages(provider))


def test_ordinary_draft_cannot_relabel_ai_evidence_in_same_request(journal, provenance):
    _commit(journal, provenance)
    service = RetrievalService(journal[1])
    registry = ToolRegistry(service, draft_service=journal[3])
    service.search_journal(context(include_ai_discussions=True), "SyntheticAdvice")
    assert registry.has_ai_discussion_evidence("recall-request")
    args = json.dumps({"operations": [{"section": "🧠 Notes", "content": "I completed a marathon."}]})
    result = registry.invoke(context(), "draft_daily_entry", args)
    assert result.error == "ai_discussion_requires_handoff"
    # An unrelated new user entry is not permanently blocked by past AI retrieval.
    assert registry.invoke(context(request_id="new-personal-request"), "draft_daily_entry", args).error is None


def test_explicit_recall_cannot_fall_through_to_plain_draft_without_search(journal):
    registry = ToolRegistry(RetrievalService(journal[1]), draft_service=journal[3])
    result = registry.invoke(context(include_ai_discussions=True), "draft_daily_entry", json.dumps({
        "operations": [{"section": "🧠 Notes", "content": "A rephrased AI suggestion."}]}))
    assert result.error == "ai_discussion_requires_handoff"


def test_typed_ai_history_cannot_be_rewritten_as_a_plain_draft(journal):
    registry = ToolRegistry(RetrievalService(journal[1]), draft_service=journal[3])
    result = registry.invoke(context(ai_discussion_history=True), "draft_daily_entry", json.dumps({
        "operations": [{"section": "🧠 Notes", "content": "Advice copied from the preceding turn."}]}))
    assert result.error == "ai_discussion_requires_handoff"
