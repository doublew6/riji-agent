## Summary

What this PR does and why. Link the issue: `Closes #`.

## Changes

-

## Privacy / boundary checklist

- [ ] Staged privacy scan passes locally
      (`python scripts/privacy_scan.py --staged`).
- [ ] No secrets, credentials, journal bodies, or absolute paths are logged or
      returned in errors.
- [ ] Nothing new sends the full vault, raw Markdown files, or SQLite databases
      off the machine.
- [ ] The model still reaches the journal only through registered, bounded
      tools; `private: true` notes stay excluded.
- [ ] `.env.example` / docs / tool schemas updated if config or behavior changed.

## Tests

- [ ] Added or updated tests, including the failure path for permission, write,
      idempotency, or tool-call changes.
- [ ] Full suite passes locally (`uv run pytest` or the Docker equivalent).

Paste the test result summary:

```
```
