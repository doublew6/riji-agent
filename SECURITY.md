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
