import json
from pathlib import Path

import pytest

from riji_agent.agent.tools import ToolRegistry
from riji_agent.drafts.service import DraftService
from riji_agent.drafts.store import DraftStore
from riji_agent.journal.index import JournalIndex
from riji_agent.retrieval.models import ToolContext
from riji_agent.retrieval.service import RetrievalService

TEMPLATE = "# {{date}}\n\n## 🌆 Evening\n\n## 🧠 Notes\n"


def _ctx() -> ToolContext:
    return ToolContext(request_id="r1", session_id="u1:gentle:c1", feishu_user_id="u1", persona_id="gentle")


@pytest.fixture
def parts(tmp_path: Path):
    root = tmp_path / "riji"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "daily.md").write_text(TEMPLATE, encoding="utf-8")
    index = JournalIndex(database_path=tmp_path / "data" / "idx.sqlite3", journal_root=root)
    draft_service = DraftService(DraftStore(tmp_path / "data" / "d.sqlite3"), root, index)
    retrieval = RetrievalService(index)
    yield retrieval, draft_service
    index.close()


def test_draft_tool_creates_an_awaiting_draft(parts) -> None:
    retrieval, draft_service = parts
    registry = ToolRegistry(retrieval, draft_service=draft_service)

    args = json.dumps({"operations": [{"section": "🌆 Evening", "content": "评审通过"}]})
    invocation = registry.invoke(_ctx(), "draft_daily_entry", args)

    assert invocation.ok is True
    assert invocation.payload["awaiting_confirmation"] is True
    assert draft_service.get_latest_awaiting_for_session("u1:gentle:c1") is not None


def test_draft_tool_is_exposed_in_specs_only_when_enabled(parts) -> None:
    retrieval, draft_service = parts
    with_drafts = {t["function"]["name"] for t in ToolRegistry(retrieval, draft_service=draft_service).tool_specs()}
    without = {t["function"]["name"] for t in ToolRegistry(retrieval).tool_specs()}
    assert "draft_daily_entry" in with_drafts
    assert "draft_daily_entry" not in without


def test_draft_tool_unavailable_without_service(parts) -> None:
    retrieval, _draft_service = parts
    registry = ToolRegistry(retrieval)  # no draft service
    invocation = registry.invoke(_ctx(), "draft_daily_entry", "{}")
    assert invocation.error == "unknown_tool"
