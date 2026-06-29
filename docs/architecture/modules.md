# Module Boundaries: core, im, agent, models

**Status**: living document
**Audience**: contributors adding or replacing an adapter

riji-agent is a **local journal core** with three replaceable adapter layers
around it: IM (chat transport), agent runtime (routing + orchestration), and
model (reasoning). The batteries-included default stack is Feishu + Hermes +
DeepSeek, but each layer is selected by config and resolved through a small
registry, so adding an adapter means *registering* it — never editing the wiring.

```text
   IM adapter            agent runtime            model adapter
  (im/, default     →   (hermes/, default    →   (models/, default
   feishu)               hermes)                   deepseek)
        │                     │                         │
        └─────────────►  local journal core  ◄──────────┘
              journal/ retrieval/ memory/ drafts/ audit/ personas/ yangming/
```

## The local core (never depends on an adapter)

These packages own the vault, local state, and policy. They must not import
`im/`, `hermes/`, or any model client:

- `journal/` — parse Obsidian Markdown, build the SQLite index, atomic append.
- `retrieval/` — bounded search/timeline tools; enforces snippet caps and the
  `private: true` exclusion.
- `memory/` — shared confirmed memory vs. persona-private candidates/sessions.
- `drafts/` — patch → confirm token → atomic append state machine.
- `audit/` — tool-call metadata (source ids, not full text).
- `personas/`, `yangming/` — mentor prompts and the citable KB.

Dependency rule (from the MVP architecture): edges point **inward only**.
`api/transport → personas/drafts → journal/retrieval/memory/audit`. The core
never reaches outward to a transport or model.

## The three adapter layers

Each layer has the same shape: a neutral contract, a default adapter, and a
registry that maps a config name to it.

| Layer | Config | Contract | Default | Registry |
| --- | --- | --- | --- | --- |
| IM | `RIJI_IM_PROVIDER` | `im.models.IncomingChatMessage` | `feishu` (`im/feishu.py`) | `im/registry.py` |
| Agent runtime | `RIJI_AGENT_RUNTIME` | `agent.runtime.AgentRuntime` | `hermes` (`hermes/`) | `agent/registry.py` |
| Model | `RIJI_MODEL_PROVIDER` | `models.types.LLMProvider` | `deepseek` (`models/deepseek.py`) | `models/registry.py` |

The model layer is the fully worked example: `models/registry.py` maps a name to
a factory, `wiring.build_production_gateway` calls `build_model_provider(settings)`
instead of constructing a provider directly, and a generic
`OpenAICompatibleProvider` ships as a second adapter (`RIJI_MODEL_PROVIDER=openai`).
`DeepSeekProvider` is a thin preset over it.

## Adding a model adapter (worked example)

1. Implement the `models.types.LLMProvider` protocol (one `complete()` method).
   Keep the API key inside the object; never log it or put it in error text.
2. Add any config fields it needs to `config.Settings` (optional, defaulting off
   so the DeepSeek default path is unaffected).
3. Register a factory in `models/registry.py`:
   `register_model_provider("myprovider", _build_myprovider)`.
4. That is all wiring needs — `build_model_provider` now resolves the new name,
   and config validation accepts it because it is in `supported_model_providers()`.
5. Add tests for dispatch and for the misconfiguration (e.g. missing key)
   failing at load, not at first request.

Adding an IM or agent-runtime adapter follows the same three steps against
`im/registry.py` / `agent/registry.py` and the corresponding contract.

## Background service backends

Local background-service management (`riji-agent service ...`) uses the same
shape, selected by the CLI `--target` (default `auto`) rather than by env config.
`service/base.py` defines the neutral `ServiceManager` contract + `ServiceStatus`;
each platform backend (`launchd.py` macOS, `systemd.py` Linux, `windows.py`
Windows Task Scheduler) implements it; `service/registry.py` maps a target name
to a factory and `default_target()` resolves the host platform. Backends take an
injectable command runner so every backend is unit-tested on any OS with a fake.

## Boundary tests

`tests/test_model_provider_layer.py` asserts the `models/` package never
references journal, IM, or transport symbols (`journal_root`, `FeishuMessage`,
`HermesGateway`, …). Keep adapter packages free of core/transport imports so the
layering stays honest.
