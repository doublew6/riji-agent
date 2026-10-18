# Diary input viewport correction

The user reported that confirming input on iPhone left the caret at the intended
source location while scrolling the page toward the document start. This was
reproduced with a synthetic long Markdown diary, native Chinese marked-text
commitment, a table, CRLF and combining Unicode. After commitment the old code
moved the vertical offset from about 4,362 to 1,024 points; the caret was no longer
visible although the expected text and selection survived.

`DiaryBodyEditor.Coordinator.style` now completes TextKit layout before restoring
the selection and offset. Previously the offset was restored against an
incompletely laid-out document. The correction does not defer a restoration task,
force every edit to scroll to the caret, alter Markdown bytes or change storage.
There is no new pending callback that could override subsequent user scrolling
or another date.

## Verification

- The pre-fix native regression failed on offset and actual caret geometry. The
  same case passed with the correction, including composition, save-state refresh,
  continued input and keyboard dismissal.
- All 25 then-current native body-editor tests passed, including source fidelity,
  composition, undo, task toggles, table layout and document position restoration.
- Four actual Simulator UI journeys passed: confirmed input with autosave retaining
  the bottom position; original-file edits and cold reopening; table editing/undo;
  date and destination navigation retaining the reading position. The screenshot
  of continued input and its visible caret was inspected.
- The signed iPhone build passed and `codesign --verify --deep --strict` succeeded.
  Bundle identity and provisioning still target the existing app and phone.
- Independent review led to two focused verification additions: actual caret
  visibility after the next line, and actual continuous-input timing. Both passed.
  In total, 27 distinct native editor cases have passing evidence across runs;
  this was not a single full 27-case run.

The 90-heading synthetic diary averaged 60 ms per insertion/layout (13 samples,
maximum 86 ms) on this Simulator. The deliberately extreme 2000-heading diary
averaged 682 ms (maximum 716 ms), versus 589 ms (maximum 742 ms) in a separately
built pre-fix source copy. Large-document full-body formatting is already costly,
and the new synchronous layout is not free. Performance optimization is recorded
separately; these observations are not a physical-phone latency guarantee.

Evidence is local and ignored under `output/ios-editor-viewport-2026-09-17/`:
`baseline-scrolled.log`, `layout-fix-native.xcresult`, `final-native-ui.xcresult`,
`ui-evidence/`, `phone-build.log` and `release-manifest.json`.
The review and timing evidence is in `review-native`, `baseline-timing` and
`daily-timing` logs/result bundles. The baseline timing project was isolated under
`/tmp`; the working production correction and signed candidate were not rolled back.
The first exploratory test did not establish its initial scroll position; that
run is retained separately as `baseline-native`, not used as the regression proof.

## Phone handoff

This correction was included in the later managed-connection build and installed
on the personal phone on 2026-09-17. A fresh backup and before/after hash comparison
confirmed all original files survived the overlay installation. iOS rejected
first launch because the phone was locked, so physical keyboard acceptance remains
pending. The current signed artifact is
`output/ios-managed-connection-2026-09-17/iphone/Riji.app`; the earlier viewport-only
candidate is retained as historical evidence and should not replace that newer app.
See the [managed-connection record](ios-managed-connection-2026-09-17.md).

On the updated phone, open a long diary, scroll to the bottom, confirm Chinese
input several times, press Return, continue typing and dismiss the keyboard.
The active text should stay visible. Check saved content and native undo separately.
This final physical keyboard check remains pending; Simulator evidence does not
claim the user's exact keyboard journey passed on the phone.
