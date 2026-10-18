---
title: 'Refresh shared diary files after external edits'
type: 'bugfix'
created: '2026-09-18'
status: 'done'
route: 'dispatch'
baseline_commit: '5b87e5451e084045809b14111e57b843546df643'
review_loop_iteration: 0
context: ['PROJECT_GUIDE.md', 'docs/PRD.md', 'docs/architecture/mvp-architecture.md']
---

<frozen-after-approval reason="User explicitly requested automatic refresh after Obsidian/Mac edits and usable conflict recovery on 2026-09-18">

## Intent

**Problem:** Riji can keep a stale diary after another editor changes the shared Markdown, then report a confusing conflict on ordinary continued use.

**Approach:** Save visible input when leaving the app, refresh on return and when external changes arrive while active. Adopt the latest file automatically only when there is no divergent local input. For real competing edits, preserve both and offer clear version selection or manual merge.

## Boundaries & Constraints

**Always:** Keep coordinated byte comparison before writes, original vault identity, exact UTF-8/CRLF, protected metadata, durable draft/revision recovery and source-bound operations. Retain table editing, composition, undo, viewport, date swipe and four tabs. Unchanged refresh must not rebuild the editor, interrupt input, create files or show a permanent status bar. Revalidate source/day/revision after asynchronous reads. Test synthetic files only.

**Never:** Last-writer-wins, silent divergent merge, drop draft on read/retain failure, change AI/Air services, migrate files, treat iCloud propagation as immediate, or claim Simulator proves device sync. Do not stage/commit the mixed workspace or touch its existing staged voice spec.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
| --- | --- | --- | --- |
| Sequential editors | Clean diary; Obsidian/Mac changes file before resume or active refresh | New bytes appear automatically; next edit uses new base | Missing/download/permission errors preserve visible content and offer retry |
| No change / own save | Repeated active ticks or identical file bytes | No model reset, selection/undo loss or new history/file | No false conflict |
| Pending input | Body/table IME or typing begins during read; user leaves app | Commit/retain original-day input before background flush; stale refresh never overwrites it | Failed save retains draft and stays actionable |
| Net-zero edit | User returns exactly to saved base | External changes can be adopted without reporting competing edits | Retire obsolete buffer safely; never clear uncertain operations blindly |
| Genuine conflict | Divergent local and current file; external changes again during review | Explain both versions; explicit latest/local/manual merge revalidates and archives both | Failed/stale resolution retains input and allows refreshed review |
| Unsupported cloud state | Actual NSFileVersion conflict, placeholder or moved source | Distinguish system cloud conflict from stale editor; preserve/export available local draft and retry | Never claim normal merge resolves OS versions or force-write an unavailable file |

</frozen-after-approval>

## Code Map

- `ios/Riji/RijiApp.swift`: active-only resume and 60s tick; no background body flush. `RijiNavigationView.swift` handles only mobile backend suspension.
- `ios/Riji/DiaryViewModel.swift`: `resume` skips dirty state then calls full same-day navigation; `load` restores buffers, `updateBody` sets dirty even after net-zero undo. Reuse `flushBody`, retention and resolution, but automatic refresh must not flush/reload/reset unchanged state indiscriminately.
- `ios/Riji/DiaryBodyEditor.swift`, `MarkdownTextView.swift`, `TodayDiaryView.swift`: existing recursive `commitEditing` commits ordinary/table composition; active native input needs an admission boundary for automatic replacement. Keep new persistent table editor. Conflict sheet currently exposes raw versions/manual merge without simple selection.
- `ios/RijiCore/BodyRevisionStore.swift`: exact-source CAS, transaction/receipt ownership, source-bound revision archive and `resolveBodyConflict`. Reuse for explicit choices; isolate any no-op buffer retirement and test failure paths.
- `ios/RijiCore/OriginalVaultAccess.swift`: coordinated reads and writes; `assertAvailable` rejects actual unresolved NSFileVersion conflicts. Distinguish them, do not disable the guard.
- Existing tests: `OriginalVaultStateTests` clean directory refresh and original conflict; `BodyEditorStateTests` durable merge/failure/restart; `NativeBodyEditorTests` composition/table/viewport; `DiaryFlowTests` original source fixtures and recovery forms.

## Tasks & Acceptance

**Execution:**
- [x] `ios/Riji/DiaryViewModel.swift`, `RijiApp.swift`, native editor/view files as needed — lifecycle flush, bounded active refresh, stale-read guards and input admission; use a short foreground-only interval (about 3 seconds), coalesced single-flight reads rather than a new sync service.
- [x] `ios/RijiCore/BodyRevisionStore.swift` if needed — safely retire net-zero retained buffers while preserving archive/uncertain ownership; no schema migration.
- [x] `ios/Riji/TodayDiaryView.swift`, error presentation — clear latest/local/manual choices, contextual retry, stop offering directory reauthorization for ordinary content conflict. Show actual iCloud conflict separately with retained-draft export/retry, no unsupported promise to resolve OS versions.
- [x] Existing Core/State/native/UI test files — cover every matrix row, actual production lifecycle/refresh entry, races, exact saved bytes, cold reopen and failures. Keep tests registered in their proper targets.
- [x] `docs/PRD.md`, `docs/testing/ios-manual-acceptance.md`, new `docs/testing/ios-external-refresh-2026-09-18.md` — clarify behavior, record verified scope and remaining device checks.

