---
title: 'Native diary workflows and Mac manual acceptance'
type: feature
created: '2026-09-13'
status: done
route: dispatch
baseline_commit: '9f3b3d0751732da1a4116fe77a7acf88bfb208cc'
review_loop_iteration: 0
context:
  - '{project-root}/AGENTS.md'
  - '{project-root}/PROJECT_GUIDE.md'
  - '{project-root}/docs/PRD.md'
  - '{project-root}/docs/architecture/mvp-architecture.md'
  - '{project-root}/ios/README.md'
---

<frozen-after-approval reason="user authorized continuing the approved PRD and strengthening manual and automated testing">

## Intent

**Problem:** The native app currently supports only durable daily capture. The user needs the original template, review and goal workflows, plus a practical way to test them on this Mac.

**Approach:** Implement a complete offline daily-workflow increment: import and select templates, explicit blank-day creation, local search, versioned North Star definitions, daily MIT decisions and feedback, and editable source-linked weekly/monthly reviews. Supply a double-click Simulator launcher and a manual checklist. This is one increment in the remaining PRD implementation, not a redefinition of full release scope.

## Boundaries & Constraints

**Always:** Diary opens first; native simple interfaces; local Markdown is durable truth; source bytes, account-free local use, fixed vault timezone and existing drafts survive upgrades. All writes require an explicit user action, atomic persistence and stable retry identity. One confirmed current MIT per day; replacement and feedback retain history. Review sources have IDs/hashes and current-period/missing-date information. Unknown values are missing, never fabricated zeroes. Templates retain raw content and version; only known date/title substitutions execute. Unknown macros remain visible with warnings and are never evaluated. Selected local template files are copied only after preview/confirmation. Use synthetic fixtures only, clearly named, in an isolated stable namespace.

**Never:** No production backend changes, Air deployment, real-vault migration, API keys, fake AI generation, blanket overwrite of daily bodies, or silent template conversion. General daily-body editing remains an unconfirmed PRD option. Real template migration waits for the user's file path; generic import does not. AI synthesis will use the separate mobile-service increment; provide truthful manual review/MIT entry now, without disabled misleading AI buttons. Do not spawn further implementation agents or apply build skills recursively.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Template import | Markdown with frontmatter, supported and unknown macros | Preview raw source; confirmed version retains bytes; known variables instantiate; warning lists unsupported syntax | Invalid/oversize input rejected without replacing selection |
| Template-bound draft | User changes selected template after a pending draft exists | Old draft keeps original effective template and anchors | Missing/ambiguous target blocks save and retains text |
| Blank day | Confirm selected date/template twice or after existing diary | One exact template file; no overwrite, no fabricated entry | Persistence failure allows stable retry |
| Search | Daily/weekly/monthly local files, drafts, query | Saved matching files with type/date/snippet open correct target; drafts not exposed as saved | Bounded scan, no arbitrary paths |
| North Star | Multiple definitions/units/targets and later edit | Immutable versions; missing measurements remain missing | Invalid definition never replaces valid version |
| MIT | Adopt, relaunch, retry, replace, provide partial/completed/not-done feedback | One current MIT/day; dated Markdown record and immutable history; retry creates no duplicate | Partial write recovers same operation; failures retain input |
| Review period | ISO boundary week, leap-month, current period | Correct fixed-zone dates; daily sources included once; missing dates visible | No source data produces honest empty/manual state |
| Review draft | Edit, relaunch, confirm, retry, regenerate | Draft and source hashes persist; Weekly/Monthly destination; revisions retained | Source or target changed rejects stale draft without losing edits |
| Manual acceptance | Repeat launcher in normal/test mode | Builds/opens dedicated Simulator, preserves data and stable test namespace | No erase, no Air, fail clearly on missing Xcode/runtime |
</frozen-after-approval>

## Code Map

- `ios/RijiCore/LocalJournalStore.swift`: actor, atomic daily transaction/receipt recovery; do not weaken idempotency.
- `ios/RijiCore/JournalModels.swift`, `DailyTemplate.swift`, `MarkdownSectionAppender.swift`: byte-hashed drafts, fixed dates, literal Markdown headings. Existing unsupported-template rejection needs a separate safe import representation.
- `ios/Riji/DiaryViewModel.swift`: serialized persistence, captured export date, conflict reload, DEBUG UUID namespace. Extend with testable models instead of rebuilding old drafts from the newly selected template.
- `ios/Riji/TodayDiaryView.swift`: first screen and fragile-but-tested scroll preservation. Keep geometry snapshot/source-byte matching and existing UI assertions.
- `ios/Package.swift`, `ios/Riji.xcodeproj/project.pbxproj`: shared Core/State test targets and explicit project file membership.
- `ios/RijiCoreTests`, `ios/RijiStateTests`, `ios/RijiUITests`: existing 40 Swift and 5 actual Simulator UI tests must remain green.
- `scripts/`, `ios/README.md`: add dedicated Mac launch/run instructions; existing Simulator name is `Riji Development iPhone`, bundle `com.riji.local`.

