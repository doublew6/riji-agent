# Security Policy

## Security reports

Please report vulnerabilities privately to the repository owner before public
disclosure. Include the affected commit, reproduction steps, and whether secrets
or private journal content can be exposed.

## Secret Handling

Do not include secrets in issues, pull requests, logs, screenshots, fixtures, or
sample data. This includes API keys, Feishu/Lark credentials, Hermes shared
secrets, real journal text, local SQLite databases, and audit logs.

riji-agent errors and diagnostics should stay safe by default:

- startup configuration failures must not print secret values or absolute paths;
- `doctor` should report status, not raw credentials;
- audit records should store metadata and source IDs, not full note bodies;
- sample data must remain fictional.

## Publication guards

Run `python scripts/privacy_scan.py --staged` before a commit. It reads the actual
Git index, so an unstaged correction cannot hide a private value still staged
for publication. `--tracked` checks tracked working files; `--event-file PATH`
checks GitHub issue, PR, comment and review text as data without executing it.
Reports show categories and locations, never matched private values.

The reusable personal `privacy-publish-guard` skill and hooks add pre-commit,
commit-msg, pre-push and Codex PreToolUse checks. Installation and limits are
described in [publication-privacy.md](docs/publication-privacy.md). Personal
identifiers belong in a local private configuration, never in this repository's
scanner, fixtures or CI configuration. Previously committed sensitive material
requires a separate history review; editing the current file does not erase it.

The publication privacy workflow checks public Issue/PR text after GitHub
receives it. It is a secondary detection layer, not a pre-publication gate.
It executes only scanner code from the default branch, never an untrusted PR
head. New workflow code becomes active only after reaching the required branch.

Privacy-sensitive code changes must apply content permissions to metadata and
cached tool results as well as body text. Clients for local memory services must
explicitly bypass environment and operating-system proxies. Cover both with
synthetic regression tests; do not use real journals as test fixtures.

## Release Checklist

Run these checks before making the repository public or tagging a release:

```bash
git status --short
python scripts/privacy_scan.py --tracked
git log --all --name-only --pretty=format: | sort -u | rg '(^|/)(\\.env|.*\\.sqlite3|riji/|journals/|data/|.*\\.db$|.*\\.pem$|.*key.*|.*secret.*)' | rg -v '(^\\.env\\.example$|^examples/sample-vault/)' || true
git ls-files | rg '(__pycache__|\\.pyc$|\\.env$|\\.sqlite3$|^data/|^riji/|^journals/|\\.png$|\\.jpg$|\\.jpeg$)' | rg -v '^examples/sample-vault/' || true
uv run pytest
```

Expected results:

- only intentional source files are modified;
- no `.env`, SQLite, real vault, audit log, pycache, or personal image is
  tracked;
- `python scripts/privacy_scan.py --tracked` reports no private paths, secrets,
  local database files, or real incident details;
- `PRIVATE_DEMO_SENTINEL` appears only in the fictional private demo note and
  tests that prove it does not leave the demo;
- all tests pass.
