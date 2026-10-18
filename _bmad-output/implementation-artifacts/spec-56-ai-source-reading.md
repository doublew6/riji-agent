---
title: Read saved AI advice without internal metadata
type: bugfix
created: 2026-10-17
status: done
route: dispatch
review_loop_iteration: 0
baseline_commit: 5b87e5451e084045809b14111e57b843546df643
context:
  - docs/DESIGN.md
  - docs/design/ios-pages-2026-10-14/proposal.md
  - docs/testing/ios-mentor-usability-2026-10-17.md
---

<frozen-after-approval reason="User said continue modifying after the native walkthrough identified raw AI provenance crowding the diary">

## Intent

Saved AI advice currently exposes a long JSON comment, English mentor IDs and discussion IDs inside the diary. Keep the diary readable by folding recognized internal source lines in its existing editor. Keep the advice and AI nature visible. Under the document's More menu, provide AI discussion sources with readable Chinese mentor names, topic, dates and edited state; permit explicit read-only inspection of the original Markdown.

## Boundaries & Constraints

Preserve the original text storage, UTF-16 offsets, Markdown bytes, protected-marker admission, undo and composition, selection, viewport, existing editor identity, tables, date navigation and exact/idempotent AI writes. This is a display change, with no migration, remote request, production deployment, phone install, commit or publication. Work only in the already authorized shared dirty #56 increment; preserve unrelated changes and staging. The per-task pre-change snapshot is `output/ai-source-reading-2026-10-17/baseline/` because HEAD predates the shared increment.

Recognize only complete top-level paired AI-result records with structurally valid saved provenance and the canonical generated header matching that provenance. Do not treat a JSON comment as trusted or remotely verified. Older valid records remain readable without applying the short-lived remote validation used for writing. Unsupported, corrupt, unpaired, nested or code-contained records remain literal, never consume unrelated user text. Quoted apparent markers inside AI advice remain advice. Do not hide arbitrary frontmatter, HTML comments or user lines named like metadata.

Default normal rendering must not show machine fields; default accessibility reading should not recite folded machine fields either. Keep raw-source inspection explicit and read-only. Do not falsify or replace the actual UITextView text to get a cleaner accessibility value. If accessibility presentation needs a separate readable value, native tests must still prove raw text storage is unchanged; update affected UI source assertions to inspect the explicit original-Markdown surface rather than weakening exact-block checks.

## I/O & Edge-Case Matrix

| Scenario | Input/state | Expected behavior | Error handling |
|---|---|---|---|
| Valid saved result | Canonical complete record, including old timestamp | Advice and clear AI label/reminder remain; JSON, internal IDs and raw mentor IDs do not occupy reading area | No network revalidation or write |
| Source inspection | More → AI discussion sources | Chinese names, topic, dates and edited state; raw Markdown appears only after explicit disclosure | No body callbacks, new draft, save or replacement |
| Multiple records | Adjacent results, Unicode before records, LF/CRLF | Every source retains correct block association and original UTF-16 ranges | No mixed source or swallowed body |
| Unsupported source | Broken JSON/header, missing/extra/nested markers, fenced or indented code | Preserve literal text | Never conceal an unrecognized region |
| Apparent markers in advice | Quoted metadata, headings or source-like body lines | Keep the complete advice | No provenance injection or broad regex hiding |
| Editing | Typing, IME, metadata selection, undo, table editing | Metadata remains folded; ordinary body still edits; marker edits still rejected; no offset remapping | Do not restyle marked text mid-composition |
| Source/day change | Sheet open, underlying identity/day changes | Close stale source view or bind to the correct current document | No cross-document source retention |

</frozen-after-approval>

## Code Map

- `ios/RijiCore/MarkdownPresentation.swift`: existing source-preserving ranges, hidden glyph syntax and code/table handling; add narrowly scoped saved-AI projection here or a small supporting Core file (register in Package.swift if new).
- `ios/RijiCore/AIResultStore.swift`: canonical AIResultMarkdown render and unchanged storage contract. `DiscussionProvenance` in MobileRoundtableModels.swift is the saved data type; do not call MobileResultValidation.validate for old display records.
- `ios/Riji/MarkdownTextView.swift`: TextKit hidden-glyph attribute, table delimiter height collapse and protected-range font pass; apply concealed metadata after ordinary active-line/protected styles so it cannot reappear or reserve JSON-sized height.
- `ios/Riji/DiaryBodyEditor.swift`: source, viewport, composition and callback preservation. Avoid editor replacement and removeAllActions for display changes.
- `ios/Riji/TodayDiaryView.swift`: native More menu and existing sheets; add a native sources sheet, keeping raw Markdown behind disclosure. Reuse ApprovedPageStyle and the existing four-tab layout. Keep any new view in an existing compiled file or register it correctly.
- `ios/RijiCoreTests/MarkdownPresentationTests.swift`, `ios/RijiNativeTests/NativeBodyEditorTests.swift`, `ios/RijiUITests/DiaryFlowTests.swift`: existing projection, edit-protection and full save/cancel/cold-return coverage to extend.

