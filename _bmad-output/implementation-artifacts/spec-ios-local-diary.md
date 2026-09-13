---
title: 'Native iOS daily diary with durable local capture'
type: 'feature'
created: '2026-09-12'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '767b2b8c930f4656047449bb57ac35384550537d'
context:
  - '{project-root}/AGENTS.md'
  - '{project-root}/PROJECT_GUIDE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The reviewed iOS design has no native application. A user needs to open today's diary and leave a short record without logging in, configuring AI or navigating a capture wizard.

**Approach:** Build an independent SwiftUI iPhone client under `ios/`. Display today's template immediately, keep typed drafts on the phone, and append the exact visible text to its selected section after one explicit save. Provide date navigation, a diary directory and Markdown export. This is the first local slice, not the complete first release: original templates, weekly/monthly reports, north-star/MIT, voice, mentors, memory and managed AI remain required in the product plan.

## Boundaries & Constraints

**Always:** Use only the app's own sandbox. Separate user Markdown from internal drafts, transactions and receipts. Display “产品初始模板” for the bundled starting template; do not claim that personal templates have migrated. Retain the draft's day, section and source version across interruption. Show success only after durable persistence. Preserve unrelated Markdown bytes. Keep production Python code, services, configuration and permissions unchanged.

**Never:** Contact a backend, upload data, read the existing vault, request credentials, run template scripts, create fake AI results, overwrite an existing diary body, or offer a nonfunctional online action. Do not equate local saving with service synchronization or memory creation. No iCloud integration, account migration, App Store publication or real-device installation in this slice.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
| --- | --- | --- | --- |
| First opening | No diary or account | Today appears from the labeled initial template | Opening alone need not create a file |
| Local draft | Typed text, explicit day/section | Persist draft and effective template; keep unconfirmed text outside diary body | Retain visible text and report persistence failure |
| Confirm append | Visible text and unchanged source | Append exactly once to the selected heading | Missing/duplicate heading or changed source preserves draft |
| Interrupted commit | Durable operation with before/after hashes | Recover using the same operation identity | Unknown file state stops automatic writing |
| Duplicate retry | Same operation and payload | Return its existing result | Same identifier with changed payload is rejected |
| Repeated independent text | Different operation identifiers | Both deliberate records may be saved | Do not deduplicate by text alone |
| Midnight/resume | An earlier day's open draft | Keep its original date; allow explicit return to today | Never silently move text to another day |
| Write/export failure | Unavailable storage or destination | Keep existing file and draft intact | Show a safe actionable error without private paths |

</frozen-after-approval>

## Code Map

- `docs/PRD.md` FR-01/05/08/09/14/15/37/38 and the existing iOS wireframe establish the diary-first interaction. Full-release features stay documented rather than being reclassified as optional.
- `docs/product/ios-design-2026-09-12/addendum.md` separates phone persistence, server confirmation and model processing. This slice implements only phone persistence.
- `src/riji_agent/drafts/template.py` supplies heading-append semantics as a reference. Do not import Python, copy partial-heading matching, or reuse text-based duplicate detection.
- `ios/Riji/` will own SwiftUI presentation and a main-actor view model. `ios/RijiCore/` will own Foundation-based storage logic, independently testable with synthetic files.
- Current base: `767b2b8c930f4656047449bb57ac35384550537d`, branch `codex/ios-local-diary`. Existing backend is separately deployed and verified on Air.

## Tasks & Acceptance

**Execution:**

- [x] `ios/Riji.xcodeproj/project.pbxproj`, `ios/Riji.xcodeproj/xcshareddata/xcschemes/Riji.xcscheme`, `ios/Package.swift` — add a native app, a shared scheme and a dependency-free core test package; build with the installed SDK and iOS 17 deployment target.
- [x] `ios/RijiCore/JournalModels.swift`, `ios/RijiCore/DailyTemplate.swift` — represent calendar days, fixed journal timezone, drafts, source hashes and operations; instantiate only supported date/title placeholders.
- [x] `ios/RijiCore/MarkdownSectionAppender.swift` — locate exact heading boundaries outside fenced code, preserve existing content and reject ambiguous destinations.
- [x] `ios/RijiCore/LocalJournalStore.swift` — serialize storage operations, persist drafts and transactions before mutation, atomically replace Markdown, verify it and record receipts. Inject file-operation failures for testing. Keep each independent confirmation's operation identity stable.
- [x] `ios/Riji/RijiApp.swift`, `ios/Riji/DiaryViewModel.swift`, `ios/Riji/TodayDiaryView.swift`, `ios/Riji/DiaryDirectoryView.swift` — implement immediate diary reading, inline capture and one save action, retained drafts, date selection and file export. Preserve the selected day and reading position after saving.
- [x] `ios/RijiCoreTests/LocalJournalStoreTests.swift`, `ios/RijiUITests/DiaryFlowTests.swift` — cover the matrix, failures before/after file replacement, receipt/draft cleanup failure and application relaunch using synthetic data only.
- [x] `ios/README.md`, `ios/.gitignore` — document build/test commands, file locations and actual capability limits; exclude build products and local simulator data.

