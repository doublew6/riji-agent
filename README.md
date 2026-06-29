# riji-agent

[![test](https://github.com/doublew6/riji-agent/actions/workflows/test.yml/badge.svg)](https://github.com/doublew6/riji-agent/actions/workflows/test.yml)

[中文说明](README.zh-CN.md)

`riji-agent` is a local-first journal agent gateway for Obsidian-style Markdown
journals. It keeps the journal vault, local index, drafts, audit records, and
write permissions on the user's machine, while exposing only bounded,
auditable tools to an external agent or model runtime.

The batteries-included default stack is **Feishu + Hermes + DeepSeek**:

- Feishu is the default IM entry point for private chat.
- Hermes is the default agent runtime and message router.
- DeepSeek is the default OpenAI-compatible reasoning model provider.

That stack is the fastest supported path today, but the default stack is
**not the only supported architecture**. The project is built around
replaceable IM, agent, and model adapters over a local journal core: each is
selected by config and resolved through a small registry, so adding an adapter
means registering it, not editing the wiring. DeepSeek ships alongside a generic
OpenAI-compatible model adapter (`RIJI_MODEL_PROVIDER=openai`) as a worked
example. See [docs/architecture/modules.md](docs/architecture/modules.md) for
the core/im/agent/models boundaries and how to add an adapter.

## What It Is

`riji-agent` provides a local boundary for personal journal intelligence:

- reads an existing Markdown journal vault without copying it into the repo;
- builds a local SQLite index for search, timeline, and source lookup;
- lets an agent call narrow tools such as `search_journal` and `read_note`;
- blocks private notes and caps returned snippets before anything reaches a
  cloud model;
- creates journal write drafts that require explicit user confirmation before
  any Markdown file is changed;
- records metadata for audit without storing full sensitive text in logs.

## Privacy Model

This is **not a zero-egress system**. It is a local-control design with bounded
cloud reasoning. See [docs/privacy.md](docs/privacy.md) and
[SECURITY.md](SECURITY.md) before connecting real journal data.

Never sent by riji-agent:

- the complete vault;
- raw Markdown files as files;
- local SQLite databases;
- API keys, Feishu credentials, or the Hermes shared secret;
- filesystem paths or the vault directory structure;
- notes marked `private: true`.

May leave the machine when the default stack is enabled:

- Feishu/Lark receives the user's bot messages and bot replies;
- DeepSeek receives the system prompt, the user's question, and bounded journal
  snippets returned by local tools;
- Hermes receives routing metadata and the local gateway response, but should
  not directly read the vault or SQLite files.

## Quick Start

Requirements: Python 3.9+ and [uv](https://docs.astral.sh/uv/).

Try the fictional demo vault first. It does not read `.env`, your real journal,
or any real API key:

```bash
uv run riji-agent demo init --target /tmp/riji-demo-vault
uv run riji-agent chat --demo --question "launch planning"
```

The demo answer should include `[[riji/...]]` sources and exclude the sample
`private: true` note.

For the full default stack:

```bash
uv run riji-agent init --preset feishu-hermes-deepseek
# Edit .env with your journal path, DeepSeek API key, and Feishu user allowlist.
uv run riji-agent doctor
uv sync --extra dev
uv run riji-agent index    # prewarm the local index
# Validate your model key + journal retrieval end-to-end before wiring Feishu:
uv run riji-agent chat --question "本周关于发布我都记了什么？"
uv run riji-agent          # serve http://127.0.0.1:8765
```

`riji-agent chat --question "..."` runs the real agent loop and your configured
model provider against your vault over loopback — no Feishu or Hermes required —
so you can confirm the whole local path works before standing up the IM bridge.

Install riji-agent as a background user service so it restarts after login or an
accidental exit. The commands are the same on macOS (launchd), Linux (systemd
--user), and Windows (Task Scheduler); `--target` defaults to `auto` and picks
the right backend for your platform:

```bash
uv run riji-agent service install
uv run riji-agent service start
uv run riji-agent service status
```

While the machine is asleep or the user is logged out the bot cannot answer
Feishu messages; after wake/login the service manager restores the local
service. See [docs/deployment.md](docs/deployment.md#后台常驻服务macos--linux--windows)
for the per-platform details.

Open `http://127.0.0.1:8765/healthz` and expect:

```json
{"service":"riji-agent","status":"ok"}
```

`RIJI_DATA_DIR` defaults to `~/.local/share/riji-agent`; it stores local SQLite
state outside the repository. See [docs/deployment.md](docs/deployment.md) for
indexing, startup, and recovery details.

## Default Stack: Feishu + Hermes + DeepSeek

Feishu private chat reaches riji-agent through a thin Hermes-side bridge:

```text
Feishu private chat -> Hermes -> riji-agent /hermes/messages -> local tools -> DeepSeek
```

The bridge forwards message text and identity metadata to riji-agent over
loopback HTTP. It does not read the journal vault, SQLite databases, local index,
or model keys. Inside riji-agent, Feishu payloads are normalized into a neutral
IM message contract so future adapters can reuse the same gateway path.

The default Feishu Bot avatar lives at
`assets/integrations/feishu/riji-bot-avatar.png`.

```bash
uv run riji-agent hermes-bridge install
uv run riji-agent hermes-bridge status
```

Then restart `hermes gateway`. Configuration details live in
[docs/hermes-integration.md](docs/hermes-integration.md).

## Configuration And Safety

- `.env`, SQLite files, audit logs, `data/`, and accidental local journal copies
  are ignored by Git.
- `RIJI_JOURNAL_ROOT` must point to an existing journal directory.
- `RIJI_DATA_DIR` and optional `RIJI_DATABASE_PATH` must be outside the journal
  directory.
- `RIJI_IM_PROVIDER=feishu` selects the default Feishu IM adapter.
- `RIJI_AGENT_RUNTIME=hermes` selects the default Hermes agent runtime.
- `RIJI_MODEL_PROVIDER=deepseek` selects the default DeepSeek model adapter; set
  it to `openai` to use any OpenAI-compatible endpoint via the `RIJI_MODEL_*`
  variables instead.
- `RIJI_ALLOWED_FEISHU_USER_IDS` is a comma-separated Feishu open ID allowlist;
  group chats are denied by design.
- The service binds to `127.0.0.1`. Use Feishu/Hermes or a private network proxy
  for remote access; do not expose this port directly to the public internet.

## Development

```bash
uv run pytest
```

Smoke tests cover the main deployment path without reading real `.env`, a real
journal vault, or a real API key:

```bash
uv run pytest -m smoke
uv run pytest -m "not smoke"
uv run pytest
```