## Tasks & Acceptance

- [x] Implement conservative saved-source recognition and display ranges; cover every matrix parse row with meaningful Core tests.
- [x] Fold machine lines without changing source/selection/undo; verify layout compaction, accessibility presentation, native editing and marker protection.
- [x] Add More → AI discussion sources, Chinese metadata and explicit original-Markdown disclosure; keep source/day binding correct.
- [x] Adapt group and legacy save UI checks to preserve exact original-block verification through the explicit raw view, plus ordinary reading/source-sheet screenshots and cold return.
- [x] Root reviews actual task delta, runs relevant Core and serialized native/UI tests, visually inspects the isolated simulator and records actual limits.

Given a saved canonical AI result, when opening the diary, then advice is the reading focus and internal JSON/IDs are absent from normal visual and accessible presentation. Given the same document, when opening/closing source details, then actual source bytes, edits and exact AI block remain unchanged. Given malformed or unsupported content, when displaying it, then no user text is swallowed. Given an explicit save, when returning and restarting, then the exact reviewed block still exists only once in original Markdown.

## Implementation Notes

The user authorized this concrete follow-up in the existing dirty increment; no additional planning approval is needed. Root owns Xcode and simulator execution. Implementation agent may run focused package tests, but must not start Xcode/UI or operate the simulator; root will do so after production/test changes freeze. Other agents remain read-only reviewers.

Preferred product interaction: reuse the document More menu, action ID `diaryAISources`, source sheet title `AI 讨论来源`, close ID `closeDiaryAISources`, explicit raw disclosure with text ID `diaryAIRawMarkdown`. Normal source details are informational, never a claim that a local comment proves authenticity. Retain an AI nature reminder in the diary itself.

## Verification

Focused Core projection and existing AIResultStore tests; native editor projection/protection/undo/table tests; serialized group save/cancel/exact-block/cold-return and legacy save UI on Automated Tests device `<SIMULATOR_UDID>`. Use only UUID-isolated synthetic files. Do not touch the Development iPhone simulator's real-data setup or real diary. Keep first-pass failures and compare against the pre-task baseline, without resetting/staging the shared tree.


## Review Triage Log

All three formal layers returned before triage. Reviews use the seven-file task delta against the pre-task snapshot, because HEAD predates the shared increment; no index operations were performed.

