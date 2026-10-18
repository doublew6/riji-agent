# Readable cloud-conflict differences: 2026-09-19

## User-visible change

The version review previously expanded two complete raw Markdown documents. It now defaults to changed regions, labelled by diary section, with identical lines hidden. Markdown fragments use the native diary renderer; changed table rows include their unchanged column headers for context. Source text and neighbouring lines remain available through optional disclosures.

Each region can retain the current proposal, adopt the other version, or accept a local fragment edit. The proposal persists without writing the original diary. Final confirmation compares the original reviewed source with the actual proposed result, then uses the existing backup, exact-source verification and version-resolution transaction. Full-document selection, editing and export remain reachable.

## Data boundaries

- Comparison and formatting run locally. No AI, server or external upload is involved.
- Patches use original UTF-8 byte ranges; rendering is never used to reconstruct Markdown. Unselected bytes, line endings and Unicode spelling remain intact.
- The working review ID and expected proposal bytes must still match before applying a choice. Existing source/version validation remains mandatory at final confirmation.
- Two MiB text limits and bounded comparison work prevent uncontrolled allocation. Large or repetitive inputs can fall back to larger explicitly labelled difference regions.
- Read-only inspection of the existing diary on the development Mac found changed regions alongside identical lines. The original file bytes and system version inventory were identical before and after inspection. No real version was chosen or resolved.

## Verification status

Implementation, automated checks, the signed phone build and the phone overlay installation are complete. Launch was blocked by the phone lock screen; opening and reviewing the real diary remains a manual acceptance step. Evidence is stored locally under `output/ios-cloud-diff-2026-09-19/`; this ignored directory also holds private recovery evidence and must not be published.

| Check | Result | Evidence |
|---|---|---|
| Complete Swift package | 530 passed, no failures or skips: Core 273, State 252, Probe Support 5 | `output/ios-tests/20260919T030857Z-core-cfcb63de/summary.json` |
| Native controls | 59 passed, no failures or skips | `output/ios-tests/20260919T030740Z-native-821dd459/summary.json` |
| Final UI journeys | 2 passed: mixed fragment selection and fragment typing, both with retained proposals, confirmation and exact cold reopening | `output/ios-cloud-diff-2026-09-19/verified-diff-ui/summary.json` |
| Runner regression | 77 passed | `output/ios-cloud-diff-2026-09-19/runner-final-pytest.log` |
| Signed iPhone build | Signature and executable hash verified; all three final runs match its 89 source inputs | `output/ios-cloud-diff-2026-09-19/verification.json` |
| Existing diary inspection | Changed regions alongside identical lines; current bytes and system version inventory unchanged | `output/ios-cloud-diff-2026-09-19/real-diary-diff-readonly.json` |

The earlier whole-version selection/backup and full-document manual-merge UI journeys also passed after adapting their optional disclosures. Final targeted UI verification focuses on the two new difference workflows. Default smoke coverage now includes both, increasing the suite from 20 to 22 journeys.

Native coverage includes readable, noneditable table fragments and rapid fragment input with stale parent echoes, preserved selection, Chinese marked text and explicit replacement. The phone was initially unavailable. After the user reconnected it and requested installation, the verified signed app was installed over the existing app with the same bundle ID. Before installation, Documents and Library/Application Support were backed up and Preferences was confirmed empty. Before launch, all original data files were copied again and their hashes matched; no files were added or removed, and Preferences remained empty. The normal launch request contained no test environment injection but was rejected because the device was locked. Installation succeeded; the user can open the new app after unlocking. No real conflict version was selected. Evidence: `phone-install.json`, `phone-install-integrity.json` and `phone-launch.json` under the local evidence directory.

Early UI runs caught inherited accessibility identifiers hiding the per-region controls, duplicate summary identifiers in the final confirmation sheet, and an editor echo that could replace in-progress input with an older published value. These failures remain recorded; failed runs are not acceptance evidence.

Fragment editing now replaces native text only when the user explicitly chooses a different fragment. Routine draft publications do not overwrite typing or marked text. A subsequent UI run passed exact fragment typing, delayed confirmation, cold recovery and exact final reopening. One complete native run finished its 59 tests successfully but stalled in Xcode's optional simulator diagnostic collection; it was interrupted and is recorded as failed. The verified run used the same standard runner, inventory, lock and source audits, with optional diagnostic collection disabled.

## Manual acceptance

1. Open a diary with a system conflict and choose to review its versions. Confirm that the default screen shows changed regions rather than the entire source.
2. Expand context only where needed. Check a changed workout row: headers and cells should remain readable.
3. Keep one region, adopt another, and edit a third. Close the review and reopen it; the proposal should be retained while the original file remains unchanged.
4. Review the final result. Only changes that would actually be written should appear. Confirm only after reviewing the choices.
5. Reopen the diary and inspect the existing version backups. Cross-device iCloud propagation is a separate manual check; synthetic tests and app installation do not prove it.

This work does not deploy Air services, modify real diary contents, commit, push or replace unrelated working-tree changes.
