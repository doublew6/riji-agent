---
title: Apply the approved native page design
issue: 60
type: feature
created: 2026-10-14
status: done
route: dispatch
review_loop_iteration: 0
baseline_commit: 5b87e5451e084045809b14111e57b843546df643
context:
  - '{project-root}/PROJECT_GUIDE.md'
  - '{project-root}/docs/DESIGN.md'
  - '{project-root}/docs/design/ios-pages-2026-10-14/proposal.md'
  - '{project-root}/docs/testing/ios-automation.md'
---

<frozen-after-approval reason="User approved this design and its implementation on 2026-10-14">

## Intent

Apply the approved 18-screen diary, memory and settings design to the current native iOS version, and make it the durable design baseline for future features. Preserve the mentor's separately approved dark design. The user explicitly approved implementation; no further layout confirmation is needed.

## Boundaries & Constraints

Always preserve original files/templates, continuous in-place Markdown editing, cursor/viewport/IME/undo, both date swipes, table editing, atomic writes, recovery, four peer tabs, data isolation and verified operation receipts. Use semantic light/dark colors, native system fonts, scalable layouts and 44 pt controls. Keep existing accessibility identifiers and real data.

Never deploy to Air, install on the phone, publish code, alter permissions, read private diaries, import unverified #58 code, or implement the separate unapproved mentor interaction PRD. Design examples never become application data. No global appearance override that breaks mentor screens.

Dependencies: #53–57 functions exist in the copied baseline. #58 remains in-review with reproduced defects; preserve its future approved design but use existing #57 contracts now. Show real unavailable states for weekly memory reports, stable-topic rename/pin, structural decisions, remote task management, AI-generated diary reviews and full backup restore where no compatible service exists. Do not rename local manual review as AI output.

</frozen-after-approval>

## Code Map

- `ios/Riji/RijiNavigationView.swift`: four tabs with save-before-switch; settings currently one flat List.
- `TodayDiaryView.swift`, `DiaryDirectoryView.swift`, `WorkflowViews.swift`: diary, original tree, dates, MIT/goals and weekly/monthly workflow. Original-file authorization and model callbacks stay intact.
- `DiaryBodyEditor.swift`, `MarkdownTextView.swift`: persistent UIKit editor and table cells; theme updates must not rebuild text, reset selection or lose composition. Refresh static CGColor layers on trait changes.
- `CloudVersionReviewView.swift`, `MarkdownDiffView.swift`: reusable fragment diff and safe byte-checked application; ordinary body conflict lives in TodayDiaryView and still shows full versions.
- `MobileOverviewViews.swift`, `MobileViews.swift`: actual summaries/evidence/correction and per-persona observations. `themeSummaries` supplies titles, text, evidence and dates; categories become a UI filter without relaxing protocol validation.
- `MobileSettingsViews.swift`: service state, privacy purposes, draft archive; retain existing checks and confirmations.
- `ios/Package.swift`, `ios/Riji.xcodeproj/project.pbxproj`: explicitly register new native files and exclude UI from SwiftPM state target.

## Tasks & Acceptance

**Execution:**
- [x] `docs/DESIGN.md`, `PROJECT_GUIDE.md`, `docs/design/README.md`, `docs/design/ios-pages-2026-10-14/*`: record approved v1.0/date, future-page reuse rule, and actual per-screen capability status. Update generator and generated HTML consistently, without changing approved layout.
- [x] `ios/Riji/`: introduce reusable semantic palette/page, list, row and action components; apply to diary, directory, workflow, memory, settings and relevant sheets. Preserve mentor styling and system sheets.
- [x] Diary pages: quiet continuous paper; search visible in original directory; direction-to-MIT overview with original definitions; understandable period/coverage/review hierarchy; date sheet return-today; readable tables/diffs. Reuse existing diff for ordinary conflict if safe, preserving original merge safeguards. No fabricated recent-open history.
- [x] Memory pages: recent downloaded evidence updates first, actual theme summaries next with category filter, separate weekly-report unavailable state. Theme reading/evidence, source correction, comparison and mentor observations share the design. Unknown coverage is never complete.
- [x] Settings: four groups and dedicated file/template, data/AI, tasks and export/recovery destinations. Working routes for original folder recovery, current Markdown export, recordings and retained drafts; unsupported operations explicitly unavailable, not fake success or editable dummy controls.
- [x] Registration, tests and `docs/testing/ios-approved-pages-2026-10-14.md`: compile and verify scoped regression, light/dark/large text, screenshots on the dedicated synthetic simulator. Record exact tests and remaining service dependencies.

**Acceptance Criteria:**
- Given valid original content, when reading/editing or changing appearance, then source text, selection/scroll and table edits remain intact and use the approved palette.
- Given settings, when opening each group and returning, then routes work, four tabs remain and no implicit data/permission change occurs.
- Given valid/expired/offline memory, when reading themes/evidence or correcting a memory, then existing provenance and invalidation guards hold with clear actual states.
- Given an unsupported capability, when visiting its approved destination, then the user sees what is unavailable and a useful existing route, never synthetic results.
- Given future UI work, when reading the project guide, then it leads to this approved layout and shared native style.

## Implementation Notes

Work only in this isolated `codex/60-approved-ios-pages` worktree. It includes the exact uncommitted current app baseline, copied before edits; do not stage or commit that inherited work. The parent will compare against its baseline and synchronize only this task's changed files back after review, guarding against concurrent changes. The user's request to update the current version authorizes preserving that baseline; no reset or mass staging. No irreversible data actions are needed. Ordinary implementation decisions are delegated; preserve user intent if details require a platform-specific adaptation.

