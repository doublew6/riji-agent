# Original Markdown editing — 2026-09-17

Issue #54 now connects the normal selected/restored vault to the existing native
body editor. `OriginalVaultAccess` still defaults to `.readOnly`; only the normal
manual-editor adapter explicitly requests `.coordinatedUserEdit`. The synthetic
probe policy remains available to its existing isolated harness. Legacy append,
voice insertion and AI/workflow entry points remain unavailable in original mode.

A local save retains the source-bound draft and revision before coordinated
compare/replace, verifies exact source bytes, and retains a receipt for retries.
Existing nested paths remain the target. Missing dates display the original daily
template and create no file until the first edit. Original errors now retain their
source/download/conflict explanation rather than becoming a generic storage error.
The UI says “已保存在此设备” and separately explains that iCloud propagation needs
checking on the other device.

Directory identity is refreshed without Foundation URL resource caching. A fault
regression caught the old cached identifier accepting a copied replacement at a
moved collection's previous path; the corrected adapter rejects that replacement.

## Local verification

All tests use temporary synthetic Markdown and isolated app test namespaces.
No private journal was edited. No app data was erased, uninstalled or migrated;
icon/signing/project settings, the four tabs and the original iCloud probe evidence
were left intact. No Air deployment or publication was performed.

```sh
swift test --package-path ios --scratch-path /tmp/riji-ios-swift-build \
  --filter 'OriginalVaultAccessTests|OriginalVaultStateTests'
swift test --package-path ios --scratch-path /tmp/riji-ios-swift-build
xcodebuild -project ios/Riji.xcodeproj -scheme Riji -configuration Debug \
  -destination "platform=iOS Simulator,id=$RIJI_SIMULATOR_ID" \
  -derivedDataPath "${TMPDIR:-/tmp}/riji-ios-manual-derived" \
  CODE_SIGNING_ALLOWED=NO build
```

`RIJI_SIMULATOR_ID` is the existing Riji Development iPhone; no new device is needed.
The focused run passed **32 Core/State tests**. The first focused run intentionally
remains in `/tmp/riji-original-editing-focused.log`: its new moved-root regression
failed, establishing the identity-cache defect before the correction. The passing
rerun is `/tmp/riji-original-editing-focused-final.log`.

Full SwiftPM and normal Simulator build results are recorded below after execution.

Coverage includes exact Unicode/CRLF/table/frontmatter bytes at nested paths,
bookmark cold restoration through normal `VaultBrowserStore`/`DiaryViewModel`,
first-edit creation, concurrent creation, external modification/deletion, an actual
placeholder, actual file permission denial, moved/replaced roots, and a simulated
unresolved-version error. Before/after replacement and before/after receipt failures
recover the same operation through a newly constructed store, with one revision.
Failed saves keep the visible input and block date, tab and source transitions.
The OS-version error injection tests application behavior; it is not an actual
`NSFileVersion` conflict experiment.

The added native UI flow is
`DiaryFlowTests/testOriginalHomeManualEditingSavesAndColdReopensExactSource`.
It edits the synthetic original and a nested historical note, traverses all four
tabs, cold-relaunches and compares exact source text. Existing original-home/table
UI expectations now require the device-local saved status. Focused actual UI
execution belongs to the parent verification step and is not claimed by a build.

## Manual handoff and remaining physical checks

1. Open the already authorized original folder; tap ordinary body text, type a
   small change and wait for “已保存在此设备”. Reopen the same date, then the same
   nested file from Directory. Verify unchanged Markdown properties, tables,
   links, CRLF/Unicode and the exact edited text.
2. Check that exact file on Mac after iCloud propagation. Local saved status alone
   does not establish propagation or Air/AI processing.
3. Browse an absent date. Verify that no file appears until typing; the initial
   body comes from the original template, including unknown expressions.
4. With an isolated synthetic file, change/delete/move the source while a phone
   draft exists. Attempt a date/tab/source change. Riji must keep the current
   input and report the source issue; no stale overwrite or deleted-file
   resurrection is permitted. For a content conflict, inspect both versions and
   explicitly confirm a merged result only after checking the current source.

The prior [two-phase physical iCloud probe](ios-icloud-probe.md) passed phone↔Mac
byte propagation, source-bound rejection and cold bookmark restore. Its existing
evidence is unchanged. This increment was developed without the phone attached:
physical offline editing/download eviction, permission revocation or folder moves,
actual unresolved OS conflict versions, app/background/write interruption,
Chinese composition/caret behavior and device timing remain separately pending.
The synthetic and Simulator results do not close those real-device checks.


