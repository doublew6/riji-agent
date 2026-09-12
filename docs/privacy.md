# Privacy Model

riji-agent is **not a zero-egress system**. It is a local-control design for
personal journal agents: local data stays under the user's control, while the
default stack can still send bounded text to external services.

## Never Sent By riji-agent

- complete vault contents;
- raw Markdown files as uploaded files;
- local SQLite databases, including index, memory, drafts, events, and audit;
- API keys, Feishu credentials, or Hermes shared secrets;
- absolute filesystem paths and full vault directory structure (bounded source IDs may identify relative notes);
- note bodies marked `private: true`.

## May Leave The Machine

When Feishu + Hermes and a cloud model provider are enabled:

- Feishu/Lark receives the user's bot messages and bot replies.
- Hermes receives routing metadata and the local gateway response, but should
  not directly read the vault or SQLite files.
- The configured mentor provider receives the system prompt, the user question,
  and bounded journal snippets returned by local tools.
- With Mem0 auto-capture enabled, the configured memory provider receives the current ordinary user
  message in a separate extraction request. That request excludes assistant
  replies, journal snippets, control commands, journal-write instructions, and
  messages that match credential patterns.
- With explicitly enabled journal memory, the configured extraction model receives
  bounded paragraphs from selected non-private sections and bounded existing facts
  for relationship decisions. Background organization can send related stored facts
  without a new user question. See [journal memory budgets](journal-memory.md#预算与覆盖).

## Enforcement Points

- Search and timeline tools request non-private notes only.
- `read_note` requires prior same-session evidence and blocks private notes
  again before returning content.
- Tool results cap count, per-snippet length, and total text length.
- Restricted body sections are removed before deriving a title. Read tools
  revalidate permitted titles against the current source, including when an old
  index still contains a title derived from a restricted section.
- Audit stores metadata and source IDs, not full sensitive text.
- Mem0 PostgreSQL, local FastEmbed vectors, capture jobs, change logs and the
  generated `MEMORY.md` remain local. Successful jobs clear their queued plaintext.
- Journal provenance, source versions, processing state, budgets and forgetting
  fingerprints stay in a separate local SQLite file. Review deletions suppress
  re-extraction before backend cleanup and remove the selected active history text;
  they do not erase original diaries, user-held backups or provider-side requests.
- The service binds to localhost by default; do not expose it directly to the
  public internet.
- The default Mem0 HTTP client sets `trust_env=False`; local memory requests do
  not inherit environment or operating-system proxy settings. This does not
  change the separately configured cloud model transport.

## User Responsibilities

- Keep `.env`, SQLite files, audit logs, and real journal material out of Git.
- Review model and IM provider terms before connecting real personal data.
- Use `private: true` for notes that should never be returned to a model.
- Run `riji-agent doctor` after configuration changes.

## 2026-09-09：显式日记授权与三级内容权限

参见 [PRD 隐私权限与数据流增量](PRD.md#15-隐私权限与数据流增量2026-09-09v13)。Memory Review 每页顶部展示当前状态和权限入口；历史、增量、整理、召回分别授权。整篇 `memory: none/local/cloud` 和局部 `riji-memory` 标记同时约束提取与旧检索路径，单条记忆可另设权限。仅本地内容不调用云端提取，当前没有本地生成模型。

保存授权绑定当前来源、版本范围、模型接收方和预算；配置变化后重新授权。暂停停止日记提取与整理；撤回同时停止日记派生记忆的新云端使用。原生聊天与原有受限问答是独立数据流，仍受来源私密标记约束。旧请求和用户独立备份无法由此撤回。

用户于 2026-09-09 追加确认日常日记处理每 UTC 日 100000 字符，并授权本次历史初始化不设每日总量上限。初始化例外默认关闭，显式启用后先扫描、保存新预算历史授权，再固定已授权证据 ID / 版本与当前有效版本的交集；未扫描或空授权不启动，不能把空授权后出现的文件自动纳入。仅该批及以其产出记忆为种子的必要有界整理按 `initialization:UTC-day` 独立计量，不占日常额度，旧日账本不清零或追溯转移。后续授权、扫描或重试不扩充固定清单，新文件、新版本和后来补入的旧日期内容仍用日常预算。提取终态且必要整理完成后持久关闭，配置仍开启也不能重开。

用户于 2026-09-10 进一步确认：本固定批次已按失败 ID / 版本显式登记的一次整理恢复也计入初始化账本，无每日总量上限。只有配置仍启用、当前范围匹配冻结批次、原首次发送记录及未消费的恢复登记有效时才适用；不重新开放原批次，不自动再次恢复已发送、失败或终结的恢复项。此前已发送的请求保留原账本，不清零、不追溯转移。普通整理和日常增量仍受每日上限约束；恢复额度状态与暂停 / 用途授权分开展示并分别执行。

该决定取代旧每日 40000 字符上限，但保留 900 字符片段、同一来源版本累计 4000 字符、private/local/none、四种用途及派生权限。Review 显著区分日常预算和初始化模式/累计用量，初始化累计值汇总该批各 UTC 日账本。暂停、撤回和发送前校验继续有效；无初始化每日总量上限不是全库上传、无计量或模型套餐无限的授权。

## Optional Codex Provider (2026-09-09)

Mentor replies and background memory processing select providers independently.
When either uses Codex, its permitted context is sent to OpenAI's Codex/ChatGPT
service (`chatgpt.com`). This remains cloud inference even though the user pays
through a ChatGPT subscription and holds authoritative data locally. The Review
permission display and journal consent binding include each actual destination
and model; old DeepSeek consent cannot silently authorize a Codex switch.

The official `codex exec` runtime manages ChatGPT login and credential refresh.
A dedicated persistent `RIJI_CODEX_HOME` requires one initial official login and
then serves mentor and memory requests. Personal Codex credentials and global
configuration are neither copied nor linked into it, avoiding inherited AGENTS,
MCP, skills, hooks and connector capabilities.
riji-agent does not extract, copy, log or place authentication tokens in model
context. Each call uses an ephemeral context, without restoring personal Codex
tasks or other mentors' history. General file, shell, browsing and connector tools
are disabled; the local gateway alone executes approved journal tools and checks
write confirmation. An empty working directory or read-only sandbox alone is
insufficient to establish that boundary.

Ephemeral contexts do not guarantee zero provider retention. ChatGPT account data
controls apply to this login method; API retention or training promises must not
be substituted for those controls. A quota or authentication failure retains local
work and pauses or backs off; it does not silently send the payload to a fallback
provider. See [Codex setup and acceptance](codex-provider.md) for the operational
requirements and [official authentication](https://learn.chatgpt.com/docs/auth)
for the distinction between ChatGPT and API-key access.
