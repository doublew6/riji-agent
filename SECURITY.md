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

Run these checks on the exact publication candidate before tagging a release.
The working-file audit includes non-ignored new files; a tracked-only scan does
not cover them. Create an ignored local report directory first.

```bash
git status --short
mkdir -p output/publication-audit
python scripts/privacy_scan.py --all-files --report output/publication-audit/working.json
python scripts/privacy_scan.py --history --head HEAD --report output/publication-audit/history.json
python scripts/privacy_scan.py --staged
uv run pytest
```

Expected results:

- only reviewed files are staged for publication;
- no `.env`, SQLite, real vault, audit log, pycache, or personal image is
  tracked;
- the reports have no unresolved text findings; review images, binary files,
  size-limit entries and commit identities separately, recording the reviewed
  hashes (exit status 2 can mean manual review is required);
- each public branch and tag is checked at a frozen commit when auditing existing
  publication history; `HEAD` alone does not cover other branches;
- public image hashes match the reviewed [asset inventory](assets/PUBLICATION.md);
- public iOS binaries are built from a clean source export without the optional
  personal portrait overlay, then their compiled resource catalog is inspected;
- `PRIVATE_DEMO_SENTINEL` appears only in the fictional private demo note and
  tests that prove it does not leave the demo;
- all tests pass.

Keep raw audit inputs and private backups in ignored local storage. They may
contain the very data being removed. A clean source scan is neither permission
to publish those backups nor proof that a binary built in a personal working
directory excludes ignored resources.
