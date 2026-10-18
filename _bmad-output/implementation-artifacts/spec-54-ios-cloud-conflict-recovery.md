---
title: 'Open and resolve iCloud diary versions'
type: 'bugfix'
created: '2026-09-19'
status: 'done'
route: 'dispatch'
baseline_commit: '5b87e5451e084045809b14111e57b843546df643'
review_loop_iteration: 0
context: ['PROJECT_GUIDE.md', 'docs/PRD.md', 'docs/architecture/mvp-architecture.md']
---

<frozen-after-approval reason="User requested conflict recovery; existing authorization covers this repair">

## Intent

**Problem:** An unresolved iCloud system version makes Riji reject even readable diary content. A cold open has neither a page nor a usable resolution action.

**Approach:** Open readable content with a conflict notice and provide an explicit version review, export, choice and manual merge flow. Preserve every available version before resolving. Ordinary external updates still refresh automatically.

## Boundaries & Constraints

**Always:** Bind operations to the authorized collection, relative path and day. Keep conflicted content read-only until resolution. Preserve ordinary editor metadata protection, source CAS, durable recovery, table editing and navigation. Distinguish system versions from existing body conflicts. Show full Markdown including properties when choosing a whole-file version. Store recoverable evidence locally before any source mutation. User confirmation in the app authorizes the selected whole-file result, including reviewed metadata. Keep independent body drafts and transactions intact; never silently replay them after cloud resolution.

**Never:** Pick a winner automatically by date or size; erase unreviewed versions; loosen ordinary write guards; delete user files or resolve real diary versions during development. No Air/backend changes, publication, broad staging, or cleanup of the mixed working tree. Synthetic tests only. This task implements recovery UI, not an iCloud sync engine.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Cold open | Readable current file, unresolved variants | Correct day opens read-only, version action reachable | No source writes |
| Review | Current plus readable variants, optional local draft | Full version previews and export; explicit chosen result or manual merge | Closing leaves source unchanged and retains merge text |
| Confirm | Reviewed bytes and version identities unchanged | Persist verified backups and result; coordinated replacement; resolve only reviewed versions; verify state | Local drafts remain separately recoverable |
| Stale review | Changed current bytes, collection/path, or version set | Reject old confirmation; show refresh action | Keep merge/proposal and all prior evidence |
| Partial availability | Unreadable/missing variant or incomplete conflict metadata | Read available current content and export, retry versions | No resolution until complete evidence available |
| Interrupted operation | Backup, write, verification, or exact-version marking fails | No unsupported success; reopen/retry safely from durable evidence | Do not overwrite later changes or erase unreviewed versions |

</frozen-after-approval>

## Code Map

- `ios/RijiCore/OriginalVaultAccess.swift`: `readOptional` reads bytes then discards them on `assertAvailable`. Split display snapshots from strict `read` used by commits. Keep scope/identity/descriptor checks. Add a narrow injectable system-version provider; default uses NSFileVersion, coordinated version reads, persistent identities and exact reviewed objects.
- `ios/RijiCore/LocalJournalStore.swift`, `JournalModels.swift`: `open` can carry optional cloud state in JournalSnapshot; all transaction reads remain strict. A new dedicated cloud recovery store may reuse JournalFileSystem for verified backups, proposal retention and retry evidence.
- `ios/RijiCore/BodyRevisionStore.swift`: preserve existing body recovery; do not use body CAS as whole-version restoration or retire independent edits.
- `ios/Riji/DiaryViewModel.swift`: load, changeStore preflight, refresh, canEditBody and source/day guards need cloud state; conflict review must not be replaced during refresh. Existing local drafts can coexist with system versions.
- `ios/Riji/TodayDiaryView.swift`, `VaultBrowserStore.swift`, `VaultFileView.swift`: actionable compact notice, version sheet, raw full-source previews, merge input and export. Directory reads must also remain available, with an honest warning. Use existing native Markdown reader where useful.
- `ios/Package.swift`, `ios/scripts/`, `ios/Riji.xcodeproj`: no project generator was found; update explicit Swift package and Xcode project source membership, including App/State/Native targets.
- `ios/RijiCoreTests`, `ios/RijiStateTests`, `ios/RijiUITests/DiaryFlowTests.swift`: synthetic provider and failure hooks. Existing tests only simulate throwing `.conflict`, insufficient for actual version handling.

