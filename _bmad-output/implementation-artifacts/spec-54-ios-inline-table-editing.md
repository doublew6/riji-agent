---
title: 'Edit Markdown table cells without losing the grid'
type: 'feature'
created: '2026-09-18'
status: 'done'
route: 'dispatch'
baseline_commit: '5b87e5451e084045809b14111e57b843546df643'
review_loop_iteration: 0
context: ['PROJECT_GUIDE.md', 'docs/PRD.md', 'docs/architecture/mvp-architecture.md']
---

<frozen-after-approval reason="User explicitly requested implementation of inline table editing, Return for a new row, and row/column addition on 2026-09-18">

## Intent

**Problem:** Tapping a displayed table replaces its grid with pipe-delimited source. Typing loses table presentation, and there are no row/column insertion controls.

**Approach:** Edit directly inside native table cells while retaining the surrounding grid. Return inserts a row below the current row (after the separator for a header), focusing the same column. Bottom and right plus buttons append a row/column. Save through the existing source Markdown editor and autosave pipeline.

## Boundaries & Constraints

**Always:** Keep untouched document bytes, original newline style, protected metadata, Unicode, existing alignments and data. Keep four bottom tabs, removed permanent save footer, date swipe directions, recovery/conflict behavior, original templates and vault locations. Honor read-only and stale-document boundaries. Preserve both source undo/redo and composed input, and keep the edited cell visible. Test only synthetic data.

**Never:** Introduce an append panel, web editor, new canonical format, backend changes, Air deployment, phone data deletion or vault migration. Do not normalize the whole document. Do not claim physical-device/iCloud acceptance from Simulator results. Do not stage or commit the mixed tree.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Cell input | Existing table, empty/text cell, Unicode/emoji, CRLF | Tap and type inside persistent grid; untouched content unchanged; auto-save and cold reopen | Existing save failures retain latest draft |
| New structure | Return in header/body; bottom/right plus | One new row below current or at end, or column at right; valid header delimiter; focus new cell | Bound allocations, refuse unsafe operation without losing text |
| Composition | Chinese marked text, layout/save echo, dismiss/tab/day transition | No first-responder replacement or lost candidate; final text reaches original day | Never write old-cell callback into new day |
| Markdown syntax | Pipes, backslashes, multiline paste, inline formatting, uneven rows | Safe valid cell encoding; reading does not rewrite bytes; existing hidden extra cells retained | Unsupported structural edit rejected visibly, never silently discard data |
| Undo and recovery | Cell/row/column operation, autosave, undo/redo; failed save | Exact source restoration, retry/cold recovery remain usable | Read-only/protected/stale edits rejected |
| Gestures | Wide/long table; cell editing, resize | Horizontal table scroll, vertical diary scroll, active cell above keyboard; no date swipe while editing | Oversized tables retain existing bounded source fallback |

</frozen-after-approval>

## Code Map

- `ios/Riji/MarkdownTextView.swift`: TextKit source projection, `projectTables` currently recreates disposable grids; `MarkdownTableView.openSource` reveals whole source. Keep stable active editor, row heights and scroll ownership.
- `ios/Riji/DiaryBodyEditor.swift`: Coordinator admission, styling, source echoes, day identity, undo and viewport. Child table composition must participate in transitions and editable changes.
- `ios/RijiCore/MarkdownPresentation.swift`: UTF-16 table/cell ranges; parser pads short rows and hides extra cells. Unmatched code/wiki currently invalidate rows; editing must not break persistence.
- `ios/RijiCore/BodyRevisionStore.swift`, `ios/Riji/DiaryViewModel.swift`: existing preservation/conflict/recovery pipeline, reuse without bypass.
- `ios/Riji/TodayDiaryView.swift`: `commitEditing(in:)` walks native text views. Keep footer removal; adjust only if nested composition requires it.
- `ios/RijiCoreTests/MarkdownPresentationTests.swift`, `ios/RijiNativeTests/NativeBodyEditorTests.swift`, `ios/RijiUITests/DiaryFlowTests.swift`: existing table byte/undo/gesture checks; replace obsolete source-reveal expectations without weakening exact source assertions.

