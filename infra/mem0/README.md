# Mem0 Self-Hosted for riji-agent

Production runs only on the Air Mac. Follow the repository `AGENTS.md` Air identity,
backup, test and service sequence before using any Compose command below. Preserve
the shared Colima runtime and the documented stuck legacy container; do not run a
full reconciliation or cleanup blindly. The development Mac is not a deployment target.

This stack is pinned to Mem0 `2.0.20` / commit
`9a7924befd7026e41e445ba809370009e5e985a6`, FastEmbed `0.8.0`, and
pgvector `0.8.6` on PostgreSQL 17. It listens on loopback only:

- API: `http://127.0.0.1:38881`
- official Dashboard: `http://127.0.0.1:38880`
- PostgreSQL: internal Docker network only

## Journal memory API extension (#40)

The API image now installs `riji_routes.py` via `patch_server.py`. Journal memory
requires this rebuilt image, not just updated Python application code. All four
routes require the existing server admin credential, held only by riji-agent:

- `POST /riji/memories/explicit`: validated content with `infer=False`, scoped operation
  ID, serialized retry lookup and a hydrated memory result.
- `GET /riji/memories/operation`: lookup of an uncertain write without embedding or LLM calls.
- `GET /riji/memories/export`: complete user-filtered export; the client requests
  100001 rows and the endpoint rejects reaching the cap instead of silently truncating.
- `DELETE /riji/memories/{id}/purge`: removes the selected vector and SQLite history,
  retaining only an operation tombstone to reject delayed replay.

The extension was checked against the published `mem0ai==2.0.20` wheel and the pinned
server source. `get_all` takes `filters={"user_id": ...}`, not a top-level user ID.
The regular SDK ADD response needs hydration via GET. Contract tests use those actual
signatures plus a real isolated SQLite history file; they do not replace a live Air
PostgreSQL/FastEmbed test. See [the acceptance record](../../docs/journal-memory-acceptance.md).

Use Memory Review for diary-derived correction and forgetting so application-level
evidence and suppression stay consistent. The upstream Dashboard is useful for
inspection but its direct edits do not implement the journal lifecycle protocol.

Preserve the existing `mem0_history` volume during rebuilds: it now also stores the
idempotency lock and forgotten-operation records. Back up the application files
`journal-memory.sqlite3` and `memory-operations.sqlite3` with their live SQLite backup
API, in addition to PostgreSQL and history backups. The portable JSON workflow and
its empty-target recovery rules are documented in [the operating guide](../../docs/journal-memory.md).

## Start

```bash
cp infra/mem0/.env.example infra/mem0/.env
# Replace every placeholder in infra/mem0/.env.
docker compose --env-file infra/mem0/.env -f infra/mem0/compose.yaml up -d --build
```

Use `ADMIN_API_KEY` as `RIJI_MEM0_API_KEY` for a single-user local install, or
create a per-user API key in the Dashboard. The API image build downloads
`BAAI/bge-small-zh-v1.5` from FastEmbed's official Google Storage fallback and
validates the ONNX file. Container startup copies that seed into the persistent
`fastembed_models` volume and loads it with Hugging Face offline mode enabled.
This keeps normal starts independent of external model registries.

The FastEmbed model emits 512-dimensional vectors. Both the embedder's
`embedding_dims` and pgvector's `embedding_model_dims` must be set to `512`;
the pgvector default of 1536 allows health checks to pass but rejects every
actual memory insert. Always validate a real capture and recall after deployment.
An existing table keeps its original dimension when configuration changes.
For an empty legacy table only, after a verified PostgreSQL backup and a
transactional empty-table check, alter `riji_memories.vector` to `vector(512)`.
Never cast a populated table or truncate memories to repair this mismatch;
populated collections need a separately reviewed re-embedding migration.

Run Compose from this directory. The stack deliberately uses no bind mounts;
database, history, and model data live in named volumes, while initialization
scripts are baked into the pinned images.

## Backup

Choose a private directory outside the Git worktree and set `umask 077` first.
PostgreSQL is the authoritative memory store; the stopped history SQLite copy
preserves the upstream per-memory history shown by Memory Review.

```bash
docker compose --env-file infra/mem0/.env -f infra/mem0/compose.yaml exec -T postgres \
  pg_dumpall -U postgres > /private/backup/riji-mem0-postgres.sql
docker compose --env-file infra/mem0/.env -f infra/mem0/compose.yaml stop dashboard mem0
docker compose --env-file infra/mem0/.env -f infra/mem0/compose.yaml \
  cp mem0:/data/history/history.db /private/backup/riji-mem0-history.db
docker compose --env-file infra/mem0/.env -f infra/mem0/compose.yaml start mem0 dashboard
```

Also back up `infra/mem0/.env` separately in an encrypted credential store. The
FastEmbed model volume is a rebuildable cache and does not need backup.

To restore, first verify both backup files, then prepare an empty PostgreSQL
volume for the same pinned stack. Start only `postgres`, pipe the SQL dump into
`psql -U postgres`, create (but do not start) the `mem0` container, copy the
history file back to `/data/history/history.db`, and finally start `mem0` and
`dashboard`. Run `riji-agent doctor` and regenerate `MEMORY.md` before switching
traffic back. Do not use `docker compose down -v` unless a verified backup exists;
`-v` permanently removes all memories, accounts, API keys, and history.
