# Publication privacy checks

The project scanner detects recognizable private data in code and GitHub text.
Detection is not a guarantee against disclosure. The optional personal guard
also runs outside this project.

| Entry point | Check |
| --- | --- |
| Git pre-commit | Actual staged blobs, including alternate indexes |
| Git commit-msg | Final commit message |
| Git pre-push | Outgoing commits, including intermediate versions |
| Codex PreToolUse | Supported literal Git/gh commands and GitHub MCP mutations |
| Safe gh wrapper | Freeze, scan and publish the exact title/body payload |
| GitHub workflow | Detect private Issue/PR/comment/review text after publication |

The personal installation lives in `~/.local/share/privacy-publish-guard` and
the skill in `~/.codex/skills/privacy-publish-guard`. Its private identifiers are
stored only in mode-0600 `~/.config/privacy-publish-guard/config.json`. Preserve
existing Git hooks and Codex hook definitions when installing. A repository's
own `core.hooksPath` overrides the global setting and needs explicit integration.

Codex hooks must be configured, trusted through the official `/hooks` interface,
and loaded in the relevant session. Installation alone is not proof of active
enforcement. The runtime does not rerun PreToolUse for `write_stdin`; prepare an
explicit body file and use the wrapper instead of an interactive publisher.
See the [official hook documentation](https://learn.chatgpt.com/docs/hooks).

Example, after installation:

```sh
python ~/.local/share/privacy-publish-guard/privacy_guard.py --require-config publish-gh --repo . -- issue create --title 'Public summary' --body-file /tmp/public-body.md
```

The wrapper does not authorize publication or bypass GitHub authentication.
Remove private values when blocked and check again. Keep any narrow exception
for a reviewed harmless value in the private configuration; do not bypass hooks
or exempt an entire test directory. Scanner output contains finding categories
and locations, not the matched secret.

Checks cover recognizable credentials, personal paths, addresses and configured
identifiers. They cannot prove that arbitrary prose is anonymous, inspect image
pixels/EXIF, or guard every browser, API client, encoded payload or external
script. Review attachments and semantic private facts before sending. Git author
identity is separate metadata and should use the intended public identity.

When changing runtime privacy behavior, also check these relevant boundaries:

- Restricted body text must not reappear in titles, metadata, stale index results,
  logs or model prompts. Existing indexes need safe read-time revalidation.
- A loopback memory client must explicitly opt out of environmental and system
  proxies; test the selected route without sending real memory content.
- Local authoritative storage, cloud synchronization, model inference and IM
  transport are different data flows. Describe each accurately.

Historical cleanup is a separate operation with its own backup and exact scope.
A clean current tree and passing hooks do not remove old Git objects, prior
notification emails, caches, forks or clones.