## Tasks & Acceptance

**Execution:**
- [x] `ios/RijiCore/` -- implement template library, blank-day creation, saved-file search, period sources/review store, North Star/MIT persistence and recovery.
- [x] `ios/Riji/` -- expose native import, selection, date creation, search, goal/MIT and period-review flows from the diary; editable preview and explicit confirmation; persistence errors retain visible inputs.
- [x] `ios/Package.swift`, `ios/Riji.xcodeproj/` -- register all source/tests, keep production and test paths compiling.
- [x] `ios/*Tests/` -- cover every matrix row, meaningful storage failures/retries/stale revisions, and native UI navigation through template, MIT and weekly review.
- [x] `Run Riji Simulator.command`, `scripts/run_ios_simulator.sh`, `ios/README.md`, `docs/testing/ios-manual-acceptance.md` -- double-click normal start and explicit isolated sample mode, synthetic template fixture, exact manual steps and expected filesystem results.

**Acceptance Criteria:**
- A user can launch on the Mac, start on today's diary, import a template, create a date, record text, search it, maintain goals, adopt/replace an MIT, log feedback, edit and confirm a week/month review, close/reopen and observe durable results without login.
- Repeated launcher runs preserve the user's manual test records. Existing daily capture, export, scroll position, day-change recovery and failure behavior remain verified.
- Documentation identifies these as real local functions and names AI synthesis/account/sync/audio as subsequent work, without claiming unexecuted Air or device validation.

## Implementation Notes

- The user explicitly authorized continued PRD implementation and stronger testing on 2026-09-13; no additional local implementation approval is needed. This increment isolates changes to native code and development aids. A pending template-path question affects actual legacy migration only.
- Sanctioned hedging: if a supporting type or file split differs from this map, choose the smallest implementation preserving all frozen behaviors and record it here.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Evidence and route |
|---|---|---|
| Blind 1: orphan template import | high | `confirmImport` holds only a volatile ID, while listing never recovers persisted template transactions; a new-ID import is blocked by that transaction. Patch the existing recovery entry path and test relaunch. |
| Blind 2: optional goal fields | high | Both optional fields are interpolated into Markdown without the required-field marker/size checks; the isolated probe saved a marker and then goal reads threw storage. Patch identical validation. |
| Blind 3: implicit feedback | medium | The untouched picker has no field value; `previewMIT` supplies partial and Core accepts it. Patch the default to missing and require explicit selection. |
| Blind 4: stale MIT sections | medium | Template selection updates WorkflowViewModel, but the picker uses the unchanged diary snapshot until an explicit diary navigation. Patch MIT entry to refresh the actual day before presenting sections. |
| Blind 5: wrong saved template label | low | `load` uses the selected global template even for already saved Markdown with no draft provenance. Patch to a neutral saved-diary label; do not invent a source version. |
| Blind 6: ineffective MIT refresh | medium | Refresh discards only the candidate, retaining stale `goals`/`mit` and the selected superseded goal ID. Patch dependency reload and require a new valid goal selection; preserve typed text. |
| Blind 7: oversized search abort | medium | `readSavedFile(maximumBytes:)` throws scanLimit before matches can be returned. Patch partial-results handling for that demonstrated size boundary and test other files remain searchable. |
| Blind 8: kind-prefix starvation | medium | Candidates are ordered by IDs beginning weekly/monthly/daily; the 100-result or byte limit can exclude newer diary files. Patch chronological ordering and make actual bounded coverage explicit; remove the misleading query-narrowing advice. No unbounded-search promise or new public API is needed. |
| Blind 9: unavailable existing Simulator | high | The `available` inventory filter hides the named device and reaches create, so a different vault is opened. Patch all-device discovery and an unavailable-runtime failure; never replace that device. |
| Blind 10: incompatible Simulator pair | medium | Runtime and device types are chosen independently. Actual installed `types[-1]` is iPhone 6s Plus with max iOS 15.255.255, incompatible with selected iOS 26.5; the reported newer-minimum case is the same missing compatibility check. Patch both min/max bounds and test. |
| Edge 1: optional provenance | high | Independently reproduced by the isolated optional-measurement probe; same required-field validation omission as Blind 2. Patch. |
| Edge 2: implicit feedback | medium | The isolated State probe confirmed partial with no selection. Same root cause as Blind 3. Patch. |
| Edge 3: failed review autosave loses edits | high | Isolated State probe observed visible text after failed draft write; `openReview` then cleared the error and loaded older disk text. Patch persistence flushing/retry so a failed flush cannot replace visible review/form state, and add failure/reload tests. |
| Edge 4: duplicate template IDs | medium | The isolated probe imported initial bytes and returned two entries with one ID. Patch content-identity deduplication while retaining imported bytes. |
| Edge 5: unavailable Simulator | high | Same verified filtered-discovery path as Blind 9. Patch. |
| Edge 6: future report mislabeled | low | Future dates are reachable through the unrestricted DatePicker; `isCurrentPeriod == false` is always formatted as historical. Patch the three-way phase label. |
| Verification 1: cross-transaction tests | medium | Pre-verified gap: the new ordinary-commit guard can be removed without existing same-workflow tests noticing. Patch with both-direction diary/MIT pending-operation tests and exact recovery assertions. |
| Verification 2: goal edit State coverage | medium | Pre-verified gap: Core tests supply previousID directly, while State only tests invalid forms. Patch the real editGoal/preview/confirm/reload path with two metrics and old-byte assertions. |
| Root 1: navigation load dropped while busy | high | Directory navigation shares one WorkflowViewModel. System Back remains available while a Form is disabled; a new destination task calls loadMIT/loadGoals, but run returns immediately while the old async action is busy, so the destination never loads and may retain the previous form name and fields. Patch serialization of pending page loads without dropping them, respecting task cancellation, and add a controlled in-flight action/next-load State regression. |
| Verification other 1: implicit feedback | medium | Checked picker and preview default, independently reproduced by the State probe. Same root cause as Blind 3/Edge 2. Patch. |

