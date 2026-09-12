"""Codex isolation checks using synthetic files and a loopback model endpoint.

The wire check is opt-in: set RIJI_TEST_CODEX_BIN to an installed official CLI.
It uses no account login, never sends a model request to a cloud provider, and
retains only tool names, store flags and synthetic-canary presence from requests.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from riji_agent.models.types import LLMError


@pytest.mark.parametrize("entry", [
    "AGENTS.md", "AGENTS.override.md", "config.toml", "skills", "plugins", "rules",
])
def test_codex_home_rejects_instruction_sources(tmp_path: Path, entry: str) -> None:
    from riji_agent.models.codex_runtime import check_home

    home = tmp_path / "codex"
    home.mkdir(mode=0o700)
    target = home / entry
    if entry in {"skills", "plugins", "rules"}:
        target.mkdir()
        (target / "SKILL.md").write_text("RIJI_SKILL_CANARY", encoding="utf-8")
    else:
        target.write_text("RIJI_AGENTS_CANARY", encoding="utf-8")
    with pytest.raises(LLMError, match="^codex_home_not_isolated$"):
        check_home(home)


def test_codex_rejects_profile_with_executable_mcp_and_hook_canaries(tmp_path: Path) -> None:
    from riji_agent.models.codex_runtime import check_home

    home = tmp_path / "codex"
    home.mkdir(mode=0o700)
    marker = tmp_path / "unexpected-execution"
    script = f"from pathlib import Path; Path({str(marker)!r}).write_text('executed')"
    launcher = json.dumps([sys.executable, "-c", script])
    config = (f'developer_instructions = "RIJI_CONFIG_CANARY"\nnotify = {launcher}\n'
              f'[mcp_servers.canary]\ncommand = {json.dumps(sys.executable)}\n'
              f'args = {json.dumps(["-c", script])}\n')
    (home / "config.toml").write_text(config, encoding="utf-8")
    (home / "hooks.json").write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": shlex.join([sys.executable, "-c", script])}]}]},
    }), encoding="utf-8")
    with pytest.raises(LLMError, match="^codex_home_not_isolated$"):
        check_home(home)
    assert not marker.exists()


def test_codex_home_allows_owned_regular_auth_file(tmp_path: Path) -> None:
    from riji_agent.models.codex_runtime import check_home

    home = tmp_path / "codex"
    home.mkdir(mode=0o700)
    auth = home / "auth.json"
    auth.write_text('{"synthetic": true}', encoding="utf-8")
    check_home(home)
    assert auth.read_text(encoding="utf-8") == '{"synthetic": true}'


def test_codex_home_rejects_linked_auth_file(tmp_path: Path) -> None:
    from riji_agent.models.codex_runtime import check_home

    home = tmp_path / "codex"
    home.mkdir(mode=0o700)
    original = tmp_path / "synthetic-auth.json"
    original.write_text('{"synthetic": true}', encoding="utf-8")
    (home / "auth.json").symlink_to(original)
    with pytest.raises(LLMError, match="^codex_home_not_isolated$"):
        check_home(home)
    assert original.read_text(encoding="utf-8") == '{"synthetic": true}'


@dataclass
class WireEvidence:
    canaries: tuple[str, ...]
    requests: list[dict[str, Any]] = field(default_factory=list)
    invalid_requests: int = 0

    def observe(self, payload: dict[str, Any]) -> None:
        rendered = json.dumps(payload, ensure_ascii=False)
        tools = payload.get("tools", [])
        self.requests.append({
            "tool_names": [tool.get("name", tool.get("type")) for tool in tools],
            "tool_count": len(tools),
            "store": payload.get("store"),
            "canaries_present": {marker: marker in rendered for marker in self.canaries},
        })


def _response_events() -> bytes:
    text = '{"content":"Synthetic completion","tool_calls":[]}'
    part = {"type": "output_text", "text": text, "annotations": []}
    item = {"id": "msg_riji_synthetic", "type": "message", "role": "assistant",
            "status": "completed", "content": [part]}
    response = {"id": "resp_riji_synthetic", "object": "response", "created_at": 1,
                "status": "completed", "output": [item],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
    events = [
        {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
        {"type": "response.output_item.added", "output_index": 0,
         "item": {**item, "status": "in_progress", "content": []}},
        {"type": "response.content_part.added", "item_id": item["id"],
         "output_index": 0, "content_index": 0, "part": {**part, "text": ""}},
        {"type": "response.output_text.delta", "item_id": item["id"],
         "output_index": 0, "content_index": 0, "delta": text},
        {"type": "response.output_text.done", "item_id": item["id"],
         "output_index": 0, "content_index": 0, "text": text},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": response},
    ]
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                   for event in events).encode()


def _handler(evidence: WireEvidence) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            size = int(self.headers.get("Content-Length", "0"))
            if self.path != "/v1/responses" or not 0 < size <= 2_000_000:
                evidence.invalid_requests += 1
                self.send_error(400)
                return
            try:
                payload = json.loads(self.rfile.read(size))
                evidence.observe(payload)
            except (ValueError, TypeError, AttributeError):
                evidence.invalid_requests += 1
                self.send_error(400)
                return
            body = _response_events()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            body = b'{"data":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


def _synthetic_provider_args(port: int) -> list[str]:
    values = {
        "model_provider": "riji_synthetic",
        "model_providers.riji_synthetic.name": "Riji synthetic isolation check",
        "model_providers.riji_synthetic.base_url": f"http://127.0.0.1:{port}/v1",
        "model_providers.riji_synthetic.wire_api": "responses",
        "model_providers.riji_synthetic.requires_openai_auth": False,
        "model_providers.riji_synthetic.request_max_retries": 0,
        "model_providers.riji_synthetic.stream_max_retries": 0,
        "model_providers.riji_synthetic.stream_idle_timeout_ms": 5_000,
        "features.enable_request_compression": False,
        "check_for_update_on_startup": False,
    }
    return [argument for key, value in values.items()
            for argument in ("-c", f"{key}={json.dumps(value)}")]


def _fixture_files(root: Path) -> tuple[dict[Path, bytes], tuple[str, ...]]:
    markers = ("RIJI_PARENT_AGENTS_CANARY", "RIJI_PARENT_CONFIG_CANARY",
               "RIJI_PARENT_SKILL_CANARY")
    script = f"from pathlib import Path; Path({str(root / 'unexpected-execution')!r}).touch()"
    project_config = (
        f'developer_instructions = "{markers[1]}"\n'
        f'notify = {json.dumps([sys.executable, "-c", script])}\n'
        f'[mcp_servers.canary]\ncommand = {json.dumps(sys.executable)}\n'
        f'args = {json.dumps(["-c", script])}\n'
    )
    sources = {
        root / "AGENTS.md": markers[0],
        root / ".codex" / "config.toml": project_config,
        root / ".codex" / "hooks.json": json.dumps({
            "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": shlex.join([sys.executable, "-c", script])}]}]},
        }),
        root / ".agents" / "skills" / "canary" / "SKILL.md":
            f"---\nname: canary\ndescription: synthetic isolation fixture\n---\n{markers[2]}\n",
        root / "vault-sentinel.md": "Synthetic diary; must never be read or changed.",
    }
    for path, content in sources.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return {path: path.read_bytes() for path in sources}, markers


@pytest.mark.skipif(not os.environ.get("RIJI_TEST_CODEX_BIN"),
                    reason="Set RIJI_TEST_CODEX_BIN for the offline official-CLI wire check")
@pytest.mark.parametrize("model", ["gpt-5.6-terra", "gpt-5.6-luna"])
def test_official_codex_wire_has_no_tools_or_unapproved_context(tmp_path: Path, model: str) -> None:
    from riji_agent.models.codex import _consume_events, parse_turn
    from riji_agent.models.codex_runtime import child_environment, command, check_home

    home, workdir = tmp_path / "codex", tmp_path / "fixture" / "empty"
    home.mkdir(mode=0o700)
    workdir.mkdir(parents=True)
    untouched, canaries = _fixture_files(workdir.parent)
    check_home(home)
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({
        "type": "object", "additionalProperties": False,
        "properties": {"content": {"type": "string"},
                       "tool_calls": {"type": "array", "items": {"type": "string"}}},
        "required": ["content", "tool_calls"],
    }), encoding="utf-8")
    evidence = WireEvidence(canaries)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(evidence))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    argv = command(os.environ["RIJI_TEST_CODEX_BIN"], model, home, workdir, schema)
    argv += _synthetic_provider_args(server.server_port)
    environment = child_environment(home)
    environment = {key: value for key, value in environment.items()
                   if "proxy" not in key.lower()}
    environment["NO_PROXY"] = "127.0.0.1,localhost"
    try:
        result = subprocess.run(argv, input="Return a synthetic structured completion.",
                                cwd=workdir, env=environment, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, timeout=45)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    assert result.returncode == 0, "The official CLI did not complete the synthetic response"
    # Run the actual transport's event guard too: warnings are not tool activity,
    # but unhandled ErrorItems would still abort an otherwise successful response.
    state = {"buffer": b"", "bytes": 0, "text": None, "completed": False}
    _consume_events(result.stdout.encode(), state)
    assert state["completed"] and not state["buffer"].strip()
    assert parse_turn(state["text"], []).content == "Synthetic completion"
    assert evidence.requests, "The CLI did not reach the synthetic loopback provider"
    assert evidence.invalid_requests == 0
    assert all(request["tool_count"] == 0 for request in evidence.requests), evidence.requests
    assert all(request["store"] is False for request in evidence.requests), evidence.requests
    assert not any(present for request in evidence.requests
                   for present in request["canaries_present"].values()), evidence.requests
    assert all(path.read_bytes() == content for path, content in untouched.items())
    assert not (workdir.parent / "unexpected-execution").exists()
