# Journal save receipt regression

A confirmed save must remain visible in the mentor's conversation history, and a
later save-status question must verify the local draft and file before claiming
that no draft exists. A formatting change between a Markdown list item and an
identical plain paragraph must not cause a false missing-content result.

This guide preserves the product behavior and synthetic regression evidence.
Private incident messages, actual diary status, file hashes, deployment times,
process IDs and backup identifiers are maintained outside the public repository.

## Changes

- Save-status questions use local draft/file verification before model routing.
- Confirmation and verification receipts, including failures, enter the same
  user/persona/chat history. Event deduplication prevents duplicate receipts and writes.
- A follow-up to a missing-draft error rechecks local state. Mentioning an earlier
  confirmation does not create a new preview.
- Repeating a confirmation after completion returns the verified receipt. A newer
  pending draft cannot borrow an older draft's success result.
- Verification accepts identical complete text as a plain paragraph or unordered
  Markdown list item inside the intended section. Different text, partial matches,
  blockquotes and other sections do not pass. Existing paragraphs are not rewritten
  merely to restore a bullet marker.
- Receipts identify the intended date and section. Missing-content responses do
  not guess which application changed the file.

Preview, explicit confirmation, ownership, expiry, atomic append, stable read-back
and idempotent recovery remain required. No background writer resurrects future
user edits or deletions.

## Recorded code validation

The focused synthetic regression first reproduced 10 failures, covering status
routing, missing history, formatting, false previews, follow-ups and repeat
confirmation. Fixtures contain no private diary content.

| Check | Result and limitation |
| --- | --- |
| Local regression | 691 passed, 2 skipped, 1 pre-existing deprecation warning|
| Unrestricted legacy directory suite | 695 passed, 12 failed, 2 skipped; image/Web tests outside the release's source tree account for the 12 failures|
| Baseline control |All 12 image/Web failures reproduced with the original code in an isolated temporary package|
| Release manifest | 688 passed, 2 skipped, 3 service-factory failures due to non-interactive PATH|
| Service-factory retest |All 10 tests passed after including the runtime executable directory in PATH, covering all 3 prior failures|

The unrestricted result is not a clean full-suite pass. Synthetic gateway tests
cover strict confirmation, receipt history, formatting and duplicate prevention;
actual transport delivery remains a separate deployment check.

## Deployment verification

Before replacing files, verify the host and process ownership, back up the exact
release manifest, and dry-run the source transfer. Test with the production Python
and established runtime executable. Keep real backup paths and rollout evidence
in private operating records.

After an authorized activation, check service state, loopback-only listening,
`/healthz`, Hermes bridge status and `doctor`. Use a user-authorized local verification
to confirm that the complete saved entry occurs exactly once in the intended
section, without rewriting it. Do not publish the entry, its hash or its timestamps.

Configuration, account authentication and memory API checks do not by themselves
prove proxy connectivity or Feishu delivery. Report each validation scope separately.
