---
title: Correct bidirectional calendar-day swipes
type: bugfix
created: 2026-09-18
status: done
route: dispatch
baseline_commit: 5b87e5451e084045809b14111e57b843546df643
context:
  - PROJECT_GUIDE.md
  - docs/PRD.md
  - docs/architecture/mvp-architecture.md
---

<frozen-after-approval>
## Intent

The owner explicitly authorized implementation now: moving a finger left-to-right
opens the previous calendar day; moving right-to-left opens the next calendar day.
This supersedes the older FR-05 left-only behavior. Keep calendar dates, not nearest
existing-file navigation. Browsing a missing or future day previews the existing
template without creating a file. Do not add a today-only ceiling.

## Boundaries

Preserve the existing save/conflict/recovery gate, source-day callback validation,
per-document caret and reading position, native editing and the TextKit viewport
fix. Both directions must reject composition, selection, active editing, table
interaction and predominantly vertical or short drags. Keep the 80-point deliberate
completion threshold and bounded direction ratio. Date arithmetic uses Gregorian
UTC labels to preserve local missing-midnight/missing-day labels. Refuse unsupported
year bounds without mutating the selected day. No production data, Air services,
credentials or remote operations. Never commit or stage from the implementation
agent. Existing mixed iOS work is authorized context and must be preserved; inspect
the narrow before/after change, not every difference from Git HEAD.

## I/O and edge-case matrix

| Given | When | Then |
|---|---|---|
| September 12 reading | deliberate rightward gesture | September 11, unchanged source bytes |
| September 12 reading | deliberate leftward gesture | September 13, even if missing |
| Missing next day | navigate without entering text | original template preview; no file created |
| Year/month/leap/DST/skipped-local-day boundary | either direction | literal adjacent calendar label |
| Year 0001/9999 boundary | attempt outside supported dates | refuse and retain selected day |
| Current content cannot save or unresolved merge exists | either direction | current day, input and recovery retained |
| Editing/composition/selection/table/short/diagonal/vertical gesture | either direction | ordinary interaction; no date change |
| A navigation is busy or callback has an old source day | another swipe | no duplicate or stale navigation |
| A long document is left then revisited | opposite swipe | its saved reading/caret position restored |
</frozen-after-approval>

## Code Map

- `ios/Riji/MarkdownTextView.swift`: current `isLeftward`, `previousDayPan`, admission and completion; replace with shared typed direction while preserving table and selection guards.
- `ios/Riji/DiaryBodyEditor.swift`: replace the one-direction callback, retain viewport capture and existing style layout order.
- `ios/Riji/TodayDiaryView.swift`: route direction only when the callback source day still matches selection.
- `ios/Riji/DiaryViewModel.swift`: generalize `canNavigatePreviousDay` and adjacent-label calculation; reuse `navigate(to:)` and destination flush gates.
- `ios/RijiStateTests/BodyEditorStateTests.swift`: existing navigation/save/merge/date-label tests.
- `ios/RijiNativeTests/NativeBodyEditorTests.swift`: production gesture harness and table admission tests.
- `ios/RijiUITests/DiaryFlowTests.swift`: actual gesture/date/reading-position and failed-save journeys; update old directional expectations.
- `docs/PRD.md`, `docs/product/ios-personal-mvp-development-plan.md`, `docs/testing/ios-personal-mvp-acceptance.md`, `ios/README.md`, `docs/testing/ios-manual-acceptance.md`: current direction contract. Historical documents retain original evidence with a dated supersession note if needed.

## Tasks and acceptance

- [x] Generalize the four production files with one direction mapping and calendar arithmetic; no unrelated refactor.
- [x] Update and extend state/native tests with independent literal expected dates and both signs; matrix cases must run, not merely exist.
- [x] Update actual UI tests for rightward previous, leftward next, round trip and missing-day preview; retain long-table/scroll and failing-save coverage.
- [x] Update current requirements and Mac manual steps; write `docs/testing/ios-bidirectional-swipe-2026-09-18.md` with exact evidence and remaining physical acceptance.

Given the current editor, when reading and swiping in either direction, then the
visible date and document agree with the user's requested mapping. Given a failed
save, when the user attempts either direction, then no edits are lost and no new
day is displayed. Given the existing project, when tests/build run, then existing
diary, table, four-tab and mobile connection behavior remains valid.

## Verification

Run the Swift package tests and focused native/UI navigation matrix on the existing
`Riji Development iPhone` simulator, without erasing it. Use isolated test namespaces
already in the harness. Save logs/result bundles under the ignored
`output/ios-roadmap-2026-09-18/swipe/`; coordinate with the root before any simulator
test to avoid concurrent Xcode jobs. Root owns signed phone build/installation and
read-only before/after preservation evidence. Never claim actual phone acceptance
from Simulator results.

