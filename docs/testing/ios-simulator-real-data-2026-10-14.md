# Manual simulator real-data restoration — 2026-10-14

The user requested real diary files and service data in the manual simulator.
`Riji Development iPhone` on iOS 26.5 now runs normal mode. The separate iOS 27.0
`Riji Automated Tests` device was shut down without erasing its data.

## Original diary directory

The saved directory bookmark resolved to an existing, readable original vault,
but its stored original-source identity differed from the current identity.
The app rejected that selection and entered recovery mode. A read-only probe
linked against the current RijiCore confirmed bookmark resolution, coordinated
file access, and diary discovery; this was not a missing-vault diagnosis.

The normal app state and previous selection were backed up locally. The existing
Debug simulator bootstrap then reselected the same verified directory and
persisted its current identity. Old recovery records were preserved, not applied
or migrated. A subsequent ordinary launcher run reused the restored selection;
a local screenshot confirmed the original-file indicator and actual diary.

No diary editing input was issued during the restoration. The current diary's
hash changed during the live session, while its template hash stayed unchanged;
the writer was not established. The current file was retained without rollback.
Private paths, backups and screenshots remain in ignored local output.

## Keychain and real service

The launcher previously disabled code signing. Its simulator binary lacked the
entitlements needed by the app's Keychain query, which returned
`errSecMissingEntitlement` (-34018), preventing installation-package import.
The launcher now enables Xcode's ad-hoc simulator signing so Xcode embeds the
required simulator entitlement metadata. No team identifier was hardcoded and
physical-device signing was not changed.

After verifying the Air host, a separate simulator device was authorized under
the existing verified principal. The phone registration was left unchanged.
The installation package was imported into the normal app's Keychain, its
success receipt matched the issued device, and temporary credential package
copies were removed after verification. Production services were not restarted.

## Verification and limits

- The actual normal launcher built, installed and opened the app successfully.
- All 15 launcher regression tests passed.
- A read-only Keychain probe with the app's generated simulator entitlement
  metadata returned `errSecSuccess` (0). It requested neither credential data
  nor attributes. Direct SQLite row counts were not used as Keychain evidence.
- Authenticated capabilities and memory-list requests both returned HTTP 200.
- No model message was sent and no memory mutation was requested.

This verifies original-directory restoration and simulator authorization, not
a complete mentor/roundtable journey, phone chat-history transfer, physical
device iCloud behavior, or resolution of the separate phone connection error.
The reported trackpad behavior was not reproduced through UI automation; the
manual guide documents click-and-drag as the simulator gesture.
