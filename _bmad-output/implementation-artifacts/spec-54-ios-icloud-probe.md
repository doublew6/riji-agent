---
title: 'Issue 54: isolated physical iCloud save probe'
type: 'chore'
created: '2026-09-16'
status: 'done'
route: 'dispatch'
baseline_commit: '77c2c9b231d4e0280b18e3a0ca7270ba4616d5b5'
review_loop_iteration: 0
context:
  - 'PROJECT_GUIDE.md'
  - 'docs/product/ios-personal-mvp-development-plan.md'
  - 'docs/testing/ios-original-vault-2026-09-16.md'
---

<frozen-after-approval reason="continued confirmed issue 54 and development-plan 01/03; user confirmed phone tables and requested continuation">

## Intent

The original folder and Markdown table implementation is installed on the phone; the user confirmed tables render correctly. The next dependency is real phone/iCloud save verification, using synthetic data before enabling private-original editing. Prepare and run an isolated physical-device probe that exercises the existing writer, records actual results, and distinguishes local persistence from cross-device propagation. User interaction is needed only to choose the prepared test folder in the iOS system picker.

## Boundaries & Constraints

Always retain the production `.readOnly` policy and existing application state. Only an explicitly selected, newly generated synthetic fixture may receive probe writes. Verify the complete fixture identity, exact expected bytes, allowed paths, absent symlinks and real iCloud metadata before constructing a writable adapter. Never use the real private vault for conflict injection, change its bookmark, log its paths/contents, or copy private templates into test code. Never uninstall the application, alter Air, publish, or change the normal native/UI test targets. Keep test hooks in a dedicated hosted XCTest target, outside application sources and the normal scheme. No extra permanently installed probe app or arbitrary original-write feature toggle.

This increment prepares reproducible actual-device evidence; it does not declare the full cloud gate passed based on simulator tests. Download eviction, unresolved OS conflict versions and interruption acceptance remain separately pending unless actually executed.

</frozen-after-approval>

## Code Map

- `ios/RijiCore/OriginalVaultAccess.swift`: coordinated reads and conditional writes, production default read-only, existing explicit `.syntheticFixture` adapter.
- `ios/RijiCore/BodyRevisionStore.swift`: source-bound `BodyEdit`, retain/commit/recover/revisions. A nested deletion can produce changedIdentity; assert preservation, not one exact error.
- `ios/RijiCoreTests/OriginalVaultAccessTests.swift`: synthetic writer failure cases already covered locally.
- `ios/Riji/RijiApp.swift`, `DiaryViewModel.swift`: `RIJI_TEST_CONTAINER` UUID isolates normal app startup data in DEBUG.
- `ios/Riji.xcodeproj/project.pbxproj`: current NativeTests is unhosted and duplicates app UI source. Add a separate hosted unit target with TEST_HOST/BUNDLE_LOADER and App dependency; do not modify existing target conditions.
- `ios/Riji.xcodeproj/xcshareddata/xcschemes/Riji.xcscheme`: preserve; new probe-only scheme.
- `output/ios-icloud-probe-2026-09-16/before-source/ios`: exact pre-increment snapshot including previous dirty work. Preserve all previous changes.

## Tasks & Acceptance

- [x] Add a test-only shared synthetic fixture description and strict validation. Exact run UUID marker plus bounded, enumerated expected paths/bytes are required; wrong selection, extra files, symlinks or placeholders stop before any writer construction. Provide local negative tests without requiring a phone or cloud. Shared code belongs in test support, not exported production Core APIs.
- [x] Add dedicated hosted `RijiICloudProbeTests` target and `RijiICloudProbe` scheme. Run only when explicit physical probe run UUID is supplied; do not silently pass or pretend simulator is cloud. Fixed test namespace must be injected before host initialization, with per-run bookmark/state under it. Present one system folder picker, show concise Chinese instructions, resolve security-scoped bookmark on a second test invocation without requesting selection again. Balance access and coordinate file operations off main thread; handle cancellation, timeout and unavailable source as recorded failures.
- [x] Initial phone phase verifies the pristine fixture; saves a nested synthetic day using retain/commit; retries same edit ID idempotently; retains separate drafts for later external modify/delete/create cases. Write a marker/receipt only after exact data and actual success checks. Durable phase data must allow second process invocation, not an in-memory-only bookmark check.
- [x] Add a small developer CLI under `ios/scripts` using Foundation coordination and the shared synthetic protocol. Commands create a new fixture (never overwrite an existing path), inspect/poll exact phone output, and apply deterministic Mac changes only after all fixture checks and phone-ready receipt. Mac edit one saved day, modify a drafted day, delete a drafted day, create a previously absent drafted day. Commands refuse wrong run/unknown or non-synthetic bytes. Emit synthetic hashes/status only and retain deletion bytes in local evidence if needed. This is manual coordinated cross-device validation, not an automatic agent or server.
- [x] Second phone phase restores the per-run bookmark in a new process and requires exact Mac phase bytes. Verify Mac update is readable, each stale draft is refused, external bytes remain exact, deleted original is not resurrected anywhere, retained drafts survive and no successful receipts exist for rejected operations. Record private local JSON summary with distinct physical/cloud/read/save/conflict results. Do not label stale source as an actual NSFileVersion conflict.
- [x] Document exact create/build-for-testing/run/Mac-step/rerun/evidence workflow, with remaining gate items explicit. Tests compile on simulator without triggering picker; local guard tests run without selecting real files. Physical evidence may remain pending awaiting the user's one folder selection; report that precisely rather than enable private writing.