**Acceptance Criteria:**

- Given a fresh install offline, when the app opens, then today's diary is usable without a welcome, account or configuration screen.
- Given a saved local entry, when the user exports its diary, then a standard Markdown file contains the confirmed text and no internal recovery state.
- Given the existing backend checkout, when native tests and app builds run, then they require no Python service or production data and leave backend files unchanged.
- Given the installed development tools, when verification finishes, then the report distinguishes successful compilation, core tests, simulator interaction and any unverified real-device behavior.

## Implementation Notes

- User approved the specification and explicitly requested continuing app code. Public Issue creation was separately blocked by automatic publication approval; keep its exact local scope in `docs/product/ios-design-2026-09-12/ios-first-slice-issue.md` as the tracking record while publication approval is pending. Continue the authorized local implementation; perform no remote publication.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Evidence and route |
| --- | --- | --- |
| Blind 1: canonical Unicode equality | medium | `JournalDraft.updating` uses canonical String equality; canonically equal but byte-distinct visible input retains old bytes. Patch raw-byte comparison, including stored payload binding. |
| Blind 2: appended syntax hides/duplicates headings | medium | Appending an open fence before later template headings removes them from the next parse; repeated existing headings make the target ambiguous. Patch validation of existing anchors in the resulting bytes before persistence, using the existing safe-error path. |
| Blind 3: comment-contained headings | medium | The parser treats `## Other` inside a multiline HTML comment as the insertion boundary, placing saved text inside the comment. Patch comment context in the existing parser. |
| Blind 4: foreground midnight | medium | `resume` only runs after scene activation; an uninterrupted foreground session retains stale `today`. Patch date-change notification handling without moving the open draft. |
| Blind 5: unknown-source recovery locks editing | low | The conservative stop is real after an unknown file mutation, but the app has no external-vault, sharing, body editor or second writer. Ordinary app calls cannot produce this mutation; retained bytes remain visible and protected. Reject a new conflict-resolution flow for this rare corruption case: it adds recovery choices beyond a direct correction. |
| Blind 6: source-review/navigation interleaving | medium | The main actor can reenter while `reviewCurrentSource` awaits; it has no busy guard for its own operation. Patch the existing busy serialization and await pending draft work. |
| Blind 7: obsolete queued writes | low | Every queued revision currently reaches the filesystem even when newer input is ready, delaying save/navigation. Patch by skipping superseded queued revisions before their write; retain the latest persistence barrier. |
| Blind 8: interrupted temporary files | low | Process death before rename bypasses defer and can retain a sandbox-private temporary file. This does not mutate the confirmed journal or recovery state; ordinary completed writes remove it. Reject new startup cleanup/process-kill infrastructure for the negligible, uncommon orphan-space issue in this slice. |
| Blind 9: empty draft directory entry | low | The directory uses filename presence, so clearing input leaves a misleading draft-only row. Patch directory filtering based on nonempty retained text or a real pending operation. |
| Blind 10: reading-position verification | medium | The current test measures only the first screen, so a reset-to-top regression would pass. Patch with a long saved diary, actual scroll and anchored position comparison. |
| Edge 1: open fence | medium | Same concrete output-anchor loss as Blind 2. Group with its parser validation patch. |
| Edge 2: foreground midnight | medium | Same missing date-change trigger as Blind 4. Group with its notification patch. |
| Edge 3: skipped timezone date | low | The public day type accepts a date that may not exist in a timezone. The actual date-picker caller receives only dates generated in the persisted journal timezone; the app has no import or timezone-change path to create the Apia skipped date. Reject API branching for this unreachable-in-app, historical date case. |
| Edge 4: source-review navigation race | medium | Same await reentrancy as Blind 6. Group with its busy-state patch. |
| Edge 5: export date/filename race | medium | Export reads the date before awaiting but the filename is read again from mutable selection in the view. Patch capture of the date together with export data. |
| Verification 1: failure UI state | medium | Pre-verified gap: store tests do not instantiate ViewModel, and all UI saves succeed. Patch targeted ViewModel tests of retained visible text/date/section, no success on failure, pending state and once-only retry. |
| Verification 2: actual exported bytes | medium | Pre-verified gap: the UI test checks completion but not the actual destination bytes; core output assertions do not pass through FileDocument. Patch destination-byte evidence and exclusion of a retained unconfirmed draft. |
| Verification 3: scrolled reading position | medium | Pre-verified gap: no existing test starts from a scrolled long diary. Group with Blind 10 and exercise an insertion before the visible anchor. |

