---
title: 'Safe diary navigation and fixed directory settings'
type: 'feature'
created: '2026-09-15'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '77c2c9b231d4e0280b18e3a0ca7270ba4616d5b5'
context: ['PROJECT_GUIDE.md', 'docs/PRD.md', 'docs/architecture/mvp-architecture.md']
issue: 53
---

<frozen-after-approval reason="User approved PRD v3.0, the first increment, and explicitly requested development">

## Intent

Make daily navigation match the approved personal MVP: left swipe goes to the previous calendar day, the directory has a fixed bottom Settings entry, and the main destinations are Diary, Mentors and Memory. Reuse the existing local editor and recovery boundary. The latter two destinations truthfully show unavailable connection status until their later integration; never fabricate replies or memories.

## Boundaries & Constraints

Always retain existing text, templates, recordings, recovery evidence and the external read-only folder browser. Preserve date picker, Return Today, document export and local workflows. Settings and its subpages use Back only, without a recursive More route. Navigation must retain document-specific caret/reading positions during the session, including directory/tab round trips.

Never activate external-folder writing, migrate data, call AI, change backend/production, or erase/uninstall the main Simulator app. No right-swipe/future-date gesture. Do not relabel unavailable integration as connected. Use synthetic inputs. No commits, staging of the mixed worktree, publication or new build-output roots.

## I/O & Edge-Case Matrix

| State/action | Expected behavior | Failure handling |
|---|---|---|
| Reading, deliberate left swipe | Previous calendar day, including year/leap/DST boundaries; current edit saved first | No date change on save error or unresolved conflict |
| Vertical/right/short/diagonal movement, editing/marked text/selection/caret or table gesture | Ordinary editor behavior; no date switch | No callback or source mutation |
| Rapid navigation and late old-day edit callback | One owned transition, content remains bound to its day | Ignore stale target callbacks; retain failed edits |
| Return to long document via dates/directory/tabs | Restore its clamped caret and reading offset, preserve source bytes | New document starts at top; no cross-day undo |
| Long directory, Settings, nested settings, Back | Settings footer stays visible, no recursive More, return restores directory position | Existing tools remain reachable |
| Switch away with unsaved edit or failed save | Retain/save before leaving; pending failure stays on Diary | Existing recovery actions remain available |

</frozen-after-approval>

## Code Map

- `ios/Riji/DiaryViewModel.swift`: `navigate(to:)` owns `flushDocumentInput` then `load`; reuse its failure/old-day admission. `prepareDirectory` flushes. Timezone comes from the store.
- `ios/Riji/DiaryBodyEditor.swift`: UIViewRepresentable and Coordinator bind the day; currently reset selection/offset on date changes. Preserve composition callbacks, styling and undo protections.
- `ios/Riji/MarkdownTextView.swift`: owns TextKit and checkbox gesture delegate. Branch new recognizers by identity; do not apply checkbox hit testing to date navigation.
- New previous-day admission must reject an unresolved `bodyConflict` or `hasRetainedBodyMerge`, even if the existing general navigation allows browsing away after retaining merge text. Keep existing read-only recovery navigation intact. Subtract calendar labels, not 86400 seconds; handle DST/nonexistent local midnights safely. Table rendering is incomplete: reject gestures beginning on table-like rows rather than claiming table scrolling is implemented.
- `ios/Riji/TodayDiaryView.swift`, `DiaryDirectoryView.swift`, `RijiApp.swift`: current root, sheets, save bar, date picker, scrollable tree and tools. Document More is separate from Settings; remove no export/recovery actions.
- `ios/Riji/WorkflowViews.swift`: reuse real template/weekday settings. New Settings can show existing timezone and factual connection/storage status without editable secret fields.
- `ios/RijiStateTests/BodyEditorStateTests.swift`, `ios/RijiNativeTests/NativeBodyEditorTests.swift`, `ios/RijiUITests/DiaryFlowTests.swift`: existing failure injection, hosted real editor, synthetic UI fixtures. Preserve all prior assertions.
- `ios/Package.swift`, `ios/Riji.xcodeproj/project.pbxproj`: explicit source lists; new native files must be registered or excluded correctly.

## Tasks & Acceptance

**Execution:**
- [x] `ios/Riji/DiaryViewModel.swift` -- add previous-day navigation using the stored timezone and existing save gate; expose any small admission state needed by root navigation.
- [x] `ios/Riji/DiaryBodyEditor.swift`, `ios/Riji/MarkdownTextView.swift`, `ios/Riji/TodayDiaryView.swift` -- implement a deliberate leftward gesture and document-specific viewport restoration, preserving native editing and bytes.
- [x] `ios/Riji/DiaryDirectoryView.swift`, `ios/Riji/RijiApp.swift`, new native view files if useful -- fixed footer, nonrecursive settings and three main destinations; preserve safe tab departure and all existing tools.
- [x] Existing State/native/UI test files and build manifests -- cover every matrix row, especially failed-save navigation and real scroll/gesture interactions; use the actual production editor.
- [x] `ios/README.md`, `docs/testing/ios-manual-acceptance.md` -- update current usable paths and 3–8 concrete Mac checks; clearly separate implemented navigation from later iCloud/Air work.

