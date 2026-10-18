# Isolated physical iCloud probe

Issue #54 / development-plan 01 and 03. This harness exercises the existing
`OriginalVaultAccess(.syntheticFixture)` and `LocalJournalStore` writer in the
existing Riji host. Production original-vault access remains `.readOnly`.
No app source, normal `Riji` scheme, NativeTests or UITests is changed by this probe.
The only Core change is a narrow error-classification correction in
`commitBodyEdit`: after the read-only gate, validate the incoming source identity
before checking retained recovery readability. All checks remain. A deleted nested
source now reports `changedIdentity` instead of masking it as a storage failure;
the raw retained draft stays unchanged, with no receipt or resurrected file.

The support code is test-only. `RijiICloudProbeTests` is a separate hosted XCTest
target, with `TEST_HOST`, `BUNDLE_LOADER` and a Riji target dependency. It does not
install another probe application. Never uninstall Riji to run these tests.

## Preconditions and private evidence

Back up the existing phone app's Documents and Library/Application Support before
installation. Retain a private file/hash manifest and compare those same original
files after both runs. Keep the existing bookmark and normal data. Install the
signed Debug host in place using the existing signing configuration; verify its
bundle ID is `com.riji.local`, the signing profile/device and the test bundle before
running. Device IDs, signing identities, bookmarks and all app data stay in ignored
`output/ios-icloud-probe-2026-09-16/`, never in a shared report.

The caller must inject `RIJI_TEST_CONTAINER=BD30E2CF-68EB-4E71-909F-58115F3EF954`
and `RIJI_TEST_ORIGINAL_VAULT=0` **before host initialization**. The dedicated scheme
sets both; inspect the generated `.xctestrun` to verify them. Setting them in XCTest
`setUp` is too late. The run UUID and explicit phase are also process environment
values. The ordinary host starts only its isolated old App diary space.

Per-run probe bookmark, source-bound edits, body receipts and JSON summaries live in:

```text
Library/Application Support/UITests-BD30E2CF-68EB-4E71-909F-58115F3EF954/
  ICloudProbe/<RUN-UUID>/
```

The original app bookmark is never used or overwritten. The second invocation must
use a new host process and the same run UUID. It resolves only this per-run bookmark,
without a picker. A stale/unavailable bookmark is a failure, never silent reselection.

## Local checks and build

```sh
swift test --package-path ios --scratch-path /tmp/riji-ios-swift-build \
  --filter 'ProbeFixtureTests|OriginalVaultAccessTests'
ios/scripts/icloud-probe.sh self-test
python3 ios/scripts/test_icloud_probe_cli.py
xcodebuild -project ios/Riji.xcodeproj -scheme RijiICloudProbe \
  -configuration Debug -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath "${TMPDIR:-/tmp}/riji-ios-manual-derived" \
  CODE_SIGNING_ALLOWED=NO build-for-testing
```

`self-test` creates and removes only its own temporary local synthetic directory;
its result explicitly says physical/cloud execution did not occur. Guard tests do
not select real files. A simulator test invocation skips explicitly before any picker;
it cannot produce a physical/cloud pass. Only a missing run UUID reports a skip;
an explicitly present invalid UUID fails with safe text. The four hosted
`ProbePhoneGuardTests` also run locally on Simulator without a picker: scenario
integrity, complete phase checks, repeated-failure evidence preservation and run
argument validation. Select this class for a focused local hosted invocation.

Build the physical test bundle using the already verified private signing setup:

```sh
xcodebuild -project ios/Riji.xcodeproj -scheme RijiICloudProbe \
  -configuration Debug -destination 'generic/platform=iOS' \
  -derivedDataPath "${TMPDIR:-/tmp}/riji-ios-manual-derived" \
  -xcconfig "$PRIVATE_SIGNING_XCCONFIG" build-for-testing
```

The signing path is a local operator input, not a committed configuration. Do not
replace the normal scheme or broaden existing test compilation conditions.

## One folder selection, two phone invocations