## Tasks & Acceptance

**Execution:**
- [x] `ios/RijiCore/MarkdownTableEditing.swift` (new), `MarkdownPresentation.swift`, `ios/Package.swift` -- bounded, source-validated cell/row/column patches; preserve untouched bytes and malformed-row data; register new source.
- [x] `ios/Riji/MarkdownTextView.swift`, `DiaryBodyEditor.swift` (plus focused native helper files/project registration if useful) -- persistent inline cell editor, plus controls, Return, lifecycle/undo/accessibility/viewport integration.
- [x] Core/native/UI test files above -- cover every matrix row; real UI typing and add controls, exact save/reopen, composition, failure and gestures. New native tests must be registered in both build targets if needed.
- [x] `docs/PRD.md`, `docs/testing/ios-manual-acceptance.md`, `docs/testing/ios-inline-tables-2026-09-18.md` -- align interaction and record evidence/limits.

**Acceptance Criteria:**
- Given a normal table, when the user taps, types or presses Return, then the grid remains, the requested row appears and typing continues in its cell.
- Given row/column plus controls, when activated, then one corresponding structure is inserted and undo restores exact prior Markdown.
- Given an original diary, when table edits save and the app restarts, then the same file contains the edits with unrelated bytes preserved.
- Given a save failure or read-only recovery, when editing or leaving is attempted, then existing safeguards retain data and never claim success.

## Implementation Notes

No intent gaps or irreversible operations. This is the user's authorized correction to Issue #54; continue within the existing mixed iOS workspace without repeated approval. Before snapshots are under `output/ios-inline-tables-2026-09-18/before/`; derive this task's diff from them, never sweep prior work into review/staging. Frozen intent does not add row deletion or drag reordering.

Implementation agent owns production code plus Core/native/UI tests. Parent handles documentation, independent review and final verification. Use only `Riji Automated Tests` (`<SIMULATOR_UDID>`), never the manual Simulator or phone. Coordinate with parent before Xcode execution; no concurrent builds. Agent may run focused Swift tests with its own scratch path. Keep visible cell editor text lossless; inline Markdown tokens may remain within the active cell, but table delimiters must never replace the grid on normal taps.

## Spec Change Log

## Review Triage Log


All three review layers completed before triage. Each finding retains its own verdict below; duplicates are grouped only for implementation.

