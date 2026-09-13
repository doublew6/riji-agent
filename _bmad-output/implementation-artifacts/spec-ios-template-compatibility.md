---
title: 'Safe Obsidian date and styled-heading compatibility'
type: feature
created: '2026-09-13'
status: done
route: dispatch
baseline_commit: 'beb26c3ec19afec480d6775cdd9ff69004ebade5'
review_loop_iteration: 0
context:
  - '{project-root}/AGENTS.md'
  - '{project-root}/PROJECT_GUIDE.md'
  - '{project-root}/docs/PRD.md'
  - '{project-root}/ios/README.md'
---

<frozen-after-approval reason="user authorized continued implementation of the approved iOS requirements and manual/automated testing">

## Intent

**Problem:** Imported daily templates can contain formatted dates and a styled Notes heading. The current app leaves those dates literal and misses the intended quick-capture destination.

**Approach:** Support the two identified public date forms and display inline heading styles while keeping exact raw anchors. Provide a persisted weekday-language preference under templates, with a visible Chinese default and English option. This is the next bounded PRD increment; audio and mobile services remain subsequent work.

## Boundaries & Constraints

**Always:** Support only `{{date}}`, `{{title}}`, `{{date:YYYY-MM-DD}}`, and `{{date:dddd}}`. Use the selected fixed JournalDay, never today's device clock or a week-numbering year. Keep raw template identity, UTF-8, frontmatter and unsupported syntax. Substitution is single-pass. New dates and newly prepared reviews use the explicit language preference. Existing diary, ordinary/MIT/review draft content and operation identity remain unchanged. Blank-day confirmation writes the exact preview. Old encoded operations retain their hashes and original retry behavior. Styled Notes selection returns the original raw heading and requires exactly one matching heading occurrence; ambiguity requires explicit selection. Heading style affects presentation only; appender validation stays exact.

**Never:** No real-vault migration, original private fixture, Air/backend change, model call, template execution, arbitrary date-format compatibility, general body editing, or re-rendering of existing draft text. No additional implementation agents or recursive skill use.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Date rendering | Year boundary, leap day, Chinese/English | Calendar date and correct weekday; same raw template ID | Unknown formats and scripts remain literal with accurate warnings |
| Literal substitution | Title contains supported token text; CRLF template | Replacement values are never reinterpreted; unrelated bytes retained | No execution or silent deletion |
| Preference | Change language, relaunch, metadata write failure | New snapshots/reviews use saved choice; UI reports only persisted choice | Failed write retains previous setting and reports error |
| Existing drafts | Old literal formatted tokens; ordinary/MIT/review drafts | Content, identities and confirmed bytes stay unchanged after upgrade/preference change | Source and transaction guards remain effective |
| Blank preview | Existing draft or new date, then change preference/template | Exact preview bytes are confirmed, including old literal content | Retry recovers same operation once |
| Legacy operation | Old BlankDayDraft JSON, transaction or receipt | Absent optional fields stay absent when encoded; legacy hash and after bytes preserved | Mismatched operation rejected |
| Styled Notes | Unique styled heading, equivalent duplicates, fenced/comment headings | Unique real Notes becomes raw target; presentation removes style delimiters | Ambiguous matches never choose a destination automatically |
</frozen-after-approval>

## Code Map

- `ios/RijiCore/WorkflowModels.swift`: TemplateVersion rendering/warnings/raw ID; BlankDayDraft needs optional frozen rendered content with backward-compatible encoding.
- `ios/RijiCore/LocalJournalStore.swift`: persisted preferences, open fallback; keep existing effective draft branch unchanged.
- `ios/RijiCore/WorkflowStore.swift`: prepare/create blank day. Copy an existing draft's effective content directly. Old missing-field blank drafts use legacy date/title-only rendering; retryWorkflow stays first.
- `ios/RijiCore/GoalReviewStore.swift`: only newly prepared review text gains preference-aware rendering; retained text and MIT snapshot stay fixed.
- `ios/Riji/DiaryViewModel.swift`: preference-aware template label and default Notes target; inspect raw headings before Set deduplication.
- `ios/Riji/WorkflowViewModel.swift`, `WorkflowViews.swift`: language preference and fixed blank preview.
- `ios/Riji/TodayDiaryView.swift`: inline heading display; retain raw IDs and verified reading-position restoration.
- `ios/RijiCore/MarkdownSectionAppender.swift`: keep exact UTF-8 matching and duplicate/fence/comment guards unchanged.
- `ios/Package.swift`, `ios/Riji.xcodeproj/project.pbxproj`: register new sources if needed.

