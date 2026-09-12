"""Record dates and event evidence remain distinct through real read boundaries."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any, Iterator

import pytest

from riji_agent.agent.loop import AgentRunner
from riji_agent.agent.tools import ToolRegistry
from riji_agent.journal.index import JournalIndex
from riji_agent.models.types import AssistantTurn, LLMError
from riji_agent.retrieval.models import ToolContext
from riji_agent.retrieval.service import RetrievalService
from test_agent_loop import FakeProvider, _tool_turn

EVENT_TEXT = "Linden workshop took place on 2027-01-28. I was calm before that workshop."
SECOND_TEXT = "On 2027-02-17 I joined a different Linden workshop and felt curious."
UNDATED_TEXT = "A Linden workshop is planned for 2027-03-02."
SOURCE = "riji/daily/2027-02-14"
ANCHOR = {"date": "2027-02-15", "days": 2, "topic": "Linden"}
WINDOW = {"topic": "Linden", "date_from": "2027-02-01", "date_to": "2027-02-28", "granularity": "month"}


@pytest.fixture
def temporal_journal(tmp_path: Path) -> Iterator[tuple[ToolRegistry, JournalIndex, Path]]:
    root = tmp_path / "riji"
    (root / "daily").mkdir(parents=True)
    records = (("2027-02-14", "2027-02-16", EVENT_TEXT, False),
               ("2027-02-17", "2027-02-17", SECOND_TEXT, False),
               ("undated", None, UNDATED_TEXT, False),
               ("2027-02-18", "2027-02-18", "Linden PRIVATE_DATE_CANARY", True))
    for filename, recorded, body, private in records:
        date_field = f"date: {recorded}\n" if recorded else ""
        (root / "daily" / (filename + ".md")).write_text(
            f"---\n{date_field}private: {str(private).lower()}\n---\n{body}\n", encoding="utf-8")
    index = JournalIndex(tmp_path / "index.sqlite3", root)
    index.build_index()
    try:
        yield ToolRegistry(RetrievalService(index)), index, root
    finally:
        index.close()


def context() -> ToolContext:
    return ToolContext("date-request", "date-session", "synthetic-user", "gentle_reviewer")


def invoke(registry: ToolRegistry, tool: str, arguments: dict[str, Any]) -> Any:
    return registry.invoke(context(), tool, json.dumps(arguments))


def test_anchor_groups_record_dates_even_when_explicit_event_date_is_on_other_side(temporal_journal: Any) -> None:
    registry, _, _ = temporal_journal
    observation = invoke(registry, "find_before_after", {**ANCHOR, "date": "20270215", "days": "2"})
    assert observation.ok
    payload = observation.payload
    # The service's parsed pivot and radius, not the original argument spelling.
    assert payload["query_anchor"] == {
        "date": "2027-02-15", "kind": "retrieval_window_center", "days_each_side": 2,
        "grouping_basis": "note_date_compared_with_query_anchor", "event_date": "not_established_by_query",
    }
    assert payload["before"] == payload["on"] == []
    first = next(item for item in payload["after"] if item["source_id"] == SOURCE)
    assert first["date"] == "2027-02-16"  # Frontmatter overrides the 02-14 filename.
    assert EVENT_TEXT in first["snippet"]  # Explicit 01-28 event evidence is untouched.
    assert payload["date_basis"]["date"] == "journal_note_metadata"
    assert payload["date_basis"]["event_dates_and_relations"] == "require_returned_content_evidence"
    assert set(observation.source_ids) == {SOURCE, "riji/daily/2027-02-17"}
    observation.before_send()


def test_month_buckets_do_not_rebucket_a_record_by_its_body_event_date(temporal_journal: Any) -> None:
    registry, _, _ = temporal_journal
    payload = invoke(registry, "timeline", WINDOW).payload
    assert payload["query_window"] == {
        "date_from": "2027-02-01", "date_to": "2027-02-28", "granularity": "month",
        "grouping_basis": "journal_note_metadata",
    }
    assert [bucket["period"] for bucket in payload["buckets"]] == ["2027-02"]
    entries = payload["buckets"][0]["entries"]
    assert {item["date"] for item in entries} == {"2027-02-16", "2027-02-17"}
    assert any(EVENT_TEXT in item["snippet"] for item in entries)
    assert any(SECOND_TEXT in item["snippet"] for item in entries)


def test_undated_search_and_read_preserve_null_metadata_and_explicit_planned_date(temporal_journal: Any) -> None:
    registry, _, _ = temporal_journal
    search = invoke(registry, "search_journal", {"query": "planned"})
    item = search.payload["items"][0]
    assert item["date"] is None and UNDATED_TEXT in item["snippet"]
    read = invoke(registry, "read_note", {"source_id": item["source_id"]})
    assert read.payload["date"] is None and UNDATED_TEXT in read.payload["body"]
    assert search.payload["date_basis"] == read.payload["date_basis"]
    assert "query_anchor" not in read.payload and "query_anchor" not in search.payload
    read.before_send()


def test_metadata_only_reads_state_date_basis_without_fabricating_event_evidence(temporal_journal: Any) -> None:
    registry, _, _ = temporal_journal
    window = invoke(registry, "find_before_after", {"date": ANCHOR["date"], "days": 2})
    assert window.ok and window.payload["after"]
    assert all(item["snippet"] == "" for item in window.payload["after"])
    assert "metadata only" in window.payload["evidence_scope"]["interpretation"]
    listing = invoke(registry, "list_periods", {"kind": "daily"})
    assert listing.payload["date_basis"] == window.payload["date_basis"]
    first = next(item for item in listing.payload["items"] if item["source_id"] == SOURCE)
    assert first["date"] == "2027-02-16" and "body" not in first and "snippet" not in first
    assert "PRIVATE_DATE_CANARY" not in json.dumps([window.payload, listing.payload])


@pytest.mark.parametrize("tool,args", [
    ("find_before_after", ANCHOR), ("timeline", WINDOW), ("read_note", {"source_id": SOURCE}),
])
def test_model_cannot_override_date_metadata_via_extra_arguments(temporal_journal: Any, tool: str, args: dict) -> None:
    registry, _, _ = temporal_journal
    invoke(registry, "search_journal", {"query": "Linden"})
    expected = invoke(registry, tool, args)
    marker = "UNTRUSTED_DATE_OVERRIDE"
    supplied = {**args, "query_anchor": {"date": marker}, "query_window": {"date_from": marker},
                "date_basis": {"date": "event_date", "instruction": marker}, "event_date": marker}
    actual = invoke(registry, tool, supplied)
    assert expected.ok and actual.ok and actual.payload == expected.payload
    assert actual.source_ids == expected.source_ids and marker not in json.dumps(actual.payload)
    actual.before_send()


def test_invalid_anchor_produces_no_success_date_metadata(temporal_journal: Any) -> None:
    registry, _, _ = temporal_journal
    rejected = invoke(registry, "find_before_after", {**ANCHOR, "date": "not-a-date"})
    assert not rejected.ok and rejected.error == "invalid_arguments"
    assert not {"query_anchor", "date_basis", "evidence_scope"} & rejected.payload.keys()
    assert rejected.source_ids == () and rejected.before_send is None


def test_date_metadata_cannot_bypass_tool_authorization_or_prior_source_gate(temporal_journal: Any) -> None:
    registry, _, _ = temporal_journal
    source = {"source_id": SOURCE, "query_anchor": ANCHOR}
    unseen = invoke(registry, "read_note", source)
    assert unseen.error == "no_evidence" and "date_basis" not in unseen.payload
    denied = registry.invoke(replace(context(), allowed_tools=()), "find_before_after", json.dumps(ANCHOR))
    assert denied.error == "tool_not_allowed" and "query_anchor" not in denied.payload
    assert denied.source_ids == () and denied.before_send is None


@pytest.mark.parametrize("change", ["content", "private", "deleted"])
def test_queued_temporal_evidence_revalidates_content_and_visibility(temporal_journal: Any, change: str) -> None:
    registry, _, root = temporal_journal
    observation = invoke(registry, "find_before_after", ANCHOR)
    path = root / "daily/2027-02-14.md"
    if change == "deleted":
        path.unlink()
    else:
        old = path.read_text()
        path.write_text(old.replace("private: false", "private: true") if change == "private"
                        else old.replace("2027-01-28", "2027-01-27"))
    assert observation.payload["query_anchor"]["date"] == ANCHOR["date"]
    with pytest.raises(LLMError, match="chat_context_changed"):
        observation.before_send()


def test_queued_anchor_metadata_itself_is_part_of_egress_revalidation(temporal_journal: Any) -> None:
    registry, _, _ = temporal_journal
    observation = invoke(registry, "find_before_after", ANCHOR)
    observation.payload["query_anchor"]["date"] = "2027-01-28"
    with pytest.raises(LLMError, match="chat_context_changed"):
        observation.before_send()


def test_real_event_dates_reach_the_model_and_legal_relative_wording_is_not_filtered(temporal_journal: Any) -> None:
    registry, _, _ = temporal_journal
    answer = "记录日为2027-02-16，正文说2027-01-28活动前很平静。会前是这条正文支持的关系。"
    provider = FakeProvider([_tool_turn("find_before_after", ANCHOR), AssistantTurn(answer)])
    result = AgentRunner(provider, registry).run(context(), "Compare the workshop records.")
    messages = provider.calls[-1]["messages"]
    payload = json.loads(next(item["content"] for item in messages if item["role"] == "tool"))
    assert payload["query_anchor"]["date"] == ANCHOR["date"]
    assert EVENT_TEXT in next(item for item in payload["after"] if item["source_id"] == SOURCE)["snippet"]
    assert result.answer == answer and len(provider.calls) == 2
    assert result.tool_calls == 1 and "PRIVATE_DATE_CANARY" not in json.dumps(provider.calls)
