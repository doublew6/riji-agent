---
title: Open original diary collection and render its tables
type: bugfix
created: 2026-09-16
status: done
route: dispatch
baseline_commit: 77c2c9b231d4e0280b18e3a0ca7270ba4616d5b5
review_loop_iteration: 0
context:
  - PROJECT_GUIDE.md
  - docs/PRD.md
  - docs/product/ios-personal-mvp-development-plan.md
---

<frozen-after-approval reason="Confirmed personal MVP PRD and explicit current correction request">

## Intent

The user sees literal Markdown tables and an unrelated bundled template on the iPhone home instead of the authorized Obsidian/iCloud diary collection. Home must open the original collection, including nested historical dates, and display its tables legibly while preserving all original bytes. This implements issue #54, personal MVP 01/03; the latest request authorizes this correction without repeating the already approved product-design checkpoint.

## Boundaries & Constraints

Always preserve the four peer bottom tabs, native continuous editor, Chinese composition, undo, source provenance and source-bound recovery. Select the original folder once using the system picker; restore its bookmark on launch. Missing authorization shows a local folder action, never a fake original template. Existing sandbox diaries remain separately reachable and unchanged. Existing selected browser bookmark is reusable after layout validation.

Do not mutate the real vault during development or enable real-vault writes before the independent physical iCloud test-library gate in the development plan passes. Implement and verify the coordinated writer against synthetic fixtures; until device acceptance is recorded, original-vault reading remains available with an honest read-only state. No migrations, uploads, AI calls, Air changes, publication, commits, or removal of existing app/recording data. Never embed personal templates/paths in source or fixtures. Do not refactor unrelated workflow/voice internals.

## I/O & Edge-Case Matrix

| Scenario | State | Expected behavior | Failure |
| --- | --- | --- | --- |
| First launch | No authorized collection | Inline folder selection; old local diaries explicit | Cancel retains state |
| Restore/read | Valid collection with daily and templates | Today's original bytes; historical dates under daily recursively | Access/scan/duplicate-date ambiguity visibly blocks, never substitutes |
| New date | No original diary | Render original daily template without creating a file | Missing/unavailable template stays recoverable; unknown expressions retained |
| Edit probe | Synthetic authorized folder | Source-bound draft, coordinated compare/write, byte-verified receipt | External change/delete/create keeps both; no silent overwrite |
| Isolation | Change folder or return to local | Recovery keyed to stable vault identity and original relative path | Old drafts never target another collection |
| Availability | Cloud placeholder, conflict version, or revoked access | Retry/download or conflict state, retain original/draft | Never classify unavailable as absent |
| Tables | Valid header and delimiter rows | Aligned cell grid, readable empty cells/Unicode, usable narrow width | Invalid/fenced/protected constructs stay literal |
| Native editing | Tap rendered table then compose/select/undo | Reveal active table source in place if necessary; same native save path | No byte rewrite on render, no date swipe from table |

</frozen-after-approval>

## Code Map

- `ios/Riji/DiaryViewModel.swift`: home store fixed to sandbox; preserve native body state machine. `start/load/navigate/directoryClosed/resume` need source binding and refresh.
- `ios/Riji/VaultBrowserStore.swift`, `VaultBrowserModel.swift`: existing bookmark and read-only tree. It currently ignores coordinator callback URLs. Share authorized collection identity with home, preserve preview behavior and cancel semantics.
- `ios/RijiCore/LocalJournalStore.swift`, `BodyRevisionStore.swift`, `JournalFileSystem.swift`: reuse body drafts, recovery, and preservation rules; never simply point current initializer to external daily because it creates workflow directories. Introduce a bounded document access boundary for original source reads/conditional writes while state stays in sandbox. Guard unadapted append/voice/workflow writes in external mode.
- `ios/RijiCore/WorkflowModels.swift`: reuse safe `TemplateVersion` substitution; do not import original template into a new authority. Recognized layout: selected root has `daily/`, `templates/daily.md`; selected parent may contain exactly one `riji/` child with that layout. Respect `.obsidian/daily-notes.json` when safely present. Original observed layout has both `daily/YYYY-MM-DD.md` and `daily/YYYY-MM/YYYY-MM-DD.md`; search existing files first, new days use configured daily folder; ambiguity fails.
- `ios/RijiCore/LocalVaultReader.swift`: bounded no-symlink traversal; extend download/placeholder detection where required. No unbounded vault scan.
- `ios/RijiCore/MarkdownPresentation.swift`, `ios/Riji/MarkdownTextView.swift`: missing table projection. Keep UTF-16 source ranges, parse escaped pipes/code/wiki aliases, retain empty cells and CRLF, exclude protected/fenced/indented code. Prefer native display projection, with active-table source reveal to retain native IME and undo. Geometry responds to width and Dynamic Type; wide tables have safe readable scrolling or wrapped cells.
- `ios/Riji/DiaryBodyEditor.swift`, `VaultFileView.swift`: both use native renderer. Only necessary layout invalidation changes; never HTML round-trip serialization.
- `ios/Riji/TodayDiaryView.swift`, `DiaryDirectoryView.swift`, `RijiNavigationView.swift`, `RijiApp.swift`: inline folder entry, actual source label, selected history to home, Settings source state and explicit old-local access. Main-tab transitions must work before a diary is ready.

## Tasks & Acceptance

- [x] Add bounded original-document access and stable recovery namespace; preserve sandbox behavior and gate real writes.
- [x] Connect home, persisted folder selection, recursive history, original template, retries and external refresh. Keep unavailable states truthful and local data accessible.
- [x] Implement table projection shared by editor and reader, with source-safe native editing and date-gesture protection.
- [x] Add synthetic Core/State/native/UI tests for matrix; update package/Xcode registration for new files and test sources.
- [x] Update current implementation/manual records with exact verified scope and remaining physical gate.

