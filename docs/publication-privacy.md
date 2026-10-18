# Publication privacy checks

The project scanner detects recognizable private data in code and GitHub text;
it does not guarantee prevention of disclosure. The optional personal guard also
runs outside this project.

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

## Pre-publication review

The read-only audit modes complement the existing staged and GitHub event
checks. They never stage files, rewrite history, change refs or modify hooks.

| Mode | Exact scope |
| --- | --- |
| `--all-files` | Current tracked files and non-ignored untracked files |
| `--revision COMMIT` | One frozen Git tree and its commit metadata |
| `--history --head COMMIT` | All ancestors and unique blobs reachable from that commit |
| `--report PATH` | Save a safe JSON report for any of the three audit modes |

The report separates `findings`, `manual_review` and `identity_metadata`. Text
findings include category and location, not the detected value. Images, SVGs,
unknown binaries, links and files above scanning limits remain explicit manual
review items. Author and committer identities use fingerprints instead of
printing personal names or addresses. Exit status 2 must be investigated; it
does not by itself prove a credential leak or mean a completed review passed.

Freeze each public branch and tag separately when checking existing publication.
Also review Issue/PR titles, descriptions, comments and reviews, including image
attachments; scanning Git history does not cover GitHub conversation text.
Deleted refs, external caches, private copies and past notifications are outside
that evidence boundary.

Before changing a private document, keep its original in access-restricted,
ignored local storage. Replace machine paths, real device identifiers and
incident-specific personal details with generic instructions or synthetic
examples. Do not weaken detection rules to make a release pass. Harmless test
credentials should be clearly marked example values while retaining distinctions
needed by the tests.

## Personal artwork and public builds

The [visual asset policy](../assets/PUBLICATION.md) and
[hash inventory](../assets/publication-manifest.json) separate generic public
images from the optional local personal portrait atlas. The personal image set
is ignored as a whole. Public PRD HTML references generic offline assets; local
private originals remain outside the publication candidate.

Git ignore rules do not control Xcode resource compilation. A build from the
personal working directory can still contain the personal atlas. Public app
artifacts must come from a reviewed clean source export or checkout with the
private overlay absent. Verify the export inventory and compiled `Assets.car`
before distribution. Personal app builds may continue using the local overlay.

## Runtime privacy is a separate check

Source publication does not publish the running service's local database or
vault. Conversely, local authoritative storage does not mean all processing is
offline. Authorized conversation content and permitted excerpts can reach the
configured model provider; Feishu transport and iCloud synchronization have
their own boundaries. Optional tracing requires its own review because bounded,
redacted content can still be recorded. Revoking a source blocks future use but
cannot retract copies already sent to an external service. See
[runtime privacy controls](privacy.md); a publication scan does not verify live
production configuration, disk encryption or an external provider's retention.