All retained findings are bounded corrections to demonstrated paths or missing regression tests, add no public API, and preserve frozen product intent. No intent/spec loopback or deferred item is required. Review probes ran in an isolated temporary package: 4 tests passed while asserting the observed defects; they are evidence of reproduction, not passing product acceptance.

## Verification

Executed on the development Mac, 2026-09-13:

- 79 Swift Core/State tests passed after all review fixes; log `/tmp/riji-ios-workflow-post-review-swift.log`. Generic Simulator build also passed; log `/tmp/riji-ios-workflow-post-review-build.log`.
- 7 actual Simulator UI tests passed after the production code fixes; log `/tmp/riji-ios-workflow-post-review-ui.log`. Includes an exported synthetic file opened through Files and confirmed back into the template library. The original filename-label tap failed; selecting the document thumbnail passed, independently reproduced through the Simulator UI.
- The workflow UI test was then strengthened to enter MIT directly after template selection, without reopening the diary page; that complete MIT/relaunch/review path passed in 57.238 seconds, log `/tmp/riji-ios-workflow-direct-navigation-ui.log`.
- 9 launcher prerequisite/failure cases passed with isolated temporary tool doubles. Actual launcher also completed sample, sample again, normal, and sample return builds/launches before the discovery fixes; the pre-existing synthetic and normal diary/draft byte hashes remained unchanged. No device erase/uninstall occurred.
- The patched launcher completed another real sample build/launch; log `/tmp/riji-ios-workflow-post-review-launch.log`. The previously recorded sample and normal diary/draft byte hashes remained unchanged after this run.
- The existing Python regression suite passed: 1,934 passed and 5 skipped in 158.15 seconds; log `/tmp/riji-ios-workflow-backend-regression.log`. This does not establish mobile AI connectivity.
- Chinese manual acceptance guide is provided. Additional live manual clicks paused when the Mac locked; no physical iPhone or Air evidence is claimed.

| Matrix row | Executed passing coverage |
|---|---|
| Template import | raw-byte import, invalid/oversize input, initial-template identity deduplication, lost-ID transaction recovery and changed-source rejection, actual Files round-trip UI |
| Template-bound draft | `testSelectedTemplateDoesNotRebuildOldDraft`, `testImportedMissingOrAmbiguousAnchorBlocksAppend`, State template binding |
| Blank day | `testBlankDayIsExactTemplateAndNeverOverwrites`, workflow failure matrix |
| Search | saved-only scoped files, cross-kind chronological limit, oversized-file partial matches |
| North Star | multiple immutable metrics, missing measurements, optional-field limits/provenance rejection, real State edit/confirm/relaunch with old-byte retention |
| MIT | adoption/replacement/feedback/relaunch, explicit status, stale goal and competing-plan refresh, direct-entry snapshot, draft binding, bidirectional ordinary/MIT pending-transaction guards, retry State and native workflow UI |
| Review period | ISO/year/leap-month/current/future periods, monthly daily-once source test |
| Review draft | source/hash/relaunch/frontmatter/revisions, stale source/target/template, uncertain write State, workflow failure matrix, failed autosave reload retention and serialized/cancelled destination loads |
| Manual acceptance | launcher failure tests plus actual sample/normal/sample launches and byte preservation check |


- `swift test --package-path ios --scratch-path /tmp/riji-ios-workflow-swift` -- all Core/State tests pass.
- `xcodebuild -project ios/Riji.xcodeproj -scheme Riji -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' -derivedDataPath /tmp/riji-ios-workflow-derived CODE_SIGNING_ALLOWED=NO build` -- successful Simulator build.
- `xcodebuild -project ios/Riji.xcodeproj -scheme Riji -destination 'platform=iOS Simulator,id=<SIMULATOR_UDID>' -derivedDataPath /tmp/riji-ios-workflow-derived CODE_SIGNING_ALLOWED=NO test` -- existing plus new UI tests run and pass. Use host permissions for Xcode/Simulator.
