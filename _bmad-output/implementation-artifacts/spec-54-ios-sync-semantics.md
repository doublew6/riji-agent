# Issue 54: ordinary sync and retained conflict reviews

## Problem and intent

The user expects Riji and Obsidian to edit the same authorized iCloud Markdown files, with normal changes saved and propagated automatically. The old label "iCloud version" incorrectly made an unresolved system conflict copy look like the current server state. Retained review records could continue blocking editing even after the system conflict had disappeared, including after an already-confirmed result was present in the source.

## Changes

- Label system alternatives as conflict copies, not a cloud latest version. Explain normal system synchronization briefly.
- Provide a prominent whole-current-diary choice. Per-fragment selection remains optional; whole-file confirmation and backups remain required while actual unresolved versions exist.
- On open or external refresh, retire an untouched retained review after checking that no system conflict remains and backing up its original evidence.
- Complete bookkeeping for an interrupted, explicitly confirmed operation only when its backed-up result exactly equals the current source and the system conflict inventory is complete and empty. Do not replay a source write or change system version flags.
- Refresh an untouched comparison from the latest available source and inventory. Preserve manual input, including text still awaiting debounced persistence.
- Retain unconfirmed manual merges, differing pending results, independent body drafts/holds, unreadable evidence and new conflicts for explicit recovery.

## Acceptance and limits

Synthetic Core/State tests cover automatic retirement, exact-result recovery, changed/new/incomplete system state, missing backups, persistence failure, manual proposals and independent body recovery. UI tests cover whole-current confirmation without individual choices and existing alternate-version/difference workflows. Ordinary external edits and further automatic saves remain covered.

The real diary inspection is read-only. Local upload metadata and equal previously confirmed bytes are evidence about current state, not an end-to-end cross-device edit test. No version is chosen by date, length or an AI judgment. No Air deployment or diary rewrite is part of this correction.

See [verification](../../docs/testing/ios-sync-semantics-2026-09-19.md).