1. Generate a UUID (`uuidgen`) and retain it in private evidence as `PROBE_RUN`.
   Set `PROBE_PARENT` to an existing, downloaded iCloud Drive test parent, confirmed
   using Foundation `isUbiquitousItem`. Do not use the private journal vault itself.
   Use the actual canonical parent path; symlinked roots/ancestors are rejected.
   Set `PROBE_ROOT="$PROBE_PARENT/Riji-iCloud-Probe-$PROBE_RUN"`.

   ```sh
   ios/scripts/icloud-probe.sh create "$PROBE_ROOT" "$PROBE_RUN"
   ios/scripts/icloud-probe.sh poll "$PROBE_ROOT" "$PROBE_RUN" pristine 120
   ```

   Creation never overwrites an existing directory. It creates only a marker,
   a synthetic template and three tiny CRLF/Unicode/table documents. Download the
   whole test folder on the phone in Files if needed. Do not open it in tools that
   add configuration/hidden files. Extra files (including `.DS_Store`), wrong marker,
   unexpected bytes, links, placeholders or unresolved OS conflicts stop admission.

2. Copy the generated physical `.xctestrun` alongside the original in its build
   products directory (preserving `__TESTROOT__` resolution). Change **only** this
   probe target's `EnvironmentVariables` in the private copy:

   ```text
   RIJI_TEST_CONTAINER = BD30E2CF-68EB-4E71-909F-58115F3EF954
   RIJI_TEST_ORIGINAL_VAULT = 0
   RIJI_ICLOUD_PROBE_RUN = <PROBE_RUN>
   RIJI_ICLOUD_PROBE_PHASE = initial
   ```

   With format version 2 these values are under
   `TestConfigurations[*].TestTargets[*].EnvironmentVariables`; in version 1 under
   the probe target dictionary. Require exactly the `RijiICloudProbeTests` target,
   validate `TestHostPath` points to the existing Riji host, preserve all Xcode
   injection fields, and disable parallel/repeated execution. Do not rely on shell
   environment inheritance or `setenv` after launch. A private copy must preserve
   the build products root and paths from the generated file.

   ```sh
   xcodebuild test-without-building -xctestrun "$PROBE_XCTESTRUN" \
     -destination "platform=iOS,id=$PRIVATE_DEVICE_ID" \
     -parallel-testing-enabled NO \
     -only-testing:RijiICloudProbeTests/RijiICloudProbeTests/testPhysicalProbe \
     -resultBundlePath "$PRIVATE_EVIDENCE/initial.xcresult"
   ```

   Hosted XCTest can start Riji in the background on a physical phone. After the
   log reports that `testPhysicalProbe` has started, activate that existing process
   from another terminal (do not pass `--terminate-existing`):

   ```sh
   xcrun devicectl device process launch --device "$PRIVATE_DEVICE_ID" --activate com.riji.local
   ```

   Confirm the returned process ID is the existing test-host process. In the actual
   device run this foreground step was required before the picker became visible.

   The phone displays one system folder picker with Chinese instructions. Select
   exactly `Riji-iCloud-Probe-<RUN-UUID>`. Cancellation and the 180-second selection
   timeout record failures. No extra confirmation button or private-vault selection
   is needed. The initial phase validates cloud metadata and every allowed path/byte
   before constructing the writable adapter. It saves nested day 2099-01-01 through
   retain/commit, retries the same edit ID, verifies the success receipt, then retains
   separate drafts for modify/delete/create. It persists the bookmark and edits,
   verifies their readback, and finally creates `phone-ready.json`.