The implementation agent owns only new test/support/script files, project/scheme registration, Package test-support registration if needed, and probe documentation. Do not run Xcode, a simulator, physical device commands, or mutate iCloud yourself; the parent handles device execution after review. Run focused SwiftPM tests if applicable. Do not invoke another skill or spawn more agents. Prefer a small harness with two explicit invocations over a polling automation framework. Adjust an internal seam only if necessary to compile the probe; record the reason.

## Verification

Run the new fixture-guard tests, relevant OriginalVaultAccessTests, compile the hosted target with simulator build-for-testing, and compile/run the developer CLI against a temporary synthetic local directory. Before physical execution, check host namespace injection and code signing, back up existing app data and install in place. Record physical stages separately, and compare pre-existing data hashes afterwards. No broad repeat of unaffected backend/UI tests.

## Implementation Notes

The prepared harness is complete. Physical execution remains a distinct pending result, requiring system folder selection. The user has already authorized this verification through the approved plan and continuation. No new original-write gate is enabled. The incremental review diff uses the exact pre-increment snapshot to avoid mixing earlier dirty changes; staging is unchanged. Local guard/adapter tests: 20 passed. CLI local self-test passed. Simulator hosted build passed after fixing a scheme build-order cycle. Signed physical build is running.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Evidence and route |
| --- | --- | --- |
| Blind 1 | medium | Both reviewers independently replayed nested deletion against current Core: recovery masks the incoming edit's changedIdentity as storage. Patch by reordering existing incoming-edit validation before recovery aggregation, preserving all guards and adding an exact-error/data-retention regression. |
| Blind 2 | medium | record writes summary-initial atomically on both success and failed repeated attempts; prepareInitial fails first, then catch destroys the authoritative successful phase. Patch to preserve success and retain separate failure attempts. |
| Blind 3 | medium | The decoded drafts array is not checked before zip; a valid but truncated state can omit scenarios while outcome says passed. Patch exact known fixture-state validation and required-result checks; add local hosted guards. |
| Blind 4 | low | mac-ready is emitted before local hash evidence; byte changes are already actual, but a local evidence failure produces ambiguous stage completion. Directly move hash persistence/readback before the marker. |
| Blind 5 | low | Present invalid UUID follows the same skip guard as absent UUID. Directly distinguish malformed explicit requests from opt-out. |
| Blind 6 | low | Poll catches every fixed error then replaces it with deadline. Direct correction: stop on definite wrong identity/unsafe path/conflict and retain a safe final code on timeout. |
| Blind 7 | medium | Deadline checks occur only between synchronous coordinated reads, so a blocked provider has no bound. Patch a read-only CLI watchdog with monotonic budget; mutation commands remain exempt. |
| Blind 8 | low | Generic mapping intentionally avoids leaking Foundation paths. Richer failure-step taxonomy could aid rare corrupted-evidence debugging, but is not needed for pass/fail or preservation; reject additional branching/state for this developer-only diagnostic refinement. |
| Edge 1 | medium | Same reproduced nested deletion as Blind 1; fix error ordering in Core rather than accepting arbitrary storage failures as concurrency success. |
| Verification | — | Reviewer reported no verification gaps; physical execution remains explicitly pending, and new review regressions require execution before device use. |

Review patches complete: the adapter/Core body suites and fixture guards pass 40 tests; hosted local guards pass 4 tests; CLI regression passes 4 tests. Normal Simulator data retained all original file hashes. Final physical test compilation and signing pass. The first device launch exposed a missing app runtime framework search path (the package product was embedded but absent from the host rpath); adding the standard inherited `@executable_path/Frameworks` setting to App configurations fixes the observed dyld failure. The second physical launch reached XCTest and the system directory picker. This does not establish an iCloud write result.

Final prepared-harness status: complete and reviewed; actual physical acceptance remains incomplete. The second device invocation reached the system picker but ended after 180 seconds with pickerTimeout. No folder bookmark or phase state was written and no phone fixture write was executed. Normal Riji was relaunched without test environment and its running process verified. All original phone data files matched the fresh pre-run backup; normal Simulator retained all original data files. The existing mixed staging and work were preserved; no commit, push, Air operation or private-original write was performed. The user must select the dedicated fixture on a new explicit initial invocation before advancing to Mac edits.

Physical follow-up completed: after explicitly foregrounding the existing test host, initial and verify both passed in separate processes. Mac receipt hydration was explicitly requested for the known synthetic phone-ready file, then exact phoneReady/MacReady validations passed. Exact return bytes and stale modify/delete/create refusals were verified. All original phone files retained hashes, normal Riji was restored and its live process checked. Full offline/revocation/OS-conflict/interruption gate remains pending and private writing stays readOnly. No new source-code changes, publication or Air actions were made in this continuation.
