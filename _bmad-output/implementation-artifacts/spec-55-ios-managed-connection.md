---
title: Personal iOS managed Air connection
status: done
phone_acceptance: pending-unlock
issue: 55
baseline_commit: f8d73a8395dae08a85f70efd8ff8927e88de0806
backend_commit: 5b87e54
context:
  - PROJECT_GUIDE.md
  - docs/PRD.md
  - docs/architecture/mvp-architecture.md
  - docs/architecture/ios-mobile-api.md
  - docs/testing/ios-mobile-client.md
---

## Intent

<frozen-after-approval>
The owner explicitly requests changing the personal MVP to open-and-use Air AI: no phone Tailscale, URL entry or pairing-code work. Installation provisions the connection; subsequent launches restore authorization automatically. Retain the existing Air mentor/memory/privacy services, local diary independence, four bottom tabs and the just-fixed editor viewport.

Use a dedicated authenticated HTTPS public ingress to the mobile API. This changes the earlier private-network assumption for this personal installation; it does not introduce public registration or multiuser accounts. Preserve all current mixed working-tree edits. Never commit, stage broadly, deploy or touch real credentials from the implementation agent. Root performs controlled Air deployment and personal installation separately under the user's authorization.

### Decisions

- Add an independent public-only loopback proxy at fixed port 8767, behind HTTPS port 10000; existing 8765, private proxy 8766, Serve 443/8443 and other mappings stay intact. Public routes explicitly exclude enrollment, admin, Hermes, health and Fleet. No model/admin secret ships in the app.
- Operator CLI issues a unique revocable device token for an already verified principal into a newly created private file outside the vault. No printed token, no bundled credential, no public enrollment. The installation package is delivered through trusted USB into `Library/Caches/MobileInstallation.json`; import into existing device-only Keychain, then remove the package. Cache transport avoids backups. A small receipt contains only success/error, used device ID and unused provisioned device ID so the installer can revoke unused credentials. No diary text, token or host in receipts/logs.
- Package contract: snake_case JSON `api_version:1`, `endpoint` (canonical HTTPS origin), optional `previous_endpoint`, optional `device_id` + `device_token` (both or neither). Existing credential wins: same endpoint retains identity; different endpoint requires exact previous_endpoint binding and migrates only that same device's local drafts/operation state to new origin. Never substitute another device/account. Missing credentials use supplied device token. Preserve the original archive; refuse destination collisions unless identical migration data. No automatic request replay or re-enrollment after disconnect/revocation.
- Read Keychain and validate/bound package before consuming it. A successfully read package must be atomically consumed (removed/renamed to a non-imported tombstone that contains no secret) before credential changes; failure to consume means no activation. If Keychain write fails after consumption, leave the old credential intact and report installation incomplete; installer can supply a fresh package. Locked/unreadable Keychain must not be mistaken for empty, and package remains retryable. Receipt write failure must not roll back working credentials or allow package replay.
- Settings uses normal service status, retry, privacy, drafts and explicit revoke. Remove user-facing manual URL/code controls and Tailscale instructions from normal mentor/memory/settings flows. Keep model pairing API for compatibility/tests if needed; no new setup wizard. Missing installation says service not activated / update installation; outages say retry later, diary remains available.

### Acceptance / failure matrix

| Given / when | Then |
|---|---|
| Fresh valid private install package / launch | Credential stored, package consumed, capabilities loaded automatically; no manual setup |
| Relaunch or foreground / prior credential | Same identity, no new enrollment or auto-send |
| Existing credential and authorized origin transition | Same token/device; drafts and pending IDs preserved; old archive retained |
| Wrong previous origin, destination conflict, unreadable local state | Fail closed, original credential/drafts intact |
| Locked Keychain, malformed/oversize/symlink package, incomplete credentials, non-HTTPS origin | No activation, no secret disclosure; safe retry or explicit installation error |
| Consumption or Keychain write failure | No silent replacement or replay; clear failure, original records preserved |
| Revoke/disconnect then restart | Consumed package cannot reactivate device |
| Public route with missing/invalid/revoked token, pair or admin path | Reject before body/upstream access; no enrollment or mixed service exposure |
| Oversize/slow body, saturation, rate limit, timeout, redirects | Bounded failure with generic errors, no credentials logged; later valid request works |
| Existing private adapter and diary tests | Behavior and data remain intact |
| Native settings/mentor/memory UI | Status/retry instead of URL/code; automatic setup fixture reaches mentors/memories |
</frozen-after-approval>

## Code Map

`src/riji_agent/mobile/{cli,store,proxy}.py` provides private enrollment/proxy and hashed revocable token store. Add small provisioning/public-ingress modules where helpful; prefer read-only token lookup for public admission, bounded concurrency/body deadline/rate and exact route filtering. Revalidate tokens each request; do not cache revocation. Keep private create_proxy_app default compatible. Public proxy CLI binds only 127.0.0.1:8767 with access logs/proxy headers disabled; provide a placeholder launchd template, never real host values.

`ios/Riji/MobileClientModel.swift` owns lifecycle/credentials/local state; `MobileKeychain.swift` provides real and UUID-isolated UI fixture factories. `MobileSettingsViews.swift`, `MobileViews.swift`, `RijiNavigationView.swift` own visible connection flows. `ios/RijiCore/MobilePersistence.swift` scopes drafts by origin+device and supports archive retention. Wire a small injectable installation reader into startup, foreground and retry without overriding unresolved credential restoration. Register new sources in Package.swift and Xcode project. Do not edit DiaryBodyEditor.swift.

