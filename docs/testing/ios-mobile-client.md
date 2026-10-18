# Personal iOS editing and Air acceptance

This guide covers Issue #54 original-file editing and Issue #55 native mentor and
memory access. The four bottom tabs remain **日记 / 导师 / 记忆 / 设置**. A USB
connection is needed to install a development build; ordinary diary use and AI
requests do not use that cable.

## Acceptance status

The managed-connection increment replaces manual phone setup with a trusted USB
installation package and dedicated authenticated public HTTPS ingress. Its code,
backend, native build and physical-phone evidence must be recorded separately.
The deployment/phone records below describe the preceding private-ingress candidate
unless explicitly identified as managed installation.
The [managed implementation verification](ios-managed-connection-2026-09-17.md)
records the new synthetic tests and their deployment/phone limits.

The original-file editing implementation passed the local Core/State and focused
native/Simulator checks recorded in [the editing record](ios-original-editing-2026-09-17.md).
The backend is now deployed to the verified Air runtime at revision
`5cfc762d4864748bbd4739d276a63763eea6edea`. Local backend regression passed
**2,020 tests with five optional skips**; **171 relevant tests** passed again in
the Air runtime before restart. The post-deployment check verified private HTTPS,
single-use pairing, all four mentor routes, memory paging, privacy state and an
actual model reply. Its temporary device was then revoked. Existing runtime
configuration (except the opt-in mobile switch and deployment revision), source
`.env`, service definition and pre-existing Tailscale mappings were preserved.

The native client's independent reviews and accepted repairs are complete. The
final full macOS Core/State run passed **426 tests**, including **71 mobile client
tests**, after the credential recovery and overlapping-operation repairs. These
checks alone do not run UIKit or prove physical-phone/iCloud propagation.

After the user completed Xcode setup, Xcode 27.0 (27A266a) built both the Simulator
test targets and signed iPhone app successfully. The existing iOS 26.5 runtime was
remounted with `simctl runtime scan-and-mount`; no Simulator was erased or replaced.
All **37 native tests** passed, and all **nine selected UI journeys** have passing
evidence across the initial run and focused reruns. The initial run's two failures
were test queries for a combined connection label and a system share action exposed
as a cell. The recovery rerun then exposed an immediate assertion during the removal
animation; waiting for the actual disappearance passed. Production client code was
unchanged. Original-file editing, cold reopening and table undo passed in the initial
run. The pre-existing Simulator data files retained their original hashes before
returning to normal use. Actual chat, memory and export screenshots were inspected.

The iPhone app signature, bundle identity, icon and provisioning for the existing
phone passed validation. After reconnection and user unlock on 2026-09-17, the
candidate was installed over the existing `com.riji.local` app on the connected iPhone, without uninstalling it. All pre-existing App data files were backed
up and retained identical hashes after installation. `devicectl` confirmed a normal
launch and the matching Riji process. This is installation/startup evidence, not
an observed physical editing or AI journey. Phone pairing and the four-stage
diary-to-memory path remain unverified. Local logs
and result bundles are under `output/ios-development-2026-09-17/`:
`client-ios27-native-ui`, `client-ios27-ui-selector-rerun`, and
`client-ios27-recovery-final`; the failed runs are retained as failed evidence.
The Xcode 27 Device Hub launcher adaptation also passed ten isolated launcher tests.
The normal launcher then built, installed and launched the app on the existing
Simulator successfully; Device Hub showed the original collection, body editor
and device-local saved state. The launcher-only compatibility fix is local commit
`f8d73a8395dae08a85f70efd8ff8927e88de0806`; no push was performed.
The private phone backup and install/launch/data-integrity receipts are under
`output/ios-development-2026-09-17/phone-install/`. The backup covers Documents,
Application Support and Preferences; it does not copy the external iCloud vault
or export Keychain. Tailscale is installed on the phone, but its connected account
and private-network reachability still need confirmation.

## Check editing first

Post-install feedback on 2026-09-17 exposed a scroll jump when confirming input.
The [viewport correction](ios-editor-viewport-2026-09-17.md) is reproduced, fixed
and tested locally; its new phone installation remains pending. The installation
record above describes the earlier candidate and must not be read as this fix
already reaching the phone.

1. Open Riji. In **日记**, open the already-authorized original folder. Check that
   today's date and original template match the expected document.
2. Tap directly in the document body and type a short, recognizable test sentence.
   No separate append field or heading selector should be required.
3. Dismiss the keyboard. Check the local-save status, open another tab and return,
   then close and reopen the app. The sentence should remain in the same location.
4. In a disposable iCloud test diary, verify the exact change in Obsidian on Mac.
   “Saved on this device” alone does not establish iCloud propagation.
5. Test concurrent changes only in a disposable document: change it on Mac while
   the phone has unsaved input. The app must retain both versions and offer
   recovery instead of silently replacing either side.

Original daily files are editable. Other files in the folder tree currently open
as previews; editing every Obsidian file or running its plugins is not implied.
Offline, unavailable-folder and conflict cases must preserve local input and show
the source problem. Existing recordings and App-local documents remain available.

## Connect to the personal Air

