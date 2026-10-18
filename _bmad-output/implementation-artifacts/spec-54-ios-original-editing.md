---
title: 'Edit original daily Markdown from the iPhone'
type: 'feature'
created: '2026-09-17'
status: 'in-review'
route: 'dispatch'
baseline_commit: '77c2c9b231d4e0280b18e3a0ca7270ba4616d5b5'
review_loop_iteration: 1
context: ['PROJECT_GUIDE.md', 'AGENTS.md', 'docs/PRD.md', 'docs/architecture/mvp-architecture.md']
---

<frozen-after-approval reason="Confirmed personal MVP and explicit continuation request on 2026-09-17">

## Intent

**Problem:** The normal app opens the original vault correctly but prohibits all body input. The user explicitly requests usable original Markdown editing while development continues without the phone attached.

**Approach:** Connect the existing native inline editor, durable revisions and coordinated original writer for direct user edits. Keep Air integration as the next independent increment. This increment continues Issue #54 in the existing authorized mixed working tree, preserving unrelated work and staging.

## Boundaries & Constraints

**Always:** Preserve original source bytes, template structure, source-bound recovery and four navigation tabs. The iPhone/Mac two-phase synthetic iCloud probe has passed; preserve its evidence. Reuse compare-before-write and exact verification, retain both versions on conflict, never infer successful cloud propagation from local save. Make normal direct user edits writable through an explicit coordinated-user-edit policy; default construction remains read-only. Extend fault coverage for unavailable files, moved roots, conflicts, and interrupted writes before enabling the path. Physical offline/revocation/OS-conflict/interruption acceptance still must be reported separately as pending; do not fabricate completion.

**Never:** Replace the native editor with an append box; rewrite templates; auto-recreate a deleted original; enable legacy append/voice/AI writes to the original vault; mutate private journals during tests; change icon/signing; erase, uninstall or replace existing app data; stage unrelated files; deploy Air or publish from this increment.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Existing original | Authorized daily file, Unicode/CRLF/table | Inline edit saves exact bytes to the same nested path; cold reopen restores | Retain source-bound recovery before source mutation |
| Absent date | Original template preview | Browsing creates nothing; first user edit creates the expected daily file | Concurrent creation rejected |
| Changed original | Another writer modifies/deletes/moves after opening | Preserve original and typed draft, refuse stale replacement | Accurate source/conflict error; no misleading generic success |
| Unavailable file | Placeholder, denied access, moved root, unresolved version | Refuse source write and retain typed input | Actionable local error without sensitive paths |
| Interrupted save | Failure before/after replacement or receipt | Retry resolves same operation, no duplicate history | Pending state survives new store/process |
| Navigation | Edit then date/tab/source change | Flush before leaving; block transition on failed save | Current text/caret remain available |

</frozen-after-approval>

## Code Map

- `ios/RijiCore/OriginalVaultAccess.swift`: read-only default and fixture-only policy, coordinated writer, cloud availability checks.
- `ios/Riji/VaultBrowserStore.swift`: restores/selects authorized root, constructs adapter and isolated home store.
- `ios/RijiCore/BodyRevisionStore.swift`: retained draft, operation, receipt, revision and conflict state; preserve existing identity checks.
- `ios/Riji/DiaryViewModel.swift`: `canEditBody`, autosave, `persistBody`, restore, navigation gating; currently collapses OriginalVaultError into generic storage errors.
- `ios/Riji/TodayDiaryView.swift`, `RijiNavigationView.swift`: existing inline editor/save status and obsolete read-only settings copy.
- `ios/RijiCoreTests/OriginalVaultAccessTests.swift`, `ios/RijiStateTests/OriginalVaultStateTests.swift`, `ios/RijiUITests/DiaryFlowTests.swift`: synthetic fixtures and current read-only expectations.
- `docs/testing/ios-icloud-probe.md`: actual two-phase pass and separate unexecuted physical cases.

## Tasks & Acceptance

