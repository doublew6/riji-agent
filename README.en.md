# riji-agent

[![test](https://github.com/doublew6/riji-agent/actions/workflows/test.yml/badge.svg)](https://github.com/doublew6/riji-agent/actions/workflows/test.yml)

[中文](README.md)

`riji-agent` is a local-first journal agent gateway for Obsidian-style Markdown
journals. It keeps the journal vault, local index, drafts, audit records, and
write permissions on the user's machine, while exposing only bounded,
auditable tools to an external agent or model runtime.

## Project Goal

The goal is simple: make AI-assisted journaling useful for long-term personal
growth without turning a private journal into cloud infrastructure.

Daily notes, weekly reviews, monthly reviews, and thematic reflection work best
when they become a steady practice: record what happened, notice what changed,
turn the insight into the next small action. AI can help make that loop more
structured and easier to keep: it can retrieve old notes, summarize a period,
draft a review, extract action items, or prepare a journal entry from a chat
message. The important boundary is that AI remains an assistant to reflection
and action, not the owner of the journal.

`riji-agent` is the local gateway for that workflow. Template and skill
repositories can define how to write, review, and summarize; this project
decides what a model is allowed to see, which tools it may call, and when a
Markdown file may actually be changed.

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

riji-agent also has a first built-in journal capability pack,
`personal-growth`. The pack is the product boundary for reusable journaling
templates, skills, and future automations derived from
`doublew6/whit-riji-skills` and the riji-related workflows in
`doublew6/codex-automations`. Pack loading is capability metadata only: journal
writes still require a draft preview or the controlled writer boundary, and
pack automations must not upload the complete vault, raw Markdown files, SQLite
databases, API keys, or webhook URLs. See
[docs/architecture/packs.md](docs/architecture/packs.md).

## Scope

`riji-agent` provides the local boundary for personal journal intelligence. It
is responsible for:

- reading an existing Markdown journal vault without copying it into the repo;
- building a local SQLite index for search, timeline, and source lookup;
- letting an agent call narrow tools such as `search_journal` and `read_note`;
- blocking private notes and capping returned snippets before anything reaches a
  cloud model;
- creating journal write drafts that require explicit user confirmation before
  any Markdown file is changed;
- recording metadata for audit without storing full sensitive text in logs.

It is not a prompt collection or a template registry. A journaling skill layer
can decide *how* to produce a daily note, weekly review, monthly review, travel
log, or reflection summary. `riji-agent` supplies the safer local execution
boundary those skills need when they touch a real journal.

## Journaling Workflow

A typical personal-growth loop looks like this:

1. Capture a daily note or chat message.
2. Let the agent retrieve only the minimum relevant journal snippets.
3. Generate a draft entry, review, or answer with source links.
4. Show the proposed change in chat.
5. Write to Markdown only after explicit confirmation.

Example:

```text
User: Record this in my journal: I finished the privacy review before launch.
Mentor: Draft ready. Preview follows... Reply "confirm save" to write it.
User: confirm save
Mentor: Saved to [[riji/daily/2026-06-30]].
```

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

### Feishu Voice Replies

By default, Feishu replies are text-only. Set:

```bash
RIJI_FEISHU_VOICE_REPLY_MODE=text_and_voice
```

to keep the text reply and also generate a local audio attachment for
Hermes/Feishu.

Available TTS providers:

- `macos_say`: zero extra dependencies and fully local, but mechanical; useful
  as the fallback provider.
- `melotts`: optional local open-source TTS that is usually more natural than
  `macos_say`. Install MeloTTS into the same virtualenv before enabling it:

```bash
uv pip install melotts
```

Then configure:

```bash
RIJI_TTS_PROVIDER=melotts
RIJI_TTS_LANGUAGE=ZH
RIJI_TTS_VOICE=ZH
RIJI_TTS_DEVICE=auto
RIJI_TTS_SPEED=1.0
```

`melotts` has a heavy dependency tree and model cache, so it is intentionally
not part of the default dependency lock. It may download or prepare model cache
assets on first use. Keep those assets outside the repository and outside the
journal vault. Cloud TTS providers are intentionally not the default; future
providers such as `edge_tts` or Azure Speech should be explicit opt-ins because
reply text leaves the local machine.

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