## Review loop 1 repairs

Local recovery validation now checks the retained collection identity, allowed daily
path and date without touching the live source. Source commits and conflict
resolutions still validate the actual current collection/path and exact bytes.
This lets later keystrokes persist even after a file or the whole root becomes
unavailable, without giving the retained record permission to write another source.

The selected bookmark now also retains a bounded local recovery reference before
editing is enabled. If source restoration fails at cold launch, the source-bound
local draft remains visible for text selection/copy and **导出本机草稿**. Its export
uses a `-recovery.md` filename and explicitly remains unsaved to the source. The
reference cannot grant file access. A fresh picker selection for the same collection
updates authorization before retrying the existing edit; an unrelated collection
cannot consume that pending input. The failed-save page exposes this picker directly.
Deleted or moved-to-a-different-relative-path files still fail the live write checks.

Coordinated replacement binds the descriptor used for traversal to the collection's
actual device/inode identity, rechecks the current named parent before rename, and
preserves the existing mode, ACL and extended attributes on the temporary inode.
Metadata-copy failure abandons the temporary file and preserves the original.
The root-substitution reproducer is now covered both at the coordinator callback
and immediately before replacement. An actual filesystem test preserves mode 0640,
a synthetic xattr and a macOS ACL through the write.

Focused verification passed **98 tests**, zero failures, using:

```sh
swift test --package-path ios --scratch-path /tmp/riji-ios-swift-build \
  --filter 'OriginalVaultAccessTests|OriginalVaultStateTests|BodyRevisionStoreTests|BodyEditorStateTests|VaultBrowserTests'
```

The initial 98-case passing log is `/tmp/riji-original-editing-loop1-focused-v3.log`; earlier logs
retain the changed old expectation and test-compilation corrections. Tests also
cover original nested conflict review, retained merge cold restore, confirmed merge,
source identities and both original versions. The added native UI case is
`testOriginalTableInPlaceEditUndoAndColdReopenPreservesExactSource`; it edits inside
a rendered original-table cell, undoes the change, edits again, waits for local save
and cold-reopens with exact source comparison. Parent verification owns UI execution
and full regression results. The ordinary Simulator build log is
`/tmp/riji-original-editing-loop1-build.log`.

[The manual guide](ios-manual-acceptance.md) now distinguishes editable daily notes
from read-only templates, weekly/monthly files and general file previews. Real-device
permission revocation, unresolved OS conflict versions, iCloud metadata behavior and
interruption acceptance remain pending; synthetic metadata/ACL and Simulator tests
are not substituted for those cases.

The final code also preserves a retained draft's old relative source ID after cold
start when another program moved the file. Later typing remains bound to that old
source; it cannot silently retarget the moved file. Its new regression passed in
`/tmp/riji-original-editing-loop1-focused-final.log`. That final 99-case run passed
98 cases; the existing continuous-typing timing test exceeded its checkpoint-count
bound (6 versus 5) while the build ran. Its isolated rerun passed on unchanged production code, recorded in
`/tmp/riji-original-editing-loop1-timing-rerun.log`; the failing run remains evidence.
No production change was made to that unrelated timing path.

The final ordinary Simulator build passed:
`/tmp/riji-original-editing-loop1-build-final.log`. No Xcode UI tests were run by the
implementation task; the parent runs the newly wired table edit/undo case and the
full suite against this frozen candidate.

The parent UI run reached exact in-place table typing and original-file save, but
XCTest's synthesized Cmd-Z did not change the text or caret. The test now taps the
visible keyboard toolbar's Undo button, retaining the exact source and cold-reopen
assertions. No production editor change was needed: the targeted native original-
file test confirms autosave retains both Undo and Redo and checks the exact CRLF,
Unicode and empty-cell bytes in both the editor and the source file. That new test
and two existing presentation/undo regressions passed (3 tests, 0 failures) in
`output/ios-development-2026-09-17/editor-loop1-native-undo-final.log` with matching
`.xcresult`. The earlier `editor-loop1-native-undo.log` records an incomplete synthetic
fixture (missing templates directory), corrected before the final run. Parent owns
the toolbar-based UI rerun; this native run does not establish hardware Cmd-Z support.
