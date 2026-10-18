---
title: 'Keep the diary viewport stable when committing input'
type: 'bugfix'
created: '2026-09-17'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** On the installed iPhone build, confirming typed diary input leaves the insertion point in the correct source location but scrolls the page to the top. Repeatedly scrolling back interrupts writing.

**Approach:** Preserve the current writing viewport across native input commitment, Markdown presentation updates, autosave and keyboard dismissal. Keep native composition, selection and undo, original source bytes and document navigation intact. Given a long diary with the caret near its end, when Chinese composition is committed or input is confirmed, then the relevant text remains visible and continued typing edits the same location. Given deliberate scrolling or switching days, when the corresponding layout completes, then no stale restoration overrides the user's new position.

</frozen-after-approval>

## Implementation Notes

- This is a focused continuation of the user's installed-app feedback, within the existing authorized mixed worktree. Prior edits and staged voice work are preserved. Baseline revision: `f8d73a8395dae08a85f70efd8ff8927e88de0806`. File snapshots: ignored `output/ios-editor-viewport-2026-09-17/before/`.
- Small reversible footprint: `DiaryBodyEditor.swift`, `MarkdownTextView.swift`, focused native/UI regressions and acceptance notes. No storage migration, real-vault fixture writes, backend/service/network change or public deployment.
- The native coordinator styles the full text storage and invalidates glyphs, then restores a numeric offset immediately; later UIKit layout remains a candidate cause. Existing native tests assert composition/caret bytes mostly in short documents and do not establish long-document viewport stability after settled layout.
- Reuse the existing test-only isolated UI namespace, native hosted harness, source-preserving projection and session viewport cache. Confirm the failure before choosing the smallest repair.
- The separate backend question is an explanation/product-direction clarification: a normal app should use a preconfigured HTTPS origin and unobtrusive owner authentication, while Air remains the backend. The current private Tailscale route is not that finished experience. Do not weaken authentication or publish the existing service as a shortcut.
- Reproduced the old-code defect after establishing a valid long-document starting viewport: native commitment changed offset from approximately 4362 to 1024 while source bytes/caret remained correct. The first exploratory run failed its starting-position prerequisite and is not the regression proof.
- Repair: complete the invalidated TextKit document layout and view layout before restoring the captured selection/offset. No delayed callback, scrolling override, source rewrite or new API is introduced. The exact same long composition regression passes afterward.
- Verification: the full then-current 25 native editor tests and four actual UI journeys passed; a focused review rerun passed the strengthened caret-after-newline assertion and a new long-input timing test. An additional 90-heading timing test passed. This is 27 distinct native editor cases with passing evidence across runs, not a single full 27-case run. Existing UI screenshots show continued text and caret at the bottom after actual original-file autosave. The signed phone build and signature/provisioning checks passed. Its source hashes match the fixed production files; subsequent changes are tests/docs only.
- Independent oneshot review found no demonstrated new production logic defect. No other review layers are configured for this small route. The two verification suggestions were addressed below. Extremely large-document performance remains a bounded follow-up, not silently claimed solved.
- Installation remains pending because the previously paired phone is unavailable. Candidate: ignored `output/ios-editor-viewport-2026-09-17/iphone/Riji.app`; retain the data-preserving update procedure. No Air configuration/service change, external message, commit or push occurred. The source/test files are still mixed with earlier untracked feature work, so committing them wholesale would violate the project rule to preserve unrelated increments.

## Review Triage Log

- Low, patched: the earlier 2000-heading test did not measure actual input. Added actual insertion/layout timing with retained exact-text assertions. On this Simulator, 13 samples averaged 60 ms (maximum 86 ms) for 90 headings. The deliberately extreme 2000-heading case averaged 682 ms (maximum 716 ms); an isolated pre-fix source copy averaged 589 ms (maximum 742 ms). These are observations, not a performance guarantee or proof that the added layout work has no cost. The pre-existing full-document formatting bottleneck and its additional layout cost are recorded as follow-up.
- Low, patched: after continued input/newline the new native regression checked only offset. Added the actual caret-rectangle intersection check after settled layout; the focused rerun passed. No production change was required.