## Tasks & Acceptance

**Execution:**
- [x] `ios/RijiCore/` -- implement safe rendering, preference persistence, exact blank preview and legacy compatibility.
- [x] `ios/Riji/` -- expose language under templates, render heading styles and select a unique raw Notes target.
- [x] `ios/*Tests/` -- test every matrix row and add a synthetic native import/date/styled-Notes flow without weakening existing UI assertions.
- [x] `ios/README.md`, `docs/testing/ios-manual-acceptance.md`, `docs/product/ios-implementation-status.md` -- document actual support and repeatable Mac checks; preserve unrelated guidance.

**Acceptance Criteria:**
- Given a synthetic template using both formatted tokens and styled Notes, when imported, selected and used for a new date, then quick capture saves into its exact original heading and the exported bytes match the chosen language and preview.
- Given saved files or existing drafts before upgrade, when preferences change and the app relaunches, then no existing text, identity or pending-operation result changes.

## Implementation Notes

Investigation found no irreversible action. Footprint spans rendering, persistence, native preferences and tests; dispatch is appropriate. Chinese is a visible product default, not a claim about the private vault's locale. With no Notes heading, retain the existing first-section behavior; multiple equivalent Notes require explicit selection. The user's continuing implementation authorization covers these reversible choices.

## Spec Change Log

## Review Triage Log

| ID | Verdict | Evidence | Route |
|---|---|---|---|
| B1 | medium | The token regex stops `${...}` at the first closing brace and requires a closed `<%...%>`; nested or unfinished unsupported expressions expose later supported date tokens to substitution. | patch; group macro boundaries with E1 |
| B2 | medium | `instantiate` applies the regex through Dataview script fences, while warnings promise their source remains literal. A supported token inside the warned script body is currently changed. | patch |
| B3 | medium | After a preference replacement reports failure, the rollback is another fallible write. A second failure before that replacement leaves new bytes on disk while the published UI remains old. | patch; group preference recovery with E3 |
| B4 | medium | `open` renders fallback before choosing existing file bytes, and `load` always reads the language after publishing part of the new snapshot. Corrupt preference metadata therefore blocks unrelated saved content and can leave a mixed old/new page state. | patch |
| B5 | medium | Ambiguity counts H1 Notes, while selectable sections exclude all H1 headings. When H1/H2 Notes coexist, the user cannot explicitly choose the H1 candidate offered by the ambiguity decision. | patch |
| B6 | medium | Foundation Markdown projection converts link labels and image alternative text to Notes, making non-style syntax an automatic capture target. The change intends style normalization only. | patch |
| B7 | medium | `createBlankDay` returns for any existing target before comparing bytes. If another workflow creates different content after preview, `confirmBlank` clears the candidate and reports success for content the user did not confirm. | patch |
| B8 | low | Frozen legacy/current content carries selected-template metadata whose source cannot be inferred from that content; the native preview deliberately shows a neutral fixed-content label, so it makes no false template-origin claim and writes frozen bytes. Detailed provenance would help the uncommon inspection of a retained draft through blank-day creation. | reject: adding provenance schema/branches exceeds a direct correction for this uncommon cosmetic omission |
| B9 | low | Supported tokens retained in an old frozen preview are excluded from template warnings even though confirmation saves them literally. The raw preview remains visible, but a direct notice should explain that no further expansion occurs. | patch |
| B10 | medium | The new ambiguity test chooses Alpha after blocking save. It does not exercise explicit selection of either equivalent raw Notes anchor, or rejection and input preservation for identical raw duplicates, although those are the new interaction's advertised outcomes. | patch |
| E1 | medium | The same first-closing-brace token pattern leaves the later token in `${f({}, '{{date:dddd}}')}` outside protection. | patch; group macro boundaries with B1 |
| E2 | medium | `parseHeading` accepts `##` with an empty raw title and the baseline `canSave` allowed its persisted draft. The new empty sentinel, save guard and Picker filter remove that formerly valid target. | patch |
| E3 | medium | The second rollback write has no durable recovery record; consecutive write failures can preserve the new choice despite the method reporting failure and UI retaining the old choice. | patch; group preference recovery with B3 |
| Root1 | medium | The replacement lexical scanner counts a closing brace or `%>` inside a block/line comment as a delimiter; `${ 1 /* } */ + '{{date:dddd}}' }` and the corresponding Templater expression can still expose an internal token. | patch: preserve comment lexical boundaries inside the demonstrated opaque expressions |
| Root2 | low | The new legacy-empty-heading test compares output from two unsorted JSON encoders, although JSON key order is not its contract. A shared sorted-key encoder makes that byte assertion depend on the stored schema rather than key iteration order. | patch: deterministic test encoding only |
| V1 | low | Filed regression-gap evidence: existing assertions observe only rendered characters. Removing attributed emphasis would pass both State and UI coverage despite losing requested bold/italic heading presentation. | patch |

