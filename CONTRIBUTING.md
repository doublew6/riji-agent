# Contributing to riji-agent

Thanks for your interest. riji-agent is a local-first journal agent gateway; the
overriding goal is to keep the journal vault, local state, and write permissions
on the user's machine while exposing only bounded, auditable tools. Please keep
that boundary intact in every change.

Read [`PROJECT_GUIDE.md`](PROJECT_GUIDE.md) first — it is the authoritative rule
source for architecture boundaries, the draft-write flow, and language/style.

## Development setup

Requires Python 3.9+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
uv run pytest
```

No uv? Use Docker (the host interpreter may be too old to run the test suite):

```bash
docker run --rm -v "$(pwd)":/app -w /app python:3.11 \
  bash -c "pip install -e '.[dev]' -q && python -m pytest"
```

Useful test selections:

```bash
python scripts/privacy_scan.py --tracked  # public-tree privacy check
uv run pytest -m smoke        # fast end-to-end check of the main path
uv run pytest -m "not smoke"  # the rest of the unit tests
```

## Branching and commits

- One issue per branch, named `issue/<number>-<short-slug>`.
- Keep commits small and focused; the commit message states the issue scope.
- Don't bundle unrelated refactors. If you find unrelated local changes, leave
  them; never reset or force-overwrite another contributor's work.

## Privacy scan before commit

Run the staged-file privacy scan before every commit:

```bash
python scripts/privacy_scan.py --staged
```

To make Git run it automatically, install the local pre-commit hook:

```bash
ln -sf ../../scripts/pre-commit .git/hooks/pre-commit
chmod +x scripts/pre-commit
```

The hook and CI share the same scanner. The hook checks staged files so commits
fail early; CI checks the tracked tree so pull requests cannot merge with
private paths, secrets, local databases, or real incident details in source.

## Before opening a PR

- Run `python scripts/privacy_scan.py --tracked`.
- Run the most relevant tests, then the full suite; report the result in the PR.
- Add or update tests. Permission, write, idempotency, and tool-call paths
  **must** test the failure case, not just the happy path.
- Update docs, tool schemas, and `.env.example` when behavior or config changes.
- Link the PR to its issue.

## Hard boundaries (never regress)

- Only riji-agent may read/write the vault, SQLite, drafts, and audit data.
- Never send the full vault, raw Markdown files, SQLite databases, API keys,
  credentials, or absolute filesystem paths to any cloud service.
- Never log secrets, credentials, journal bodies, or absolute paths. Outward
  errors must stay sanitized (see `ConfigurationError`).
- The model reaches the journal only through registered, bounded tools — never
  arbitrary filesystem, shell, or network access.
- Notes marked `private: true` must never be returned to a model.

## Security

Please report vulnerabilities privately as described in
[`SECURITY.md`](SECURITY.md), not in public issues.
