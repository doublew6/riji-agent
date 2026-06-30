"""MVP smoke test over the real HTTP app (``-m smoke``).

Drives the deployment path — riji-agent app + /hermes/messages + a stubbed
DeepSeek tool loop + persona routing — with temporary fixtures only. It never
reads the real .env, the real journal vault or a real API key.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from riji_agent.agent.tools import ToolRegistry
from riji_agent.config import Settings
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.gateway import HermesGateway
from riji_agent.hermes.responder import AgentResponder
from riji_agent.journal.index import JournalIndex
from riji_agent.models.types import AssistantTurn, ToolCall
from riji_agent.main import create_app
from riji_agent.memory.store import MemoryStore
from riji_agent.personas.registry import PersonaRegistry
from riji_agent.retrieval.models import ToolContext
from riji_agent.retrieval.service import RetrievalService

pytestmark = pytest.mark.smoke

SECRET = "smoke-shared-secret"
STUB_KEY = "stub-key-not-real"
SECRET_NOTE = "这段绝不应完整出云的超长私密内容编号X7Y9Z_请勿外泄到云端模型上下文里面去"


def _collect_source_ids(obj: Any, out: List[str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "source_id" and isinstance(value, str):
                out.append(value)
            else:
                _collect_source_ids(value, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_source_ids(item, out)


class StubDeepSeek:
    """One search round, then a sourced final answer. No network, no key."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def complete(self, messages, tools) -> AssistantTurn:
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        tool_results = [m for m in messages if m.get("role") == "tool"]
        tool_names = {t["function"]["name"] for t in tools}

        if not tool_results and "search_journal" in tool_names:
            question = next(
                (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
            )
            return AssistantTurn(
                content=None,
                tool_calls=(ToolCall("c1", "search_journal", json.dumps({"query": question})),),
            )

        sources: List[str] = []
        for message in tool_results:
            _collect_source_ids(json.loads(message["content"]), sources)
        if sources:
            cites = " ".join(f"[[{s}]]" for s in dict.fromkeys(sources))
            return AssistantTurn(content=f"根据日记：{cites}")
        return AssistantTurn(content="日记中未找到足够证据")

    def sent_text(self) -> str:
        return json.dumps(self.calls, ensure_ascii=False)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def app_stack(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "riji"
    _write(root / "daily" / "2026-06-10.md", "---\ndate: 2026-06-10\ntags: [work]\n---\n# 2026-06-10\n项目进展启动，确定方向。\n")
    _write(root / "daily" / "2026-06-20.md", "---\ndate: 2026-06-20\ntags: [work, ai]\n---\n# 2026-06-20\n项目进展遇到阻碍，调整计划。\n")
    _write(root / "daily" / "2026-06-24.md", "---\ndate: 2026-06-24\ntags: [ai]\n---\n# 2026-06-24\n项目进展评审通过，团队庆祝。\n")
    _write(root / "daily" / "2026-06-25.md", f"---\ndate: 2026-06-25\nprivate: true\n---\n# 2026-06-25\n{SECRET_NOTE}。\n")

    data_dir = tmp_path / "state"
    settings = Settings(
        _env_file=None,
        RIJI_JOURNAL_ROOT=str(root),
        RIJI_DATA_DIR=str(data_dir),
        DEEPSEEK_API_KEY=STUB_KEY,
        RIJI_ALLOWED_FEISHU_USER_IDS="ou_1",
        HERMES_SHARED_SECRET=SECRET,
    )

    index = JournalIndex(database_path=data_dir / "index.sqlite3", journal_root=root)
    index.build_index()
    retrieval = RetrievalService(index)
    registry = ToolRegistry(retrieval)
    provider = StubDeepSeek()
    store = MemoryStore(data_dir / "mem.sqlite3")
    gateway = HermesGateway(
        hermes_secret=SECRET,
        allowed_user_ids=settings.allowed_feishu_user_ids,
        registry=PersonaRegistry(),
        store=store,
        events=EventLog(data_dir / "events.sqlite3"),
        responder=AgentResponder(provider, registry),
    )
    app = create_app(settings, gateway=gateway)
    return SimpleNamespace(
        client=TestClient(app), provider=provider, retrieval=retrieval,
        store=store, root=root, data_dir=data_dir,
    )


def _post(client: TestClient, text: str, *, event_id: str = "e1", chat_type: str = "p2p",
          user: str = "ou_1", secret: str = SECRET):
    headers = {"X-Hermes-Secret": secret} if secret is not None else {}
    return client.post(
        "/hermes/messages",
        json={"event_id": event_id, "feishu_user_id": user, "chat_id": "c1",
              "chat_type": chat_type, "text": text},
        headers=headers,
    )


def _ctx() -> ToolContext:
    return ToolContext(request_id="r", session_id="s", feishu_user_id="ou_1", persona_id="gentle_reviewer")


# 1. startup & config boundary
def test_smoke_healthz_and_docs_closed(app_stack: SimpleNamespace) -> None:
    health = app_stack.client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"service": "riji-agent", "status": "ok"}
    assert app_stack.client.get("/openapi.json").status_code == 404  # docs disabled


# 2. journal index & local retrieval
def test_smoke_index_and_retrieval(app_stack: SimpleNamespace) -> None:
    from datetime import date

    result = app_stack.retrieval.search_journal(_ctx(), "项目进展")
    assert result.items and result.items[0].source_id.startswith("riji/daily/")
    assert result.items[0].snippet  # a snippet, not the whole vault

    periods = app_stack.retrieval.list_periods(_ctx())
    assert "riji/daily/2026-06-24" in {p.source_id for p in periods.items}

    timeline = app_stack.retrieval.timeline(_ctx(), "项目进展", date(2026, 6, 1), date(2026, 6, 30))
    assert timeline.notes_found >= 3


# 3. agent tool loop via HTTP (stub model)
def test_smoke_agent_tool_loop_returns_sourced_answer(app_stack: SimpleNamespace) -> None:
    resp = _post(app_stack.client, "项目进展")
    assert resp.status_code == 200
    assert "[[riji/daily/" in resp.json()["reply"]  # only possible after a real search round
    assert len(app_stack.provider.calls) >= 2  # plan -> tool -> finalise


# 4. persona routing + shared/isolated memory
def test_smoke_persona_switch_and_memory(app_stack: SimpleNamespace) -> None:
    cid = app_stack.store.add_candidate("ou_1", "gentle_reviewer", "用户正在准备一场演讲")
    app_stack.store.confirm_candidate(cid)
    app_stack.store.add_candidate("ou_1", "gentle_reviewer", "未确认的私有观察")

    assert _post(app_stack.client, "/导师 直率教练 我最近怎么样", event_id="e1").json()["persona_id"] == "blunt_coach"
    assert _post(app_stack.client, "/导师 未来的我 给点建议", event_id="e2").json()["persona_id"] == "future_self"

    sent = app_stack.provider.sent_text()
    assert "用户正在准备一场演讲" in sent  # confirmed memory shared into the model context
    assert "未确认的私有观察" not in sent  # unconfirmed never shared
    assert app_stack.store.get_session_history("ou_1", "blunt_coach", "c1") != []
    assert app_stack.store.get_session_history("ou_1", "gentle_reviewer", "c1") == []  # isolated


# 5. gateway auth & idempotency
def test_smoke_auth_rejections(app_stack: SimpleNamespace) -> None:
    assert _post(app_stack.client, "hi", secret="wrong").status_code == 401
    assert _post(app_stack.client, "hi", chat_type="group").status_code == 403
    assert _post(app_stack.client, "hi", user="ou_evil").status_code == 403
    assert _post(app_stack.client, "hi").status_code == 200


def test_smoke_duplicate_event_is_idempotent(app_stack: SimpleNamespace) -> None:
    first = _post(app_stack.client, "记一笔", event_id="dup")
    before = len(app_stack.provider.calls)
    second = _post(app_stack.client, "记一笔", event_id="dup")
    assert second.json()["deduplicated"] is True
    assert second.json()["reply"] == first.json()["reply"]
    assert len(app_stack.provider.calls) == before  # model not called again


# 6. privacy minimisation
def test_smoke_private_content_never_reaches_model(app_stack: SimpleNamespace) -> None:
    # query a substring that only the private note contains -> it must be excluded
    _post(app_stack.client, "私密内容")
    sent = app_stack.provider.sent_text()
    assert SECRET_NOTE not in sent  # private note body never sent to the model
    assert STUB_KEY not in sent  # API key never in model context
    assert str(app_stack.root) not in sent  # no absolute vault path
    assert str(app_stack.data_dir) not in sent  # no database path
    assert "riji/daily/2026-06-25" not in sent  # private note never surfaced as a source
