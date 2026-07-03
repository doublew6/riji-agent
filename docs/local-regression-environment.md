# Local regression environment

This project keeps a deterministic local regression environment in
`tests/test_local_regression_environment.py`.

It uses:

- a temporary journal vault and daily template
- temporary SQLite databases
- a fake calendar provider, not real Feishu
- a responder that fails if a scenario unexpectedly calls the model

The suite covers user-facing regressions that previously appeared in IM flows:

- creating a calendar event after preview and confirmation
- saving corrected journal draft content after a plain `确认保存`
- keeping journal-record requests out of the calendar flow even when the text
  contains scheduling-like words

Run it locally with:

```bash
uv run pytest tests/test_local_regression_environment.py
```

When a new IM behavior bug is found, add the failing path here first with
privacy-safe generic sample text, then implement the fix. The GitHub workflow
runs this suite explicitly on every pull request, before the full test suite.