Relevant tests: `tests/test_mobile_{api,composition,proxy}.py`, Swift core/state mobile tests, `ios/RijiUITests/DiaryFlowTests.swift`. Never real Keychain/network in fixtures. The dedicated test simulator is selected using its local `<SIMULATOR_UDID>`; root owns native UI runs and signing. Prior snapshot for this change is in ignored `output/ios-managed-connection-2026-09-17/before/`.

## Tasks & Acceptance

- [x] Implement private operator provisioning with verified owner, exclusive 0600 output, cleanup/revoke on failure, and the package contract.
- [x] Implement independently bounded authenticated public mobile ingress and launchd template; regression-test old private ingress.
- [x] Implement one-shot installation import, safe credential/state migration, receipts and automatic connection lifecycle.
- [x] Simplify normal connection UX; add isolated managed-install UI fixture and UI assertions.
- [x] Run backend targeted and Swift package suites, cover every matrix row, record exact validation.
- [x] Update architecture, app README and testing guidance, distinguishing code readiness from actual deployment/phone verification.

## Execution Notes

Root handles remote preflight/deployment, native build/UI testing and phone availability concurrently with implementation. Implementation agent may make smaller equivalent internal choices consistent with the frozen safety/experience constraints and report them. No publication or real backend mutations from tests.

Implementation completed for review on 2026-09-17. Full backend: 2,036 passed / 5
optional skips; final backend subset: 57 passed. Full Swift package: 444 passed;
final mobile subset after migration-publication repair: 89 passed. Exact commands,
test boundaries and retained logs are in `docs/testing/ios-managed-connection-2026-09-17.md`.
No commit, staging, production credential issuance, Air deployment or diary-editor
change was performed by the implementation agent. Root owns actual rollout and
native/phone acceptance. Formal review remains separate from these checkmarks.

## Review Triage Log

Review uses the isolated before/after snapshot for this increment, retaining pre-existing work; no index mutation or publication. Three required review layers completed.

| Finding | Verdict | Evidence and route |
|---|---|---|
| Blind 1 — migration encoded size | high | Longer endpoint can push an otherwise readable archive over the loader's 8 MiB cap; Keychain then points at unreadable state. Patch pre-consumption validation and regression. |
| Blind 2 — rollback destination conflicts | false | Collision refusal is required by the frozen matrix; the original and new archives remain readable/exportable. Automatic merging/overwriting would violate preservation. Clarify that rollback needs conflict resolution before a new package; do not weaken collision rejection. |
| Blind 3 — stale successful receipt | medium | A previous receipt survives a failed receipt write and lacks attempt correlation. Patch receipt invalidation; operator acceptance must check package consumption and current issued IDs, not status alone. |
| Blind 4 — replacement between compare/unlink | medium | Trusted USB installer can replace the pathname during an active app's read. Patch atomic claim/validation/consumption and preserve any newer package; document serialized transfer. |
| Blind 5 — server/client size contracts | low | Operator can issue a package the client necessarily rejects. Direct bounds correction before issuance plus tests. |
| Blind 6 — anonymous quota starvation | medium | Anonymous requests spend the same minute bucket as authorized requests. Patch admission budget isolation, retaining bounded pre-auth work. |
| Blind 7 — ineffective header limit | medium | Installed uvicorn standard selects httptools, so the h11-only parameter is ineffective. Explicit protocol and total-header guard with protocol-level validation. |
| Blind 8 — synchronous SQLite on event loop | medium | Locked read-only lookup blocks each request for up to 50 ms before concurrency admission. Patch bounded admission before threaded lookup and deadline regression. |
| Blind 9 — framework method errors | low | TRACE produces generic 405 without no-store instead of documented route rejection. Direct framework exception response correction and test. |
| Blind 10 — server concurrency response | low | Server-level plain-text 503 is already safely handled by MobileAPI's generic error fallback; no credential loss. Clarify application-vs-transport error contract and include no-store default server headers, retaining connection bounds. |
| Edge 1 — expanded migration size | high | Same verified overflow as Blind 1; grouped into its patch. |
| Edge 2 — second destination read race | medium | migrate's second load returns early for any newly appeared destination without comparing it to the validated source. Patch equality revalidation at that read and retain exclusive publication. |
| Gap 1 — no valid POST through public proxy | medium | Reviewer mutation erased forwarded bodies while all 57 tests passed. Add an actual mobile-router integration proving request text, operation ID, effect and receipt through the proxy. |

All retained changes are local corrections within the frozen authorization and preservation boundaries. No public enrollment, multiuser design, or changed user decision is needed.

## Root completion and physical acceptance

All retained review findings were repaired and verified. The final complete backend
run passed 2,045 tests with five optional skips; focused Swift review regressions,
native/editor tests and managed UI flows passed. The new ingress is deployed on
verified Air hardware, with 110 Air tests and an actual public HTTPS mentor,
memory-read and revocation smoke check. Existing services and environment files
remain unchanged. Backend commit `5b87e54` is local only; native privacy guards
passed and the pre-existing voice staged blob was preserved. The broader iOS work
remains uncommitted, with the signed artifact and source manifest retained.

The app is installed on the personal iPhone; all original files were identical
before first launch. iOS rejected launch because the phone was locked. Package
consumption, receipt correlation, unused-device revocation/cleanup and actual
carrier-network acceptance remain pending unlock. No physical-phone service
success is claimed. See `docs/testing/ios-managed-connection-2026-09-17.md`.
