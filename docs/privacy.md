# Privacy Model

riji-agent is **not a zero-egress system**. It is a local-control design for
personal journal agents: local data stays under the user's control, while the
default stack can still send bounded text to external services.

## Never Sent By riji-agent

- complete vault contents;
- raw Markdown files as uploaded files;
- local SQLite databases, including index, memory, drafts, events, and audit;
- API keys, Feishu credentials, or Hermes shared secrets;
- filesystem paths and vault directory structure;
- note bodies marked `private: true`.

## May Leave The Machine

When the default Feishu + Hermes + DeepSeek stack is enabled:

- Feishu/Lark receives the user's bot messages and bot replies.
- Hermes receives routing metadata and the local gateway response, but should
  not directly read the vault or SQLite files.
- DeepSeek/default model provider receives the system prompt, the user question,
  and bounded journal snippets returned by local tools.

## Enforcement Points

- Search and timeline tools request non-private notes only.
- `read_note` requires prior same-session evidence and blocks private notes
  again before returning content.
- Tool results cap count, per-snippet length, and total text length.
- Audit stores metadata and source IDs, not full sensitive text.
- The service binds to localhost by default; do not expose it directly to the
  public internet.

## User Responsibilities

- Keep `.env`, SQLite files, audit logs, and real journal material out of Git.
- Review model and IM provider terms before connecting real personal data.
- Use `private: true` for notes that should never be returned to a model.
- Run `riji-agent doctor` after configuration changes.