**Acceptance Criteria:**
- Given a saved or retained local diary, when using the new navigation, then dates, source bytes, recovery state and document positions remain correctly associated.
- Given a long directory, when opening Settings and returning, then the fixed footer stays reachable and the tree remains at its prior position.
- Given a new build in sample mode, when checking the three destinations, then Diary works and unavailable AI destinations show honest status without simulated content.

## Implementation Notes

- Product approval and the explicit “start development” instruction authorize this already-reviewed increment and continued work on the existing dirty baseline. No new product decision requires a repeat approval. A local byte-exact worktree/index backup and normal Simulator data backup precede edits under ignored `output/ios-personal-mvp-implementation-2026-09-15/`; unrelated memory privacy files must remain unchanged.
- Implementation owns only the files in the tasks plus this spec. Root independently investigates external-file coordination with temporary synthetic probes, without activating a writer or editing these implementation files. Do not invoke nested build workflows or spawn further agents.

## Spec Change Log

- 2026-09-15: Approved increment implemented on the backed-up dirty baseline. Frozen intent and acceptance are unchanged; ready for root review and sample-mode acceptance.

## Review Triage Log

Independent review completed with all three context-free layers; no layer was skipped. Each finding was adjudicated before grouping:

| ID | Verdict / route | Evidence and disposition |
| --- | --- | --- |
| Blind 1: stale App diary list | medium / patch | `BlankDayView.confirmBlank` saves and calls `navigate`; `load` never updates `directory`. Moving `loadDirectory` to stack lifetime therefore misses the return refresh. Restore only list refresh on appearance, preserving browser lifetime, and exercise create/back/list. |
| Blind 2: pixel viewport after layout/font change | low / reject | Stored `CGPoint` restores pixels and can show a different paragraph after reflow; source and caret identity remain intact. Changing font/width while away is outside the ordinary same-layout round trip, with minor repositioning harm. Character-anchor and reflow state would add substantial machinery; retain this explicit limitation for later accessibility/device acceptance. |
| Blind 3: curved multi-direction drag | low / reject | The recognizer compares initial and final vectors; an unusual left/down/up/left path can finish inside the accepted vector. It does not bypass editing or save guards. Tracking cumulative trajectory adds gesture state for an uncommon, negligible case; ordinary vertical/diagonal/right/short gestures are covered. |
| Blind 4: pipe in an ordinary alias/code line | low / reject | Any pipe-containing row is deliberately reserved, so aliases may require starting the swipe on another row or using the date picker. This restriction is documented. Replacing it with a table parser adds complexity before table rendering exists, for a minor gesture inconvenience. |
| Blind 5: later tab taps during an owned transition | false / reject | `isSwitching` admits exactly one destination until its save ends; `selected` changes only after successful retention. This matches the frozen rapid-navigation contract. A latest-request-wins/cancel model is an alternative behavior, not the promised behavior or a loss of edits. |
| Blind 6: normal-body composition/departure coverage | medium / patch | Existing native composition coverage calls the production helper for legacy/merge editors, while normal-body gesture tests only check admission. Add normal-body composition plus actual destination retention/failure coverage; no new production behavior is needed. |
| Blind 7: fixed viewport-test delays | low / patch | New tests assert updated text/layout exactly after 100 ms; the asynchronous SwiftUI render need not be complete on a loaded test host. Replace those waits with bounded observable readiness conditions while retaining every assertion. |
| Blind 8: large-font / VoiceOver navigation | maybe-false / defer (medium, unverified) | No automated evidence establishes actual large-font footer hit targets or VoiceOver focus after returning. Code uses native links, labels, and a date-picker alternative, but cannot establish accessibility behavior from the diff. Physical accessibility acceptance must exercise those cases before claiming them passed. |
| Blind 9: incomplete final validation record | low / patch | The record intentionally contained baseline/probe results while tests/review were active. Root will append exact final pass/failure evidence, data hashes and the manual-GUI limitation before delivery. |
| Edge 1: year 0001 underflow | low / reject | A Foundation probe confirms previous day of `0001-01-01` formats as `0001-12-31` with era 0. The review's guard snippet is a proposed missing guard, not current code. This ancient era boundary is not encountered in everyday personal diary use; a new era guard solely for this negligible case is rejected under the review's rare-low rule. No claim of BCE navigation is made. |
| Gap 1: two actual date-picker boundary consumers | medium / patch | Pre-verified regression gap: State navigation bypasses both pickers, existing UI only cancels Today picker or uses a normal BlankDay date. Exercise real open/confirm/preview for São Paulo's missing midnight and Apia's missing day using synthetic fixtures. |

Patch work was sent to the original implementation agent. Root owns final documentation. The unverified accessibility item is recorded separately for later acceptance; no frozen intent changes or loopback are required.