The operator completes the personal installation over trusted USB as described in
[deployment](../deployment.md#managed-personal-installation-dedicated-authenticated-public-ingress).
The app imports `Library/Caches/MobileInstallation.json` into device-only Keychain,
consumes the package and produces a secret-free receipt. The phone requires no
Tailscale, address entry or pairing code. No model key or Review credential enters
the app. Air must be awake with its service and dedicated public ingress healthy.

1. Open the installed app and then **设置 → AI 服务**. Check **服务已连接** and the
   actual capability states. The service page has status/retry, no address/code form.
2. Open **导师** and **记忆** directly. Close/reopen and background/foreground the
   app; the same identity and unsent drafts remain, with no automatic send.
3. A missing package/credential displays **服务尚未激活** and requests an updated
   personal installation. A package or Keychain failure remains an explicit error;
   the operator checks the receipt and unused provisioned device before retrying.
4. Turn off network access temporarily. Local diary editing must remain available;
   mentor and memory failures remain explicit. Restore connectivity and use
   **重试连接**. A consumed package cannot reactivate a disconnected/revoked device.

The operator setup and rollback boundaries are in
[deployment](../deployment.md#personal-mobile-ingress-opt-in). Device re-pairing
creates a distinct native conversation namespace; an authorized origin transition
preserves the current device and archives. Earlier Feishu chat history is not
imported. Disconnecting locally does not revoke an Air credential;
**撤销此设备授权** is the explicit server action.

## Check mentors and memory

Use synthetic content for destructive or failure checks. Reading the user's real
memory list does not authorize changing arbitrary records as test fixtures.

| Journey | Expected result |
|---|---|
| Open **导师**, choose one of the four mentors | Open that mentor's private conversation directly |
| Write a message, switch mentors and return | Each mentor retains its own unsent draft |
| Send a message | Display accepted/processing state, then the actual response from Air |
| Lose connection while sending | Retain input and the original operation; **核对原操作状态** queries that same operation |
| Air reports no durable record | **重试原请求** retries the same ID and exact body only on explicit action |
| Air reports an unknown outcome | Keep it unresolved; do not issue an automatic replacement request |
| Open **记忆** and a record | Show the fact, state, usage permission and available source location |
| Draft a correction while the record changes remotely | Preserve the draft and require review of the changed version |
| Reconfirm/archive/restore a synthetic record | Refresh the authoritative state after Air confirms the action |
| Delete a synthetic record | Require confirmation; delete the memory while retaining the original diary |
| Change a source or revoke its use | Old source-dependent replies become unavailable; subsequent safe work uses current permissions |

Some actions are asynchronous. An accepted operation is not proof that a mentor
has finished generating, or that a changed diary has already been extracted into
memory. Verify the resulting conversation/record, not only its receipt.

## Check extraction controls

In **设置 → AI 服务 → 资料与 AI 使用**, inspect the actual source sections,
date range, recipient mentors and model destinations before changing the four
purpose choices. Folder access is separate from AI consent. Granting consent must
leave extraction paused until an explicit resume; viewing this page must not
grant permission.

For a new-diary-to-memory check, use a disposable file inside an already-approved
test scope, wait for iCloud to reach Air, then observe discovery and extraction
progress and inspect the resulting fact/source. Keep a separate record of the
phone save, Air file arrival, extraction and native memory view. This end-to-end
case remains unverified until all four observations exist.

Roundtable and AI-generated daily priorities/weekly/monthly workflows are separate
increments. This connection does not advertise them as available. No raw vault
upload or alternative phone-to-Mac file synchronization is added.

## Deployment and rollback record — 2026-09-17

Air identity, Fleet preflight and actual process/container ownership were checked
before changes. The verified source whitelist contained 30 files; its hashes were
checked after deployment. Existing source/configuration and 13 SQLite databases
were backed up under the Air runtime's private `deploy-backups` directory before
configuration changes. Backup location and command evidence remain in the ignored
local `output/ios-development-2026-09-17` directory; no secrets are in this guide.

The main service restarted successfully and reports the committed revision on its
loopback Fleet endpoint. The existing Hermes bridge, Mem0 API and Dashboard remain
healthy. A new project-specific mobile proxy binds only `127.0.0.1:8766`; private
Tailscale HTTPS on port 8443 forwards only to that proxy. Existing 443/5173 mappings
were preserved and Funnel was not enabled. Fleet observes `riji-agent-mobile-proxy`;
its initial claim response was lost, so the result was checked by reading the
project rather than repeating the mutation. The proxy rejects administrative,
Hermes, health and Fleet paths; unauthenticated mobile reads return 401.

A separate development-Mac check verified the private TLS path, 401 for an
unauthenticated mobile request and 404 for administration. The synthetic production
smoke test received a completed mentor response from the
existing configured model, confirmed another mentor remained empty, read a
100-record memory page with a next cursor and the actual privacy state, then revoked
the temporary credential. It did not mutate real memories, grant new purposes,
resume extraction, migrate memory providers or send a Feishu message.

For rollback, use the recorded private backup and its existing/new-file manifest.
Stop only this deployment's new mobile proxy and remove only the new 8443 Serve
mapping; preserve all earlier mappings. Restore the backed-up runtime environment
and replaced source files, then run the same runtime's doctor and service restart.
Do not overwrite current databases from their backups without a separately reviewed
data recovery need. Never stop shared Colima or delete Mem0 volumes.
