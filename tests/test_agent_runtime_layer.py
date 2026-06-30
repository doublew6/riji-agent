from __future__ import annotations

from pathlib import Path

from riji_agent.agent.hermes import HermesAgentRuntime, build_hermes_runtime_router
from riji_agent.agent.runtime import AgentRuntime
from riji_agent.hermes.gateway import HermesGateway


def test_hermes_is_exposed_as_default_agent_runtime_adapter() -> None:
    assert HermesAgentRuntime is HermesGateway
    assert build_hermes_runtime_router.__name__ == "build_hermes_router"


def test_agent_runtime_contract_is_provider_and_transport_neutral() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "riji_agent"
        / "agent"
        / "runtime.py"
    ).read_text(encoding="utf-8")

    forbidden = ("Feishu", "Hermes", "DeepSeek", "journal_root", "SQLite")
    for phrase in forbidden:
        assert phrase not in source


def test_agent_runtime_protocol_defines_handle_boundary() -> None:
    assert hasattr(AgentRuntime, "handle")