**Execution:**
- [ ] `OriginalVaultAccess.swift`, `VaultBrowserStore.swift` -- explicit normal coordinated direct-edit policy and safe construction, retaining read-only default.
- [ ] `DiaryViewModel.swift`, `TodayDiaryView.swift`, `RijiNavigationView.swift` -- correct editable/status/error presentation and unchanged guarded navigation; all changes restricted to editor scope.
- [ ] `OriginalVaultAccessTests.swift`, `OriginalVaultStateTests.swift`, `DiaryFlowTests.swift` -- realistic matrix coverage through normal app paths with synthetic files, including cold restore and native edit-save-reopen.
- [ ] `docs/testing/ios-original-editing-2026-09-17.md`, `docs/product/ios-implementation-status.md` -- verified commands, concise manual steps and physical limits.

**Acceptance Criteria:**
- Given an authorized original daily note, when the user taps and edits its body, then the same Markdown file saves and reopens with exact edits and preserved untouched syntax.
- Given a failed or conflicting save, when the user changes tabs or dates, then their current input remains and the error explains the source issue without pretending the save succeeded.
- Given the original template and an absent date, when the user only browses, then no file is created; when they type, then one file is created without a custom replacement template.
- Given existing app data, when this update is built/tested, then prior local files, original bookmarks, icon and four bottom tabs remain intact.

## Implementation Notes

The user already authorized this implementation and continuing within the current work. No repeated product approval is required. Baseline copies of editor files and git status are in ignored `output/ios-development-2026-09-17/editor-before/`; do not bulk-stage the mixed tree. Do not start Xcode tests concurrently with other tasks. Swift tests use `/tmp/riji-ios-swift-build`; Xcode uses `${TMPDIR:-/tmp}/riji-ios-manual-derived` and the existing Riji Development iPhone simulator. If full physical acceptance is impossible without the phone, implement and test locally and list the exact remaining real-device checks.

## Spec Change Log

- Review loop 1: normal writing exposes failure-recovery and coordinator race gaps. Keep the explicit manual policy, exact source bytes, legacy isolation, identity-cache fix and existing passing tests. Extend implementation to retain source-bound local input independently of live source availability; validate current identity/path only at source reads/commits, without permitting foreign or malformed draft identities. Permit same-collection reauthorization before flushing through invalid old permission; unrelated selection must not consume/drop the current draft. Cold start must surface retained input for copy/export even if the source remains unavailable (including a missing root). Guard the descriptor actually used for coordinated replacement against a root substitution during the callback. Preserve existing regular-file mode, xattrs and ACL when replacing, fail safely if preservation cannot be completed. Add original nested conflict merge, recovery and native edit/undo verification, and align manual guidance. These are repair requirements for the already-authorized no-data-loss intent, not a new product approval.


## Review Triage Log

| Finding | Verdict and evidence | Route |
|---|---|---|
| Blind 1: newer input cannot persist after source loss | high: retainBodyEdit calls live source validation through assertReadableBodyRecovery, so later keystrokes are only in RAM | repair |
| Blind 2: reauthorization blocked by prior save failure | high: selectOriginalFolder flushes through stale permission before constructing fresh candidate | repair |
| Blind 3: cold source failure hides retained input | high: load opens live source before applying retained draft; missing root can prevent store construction entirely | repair |
| Blind 4: replacement loses mode/xattrs/ACL | medium: descriptor temporary uses 0600 and rename without metadata transfer | repair |
| Blind 5: source errors become generic storage | medium: local recovery readability mixes live source checks, losing original error | repair with local/live separation |
| Blind 6: native in-place/table/undo integration gap | medium: new test appends at EOF, existing editor tests do not cover writable original store | patch tests |
| Blind 7: old manual acceptance claims originals read-only | medium: daily editor and general file preview need distinct instructions | patch docs |
| Edge 1: root substituted inside coordinator callback | high: independent synthetic harness observed replacement receiving write; access identity is checked only before waiting | repair |
| Gap 1: source-bound conflict merge not exercised | medium: original tests stop at rejection; local merge tests use nil sourceID | patch tests |


## Verification

- `swift test --package-path ios --scratch-path /tmp/riji-ios-swift-build` -- Core/State regressions pass.
- Build normal Riji for the existing Simulator; parent runs focused UI verification after implementation.
- Manual handoff: tap ordinary body, type, wait for local-save status, reopen, compare same file on Mac after iCloud propagation. Conflicts must preserve both versions. No physical pass without actual evidence.