**Acceptance Criteria:**
- Given a clean original diary, when another editor changes it, then return/active refresh loads it and subsequent Riji edits save without false conflict.
- Given two divergent edits, when choosing a version or merging, then the reviewed source is revalidated and both prior contents remain recoverable.
- Given in-flight input/read/navigation or storage failure, when refresh happens, then no input moves to another day or disappears.

## Implementation Notes

No unresolved product choice or irreversible action. This continues authorized Issue #54 in the existing mixed workspace; no additional approval or publication. Before snapshots: `output/ios-external-refresh-2026-09-18/before/`. Implementation agent owns production and test changes; parent owns docs, independent review, final verification/build. No Xcode execution until parent coordinates it. Target only Riji Automated Tests simulator (`<SIMULATOR_UDID>`); never real/private files, manual simulator, device installs or Air. Focused Swift tests may use a unique scratch path. Report any required change to intent instead of broadening it silently.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Evidence and route |
| --- | --- | --- |
| Blind 1: readable divergent source stays unavailable | medium | Verified early return precedes resetting sourceUnavailable; recovery notice therefore never exposes comparison. Patch by clearing obsolete source state after a verified read. |
| Blind 2: failed version choice overwrites hand merge | high | New choices call resolveBodyConflict, which replaces durable merge text before source revalidation; stale-source repro loses the unique manual text. Patch explicit-choice failure retention without changing transaction ownership. |
| Blind 3: keep-local cannot replace changed protected attributes | medium | Core correctly rejects changed frontmatter; the new choice does not explain that restriction beforehand. Patch choice availability and explanation; preserve metadata checks and latest/manual alternatives. |
| Blind 4: restored moved draft lacks identity classification | medium | load detects retainedIdentityChanged but resets the new classifier to nil, hiding reauthorization. Patch the existing classification branch. |
| Blind 5: recovered source leaves navigation error | medium | show stores a source error in errorMessage; successful refresh clears only bodyError. Patch source-owned error clearing, preserving unrelated errors. |
| Blind 6: clean focused editor never refreshes | medium | Native admission excludes isFirstResponder even with no pending input; every foreground tick is rejected. Patch admission while preserving marked/changed native input and source-bound callbacks. |
| Blind 7: busy resume skips check before unlocking | medium | resume/admitsOriginalRefresh can return during a write; existing next-tick polling permits stale editing first. Patch lifecycle sequencing around the demonstrated active write/read states. |
| Blind 8: repeated scans may be expensive | maybe-false | paths/open/sourceID do rescan; bounded enumeration exists, but no measured stall or battery regression establishes user harm. Defer unverified medium performance concern pending synthetic large-vault timing and device energy measurement; do not weaken source identity checks. |
| Blind 9: held coordinator can fence indefinitely | medium | resume unlocks only after awaited read, with no deadline; a held read reproduces indefinite input gating. Patch bounded resume wait with retained text/error/retry and existing write checks. |
| Blind 10: actual Home/activate wiring not tested | medium | Existing native lifecycle calls bypass RijiApp; UI journeys remain active. Patch one real isolated Home/activate journey; native composition cases remain separate. |
| Edge 1: focused clean input skips updates | medium | Independently confirmed same admission defect as Blind 6; patch shared root cause. |
| Edge 2: moved retained source misclassified | medium | Independently confirmed same load branch as Blind 4; patch shared root cause. |
| Verification 1: net-zero save path untested | medium | Accepted filed mutation evidence: refresh/helper tests do not exercise persistBody net-zero via background flush. Patch existing-file and missing-day regressions. |
| Verification 2: failed draft retention export untested | medium | Accepted filed evidence: existing export tests fail source reads, not local buffer retention. Patch injected bodyDraft failure and exact recovery export assertions. |

All three independent layers completed before triage. Parent Simulator run also exposed two test navigation/setup failures: table lifecycle fixture omitted production global resignation; relocated refresh control was outside lazy Form snapshot. Keep assertions and repair fixture/navigation, then re-run.

## Verification

Run targeted tests during implementation. Parent runs `python3 scripts/test_ios.py --suite core`, focused native/UI flows with exact executed inventory/zero skips and screenshots, then a signed iPhone build. Physical iCloud arrival and Chinese keyboard acceptance remain separate; do not uninstall or erase user data.

Completed on 2026-09-19: full Swift package 489/489, focused Native 44/44 plus UI 5/5, runner Python 77/77. Final test runs verified unchanged source inputs and exact execution inventories, with no skips. Signed iPhone build verified and overlaid onto the registered device; all original App data files match their pre-install backup. Actual iCloud propagation/Obsidian round trip and physical Chinese keyboard acceptance remain manual checks. Review patches are complete; large-vault scan profiling remains an explicitly unverified deferred measurement. No staging, commit, push, Air deployment, simulator erase or app uninstall.