3. After the initial process exits, wait on Mac for exact phone bytes and receipt:

   ```sh
   ios/scripts/icloud-probe.sh poll "$PROBE_ROOT" "$PROBE_RUN" phoneReady 120
   ios/scripts/icloud-probe.sh mac-step "$PROBE_ROOT" "$PROBE_RUN" \
     "$PRIVATE_EVIDENCE/mac-step"
   ios/scripts/icloud-probe.sh poll "$PROBE_ROOT" "$PROBE_RUN" macReady 120
   ```

   `PRIVATE_EVIDENCE` must already exist outside iCloud; `mac-step` itself must not
   exist. The CLI refuses any fixture that is not exactly phone-ready, including
   forged receipt plus pristine bytes. Coordinated Mac changes update saved day
   01, modify drafted day 02, retain deletion bytes locally and delete nested day 03,
   then create previously absent day 04. The fixed `mac-hashes.json` evidence is
   persisted without overwrite and read back before `mac-ready.json` is published;
   final exact fixture verification remains required.
   CLI output contains only synthetic hashes, run ID and fixed status/error codes.
   A partial failure is retained for inspection; the CLI does not overwrite it or
   invent a successful receipt. Start a new run if an interrupted phase cannot be
   proven complete. Do not manually forge readiness markers to resume.

   `poll` accepts a 1–300 second monotonic read budget. Wrong fixture identity,
   unsafe links/paths and unresolved OS conflicts fail immediately. Incomplete
   propagation retries within the remaining budget; timeout exits 124 and reports
   `code=deadline` plus the last safe failure in `lastCode`. An independent,
   cancellable read-only watchdog also exits a blocked coordinator at that deadline.
   Before any completed check, `lastCode=noCompletedAttempt` means no source result
   was available. No exit watchdog is installed for `create` or `mac-step` writes.
   The local Python checks exercise wrong paths, symlinks, invalid run IDs,
   propagation timeout and a deliberately blocked read-only command without iCloud.

4. Set only `RIJI_ICLOUD_PROBE_PHASE=verify` in the same private `.xctestrun` and run
   the same command with a fresh `verify.xcresult` path after the first host has
   exited. There is no folder selection. If Mac changes have not reached the phone,
   the run records failure and makes no probe writes; wait for Files synchronization
   and explicitly retry verify in another process.

   The verify phase requires exact Mac bytes/receipt and original collection identity,
   validates exactly one saved edit and three ordered, unique draft scenarios with
   expected dates, synthetic bytes and source IDs before writes. It reads the Mac
   update and attempts each stale draft. Only source-change/identity
   refusals qualify. It then verifies every external byte, absence of the deleted
   file throughout the enumerated fixture, retained draft bytes and absence of all
   rejected-operation success receipts. The saved operation's original receipt remains.
   A nested deletion may produce `changedIdentity`; it is not required to report
   exactly the same error as an external content change.

5. Export the test attachments and copy the per-run JSON evidence privately. Compare
   pre-existing app file hashes with the backup. End the test host, then launch Riji
   normally without probe variables to use the original application state.

## Evidence interpretation and remaining gate

`summary-initial.json` and `summary-verify.json` have separate physical-device,
iCloud metadata, read, local-save, idempotency, bookmark restore, propagation and
stale-source results. A phone-ready marker is local-save evidence until Mac has
observed its exact bytes. A Mac-ready marker is evidence of the coordinated Mac
step; the second phone read demonstrates propagation back. The local deletion
backup is synthetic and does not contain original user data.
Successful summaries are immutable and authoritative. Each failed attempt uses
its own `attempt-<phase>-<UUID>.json`, so repeating initial preparation cannot erase
an earlier pass or block a valid verify. Preparation refuses an existing initial
summary/bookmark/state without retrying source work. A passed outcome requires every
check for that phase; missing/pending conflict checks cannot be silently accepted.

This is a manual, coordinated two-process/two-device validation, not a server,
continuous polling agent or general compare-and-swap guarantee for uncoordinated
third-party writers. Stale-source rejection is **not** an actual `NSFileVersion`
conflict test. Real download eviction, unresolved OS version conflicts, permission
revocation/moves, background/interruption acceptance and the rest of the 01/03 gate
remain pending unless executed separately. `fullCloudGate` is always `pending`,
and private-original writing stays read-only even when both probe phases pass.

The initial implementation passed 5 fixture guard tests and 15 relevant existing
`OriginalVaultAccessTests`, CLI compilation/local self-test and project lint. Review
adds a nested-deletion regression, four hosted local guards and four local CLI
checks; final results are recorded by the operator from actual runs. Physical
evidence remains pending the selected test folder. Release compilation of the probe
is deliberately rejected because host namespace isolation exists only in Debug.