All surviving groups have direct implementation/test corrections, add no product recovery flow or backend surface, and guard the demonstrated states above. Route: patch. No intent or frozen-matrix change is required.

## Design Notes

No unresolved product decision blocks this local slice. Initial timezone follows the device at first use and is then persisted; changing timezone is outside this slice. Missing personal templates and future online identity are separate migration work. The change adds a new client and persistence boundary, so it uses the full implementation/review route. It has no production migration, irreversible data operation or public deployment.

## Verification

**Commands:**

- `swift test --package-path ios` — core matrix and failure-path tests pass.
- `xcodebuild -project ios/Riji.xcodeproj -scheme Riji -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build` — native simulator build succeeds.
- `xcodebuild -project ios/Riji.xcodeproj -scheme Riji -destination 'platform=iOS Simulator,name=Riji Development iPhone' CODE_SIGNING_ALLOWED=NO test` — run UI acceptance on the explicitly created simulator when its runtime is installed.

Inspect first launch, draft recovery, same-page saving, midnight behavior and Markdown export; identify unverified device checks.


### Implementation acceptance audit — 2026-09-13

The root reviewer read the complete baseline diff including new files. All seven execution tasks and four acceptance criteria are satisfied for the approved local slice. Backend source and configuration have no diff from the baseline.

| Frozen matrix row | Executed passing evidence |
| --- | --- |
| First opening | Core `testFreshOpeningDisplaysTemplateWithoutCreatingDiary`; UI `testFirstLaunchShowsTodayWithoutSetup` |
| Local draft | Core `testDraftSurvivesRelaunchWithDaySectionVersionAndEffectiveTemplate`, `testFailedDraftReplaceRetainsPreviousDraftAndDiary`; UI relaunch test |
| Confirm append | Core exact-text/export, missing/duplicate headings, changed-source and byte-preservation tests; UI single-save test |
| Interrupted commit | Core 13-case persistence-boundary recovery matrix, unknown-state, failed-replacement and orphaned-transaction recovery tests |
| Duplicate retry | Core same-operation, concurrent-retry and both changed-payload rejection tests |
| Repeated independent text | Core independent identical text; UI repeated-text/export test |
| Midnight/resume | Core fixed-timezone and retained-day tests; UI `testDraftRetainsDateAndSectionAfterRelaunchAcrossMidnight` |
| Write/export failure | Core failed-draft, failed-diary, receipt/cleanup boundary and export-destination tests; UI export cancellation |

Observed results: 25 core tests and 5 UI tests passed without skipped cases; generic simulator build succeeded. Runtime screenshot inspected. Physical-device interruption, signing and storage exhaustion remain unverified as documented in the client README. Review follows this acceptance audit.


### Final verification and review outcome — 2026-09-13

All accepted patch groups were implemented and independently inspected by the root reviewer. The initial scroll test failed after being strengthened; the final implementation binds geometry to the new Markdown and restores the original visible anchor. The final all-tests run passes the unchanged 2-point position assertion. No new findings were deferred; low-impact exceptional cases and their rejection evidence remain in the triage log.

The root reran all commands from Verification against the final application code:

- `swift test --package-path ios --scratch-path /tmp/riji-ios-root-swift`: 40 passed, 0 failed (32 core plus 8 ViewModel/document-adapter tests); no skipped XCTest cases.
- Generic simulator build with `/tmp/riji-ios-root-derived`: BUILD SUCCEEDED.
- Complete `RijiUITests` on the dedicated simulator: 5 passed, 0 failed in 123.545 seconds; TEST SUCCEEDED. Result bundle: `/tmp/riji-ios-root-final.xcresult`.
- Logs: `/tmp/riji-ios-root-unit.log`, `/tmp/riji-ios-root-build.log`, `/tmp/riji-ios-root-ui.log`.

The full frozen matrix remains covered. Added state tests cover visible draft/error/pending status and stable retries after injected storage failures. The export UI test independently verifies bytes read from the actual system destination, excluding an unconfirmed retained draft. Production export does not add a read-permission requirement. Foreground day changes use the fixed journal timezone while preserving the selected draft day. Source code, production configuration and dependencies outside `ios/` remain unchanged from the baseline.

The first local slice is complete. Real-device validation and the remaining full-release product capabilities are unchanged follow-on work, not claimed by this completion.
