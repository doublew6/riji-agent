"""Synthetic regressions for journal metadata and direct local Mem0 transport."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Sequence

import httpcore
import httpx
import httpx._utils
import pytest

from riji_agent.agent.loop import AgentRunner
from riji_agent.agent.tools import ToolRegistry
from riji_agent.journal.index import JournalIndex
from riji_agent.journal.parser import parse_note
from riji_agent.memory.mem0 import Mem0Client
from riji_agent.memory.models import MemoryScope
from riji_agent.models.types import AssistantTurn, ToolCall
from riji_agent.personas.registry import PersonaRegistry
from riji_agent.retrieval.models import ToolContext
from riji_agent.retrieval.service import RetrievalService

_CANARY = "RIJI_SYNTHETIC_RESTRICTED_HEADING"
_SOURCE = "riji/daily/2026-01-01"
_PUBLIC_TEXT = "Synthetic allowed diary content."
_BLOCKS = (
    f"<!-- riji-memory: local -->\n# {_CANARY}\n<!-- /riji-memory -->",
    f"<!-- riji-memory: none -->\n# {_CANARY}\n<!-- /riji-memory -->",
    f"<!-- riji-memory: local -->\n# {_CANARY}",
    f"<!-- riji-memory: local -->\n<!-- riji-memory: cloud -->\n# {_CANARY}\n"
    "<!-- /riji-memory -->\n<!-- /riji-memory -->",
)


def _write_source(tmp_path: Path, block: str, prefix: str = "") -> Path:
    path = tmp_path / "vault" / "daily" / "2026-01-01.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"{prefix}## 🧠 Notes\n{_PUBLIC_TEXT}\n{block}\n", encoding="utf-8")
    return path


class _OfflineProvider:
    def __init__(self, turns: Sequence[AssistantTurn]) -> None:
        self._turns = iter(turns)
        self.calls: list[list[dict[str, Any]]] = []

    def complete(self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]) -> AssistantTurn:
        self.calls.append(json.loads(json.dumps(list(messages))))
        return next(self._turns)


def _journal_tool_calls() -> tuple[ToolCall, ...]:
    requests = (
        ("search_journal", {"query": "Synthetic allowed diary content"}),
        ("read_note", {"source_id": _SOURCE}),
        ("list_periods", {}),
        ("timeline", {"topic": "Synthetic allowed diary content",
                      "date_from": "2026-01-01", "date_to": "2026-01-01"}),
        ("find_before_after", {"date": "2026-01-01", "days": 1}),
    )
    return tuple(ToolCall(name, name, json.dumps(arguments)) for name, arguments in requests)


def _seed_legacy_titles(path: Path) -> None:
    # Simulate a persisted pre-fix index: its file hash is still current, so a
    # normal incremental scan will not regenerate the cached title.
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE notes SET title=?", (_CANARY,))
        connection.execute("UPDATE notes_fts SET title=?", (_CANARY,))


@pytest.mark.parametrize("block", _BLOCKS, ids=("local", "none", "unclosed", "nested"))
@pytest.mark.parametrize("legacy_index", (False, True), ids=("fresh-index", "unchanged-old-index"))
def test_restricted_heading_never_reaches_agent_through_any_journal_tool(
    tmp_path: Path, block: str, legacy_index: bool,
) -> None:
    path = _write_source(tmp_path, block)
    original = path.read_bytes()
    database = tmp_path / "data" / "index.sqlite3"
    index = JournalIndex(database, path.parent.parent)
    try:
        index.build_index()
        if legacy_index:
            index.close()
            _seed_legacy_titles(database)
            index = JournalIndex(database, path.parent.parent)
            assert index.build_index().unchanged == 1
            assert index.get(_SOURCE).title == _CANARY
        tools = ToolRegistry(RetrievalService(index))
        persona = PersonaRegistry().get("gentle_reviewer")
        calls = _journal_tool_calls()
        assert {call.name for call in calls}.issubset(persona.allowed_tools)
        provider = _OfflineProvider([AssistantTurn(None, calls), AssistantTurn("Synthetic answer.")])
        result = AgentRunner(
            provider, tools, tool_specs=tools.tool_specs(persona.allowed_tools),
            system_prompt=persona.system_prompt + persona.answer_boundaries,
        ).run(ToolContext("synthetic-request", "synthetic-user:gentle_reviewer:chat",
                          "synthetic-user", persona.persona_id), "Review the synthetic note.")
        assert len(provider.calls) == 2 and len(result.audit) == len(calls)
        assert all(entry.ok and entry.source_ids == (_SOURCE,) for entry in result.audit)
        payloads = [json.loads(message["content"]) for message in provider.calls[-1]
                    if message["role"] == "tool"]
        assert len(payloads) == len(calls)
        assert _PUBLIC_TEXT in json.dumps(payloads)
        assert "2026-01-01" in json.dumps(payloads)
        assert _CANARY not in json.dumps(provider.calls)
        assert path.read_bytes() == original
    finally:
        index.close()


@pytest.mark.parametrize("prefix,expected_title", (
    ("---\ntitle: Public frontmatter title\n---\n", "Public frontmatter title"),
    ("# Public heading\n", "Public heading"),
    ("", "2026-01-01"),
))
def test_public_title_precedence_and_source_bytes_are_preserved(
    tmp_path: Path, prefix: str, expected_title: str,
) -> None:
    path = _write_source(tmp_path, _BLOCKS[0], prefix)
    original = path.read_bytes()
    note = parse_note(path, path.parent.parent)
    assert note.title == expected_title
    assert _PUBLIC_TEXT in note.body and _CANARY not in note.body
    assert path.read_bytes() == original


@pytest.mark.parametrize("proxy_source", ("environment", "system"))
def test_mem0_requests_bypass_environment_and_os_proxy_discovery(
    monkeypatch: pytest.MonkeyPatch, proxy_source: str,
) -> None:
    proxy = "http://synthetic-untrusted-proxy.invalid:9321"
    for key in list(os.environ):
        if "proxy" in key.lower():
            monkeypatch.delenv(key)
    if proxy_source == "environment":
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.setenv(key, proxy)
    else:
        # HTTPX's urllib fallback also discovers macOS System Configuration
        # proxies without any *_PROXY environment variable.
        monkeypatch.setattr(httpx._utils, "getproxies", lambda: {"http": proxy, "https": proxy})
    sent: list[dict[str, Any]] = []

    def intercept(transport: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        sent.append({"proxied": isinstance(transport._pool, httpcore.HTTPProxy),
                     "host": request.url.host, "body": json.loads(request.read()),
                     "has_key": "X-API-Key" in request.headers})
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", intercept)
    with httpx.Client() as control:
        control.post("http://127.0.0.1:38881/probe", json={"synthetic": True})
    assert sent.pop()["proxied"] is True  # The synthetic proxy really affects a normal client.
    client = Mem0Client("http://127.0.0.1:38881", "synthetic-mem0-key")
    try:
        client.add("Synthetic local memory.", user_id="synthetic-user", scope=MemoryScope.SHARED,
                   persona_id=None, metadata={"scope": "shared"})
    finally:
        client._client.close()
    assert len(sent) == 1 and sent[0]["proxied"] is False
    assert sent[0]["host"] == "127.0.0.1" and sent[0]["has_key"]
    assert sent[0]["body"]["infer"] is False
    assert sent[0]["body"]["messages"][0]["content"] == "Synthetic local memory."
