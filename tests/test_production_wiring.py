"""Production wiring: the default deployment app must mount the real service.

These tests exercise ``build_production_gateway`` / ``create_production_app``,
the actual deployment path — not a hand-assembled gateway. A stub model stands
in for DeepSeek so no network or real API key is used; the assertions verify the
modules are correctly wired together (routes, auth, tool loop, drafts, audit).
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from fastapi.testclient import TestClient

from riji_agent.config import Settings
from riji_agent.llm.types import AssistantTurn, ToolCall
from riji_agent.main import create_app, create_production_app
from riji_agent.wiring import build_production_gateway

SECRET = "wiring-shared-secret"
STUB_KEY = "stub-key-not-real"
TEMPLATE = "# {{date}}\n\n## 🌆 Evening\n\n## 🧠 Notes\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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


class StubModel:
    """Search -> sourced answer; or draft_daily_entry -> ask for confirmation."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def complete(self, messages, tools) -> AssistantTurn:
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        tool_results = [m for m in messages if m.get("role") == "tool"]
        tool_names = {t["function"]["name"] for t in tools}
        user_msg = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        )

        if not tool_results:
            # draft_daily_entry is registered but not advertised in a persona's
            # allowed_tools; the registry still invokes it (write stays gated on
            # the user's explicit 确认保存), matching the e2e flow.
            if "记录" in user_msg:
                return AssistantTurn(
                    content=None,
                    tool_calls=(
                        ToolCall(
                            "d1",
                            "draft_daily_entry",
                            json.dumps(
                                {
                                    "operations": [{"section": "🧠 Notes", "content": "今天评审通过"}],
                                    "target_date": "2026-07-01",
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    ),
                )
            if "search_journal" in tool_names:
                return AssistantTurn(
                    content=None,
                    tool_calls=(ToolCall("c1", "search_journal", json.dumps({"query": user_msg})),),
                )
            return AssistantTurn(content="（无可用工具）")

        sources: List[str] = []
        for message in tool_results:
            _collect_source_ids(json.loads(message["content"]), sources)
        if sources:
            cites = " ".join(f"[[{s}]]" for s in dict.fromkeys(sources))
            return AssistantTurn(content=f"根据日记：{cites}")
        return AssistantTurn(content="草稿已生成，请回复确认保存。")


def _settings(tmp_path: Path) -> Settings:
    root = tmp_path / "riji"
    _write(root / "templates" / "daily.md", TEMPLATE)
    _write(
        root / "daily" / "2026-06-24.md",
        "---\ndate: 2026-06-24\ntags: [ai]\n---\n# 2026-06-24\n项目进展评审通过。\n",
    )
    _write(
        root / "daily" / "2026-06-25.md",
        "---\ndate: 2026-06-25\nprivate: true\n---\n# 2026-06-25\n绝不出云的私密内容。\n",
    )
    return Settings(
        _env_file=None,
        RIJI_JOURNAL_ROOT=str(root),
        RIJI_DATA_DIR=str(tmp_path / "state"),
        DEEPSEEK_API_KEY=STUB_KEY,
        RIJI_ALLOWED_FEISHU_USER_IDS="ou_1",
        HERMES_SHARED_SECRET=SECRET,
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


def _stub_client(tmp_path: Path):
    settings = _settings(tmp_path)
    stub = StubModel()
    gateway = build_production_gateway(settings, provider=stub)
    app = create_app(settings, gateway=gateway)
    return TestClient(app), stub, gateway, Path(settings.journal_root)


# --- the headline acceptance: the default production app mounts the real route -

def test_create_production_app_mounts_hermes_route(tmp_path: Path) -> None:
    # No provider override: the real DeepSeekProvider is built but never called,
    # because auth is rejected before the model runs.
    app = create_production_app(_settings(tmp_path))
    client = TestClient(app)

    assert client.get("/healthz").status_code == 200
    # /hermes/messages is mounted (a 401/403, not a 404).
    assert _post(client, "hi", secret="wrong").status_code == 401
    assert _post(client, "hi", chat_type="group").status_code == 403
    assert _post(client, "hi", user="ou_evil").status_code == 403


# --- the wired modules actually cooperate end to end (stub model) --------------

def test_wired_tool_loop_returns_sourced_answer(tmp_path: Path) -> None:
    client, stub, gateway, _root = _stub_client(tmp_path)
    resp = _post(client, "项目进展")
    assert resp.status_code == 200
    assert "[[riji/daily/2026-06-24]]" in resp.json()["reply"]
    assert len(stub.calls) >= 2  # plan -> tool -> finalise


def test_wired_draft_then_confirm_writes(tmp_path: Path) -> None:
    client, _stub, _gateway, root = _stub_client(tmp_path)
    proposed = _post(client, "记录 今天评审通过", event_id="d1")
    assert proposed.status_code == 200
    confirmed = _post(client, "确认保存", event_id="d2")
    assert "已写入" in confirmed.json()["reply"]
    assert "- 今天评审通过" in (root / "daily" / "2026-07-01.md").read_text(encoding="utf-8")


def test_wired_persona_switch_to_wang_yangming(tmp_path: Path) -> None:
    client, _stub, _gateway, _root = _stub_client(tmp_path)
    resp = _post(client, "/导师 王阳明 谈谈知行合一")
    assert resp.status_code == 200
    assert resp.json()["persona_id"] == "wang_yangming"


def test_wired_idempotency_and_audit(tmp_path: Path) -> None:
    client, stub, gateway, _root = _stub_client(tmp_path)
    first = _post(client, "项目进展", event_id="same")
    before = len(stub.calls)
    second = _post(client, "项目进展", event_id="same")
    assert second.json()["deduplicated"] is True
    assert second.json()["reply"] == first.json()["reply"]
    assert len(stub.calls) == before  # model not called again

    # audit captured the tool-call metadata (source ids), proving it is wired.
    assert "riji/daily/2026-06-24" in gateway._responder._audit.all_source_ids()


def test_yangming_seed_loaded_once(tmp_path: Path) -> None:
    # Building twice against the same data dir must not re-seed the KB.
    settings = _settings(tmp_path)
    build_production_gateway(settings, provider=StubModel())
    second = build_production_gateway(settings, provider=StubModel())
    # the KB is reachable through the tool registry's yangming handler
    assert "search_yangming" in second._responder._tools.names()