| ID | Verdict / route | Evidence and disposition |
| --- | --- | --- |
| B1 multiline literal blocks | medium / patch (with E3) | `parseTables` now consults protected ranges but no longer excludes multiline comment/template literals. The independent Core harness parses both wrappers as a table. Restore block exclusion while allowing inline expressions. |
| B2 untouched incomplete syntax | medium / patch | `encode` escapes an existing unmatched backtick/wiki opener on any cell edit, changing bytes the user did not touch. Preserve literal incomplete openers while still encoding structural pipes. |
| B3 delimiter run matching | medium / patch | Both encoder and parser detect a closing run by substring, then only close an equal full run; mixed run lengths can therefore reject otherwise literal input. Match complete unescaped runs consistently and add regressions. |
| B4 failed column hides row control | medium / patch (with B5) | `showFailure` explicitly hides addRow, although overwide source rows reject columns but permit new rows. Keep the valid action visible. |
| B5 failure feedback offscreen | medium / patch | The only visible error label is at the end of the table; a failed top-row edit in a long table does not bring it into view. Place feedback in the visible table region, without covering/removing the row action. |
| B6 system undo entry points | low / reject | The child intentionally has no independent undo stack and the source toolbar restores exact source. System gesture/hardware undo routing is not supported by that child. For the personal touch-first MVP these alternate entry points are unlikely in ordinary use; adding a second manager/proxy or command routing risks the verified shared history. Keep toolbar undo as the supported route. |
| B7 inactive raw cell overlay | medium / patch | `finishEditing(resign:true)` leaves the child over its hidden display label, so dismissing the keyboard leaves inline Markdown syntax visible. Restore the display cell after a successful finish, including cross-table focus changes. |
| B8 rejected marked input on teardown | low / reject | A rejected cell value is not committed to source, and the current void aggregate cannot preserve that rejected buffer after teardown. Ordinary valid composition and accepted input are covered; the demonstrated admission boundaries are protected-token injection or the 32,768-unit cell cap. Original/accepted source is retained. A separate recovery channel for rejected input adds lifecycle state disproportionate to this rare case; visible rejection feedback is fixed by B5. |
| B9 explicit replacement equal to stale echo | maybe-false (medium, unverified) / defer | Byte-only pending-echo logic cannot distinguish a hypothetical explicit replacement to the previous model value. Production `updateBody` accepts synchronously and refresh/restore callers serialize through dirty/save/conflict guards; no actual consumer trace causing this mismatch was demonstrated. A delayed-ack plus explicit same-value replacement through the real view model would settle it. |
| B10 near-limit typing performance | maybe-false (medium, unverified) / defer | Full parsing and cell-label rebuilding occur on input, bounded at 1,024 visible cells. No reliable measurement demonstrates user-visible delay; anomalous simulator durations cannot establish latency. A controlled near-limit per-keystroke latency/allocation trace would settle it. |
| E1 combining-mark pipe | medium / patch | Independent Core reproduction with U+007C+U+0301/U+FE0F splits one edited cell and pushes `keep` into a hidden extra cell. Encode structural ASCII independently of grapheme clustering and verify expected actual source-cell count. |
| E2 zero-width relayout | low / patch | `tableViews=[]` precedes the width guard while old views remain attached. Preserve the collection on this direct early-return path so zero-width layout cannot orphan editors. |
| E3 removed literal block exclusion | medium / patch (with B1) | Same deletion and independently executed wrapper examples as B1; restore exclusion without reverting inline template-cell support. |
| V1 encoded typing session gap | medium / patch | Pre-verified gap: existing successive-input tests use ordinary text; one-shot encoding tests bypass stored encoded-state continuity. Add real child insert/delete/paste continuation assertions, exact source and focus. |
| V2 bottom-row keyboard geometry gap | medium / patch | Pre-verified gap: long-table UI inputs successfully without asserting the new cell lies above keyboard/accessory. Extend the existing actual plus flow with stable visible-frame assertions. The added run14 assertion then reproduced a real defect: the new bottom cell remained below the visible diary area after the keyboard resized the parent; resize-triggered active-cell reveal is required. |

## Verification

Matrix audit: all rows have executed passing coverage. Cell input/new structure: four table UI methods and native Return/plus test; composition: retained inline composition, original-day and destination-save-failure tests; syntax: 21 MarkdownPresentation methods including continued encoded input, uneven rows and exact grapheme patch; undo/recovery: original-table autosave undo/redo, failure recovery and read-only tests; gestures: actual horizontal/vertical UI gestures, keyboard geometry, native resize/admission/oversized fallback.

All accepted review patches were implemented and included in final verification. V2's new geometry assertion exposed a keyboard-layout defect, fixed by revealing the active cell after the keyboard frame change has completed layout. Actual bottom-row screenshots before and after typing confirm the caret stays inside the intended cell.

- Parent final Core/state/probe-support run: 463/463, zero failures/skips, unchanged 81-input source manifest. Evidence: `output/ios-tests/20260918T150031Z-core-28bb3b7b/summary.json`.
- Parent final Xcode run: all 40 NativeBody methods plus four table UI methods, 44/44, exact expected inventory, complete xcresult, zero skips/expected failures, unchanged 81-input source manifest. Evidence: `output/ios-inline-tables-2026-09-18/parent-final/summary.json`; screenshots in `final-ui/` alongside it.
- Python runner tests: 77/77. Default UI smoke includes the structural table flow.
- Final signed iPhone build succeeded; strict codesign verification passed and the profile authorizes the known phone. Device remains paired with its connection tunnel unavailable; build is not installed. No physical-device/iCloud acceptance claimed.
- B9/B10 remain unverified and deferred in `deferred-work.md`; supported undo uses the source toolbar. Earlier failed diagnostic runs remain as history, not final acceptance.
- No commit or staging was performed: this task deliberately preserves the pre-existing mixed tree and staged voice spec. No backend or Air changes.