Acceptance criteria:
- Given a previously selected valid collection, when relaunching, then today's original diary appears without importing/copying it or requiring AI setup.
- Given a nested historical file, when selecting its date or tree row, then the original file opens without creating a duplicate top-level diary.
- Given a table with empty cells and mixed Unicode, when opening either home or preview, then a legible grid appears and rendering does not alter source bytes.
- Given a pending local or external edit, when navigating or changing source, then retention/save failure keeps the original source and visible input.

## Implementation Notes

Mixed working tree is the authorized ongoing iOS work; preserved and snapshotted under ignored `output/ios-vault-2026-09-16/ios-before.tgz`. New issue branch isolates the intent without resetting/staging any old changes. Scope, access and original-template choice are already confirmed; no new product decision is pending. Real device is currently unavailable; user has been asked to reconnect. Do not claim physical iCloud acceptance from simulator tests. Parent will handle simulator data backup, final device/manual verification and final documentation evidence; implementation agent owns code/tests and can leave execution of simulator UI suites to parent. Do not start simultaneous xcodebuild or simulator sessions. Core/State tests may run using the fixed scratch path. If an equivalent smaller access design better preserves safety, document it; do not relax the frozen behavior.

## Spec Change Log

## Review Triage Log


Review uses the exact per-turn source snapshot because the HEAD diff includes substantial preserved work from earlier requests. `output/ios-vault-2026-09-16/review.diff` includes new files without staging. Blind and edge layers completed; verification layer terminated before its final report, but supplied a reproduced adapter-boundary finding. Its unfinished broader coverage is not claimed as passed. The parent verified callers and the native/UI evidence directly.

| ID | Verdict | Evidence and route |
| --- | --- | --- |
| Blind 1 | high | Callback-parent roots bypass authorized-root traversal for intermediate components; independent synthetic reproduction confirms it. Patch callback path validation and anchored traversal. |
| Blind 2 | medium | connectOriginal persists before changeStore/open can fail. Patch candidate validation and selection commit ordering; preserve old bookmark on failure. |
| Blind 3 | high | Parent configuration can point outside prefix and sourceID drops a real component. Patch configuration containment before normalization. |
| Blind 4 | medium | scan throws for a non-date file placeholder despite an available requested date. Patch to ignore known non-date files, preserving uncertainty for directories. |
| Blind 5 | low | changeStore changes store but not today. Patch recomputation with next.timeZone. |
| Blind 6 | medium | Settings importer ignores failures and its form lacks model error feedback. Patch initiating surfaces with actionable errors. |
| Blind 7 | medium | Row count mismatch stops a valid table; preserve source and pad/truncate display cells. Patch parser. |
| Blind 8 | medium | UILabel receives raw cell source; confirmed visible escaped pipes/wiki delimiters. Patch using existing inline projection attributes. |
| Blind 9 | medium | Coordinator caches full presentation but static activeLines reparses it on caret movement. Patch by passing cached table ranges. |
| Blind 10 | medium | Each table allocates all cell views; input ceiling permits excessive allocations. Patch a fixed display-cell budget with native-source fallback and test. |
| Edge 1 | medium | Same failed-selection bookmark ordering as Blind 2; patch same transaction. |
| Edge 2 | high | Same callback-root traversal defect as Blind 1; patch same anchored path boundary. |
| Edge 3 | high | Same sourceID collision as Blind 3; patch collection containment. |
| Edge 4 | medium | prepareSourceChange awaits before isBusy is set; two tasks can enter. Patch transition lock before suspension with concurrency test. |
| Edge 5 | low | Same today timezone drift as Blind 5; patch successful transition. |
| Edge 6 | medium | Writer checks maximum size only when reading back after replacement. Patch pre-write size validation and retain source. |
| Edge 7 | medium | Generic browser folder selection is replaced by home layout validation even in explicitly selected old-local mode. Patch local-mode generic browsing; original-home layout validation remains. |
| Verification partial | high | Synthetic store-level cached nested path reproduction reads outside authorized folder after ancestor replacement. Patch covered by Blind 1; add store-level test instead of relying only on helper tests. |

The fixes preserve the frozen intent and existing read-only physical gate. They correct demonstrated local conditions without broadening product scope. Parent owns table patches; the original implementation agent is re-engaged for source-access/state/UI error patches.

## Verification

- `swift test --package-path ios --scratch-path /tmp/riji-ios-swift-build` — relevant Core/State cases pass.
- Xcode build and RijiNativeTests/RijiUITests on existing `Riji Development iPhone`, fixed `${TMPDIR}/riji-ios-manual-derived`, no parallel test devices.
- Synthetic independent iCloud test library on the actual phone before enabling any original-vault writer. Never exercise conflicts against personal originals.

### Final evidence and physical boundary

Local implementation verified: full SwiftPM **334/334**, native **23/23**, final focused UI **7/7**; two additional unaffected UI regressions passed earlier. Actual rendered-cell taps preserve source and use native editing; table gestures pass without changing the diary date. The screenshot uses synthetic content only. Signed generic-iOS compilation succeeds. Installation and independent iCloud write/download/conflict acceptance remain pending because the phone is unavailable; real original-vault writes remain closed.

All existing normal simulator file hashes match the before snapshot. Original-vault metadata comparison is qualified in the acceptance record (one mtime-only difference), not represented as a complete byte-hash proof. Original files were never written by this task. The frozen work boundary excludes commits/publication; inherited mixed staging is preserved.

After the user reconnected the phone, the verified signed build was installed in place and launched successfully. All 4 existing phone app-data files were backed up and retained identical hashes after installation. System folder authorization and independent iCloud acceptance still require phone-side verification; production original writes remain disabled.
