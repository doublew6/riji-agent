# iOS app icon — 2026-09-16

The owner selected concept 11 (Memory Pages) and authorized local retention and app integration. The original transparent artwork and opaque production master are preserved under [assets/branding](../../assets/branding/README.md). The app uses the opaque 1024 × 1024 export in [AppIcon.appiconset](../../ios/Riji/Assets.xcassets/AppIcon.appiconset/Contents.json).

## Verified

- The retained transparent source is byte-identical to selected concept 11.
- The shipping PNG is 1024 × 1024 and has no alpha channel.
- Both Debug and Release select AppIcon; the asset catalog is included in the Riji target resources. A structural comparison with the pre-task project confirms no other project-setting changes in this increment.
- Debug Simulator build passed. Its compiled Info.plist names AppIcon as the primary icon and the bundle contains Assets.car.
- The existing dedicated Simulator was updated in place. Its Home Screen visibly displays the olive Memory Pages icon for Riji.
- After reacquiring the post-install data-container URL, all pre-existing Documents/Application Support files matched their original SHA-256 values. The installation relocated the container; checking its old path would incorrectly report missing files. No app uninstall or data erase was performed.
- Debug physical-device build passed. Code signature and provisioning profile match; the previous signing team, app identifier and registered phone are retained. Personal signing details remain in ignored local output.

## Physical device handoff

The iPhone was initially unavailable. After the owner reconnected it, the device reported connected with Developer Mode enabled. The existing com.riji.local installation was verified before updating.

On 2026-09-16, devicectl successfully overwrite-installed the signed icon build and successfully launched com.riji.local with the test-container, sample-mode and test-date environment overrides cleared. No uninstall or device-data erase was performed. Reports are retained as iphone-install.json, iphone-launch.json and iphone-handoff-validation.json in the ignored evidence directory.

Physical installation and process launch are verified. The actual phone Home Screen was not captured; visual confirmation there is separate from the verified Simulator screenshot and compiled icon metadata. This result does not establish physical iCloud or feature acceptance. The current free profile expires on 2026-09-22 at 17:33 (Asia/Shanghai).

## Scope and evidence

This increment changes branding assets and icon wiring only. It does not activate original-vault writes, AI features or Air services; feature acceptance for the mixed working tree remains tracked separately. No commit, push or production deployment was performed.

Build logs, asset/signature checks, the pre-task project snapshot and private integrity manifests are in ignored output/ios-app-icon-2026-09-16/. Simulator icon validation is distinct from physical-device acceptance.
