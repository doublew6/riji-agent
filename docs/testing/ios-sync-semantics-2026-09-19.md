# Ordinary iCloud synchronization and obsolete review recovery

Issue: #54. Local evidence is under ignored `output/ios-sync-semantics-2026-09-19/`. Private source inspection and app-state backups in that directory must not be published.

## Observed cause

The version list comes from Foundation's unresolved conflict versions, not a read of the current cloud server file. Calling an entry an "iCloud version" misrepresented the comparison. A read-only inspection found the current Mac file downloaded and uploaded, with no transfer error or remaining conflict copies. The source hash was unchanged by inspection.

The phone retained an incomplete confirmation whose chosen bytes exactly matched both the review's current file and the inspected Mac file. Its old comparison still contained a conflict copy. No independent body draft, body transaction or active hold was present for that day. This supports completing an already-applied confirmation after fresh on-device checks; it does not authorize picking an unconfirmed version.

## Implemented behavior

Normal edits continue saving the same authorized file; iCloud manages transport. Open/refresh automatically completes obsolete local bookkeeping only with a complete, empty system conflict inventory and matching source identity. An untouched comparison is backed up before retirement. An interrupted confirmation additionally requires a verified backup and exact equality between its already-confirmed result and the current source. Neither path writes source bytes or marks system versions.

Manual merge proposals, changed pending results, new conflicts, incomplete inventories, missing/corrupt evidence and independent body recovery remain protected. Untouched reviews refresh their source and version list; in-memory manual input takes precedence over an older debounced record. A real conflict offers a prominent whole-current-diary action requiring one final confirmation, with per-fragment selection optional. Alternative versions are labeled as conflict copies.

## Verification

| Check | Result | Evidence |
| --- | --- | --- |
| Complete Swift package | 538 passed: Core 279, State 254, Probe Support 5; no failures or skips | `output/ios-tests/20260919T142602Z-core-7522911a/summary.json` |
| UI workflows | 4 passed: active external refresh and further editing, whole-current choice, alternate-version choice and backups, retained per-fragment decisions and confirmation | `verified-sync-ui/summary.json` |
| Runner regression | 77 passed | `runner-pytest.log` |
| Signed phone build | Signature verified, known phone authorized, all 89 source inputs match the before/after snapshots of both final runs | `verification.json` |

Evidence paths without a prefix are relative to the ignored directory above. Earlier failed runs remain recorded and are not acceptance evidence. One run exposed unsaved manual merge input being replaced during an explicit refresh; change detection now uses the latest in-memory draft. A synthetic failure-path fixture also needed the namespaced evidence directory rather than its parent state directory. The corrected complete run passes both paths. Native control internals were unchanged; no new complete Native suite run is claimed. Default smoke now includes the whole-current workflow, for 23 UI journeys.

The verified build was installed over the existing phone app with the same bundle ID. Before launch, all existing durable files had identical hashes after installation, no files were added or removed, and Preferences remained empty. The normal launch succeeded with test environment prefixes removed. Evidence: `phone-install.json`, `phone-install-integrity.json` and `phone-launch.json`.

A subsequent read-only check of recovery bookkeeping could not run because the device connection became unavailable (CoreDevice error 4016; device inventory then reported an unavailable tunnel). Accordingly, automatic completion of the affected real diary on this phone is not claimed as observed. Installation and the successful launch are verified; the final real diary view and a new cross-device edit remain manual acceptance. Installation itself does not prove iCloud propagation.

## Manual acceptance

1. Open the affected day on the updated phone. If its system inventory has settled and the earlier confirmed result is present, the obsolete warning should disappear without choosing each difference.
2. With no pending Riji edit, change a short line in Obsidian, then return to Riji. Confirm the same file refreshes and remains editable.
3. Edit in Riji, allow autosave, and check the result in Obsidian and on Mac after iCloud transfers it. This is the real propagation check; synthetic tests do not replace it.
4. If a genuine unresolved conflict exists, use the clearly labeled whole-current choice when that entire file is correct, or inspect only changed fragments. Confirm once, then check backups remain reachable.

No Air service update or public release is included. Inspection did not alter the real diary. Installation overlaid the existing app without deleting its data.