## Tasks & Acceptance

**Execution:**
- [x] Core access/models/store -- implement separate display and explicit durable resolution paths, bounded reads and narrow provider injection.
- [x] State and views -- open conflicted days, review bounded versions, persist merge, confirm/export/retry, retain normal refresh behavior and independent drafts.
- [x] Tests and source registration -- cover every matrix row, real UI reachability, unchanged ordinary editor regressions.
- [x] `docs/PRD.md`, `docs/testing/ios-manual-acceptance.md` -- document revised behavior and distinguish synthetic evidence from real iCloud verification.

**Acceptance Criteria:**
- Given a conflicted diary without a local draft, when opened from date selection or original directory, then its current readable body and version-handling action are visible.
- Given a completed version choice, when reopened, then the chosen bytes are exact, prior versions remain locally recoverable/exportable, and a later external edit is never overwritten by retry.
- Given a local unsaved body draft alongside cloud versions, when cloud resolution finishes, then the local draft remains visible/recoverable without automatic source mutation.

## Implementation Notes

The user has already requested this repair; no extra implementation approval is needed. Mixed-tree baseline snapshots are under ignored `output/ios-cloud-conflict-2026-09-19/before`; compare against them, not solely HEAD. Do not stage or commit. Parent owns manual evidence/documentation and final verification; implementation agent owns source/tests and must report when frozen before parent runs broader suites. If a small implementation decision is unspecified, choose the safest bounded behavior and record it here.

## Spec Change Log

## Review Triage Log

| ID | Verdict | Evidence and disposition |
|---|---|---|
| B1 | high | Completed source/version marking followed by confirmation-write failure and a later external edit leaves permanent pending. Add an explicit source-revalidated finish-with-current action; never replay stale intent. |
| B2 | medium | Evidence is decoded before date filtering, so corrupt foreign-day JSON blocks healthy pages. Isolate evidence by day and retain readable records with warnings. |
| B3 | medium | reviewCloudVersions persists before returning and ViewModel presents only after backup reads; local storage failure hides readable versions. Present read-only partial review, keep persistence mandatory for confirmation. |
| B4 | medium | initialDay and directory recovery sets include only body/legacy records. Include unresolved cloud drafts/confirmations, exclude completed backup-only history. |
| B5 | high | Editing a held draft creates another UUID and bypasses the exact-ID hold. Hold the visible draft in explicit recovery until version choice/manual merge; test typing cannot replay it. |
| B6 | low | More than 16 variants remains outside the bounded in-app review. Fix the misleading retry-only message and disclose count/limit; unlimited pagination is not required for this bounded personal-MVP repair. Preserve source and all system variants. |
| B7 | medium | Current/merge validation permits 32 MiB but confirmation permits 2 MiB. Align cloud limits and report size limits honestly; current source remains readable/exportable. |
| B8 | medium | Each queued key stroke re-encodes the full snapshot even after superseded. Coalesce pending writes and debounce while explicit close/background flush awaits the latest text. No evidence requires a new storage engine. |
| B9 | medium | Refresh archives unchanged reviews under random IDs and scans all dates. Deduplicate immutable evidence by content and isolate by day. |
| B10 | low | Available modification dates are collected but omitted; add available dates and stable backup order to disambiguate choices. |
| E1 | medium | Unavailable-source recovery returns early for a body draft without loading cloud recovery. Restore both independently, clear stale day state. |
| E2 | high | Permitted record payload can encode above the read cap, while write verification is unbounded. Apply the same encoded bound before every write and after read, and test preservation on rejection. |
| E3 | medium | Same cross-day corrupt-evidence root cause as B2, verified in cloudVersionBackups loop. |
| V1 | medium | Pre-verified test gap: availability is assigned manually and no test observes requestDownloads. Add request-gated asynchronous provider test. |
| V2 | medium | Pre-verified test gap: cloudDraft persistence failure is absent. Verify failed close keeps latest text visible, then retry/cold reopen. |
| V3 | medium | Pre-verified UI gap: manual merge never submits its own confirmation. Extend the existing journey to verify chosen bytes and cold reopen. |
| P1 | high | Full Swift run fails three original BodyRevisionStore recovery tests at pendingBodyEdit: new pre-check executes before supersession recovery. Remove/reorder redundant check while commit/resolution holds still protect source writes. |
| P2 | medium | First UI run: alternate-choice passes; merge test fails because Cmd-A did not select all in SwiftUI TextEditor. Use verified insertion input, assert exact text retention and confirm the intended unique result. |
| P3 | high | Newly added cloudBlocksEditing guard rejects an already-origin-bound native callback between cloud publication and UIKit update; cloud refresh omits previousRefreshOrigin capture. Reuse late-input preservation without permitting fresh editing while conflict is active. |
| P4 | medium | Implementation audit: holds exist only after Riji confirmation. Persist observation holds for existing local recovery when cloud conflict is seen, so external conflict resolution cannot trigger old automatic replay. |

