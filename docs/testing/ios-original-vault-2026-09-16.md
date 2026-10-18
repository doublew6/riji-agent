# iOS original diary collection and tables — 2026-09-16

Status: implementation and local verification complete; updated build installed and launched on the connected iPhone. The user confirmed correct table rendering on the physical phone. The independent two-phase physical iCloud probe has passed; private-home confirmation and the remaining production-write gate are still separate.

## Scope and baseline

Issue #54 continues personal MVP file access and original-template work. The existing navigation and native editor remain the starting point. The previous app opened sandbox diary files at home and exposed selected folders only through a read-only browser. Its renderer did not implement Markdown tables.

The branch is `codex/54-ios-original-vault`; baseline HEAD is `77c2c9b231d4e0280b18e3a0ca7270ba4616d5b5`. Existing mixed changes and staging were retained. Source snapshots, private integrity manifests and local build/test evidence are in ignored `output/ios-vault-2026-09-16/`. No private template content, journal bytes, bookmark, device identifier or signing identity is included in this record.

## Expected manual flow

1. Open the diary tab. Without an authorized collection, select the original diary folder with the system folder picker. Cancel should keep the previous source and local records.
2. After selection, the app should open today's original daily file. If that file does not exist, it should show the original daily template without creating a file merely by browsing.
3. Open a historical date stored inside a daily subfolder. Verify it is the existing original file, with no new top-level duplicate.
4. View a four-column table containing empty first cells and mixed text/numbers. Header and cells should align in both diary home and the directory preview. Long cells must remain accessible on a narrow screen.
5. Return to the app after changing a synthetic source externally. With no draft it should refresh; with a draft it must retain the source and proposed edit separately.
6. Relaunch. The authorized collection should restore. A revoked or unavailable folder should offer retry/reselection without silently showing the bundled template as an original diary.
7. Open the explicitly separate previous App diary space. Its files, imported templates, recordings and recovery records must still be available.

## Verification matrix

| Area | Required evidence | Result |
| --- | --- | --- |
| Source binding | Synthetic selection, restore, nested dates, duplicate dates, template/no-file behavior | Passed Core/State and original-home UI tests |
| File safety | Coordinated read/conditional write, concurrent change/create/delete, failure and recovery isolation | Passed synthetic adapter tests, including callback containment, interruptions and oversized edits |
| Cloud availability | Unavailable/placeholder/conflict states never treated as a missing diary | Synthetic placeholder/error handling passed; real iCloud conflict/download behavior pending |
| Table parser | Valid/invalid tables, empty cells, escaped pipes, code/wiki aliases, CRLF and Unicode | Passed, including short/extra data cells and cached selection ranges |
| Native presentation | Real layout, narrow width, source preservation, composition/undo, table gesture exclusion | 23 native tests passed; actual horizontal/vertical gesture and cell-tap UI tests passed |
| App UI | Original home and tree preview, source choice, Settings accessibility, relaunch | 7 final focused UI tests passed; 2 additional unaffected regressions passed in the preceding run |
| Existing data | Normal simulator records and original diary/template integrity | All previous simulator files retained identical hashes; original-file comparison qualified below |
| Physical iCloud | System folder authorization, independent test library, cross-device change and conflict | Updated build installed and launched on the phone after reconnection. Folder authorization and independent iCloud tests remain pending |

## Physical write gate

Original personal files remain read-only until the development-plan 01/03 gate is satisfied. A dedicated synthetic iCloud library must be used to verify actual phone authorization, download behavior, local saves, Mac propagation and simultaneous edits. Simulator/local-file tests cannot substitute for that result. No test should manufacture a conflict in the private original diary library.

Local device persistence, iCloud propagation, Air reading and AI processing are separate states. This change does not connect Air, alter its services, move private memories, or enable model calls.

## Execution evidence

Private machine evidence remains in ignored `output/ios-vault-2026-09-16/`:

- `swift-full-final.log`: full SwiftPM Core/State suite, **334 tests, 0 failures**.
- `xcode-final.log`: **23 NativeBodyEditorTests and 7 DiaryFlowTests, 0 failures**. UI coverage includes original restoration, nested history, initial folder selection, Settings, generic tree browsing, actual cell taps, table horizontal scrolling and diary vertical scrolling.
- `ui-test.log`: earlier 7-test pass, including template changes across tabs and unchanged-source Markdown export, in addition to overlapping navigation checks.
- `review-fix-state-core.log`: 91 targeted tests after source/state review fixes, superseded by the full 334-test pass.
- Initial failed native/build logs are retained as failed evidence; the regressions were corrected and verified by later successful runs.
- `home-picker-final.log`: initial-home UI recheck passed after adding picker-error feedback.
- `iphone-build-final.log`: final signed generic-iOS build with the existing private signing configuration. This is build evidence, not device-install evidence.
- `table-fixed-synthetic.png`: final native visual check with synthetic content only.

All existing normal simulator files retained identical SHA-256 hashes. The original-file metadata comparison found an mtime-only difference with unchanged size/inode; the other checked records were unchanged. Exact personal inventory counts remain in the ignored integrity manifest. No original-file writes were performed by this task. Metadata comparison does not prove byte identity for every iCloud original. Private integrity manifests remain ignored.

Review fixed selection transaction ordering, callback containment, source-ID aliasing, transition reentrancy/timezone, placeholder classification, oversized writes, UI errors, partial table rows, inline cell styling, redundant caret parsing and unbounded cell allocation. Two independent review layers completed. A third supplied a reproduced access-boundary finding before terminating without a final report; that finding was fixed and tested, but that layer is not recorded as completed.

In the previous local diary space, tapping a cell reveals the original table source in the native editor; Chinese composition and undo remain available. This does not enable external-original editing. At most 1,024 cells are projected into native grid views at once; larger tables fall back to readable source.

The simulator is returned to normal mode after checks. Existing staged work is preserved; no publication, Air update, migration, app uninstall or device erase was performed.

## Device update after reconnection

The user reconnected the existing paired iPhone after local verification. The signed build passed signature/profile validation, then updated the existing `com.riji.local` installation in place. Before installation, the app's Documents and Application Support were backed up locally. All pre-existing data files had identical SHA-256 hashes after installation; none were missing. The device reported successful installation and successful foreground launch. No app uninstall, test namespace, private-template migration or Air change was used.

Private installation receipts, backup and integrity evidence are under `output/ios-vault-2026-09-16/iphone-update.*/`. Device identifiers, signing details, bookmarks and app data stay out of this shared record. Installation/launch is not evidence of successful folder authorization or iCloud synchronization; the original collection remains read-only pending the independent test-library gate.


## Continued device validation

The user confirmed on 2026-09-16 that Markdown tables display correctly on the actual iPhone. The next bounded increment is the [isolated physical iCloud probe](ios-icloud-probe.md). Existing phone app data and its normal folder bookmark were backed up; the probe uses its own namespace and a newly generated synthetic iCloud folder outside the private journal vault. Compilation and local checks do not establish phone/iCloud propagation. Original writes remain read-only until the development-plan gate is actually satisfied.

The subsequent independent physical probe passed both phases: exact save/retry, new-process folder authorization, phone↔Mac propagation, and stale modify/delete/create refusal with retained drafts and no rejected-operation receipts. All existing phone app data files remained byte-identical, and normal Riji was relaunched. Full physical gate limits and evidence are in [the probe record](ios-icloud-probe.md); original private writing remains read-only.