## Implementation Notes

User explicitly approved direct development and splitting the four tasks. Product
PRDs are separate draft-only artifacts owned by the root. No additional planning
approval is required for this correction.

Implementation completed on 2026-09-18. Swift package: 451 passed. Native suite:
40 passed, plus the refined positive/negative 80-point admission test passed.
Eight distinct UI flows passed. The existing Settings test required an updated
unactivated-service label and a precise accessibility query; both failed attempts
remain in the ignored evidence directory. See
`docs/testing/ios-bidirectional-swipe-2026-09-18.md` for exact results and pending
physical-device acceptance. No staging, commit, Air, or remote operations.

## Review Triage Log

### Independent review triage (2026-09-18)

| Finding | Verdict | Evidence and route |
|---|---|---|
| Blind 1: pre-reform Gregorian labels | low | Reproduced Foundation's 1582 cutover; the same formatter and previous-day arithmetic predate this increment. Modern diary dates and both requested directions are unaffected. Defer the pre-existing historical-calendar domain issue; changing parsing and all stored date semantics is not a swipe patch. |
| Blind 2: opposite UI gestures can cancel | medium | The changed tests asserted only after both gestures; a pair of wrong navigations could cancel. Patch with immediate date/content/recovery checks after every gesture. |
| Blind 3: first future-date edit lacks a UI journey | medium | Missing-file preview is covered, but actual swipe-to-future editing/cold reopen is not directly exercised. Patch by adding a synthetic UI flow that asserts the future target and unchanged source. |
| Blind 4: successful tab-save recovery was lost | false | Full Native testNormalBodyDestinationSaveCommitsCompositionOnceAndRetainsFailedInputForOriginalDay directly calls prepareMainDestination with failed dirty composition, restores the store, retries that same entry before any navigation, and compares committed bytes. It ran in the 40-test suite. |
| Blind 5: target read failure lacks adjacent-flow coverage | low | Existing OriginalVaultStateTests cover unavailable source and failed candidate reads, but not this exact target-read journey. The navigation/load functions are unchanged; defer this pre-existing test coverage gap with a concrete synthetic placeholder/template retry case. |
| Blind 6: no pan-cancellation lifecycle test | maybe-false | updateUIView already cancels the recognizer when day changes and the ViewModel rejects stale source days. No reachable wrong callback was demonstrated. A real mid-gesture replacement event would settle it; hypothetical impact could be medium, so defer as unverified. |
| Blind 7: exact ratio rejection untested | low | 80/24 is allowed and broad diagonals rejected, but a 0.4 mutation could pass. Patch the existing native matrix with just-over-0.3 cases and all sign combinations. |
| Blind 8: bounds test has no unsaved text | low | Bounds return before any save/mutation, but the named retention assertion only checked a clean preview. Patch the existing State test with retained input and source-bound recovery evidence. |
| Blind 9: implementation status missing latest increment | low | The current PRD links the status page which has no dated bidirectional entry. Add a small latest record linking evidence, preserve historical records. |
| Edge 1: 1582 cutover | low | Same demonstrated pre-existing formatter/calendar limitation as Blind 1; same defer entry, not a new runtime regression. |
| Verification-gap layer | false | Reviewer reported no verification gaps; no finding to patch. |

### Review patch verification

The same implementation agent applied only the routed test/document patches. Root
read the complete patch diff and the focused result evidence: State 1, Native 1,
UI 4 all passed, with the previously existing full Native/Swift evidence retained.
The new repeatable smoke runner executed the final test tree: Swift 451, Native
40 and UI 14 all passed, with zero failures or skips. Its complete discovered,
requested and executed inventories agree; evidence is retained in
`output/ios-tests/20260918T030058Z-smoke-ab439c3b/`. Production-source hashes match the successfully
signed iPhone build. Three pre-existing or unverified historical/lifecycle items
remain recorded in deferred-work; no confirmed modern-date runtime issue remains.

The signed iPhone artifact is ready under
`output/ios-roadmap-2026-09-18/iphone/Riji.app`. Physical installation and acceptance
remain pending device connection; Simulator verification does not replace them.

The current branch contains substantial pre-existing uncommitted iOS work and a
pre-staged unrelated voice specification. No broad staging or commit is permitted
by the project preservation rule; this increment remains a narrow reviewed
before/after diff and a signed local artifact until the broader iOS tree can be
published coherently. No remote push or Air mutation was performed.