## Operator verification, 2026-09-16

After review fixes, **40** fixture/original-access/body-revision tests, **4** hosted
local guard tests on the existing Simulator, and **4** CLI regressions passed.
Normal Simulator data retained all **20** pre-existing file hashes. The signed
physical test bundle built successfully and passed strict signature verification.
A first physical launch failed before app startup because the dynamically packaged
Core framework was embedded but missing from the host runtime search path. The
standard inherited `@executable_path/Frameworks` setting corrected that packaging
error; the subsequent phone run reached the test and system folder picker.

The dedicated synthetic fixture passed exact Mac-side iCloud metadata/byte checks;
Foundation also reported every fixture item uploaded without an upload error. This
is Mac upload evidence, not proof that the phone has downloaded or saved it. Actual
phone phases, cold bookmark restoration and two-way propagation remain pending until
the selected-folder run completes. Full original-write acceptance remains separate.

Private logs, device backups, hashes and generated run configuration are retained in
ignored `output/ios-icloud-probe-2026-09-16/`. Failed attempts remain separate from
successful evidence. Existing phone Documents and Application Support were freshly
backed up before the physical test installation. No original diary, Air service or
production write policy was changed.

The physical initial run reached the system picker, then failed with `pickerTimeout` after 180 seconds without a selected URL. It did not execute any fixture save. Its failure is retained as an attempt record. Normal Riji was relaunched and its live process verified; all **5** pre-existing phone files retained identical hashes. The same pristine fixture/run can be retried because no phase state/bookmark was created. Mac changes and the verify phase have not run.


### Foreground retry and actual phone-to-Mac evidence

On the resumed run, the user reported that the picker had not appeared. Hosted XCTest had not brought its existing Riji process to the foreground. Explicitly activating the same test process exposed the picker; after the user selected the synthetic folder, the initial physical test passed. Its exported JSON attachment records successful physical/cloud admission, exact read, local save, idempotency, retained drafts and durable state. The earlier picker timeouts must not be interpreted as proof that the picker was visible or that the user declined selection.

Mac observed the new phone readiness file, but its iCloud metadata initially reported NotDownloaded. Explicitly requesting download of that single known synthetic receipt made the strict phoneReady byte/metadata check pass. The Mac then applied the prescribed changes only in the independent fixture, retained the deleted synthetic bytes locally, and passed the exact macReady check. This establishes the phone-to-Mac leg; the return leg and cold bookmark restore await the second physical invocation.


### Final two-phase physical result

Both physical phases passed on the connected iPhone, in separate test-host processes.
The initial exported attachment confirms real iCloud admission, exact saved bytes,
idempotent retry, retained drafts and durable phase state. After Mac received the
exact phone bytes and readiness receipt, its coordinated changes were read back on
the phone through the restored security-scoped bookmark. The return test confirmed:

| Check | Actual result |
| --- | --- |
| Bookmark restored in a new process | Passed without another picker |
| Phone → Mac | Exact expected bytes observed |
| Mac → phone | Exact expected bytes read |
| Mac modifies a retained-draft source | changedSource; Mac bytes and phone draft retained |
| Mac deletes a nested retained-draft source | changedIdentity; no resurrection, draft retained |
| Mac creates a previously absent day | changedSource; Mac file and phone draft retained |
| Rejected-operation receipts | None created |
| Existing user app data | All pre-existing files retained identical SHA-256 hashes |

Normal Riji was relaunched without test environment variables and its live process
verified. A final Mac inspection still matched the exact macReady fixture, proving
the rejected attempts did not change those synthetic files. Earlier launch/picker
failures and the locked-device wait remain in private evidence; they are not counted
as passes. No original private journal or Air service was modified.

This completes the scoped two-phase probe, not the entire production-write gate.
Phone offline/download-eviction, permission revoke/move, actual unresolved OS conflict
versions and background/write-interruption acceptance remain outstanding. The normal
original-vault writer remains read-only. The independent synthetic fixture and private
evidence are retained for those follow-up checks.