## Spec Change Log

## Review Triage Log

2026-10-14 — All three independent review reports received before triage. Each finding is accepted below on its own evidence; duplicate fixes are grouped only after this assessment.

| ID | Severity | Verdict and evidence |
|---|---|---|
| B1 | Medium | Patch: directory search uses the active workflow source, which can differ from the browsed external folder; restore a visible, accurate scope label. |
| B2 | Medium | Patch: direction overview omits the model's existing `mitBodyChanged` warning and can present an old confirmation as current. |
| B3 | Medium | Patch: non-scrolling calendar plus added controls can exceed medium-sheet height at large text sizes. |
| B4 | Medium | Patch: theme summaries have meaningful `stateText` that the new homepage omits. |
| B5 | Low | Patch: full evidence and unlimited summaries crowd the approved overview; add bounded homepage previews and retain full detail. |
| B6 | Medium | Patch: recent updates sort by `updatedAt` but display `observedAt`; distinguish update time from event time. |
| B7 | Medium | Patch: independent workflow and cloud confirmation sheets lack the shared page background after foregrounds changed. |
| B8 | Low | Patch: shared primary button style ignores enabled state, concealing disabled feedback. |
| B9 | Medium | Patch: export/recovery page does not surface `cloudError` from its backup action. |
| B10 | Medium | Patch: ordinary body conflict tests still use full-version editing, leaving the new fragment callback unverified. |
| V1 | Medium | Patch: independently confirmed B10; a no-op fragment callback would pass existing tests. |
| V2 | Medium | Patch: return-today test starts on today and would pass a close-only implementation. |
| V3 | Medium | Patch: new settings exporter lacks execution/file-byte verification; old exporter tests use a different route. |
| V4 | Medium | Patch: independently confirmed B2 using existing model tests proving stale MIT state. |
| V5 | Medium | Patch: native recovery composition test selects a new read-only preview instead of the actual editor; reproduced assertion failures. |
| V6 | Medium | Patch: native appearance test proves stale light table borders after dark-mode change. |
| E1 | Medium | Patch: independently confirmed V6; child traits lag the parent callback. |
| E2 | Medium | Patch: independently confirmed B2, old MIT shown without known mismatch warning. |
| E3 | Medium | Patch: independently confirmed B9, cloud backup failure invisible on this page. |
| E4 | Medium | Patch: independently confirmed V5; expand and identify the real merge editor without weakening composition checks. |
| E5 | Medium | Patch: existing service and timezone UI tests still use the old flat settings routes and will fail before their business assertions. |
| P1 | Medium | Patch: saved workflow files still show raw Markdown and the shared vault reader retains system background; use the existing rendered reader with matched semantic colors. |
| P2 | Medium | Patch: settings export completes but the new page has no visible receipt, and returning to diary clears the shared status. Display the existing verified result on the export page, explicitly as the last export; verify actual exported bytes there. |
| P3 | Low | Patch: actual largest-text screenshot shows the row symbol overlapping its title because a fixed 24 pt frame is smaller than the scaled symbol. Allow intrinsic width; repaired screenshot confirms separation. |
| P4 | Medium | Patch: normal-size memory homepage retains an empty large-title area even after the title exists and is hittable. Use the same inline native title as the other current pages, then inspect the settled screenshot. |
| P5 | Low | Patch: iOS 27's actual compact sharing panel does not expose the old private ActivityListView element. Identify observed Copy/Save to Files and exact preview text; retain retry, original-text preservation and explicit-discard assertions. |
| P6 | Low | Patched and verified: long-list disclosure container has an invalid XCTest activation point although its visible title is usable; tap the actual label, preserving exact-byte recovery and save assertions. |
| P7 | Low | Patched and verified: system Files contents can appear before the filename accessory; wait for the save panel and then require the unique filename field. Both export journeys still verify bytes read from the saved file. |

Initial verification: Swift package 604 passed; native 61 executed with 3 failed assertions in 2 tests (V5/V6); UI run interrupted when its owning worker ended. This is not a completed smoke pass. Parent owns final full verification after patches.

## Verification

Use `python3 scripts/test_ios.py --suite smoke` after edits, or scoped native/UI runs during iteration followed by smoke. Use only Riji Automated Tests with synthetic fixtures; never the manual development simulator's vault. Add meaningful tests for new settings navigation, theme filters and appearance changes preserving editor state; do not write tautological styling tests. Keep screenshots and logs in ignored output. No real-service or phone-success claims from synthetic tests.

## Completion

All accepted review findings were addressed. Final evidence: 604 Swift package tests and 62 native tests passed; all 34 smoke UI identities passed across the full run and scoped repairs, plus the independent diary exporter journey. The full smoke result remains failed (2 UI locator failures) and was not relabeled. The final targeted runs passed 3/3 and 2/2 with no skipped tests; all production source hashes match the complete smoke run. Python runner tests passed 80/80. Light/dark/large-type screenshots were inspected. See `docs/testing/ios-approved-pages-2026-10-14.md`.

Only this task's 30 files are integrated into the primary directory after baseline/hash checks, with backups and the existing index preserved. The approved design and shared-component rule are mirrored to the local project design archive. No Air deployment or phone installation is included. No commit is created: the current implementation depends on inherited uncommitted files, and the repository forbids mixing another agent's work into this issue. The isolated branch and exact task diff remain available. Unsupported service capabilities are explicitly recorded in the approved proposal and verification document, not represented as completed features.
