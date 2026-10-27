# Obsidian journal retrieval: first use

riji-agent helps people retrieve and reflect on their Obsidian or Markdown
journals. The public CLI can demonstrate retrieval and source references without
connecting a personal vault, Feishu account, or model provider.

## Install and run the offline example

Use Python 3.11+ and uv from the repository root:

```bash
uv sync --extra dev
uv run riji-agent chat --demo --question "launch planning"
```

The bundled input is the fictional
[sample vault](../../examples/sample-vault/riji). It includes a launch-planning
note and a private note used to check exclusion. The CLI searches that bundled
vault, not a user's own journal. It writes its local SQLite index to `.riji-demo/`.
Installing dependencies may use the network; running this demo does not call a
model, load model weights, read `.env`, or require API keys.

The answer starts with:

```text
Demo answer:
The fictional sample vault contains matching evidence. This local demo uses the same index path but a stub answer, so it does not read .env or call a model provider.

Sources:
```

Source lines include `[[riji/daily/2026-01-05]]` followed by matching synthetic
text. The private note's `PRIVATE_DEMO_SENTINEL` must not appear. The answer is
stubbed, so this is an integration example, not an evaluation of AI reasoning.

Validate the same path with the existing tests:

```bash
uv run pytest tests/test_demo_quickstart.py
```

## Connect a real journal deliberately

Read [privacy](../privacy.md) and [security](../../SECURITY.md), then follow
[Quick Start](../../README.en.md#quick-start). The default integration uses Feishu,
Hermes, and DeepSeek; real chat needs the configured model and credentials.
Feishu setup is separate from the offline CLI demonstration.

Authorized snippets may leave the machine for model inference. Feishu receives
messages and replies; local-first storage does not imply offline inference.
User-configured synchronization and backups can retain additional copies.

Journal changes follow draft, preview, explicit confirmation, and append. Do not
use setup instructions as permission to read a personal vault, upload notes,
enable remote reporting, or install background services.

## Capabilities and integration references

Start with the [public availability table](../../README.en.md#availability-and-development-progress).
Long-term memory and multi-agent plans have their own status and acceptance
requirements; this demo does not establish that they are released or validated.

- [Adapter and module architecture](../architecture/modules.md)
- [Feishu and Hermes setup](../hermes-integration.md)
- [Configuration template](../../.env.example)
- [Report a reproducible problem](https://github.com/doublew6/riji-agent/issues)

When reporting an issue, use fictional notes and include the command, Python
version, expected behavior, and observed behavior. Keep keys and journal text out
of public reports.