All three layers completed before triage. Macro boundary handling, script-fence protection, preference recovery, reading independence, heading selection, frozen-target comparison and their direct regression assertions can be corrected without adding a product/API surface. No finding is deferred.

## Verification

- `swift test --package-path ios --scratch-path /tmp/riji-ios-template-swift`: all Core/State tests pass, including legacy JSON/hash recovery.
- Generic Simulator build with project `ios/Riji.xcodeproj`, scheme Riji and separate derived data `/tmp/riji-ios-template-derived` succeeds.
- Full Simulator UI suite on the existing dedicated device passes, including the new template/capture flow. Use host permissions; do not erase or replace devices.
- Root reviews the complete diff and actual test evidence; no backend/Air or physical-device claim follows from these tests.

### Executed evidence — 2026-09-13

- `swift test --package-path ios --scratch-path /tmp/riji-ios-template-swift`: **92 tests passed**, 0 failures. Log: `/tmp/riji-ios-template-swift.log`.
- Generic Simulator build: **BUILD SUCCEEDED** with `/tmp/riji-ios-template-derived`. Log: `/tmp/riji-ios-template-build.log`. The final build includes the single-placeholder section Picker correction.
- Full UI suite on the existing **Riji Development iPhone**: **9 tests passed**, 0 failures. Log: `/tmp/riji-ios-template-ui-final.log`; result bundle: `/tmp/riji-ios-template-ui-final.xcresult`. This includes file-picker import/date/styled Notes capture/relaunch/export, frozen blank-preview/language-change/export, and the unchanged reading-position assertions.
- Legacy actual confirmation coverage found and fixed CRLF provenance reading: parsing tolerates a trailing CR without modifying saved bytes. A saved MIT diary with an ordinary draft now prepares a neutral fixed preview directly from the snapshot, without treating saved provenance as an imported template; existing diary bytes and the unconfirmed draft remain intact.
- Synthetic sample-export bytes match `docs/testing/fixtures/riji-synthetic-daily-template.md`; `git diff --check` passes. No backend/Air, private template, physical device, production deployment or publication was involved.

### Root verification after review fixes — 2026-09-13

All accepted patch findings, including both root follow-ups, are fixed and verified. B8 remains explicitly rejected as the uncommon cosmetic provenance omission described above; no work was deferred. The section-selection correction uses a private impossible-heading sentinel, preserving valid empty raw headings without adding fields to JournalDraft or changing old payload hashes.

- Final full Swift suite: **106 passed**, zero failures, including all 14 newly added regression test functions and the deterministic empty-heading encoding assertion. Log: `/tmp/riji-ios-template-reviewed-swift.log`.
- Generic Simulator build: **BUILD SUCCEEDED**. Log: `/tmp/riji-ios-template-reviewed-build.log`.
- Full native UI suite: **9 passed**, zero failures, including the fixed-preview notice and actual export-byte checks. Log: `/tmp/riji-ios-template-reviewed-ui.log`; result bundle: `/tmp/riji-ios-template-reviewed-ui.xcresult`.
- Root read the entire baseline diff and every implementation/review-fix delta, checked every original matrix row against actual passing tests, and ran `git diff --check`. The existing Python backend was unchanged by this increment; the preceding full result remains 1,934 passed and 5 skipped.
