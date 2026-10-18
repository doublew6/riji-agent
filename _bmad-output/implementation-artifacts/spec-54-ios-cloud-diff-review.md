---
title: 'Review diary conflicts as readable differences'
type: 'enhancement'
created: '2026-09-19'
status: 'implemented'
---

## Intent

The user cannot practically compare complete raw Markdown versions on a phone. The default recovery view must show only differences with section labels, readable formatting and limited optional context. Users can keep a fragment, take the other fragment, or edit only that fragment. Retained proposals and final confirmation must remain safe and recoverable.

## Implementation boundaries

- Compare exact UTF-8 line bytes locally; keep line endings, Unicode spelling, properties and every unselected source byte. Bounded LCS with patience anchors and explicit coarse fallback avoids unbounded work.
- Use the existing native Markdown display projection, including table grids. Extra table headers are presentation-only. Full source/export and whole-document editing remain optional.
- Bind a fragment replacement to the review ID and expected working draft. Persist through the existing cloud draft path; changing comparison targets must not discard adopted or hand-written content. Final confirmation previews actual source-to-result differences and reuses verified backups and source/version checks.
- No backend, Air, real diary resolution, cleanup, staging, commit or publication. Preserve the dirty working tree and previous staged specification.

## Verification

- Core: additions, deletions, empty input, duplicate lines, moves, CRLF, Unicode normalization, table rows, properties, multiple choices, manual fragments, stale drafts and bounded large inputs.
- State: choice persistence, no source write until confirmation, exact cold reopen and stale review/draft rejection.
- Native: readable table fragment, intrinsic height, read-only display, exact underlying bytes.
- UI: differences as the default, mixed per-fragment choices, cold recovery, final difference preview, exact confirmed result; retain whole-source/export and full-merge fallback journeys.
- Build and install the verified signed app over the existing phone app if connected; back up and compare local app data before launch. Do not infer actual iCloud conflict resolution from synthetic tests or installation.

## Result

Implemented and verified: 530 Swift package tests, 59 native tests, two final UI journeys and 77 runner checks passed. The signed iPhone build matches the unchanged 89 source inputs from all final iOS runs. Read-only inspection of the existing diary found changed regions alongside identical lines, with source bytes and system versions unchanged. The phone was subsequently reconnected and the signed app was installed with all original data files byte-identical before launch. Launch was blocked by the device lock screen; the user must unlock and open the app. No real conflict version has been chosen. See `docs/testing/ios-cloud-diff-2026-09-19.md` for evidence and manual acceptance.