- Real UI tests exposed recognition routing that rejected native scrolling and an initial threshold that rejected UIKit's 3.33-point pan. Both are corrected; completion still requires 80 points. Tests explicitly finish native editing before expecting a reading swipe.
- Settings assertions check the current page and the inaccessible diary toolbar behind its sheet. Connection status is read from its actual LabeledContent accessibility element. Original test assertions remain.
- Setting translation on a gesture without live touches did not produce a valid native begin test. The replacement checks the exact production begin-admission method with the observed small movement, alongside an actual Simulator gesture.

## Implementation Result

- Previous-day and destination navigation use the existing save boundary, with stricter unresolved-merge admission. Explicit read-only recovery browsing remains. The diary timezone determines today; Gregorian date labels and the two relevant date pickers avoid nonexistent local midnight conversion.
- Native left-pan admission is separated from checkbox handling and reserves editing, selection and table-like rows. Session-only per-day viewport storage restores and clamps selection/offset, preserving source bytes and clearing cross-day undo.
- Added Diary / Mentors / Memory destinations, honest unavailable Air pages, fixed directory Settings and Back-only subpages. Directory lifecycle now belongs to the whole navigation stack so opening Settings preserves the expanded tree. Existing tools remain available.
- Registered the new native file in Xcode and excluded it from the hostless State package. Updated the README and six Mac checks. No external writer, migration, AI call, production action, staging or publication occurred.

## Verification

Final evidence is under ignored `output/ios-personal-mvp-implementation-2026-09-15/`:

- `navigation-final-swift-tests.log`: **304 Core/State tests passed**, including all 299 original tests and five new tests (199.658 seconds).
- `navigation-final-xcode-tests.log`: **six UI flows passed** (249.676 seconds): failed-save departure, fixed Settings/tree restoration, gesture/viewport/destination round trips, original read-only tree, Markdown export, and MIT/weekly review. This combined run records the invalid native begin test above, so its overall exit was not success.
- `navigation-final-native-and-gesture.log`: after correcting that test, **31 native tests passed** (18 body editor and 13 existing voice tests; 8.317 seconds), followed by the actual navigation UI flow passing again (32.178 seconds). No microphone permission was requested.
- `navigation-original-test-audit.json`: all original methods and assertion lines remain in the three edited test files (27/15/19 methods and 134/79/225 assertion lines). `navigation-ownership-check.json` confirms the index and both unrelated privacy files equal their baseline backups. Whitespace validation passed.
- `navigation-final-ui-evidence/`: inspected screenshots show the fixed footer, restored long-document position, previous date, read-only file and Markdown presentation. Earlier failed attempt logs remain separately available.

Final root review and patch verification are complete. Physical-device gesture/input acceptance, complete table rendering, iCloud writing and Air integration remain separate increments.

### Final root verification after review patches

- `navigation-root-final-swift.log`: **304 Core/State passed**, zero failures, 224.485 seconds, exit 0.
- `navigation-root-final-xcode.log`: **32 native passed** (19 body + 13 existing voice), zero failures, 9.609 seconds; **8 UI flows passed**, zero failures, 340.203 seconds. Overall `TEST SUCCEEDED`, exit 0. This final run supersedes the earlier mixed-run failure. Exact filters are preserved in the first log command.
- The root inspected the changed list lifecycle, bounded viewport waits, real composition/retention test, and UUID/scenario/zone-gated fixture. Created-date and skipped-date screenshot evidence was also inspected. Patches did not alter the frozen behavior.
- `navigation-final-sample-launch.log`: the existing sample launcher built and launched successfully. Its recorded Riji process remained alive. Extra CUA manual interaction was unavailable because the Mac was locked; a nonblocking unlock request was sent, and that limitation was not bypassed.
- `normal-data-final-check.json`: **Original normal-space files unchanged**, none missing, zero new normal-space files. Only the main `com.riji.local` remains registered among Riji Simulator apps. The newly introduced UI test runner was archived and all **38 regular files** verified before its precise uninstall; the main App was never uninstalled.
- Root preserved the original index and unrelated privacy source/test files. No local commit was created: this increment depends on the known pre-existing uncommitted editor baseline, and committing complete files would mix ownership/scope. No push or production action occurred.
- Per the user's additional design instruction, root read the complete public `design-mobile-apps` skill and added `docs/product/ios-visual-design-direction.md`. This records the reference method and design direction; no Sleek service was activated or private design material uploaded.

- `swift test --package-path ios --scratch-path /tmp/riji-ios-swift-build` -- all Core/State tests pass.
- `xcodebuild -project ios/Riji.xcodeproj -scheme Riji -destination 'platform=iOS Simulator,name=Riji Development iPhone' -derivedDataPath "${TMPDIR:-/tmp}/riji-ios-manual-derived" -parallel-testing-enabled NO CODE_SIGNING_ALLOWED=NO test` with the explicit selected test targets recorded in the logs above.
- Logs, screenshots and recovery archives remain in ignored `output/ios-personal-mvp-implementation-2026-09-15/`. Automated tests did not request microphone permissions. The sample build is ready for the six documented Mac checks after unlocking.