All findings were classified individually before grouping. B2/E3/B9 share evidence isolation work; B7/E2 share size roundtrip limits; B5/P4 share recovery ownership. The runtime rejected a third new reviewer at its thread limit, so the completed prior edge reviewer performed the independent verification-gap pass against only this new diff. No review was omitted.

## Verification

- Run focused Core/State tests, then the complete Swift package and relevant native/UI regressions through the existing isolated test runner. No skips or real-vault fixtures.
- Build signed iPhone app after passing review; distinguish build, installation and physical iCloud roundtrip. No uninstall or user data reset.
- NSFileVersion SDK requires coordinated access to version URLs and `isResolved = true` only. Never broadly call `removeOtherVersionsOfItem`; backup and validate before marking exact reviewed versions. Remote/unavailable versions must block confirmation, not reading current content.

### Review correction notes

Keep the working readable-conflict path, full-source confirmation, exact reviewed-version marking, durable backups, four tabs, no normal success footer, and existing body CAS. Correct the demonstrated interruption, input-ownership, storage-isolation and verification cases above incrementally; preserve the mixed-tree baseline and user edits. Initial full Swift source audit was unchanged but three legacy recovery tests failed; first two UI journeys had one pass and one input-fixture failure. These are not final passing evidence.

All twenty triaged findings received the listed correction or bounded-limit handling. Corrective checks passed 34 focused cases: 16 Cloud Core, 13 Cloud State, three existing body recovery regressions and two adjacent input-origin cases. The next full package run passed Core 263 and Probe 5, with only one State assertion still expecting the retired conflict wording. The parent changed that assertion to require the cloud-conflict category and the actionable version-review label; all preservation assertions remain intact. Full final verification follows that test-only correction.

### Final verification

- Complete Swift package: 518 passed (Core 263, State 250, Probe Support 5); 57 native tests and five exact UI journeys passed; no skips or failures. All three source audits matched 86 unchanged inputs. The signed iPhone build matches those inputs and passed signature verification.
- Test runner Python regressions: 77 passed. Full details and artifact paths are in `docs/testing/ios-cloud-conflict-2026-09-19.md`.
- Read-only production adapter rechecked the real conflicted file on the development Mac: current and one alternate readable, source bytes and system versions unchanged. No real version was chosen or resolved.
- After the user connected and unlocked the phone, the exact verified signed app was installed over the existing bundle and launched successfully. All 89 pre-install Documents/Application Support files matched byte-for-byte before first launch; Preferences remained empty. No uninstall, reset or test environment injection occurred. Real phone conflict resolution and cross-device propagation remain manual acceptance, not inferred from installation or simulator success.
- No commit, staging, push or Air deployment was performed; the pre-existing staged voice specification remains untouched.