| Finding | Verdict | Evidence and route |
|---|---|---|
| Safety pre-review: fence inside opaque context changes parser state | medium | Confirmed prior order processed fence before open HTML/comment context. Already corrected and Core regression passed before formal review. patch. |
| Safety pre-review: generic reader visual/AX mismatch and no raw entry | medium | VaultTextReader uses an empty projection for plain files and no sources menu. Corrected with default-off visual/AX folding, Today-only opt-in and byte-matched installed projection. patch. |
| Blind 1: concealed ordinary header lines remain writable | medium | BodyMarkdown protects marker comments, not generated topic/date/mentor rows. Native admission can mutate invisible rows and invalidate recognition. patch: reject edits intersecting displayed concealed ranges. |
| Blind 2: source capture before composition commit | medium | Adjacent commands call endEditing; source action captures model directly, while table IME may still belong to a cell editor. patch: use existing commit path before snapshot and compare dismissal with the captured snapshot. |
| Blind 3: HTML allowlist and tab attributes | medium | HTML starts currently recognize ten exact tags and only space attributes, so article/main or pre with tabs allow an example to fold. patch: conservative block-context recognition and tests. |
| Blind 4: nested HTML | medium | One tag state clears on an inner closing tag, so a following example still inside its outer container can fold. patch: preserve nested block context. |
| Blind 5: Unicode paragraph/line separators | medium | NSString.getLineStart splits separators that AIResultMarkdown.render retains within quoted advice. The canonical comparison then rejects a valid generated block. patch: CR/LF-only source line splitting with exact ranges. |
| Blind 6: repeated same-topic source association needs excerpt/jump | low | Sources are already ordered by original block order and numbered; whole source is inspectable. Identical same-day topics can still require comparison. Uncommon cosmetic enhancement requiring new interaction/data; rejected under low-impact review rule, no patch. |
| Blind 7: no direct return-to-discussion link | false | This change adds local informational source inspection and removes no existing discussion navigation. Complete group history remains available through mentor home; cited source page has no previously promised back-link to preserve. No verified new broken behavior at this location. |
| Blind 8: edited flag wording after later diary edits | medium | Advice remains editable but saved provenance is immutable, so 用户编辑 could be read as current state. patch: clarify 保存前用户编辑 without altering source schema. |
| Edge 1: unsupported/tab HTML | medium | Same verified bad outcome as Blind 3; same patch group, independently recorded. |
| Edge 2: uncommitted source capture | medium | Same verified body/table composition path as Blind 2; same patch group, independently recorded. |
| Edge 3: Unicode separators | medium | Same renderer/parser newline discrepancy as Blind 5; same patch group, independently recorded. |
| Root follow-up: ordinary Return inside advice invalidates full-block recognition | medium | DiaryBodyEditor has no quote-continuation handler; Return creates a plain advice line and the whole-block canonical comparison then reveals JSON. The approved boundary requires canonical generated header, with ordinary advice editable. patch: retain header-only concealment for edited body formatting, preserving every body line and paired-marker ambiguity fallback. |
| Verification gap: source sheet stale after underlying update is untested | medium | Pre-verified search finds only manual-close save UI flows; deleting automatic invalidation would escape them. patch: native production-host source update/dismiss/reopen regression. |

Implementation agent re-engaged for the patch groups; no unresolved intent or architecture choice requires a plan loopback. Source summary deliberately remains local provenance rather than proof of authenticity.


### Native execution follow-up

The first Xcode run compiled successfully but four Native methods and both UI save methods failed; the result and recording remain in `native-ui.xcresult` / `native-ui.log`. Fixes are verified by a new run, never by rewriting the first result.

- The literal AX getter returned UIKit nil in a native host. The explicit projection now falls back to actual source when not folding; raw text storage remains unchanged.
- UITextView programmatic UIKeyInput insertion can bypass its delegate. Shared concealed-range admission now also protects insertText/deleteBackward entry points, keeping ordinary typing on the original path.
- Native source-host tests now wait for viewDidAppear and inspect child presentation controllers, with named wait diagnostics.
- The UI failure was not dismissal: the recording shows successful expansion. The archived AX node contains complete raw text but inherits the parent DisclosureGroup identifier. The disclosure is now a native toggle button followed by a sibling read-only Text, preserving distinct accessible IDs and the same interaction.


Native follow-up evidence: `native-source-lifecycle.log` proves the hostless presenter is stuck in transition with `view.window == nil`, while the captured source matches the model. The final Native layer verifies the real mounted Today onChange/session invalidation and composition capture, without claiming visual presentation. Complete App UI tests independently cover menu, expanded raw text and close. The final two method names explicitly describe snapshot/session state. `native-source-state.log` passes those two contracts; all 13 selected Native methods pass in the final combined run. No functionality assertion was skipped.


## Final Verification Results

- Core: 64 tests passed (MarkdownPresentation 29, AIResultStore 11, BodyRevisionStore 24); `output/ai-source-reading-2026-10-17/core-final.log`.
- Final Xcode: 13 Native and 2 full UI flows passed, 0 failures; `final-native-ui.xcresult` and `final-native-ui.log`. Earlier failed result bundles remain intact.
- UI verifies distinct raw-source identifier, Chinese summary, explicit raw disclosure, close without content changes, cancellation, exact block only once and cold-return byte equality for both group and legacy paths.
- Native verifies source session invalidation through mounted Today/file refresh; visible sheet behavior belongs to the complete App UI layer, not hostless UIKit transitions.
- Root inspected diary, source summary and expanded raw view; left Automated Tests simulator open at normal reading. Existing synthetic file remains 1169 bytes with unchanged SHA-256. All 106 monitored source/config files remained stable during the final run.
- Evidence: `docs/testing/ios-ai-source-reading-2026-10-17.md`. No new product/architecture deferrals; actual VoiceOver, real connection, iCloud propagation and physical-device acceptance remain separate unchanged validation boundaries.
- No commit: the authorized task explicitly retained the shared dirty increment locally; existing index and unrelated work were preserved. No push, Air operation, phone install or real diary mutation.
