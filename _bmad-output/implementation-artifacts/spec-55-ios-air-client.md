---
title: 'Personal iOS mentor and memory client'
type: 'feature'
created: '2026-09-17'
status: 'done'
route: 'dispatch'
baseline_commit: '77c2c9b231d4e0280b18e3a0ca7270ba4616d5b5'
review_loop_iteration: 4
context: ['PROJECT_GUIDE.md', 'AGENTS.md', 'docs/PRD.md', 'docs/architecture/mvp-architecture.md', 'docs/product/ios-personal-mvp-development-plan.md', 'docs/product/ios-visual-design-direction.md']
---

<frozen-after-approval reason="User requested Air mentor and memory integration on 2026-09-17 after confirming personal MVP">

## Intent

**Problem:** The four-tab app only exposes placeholders for mentors and memory; connecting a phone must use device authentication rather than existing administrative/model secrets.

**Approach:** Implement a native SwiftUI client over Issue #55's `/mobile/v1` contract. Keep four parallel bottom tabs, hide connection/setup under Settings, preserve uninterrupted local diary use. Open any fixed mentor directly into its existing device/persona conversation. Provide real memory lifecycle and extraction/authorization controls.

## Boundaries & Constraints

**Always:** HTTPS with normal certificate verification; no redirects or credentials forwarded to other origins. Device token in nonsynchronizing ThisDeviceOnly Keychain. Connection changes discard account-specific displayed data and reject late responses. Local draft and durable operation IDs isolated by endpoint/device/persona. Send content only on user action; durable operation query after timeout, no automatic repeated side effect with a new ID. Retain drafts until an accepted receipt; show queued/running/failed/unknown/revoked and stop controls honestly. Backend is authoritative for source freshness and privileges; don't persist model answers/memory caches beyond their validated response without revalidation. All four tabs and existing icon, native diary save/navigation stay intact. Offline local diary needs no Air.

**Never:** Copy provider/Review secrets to client, auto-grant four purposes, transmit raw vault/journal, claim old Feishu history merged, imply unsupported roundtable/workflows work, silently clear unsent drafts, alter live Air services, install on a device, create extra simulators or delete any app data. Tests use synthetic transport and isolated namespaces only.

## I/O & Edge-Case Matrix

| Input/state | Expected output | Error path |
|---|---|---|
| HTTPS server URL + one-time pairing code in Settings | Device credential in Keychain, sanitized capability status | Invalid TLS/URL/code/revoked credential stays disconnected; no secret logs |
| Select one of four server-defined mentors | Continuous native conversation with persona-isolated draft/history/request | Network down preserves draft and offers status retry, not duplicate Send |
| Send, response lost, restart | Persisted operation ID query reconciles same request | Unknown operation displayed unresolved, never blindly replays mutation |
| Memory list/detail | Facts and sanitized source/freshness info | Expired source text unavailable; errors never masquerade as empty list |
| Correct/reconfirm/archive/restore/delete | Expected-version mutation and resulting receipt | Conflict refreshes current item, retains user's correction |
| Extraction status/control and four purposes | Explicit changes based on current binding | Revoke/stale binding reflected; no inferred permission |
| Switch tab while journal has pending edit | Existing save/recovery behavior preserved | Save failure retains text; no broken editor integration |
| Endpoint/account change or revoke during request | Clear previous server data; old request ignored | Never show another endpoint's cached chat/memory |

</frozen-after-approval>

## Code Map

- `ios/Riji/RijiNavigationView.swift` -- replace mentor/memory placeholders; connection settings destination, retain diary transition logic exactly.
- New `ios/RijiCore/Mobile*.swift` -- Codable contract, strict transport, errors, local draft/pending-operation persistence with Foundation.
- New `ios/Riji/Mobile*.swift` -- Keychain credential store, observable application state and SwiftUI native views.
- `ios/Riji/RijiApp.swift` -- own shared mobile model if necessary; maintain diary startup and day changes.
- `ios/Package.swift`, `ios/Riji.xcodeproj/project.pbxproj` -- explicit source membership for production, state tests and native test target.
- `ios/RijiCoreTests/`, `ios/RijiStateTests/` -- synthetic transport, durable recovery, cross-account isolation, version conflict and privacy regression.

## Tasks & Acceptance

- [x] Native secure connection and capability state in Settings; no home configuration chore.
- [x] Four mentor list/chat, continuous history, per-persona draft, operation recovery/polling and stop/resume only if contract supports.
- [x] Memory list/detail/correction/reconfirm/archive/restore/delete with versions and explicit destructive confirmation; extraction state/purposes and controls.
- [x] Durable request IDs plus timeout/restart behavior, isolation across connection changes and safe client errors.
- [x] Focused tests, isolated build, manual walkthrough guide; preserve source-byte and four-tab behavior.

## Implementation Notes

Implementation checkout `<implementation-worktree>`, branch `codex/55-ios-air-client`. It contains a snapshot of the main checkout's current iOS tree. Work only on the new client files and listed wiring; do not modify editor/core vault files, existing icon, signing files or Python backend. No further subagents. Backend agent `/root/air_mobile_investigation` writes the contract at `docs/architecture/ios-mobile-api.md`; coordinate directly with that agent and read the complete contract before endpoint-specific implementation. Never invent a production endpoint/schema as a stub. Until it is available, read context and build independent secure transport/persistence scaffolding. Keep DTOs consistent with real backend tests; report unresolved integration issues to parent.

Keep a manifest of exact modified/new files and record a pre-edit copy of any existing files you touch under your checkout's ignored `output/ios-mobile-client/before/`. Parent integrates only your incremental patch over the snapshot, preserving editor repairs in the main checkout.

Do not run Xcode on the normal shared derivedData or install into Simulator. Use `/tmp/riji-ios-mobile55-derived` for build and `/tmp/riji-ios-mobile55-swift` for SwiftPM. Parent owns final integrated normal build/UI tests. Test synthetic fixtures may have a DEBUG-only isolated configuration behind `RIJI_TEST_CONTAINER` UUID; never read real keychain credentials under UI test mode. Update `docs/testing/ios-mobile-client.md` with actual behavior and steps, no invented real-device pass. All UI Chinese and comments English, system styling per approved design document.

## Spec Change Log

- Review iteration 2: retain rejected user input until explicit local discard; distinguish active-scope persistence warnings from old-scope recovery and provide safe retry/discard. Keep immutable pending requests, version-bound corrections, successful receipt compaction, TLS/device isolation and all earlier KEEP constraints. Add isolated UI fixtures for history, rebase and recovery without asserting unrun native passes. Backend narrow lifecycle patches collect historical dependencies on delete and bound history including the prospective next user artifact. Re-derive only the demonstrated branches; preserve the shared mixed tree.

- Review iteration 1: refine the non-frozen implementation details and verification for the individually triaged findings below. Keep device-bound authentication, existing providers and explicit consent, strict proxy/TLS, source freshness, original operation deduplication, offline diary editing, four tabs, icon/signing, all pre-existing unrelated work. Re-derive only the demonstrated faulty incremental branches; preserve the baseline and passing paths. Snapshot: ignored output/ios-development-2026-09-17/review-loop1-before/. Do not reset the mixed working tree.

## Review Triage Log

| Finding | Verdict | Route | Evidence / required outcome |
|---|---|---|---|
| B5 | high | bad_spec | The view uses the polled memory version when submitting an existing correction draft. Persist the version when drafting and require deliberate rebase after remote changes. |
| B6 | medium | bad_spec | Every full request remains in MobileLocalState permanently while save/load cap it at 8 MiB. Compact settled receipts without removing unresolved bodies or drafts. |
| B7 | high | bad_spec | setDraft replaces RAM state before a failed save; successful polls clear error and disconnect/401 discards local. Keep unsaved recovery separate and visible across credential invalidation. |
| B9 | medium | bad_spec | Encoded size validation throws before transport but markFailure assigns unknown; the same invalid body remains blocking after a 404. Reject before registering or mark definite unsent failure. |
| B10 | medium | patch | MobileConversation.notice is decoded but unused in chat, obscuring source revocation and failure recovery. Display safe actionable notice text. |
| E3 | medium | bad_spec | A 3000-family-emoji draft passes grapheme count but exceeds 64 KiB; deterministic local rejection follows the B9 unresolved path. |
| G2 | medium | patch | Verified testing gap: Swift tests HTTP 409, whereas backend records failed memory_version_changed in HTTP 200 receipt. Exercise the actual receipt and persisted draft. |
| G3 | high | patch | Verified testing gap: fake final response and separate delegate invocation never exercise production URLSession installation. Test actual transport redirect refusal with a controlled destination. |


### Integration review follow-up

| Finding | Verdict | Route | Evidence / required outcome |
|---|---|---|---|
| L1-B1 | medium | bad_spec | compact clears rejected request A after user types B; the failed operation is the remaining copy of A. Preserve rejected text as recoverable until explicitly discarded, without blocking new submissions. |
| L1-B5 | low | patch | Paging-conflict handling checks epoch before, but not after awaited refresh. Add the existing account/epoch guard after the await so stale errors cannot overwrite newer connection state. |
| L1-B6 | medium | patch | Source-revoked resume still checks obsolete frozen sources; only sending a new message uses current-source continuation. Hide the ineffective resume action and explain the existing new-message route. |
| L1-B7 | medium | patch | Real worker model timeout/transport/server/auth/quota codes fall through to generic copy. Map established safe codes and uncertainty distinctions without adding model retry behavior. |
| L1-B8 | medium | bad_spec | Recovery warning reads all scopes, labeling a new successfully saved draft unsaved; old RAM-only state has no settle action. Scope composer status, show other-scope recovery separately, and offer save retry or explicit discard after export/copy. |
| L1-B10 | medium | bad_spec | New older-history/comparison/export controls have no actual UI fixture journeys. Add synthetic states and explicit UI journeys; execution remains truthfully pending Xcode availability. |

## Verification

- Focused SwiftPM transport/state tests and simulator build using isolated derived data.
- Parent performs integrated API contract test, real UI tests and Air deployment separately.


### Final client review (all three layers collected)

| Finding | Verdict | Route | Evidence / required outcome |
|---|---|---|---|
| L2-B1 | medium | patch | visibleArchives excludes an unsaved deletion of the last draft. Include every unsavedRecovery scope even when its current text is empty. |
| L2-B2 | medium | patch | model_refused is interrupted, but new-message continuation permits only source_revoked. Show the existing explicit stop action for a refusal, then explain adjusting and sending after stop; do not change backend retry semantics. |
| L2-B3 | low | patch | The latest failed receipt survives explicit body discard; unconditional retained-input copy is then false. Derive the label from hasFailedInput. |
| L2-B4 | medium | bad_spec | discardFailedInputs loads all disconnected archives; unrelated corrupt JSON throws before selecting a healthy scope. Load and validate exactly the requested scope, preserving fail-closed warnings for actual corrupt data. |
| L2-B5 | medium | bad_spec | Recovery operations clear any storage error by matching its localized string, including current credential-store failures. Keep credential failures separate; clear only recovery errors owned by the settled scope. |
| L2-B6 | medium | bad_spec | Same-endpoint re-paired devices have indistinguishable archive headers before bulk deletion. Display a non-secret scope/device discriminator and operation target labels in rows and destructive confirmations. |
| L2-B7 | low | patch | Rebase fixture advances on second detail read, which polling can trigger. Replace read-count mutation with an explicit DEBUG-only fixture transition and stable memory row identifier. |
| L2-B8 | low | patch | The history UI journey never sets or asserts a draft despite its claim. Enter nonempty text before pagination and verify it survives. |
| L2-E1 | medium | patch | Same empty-unsaved-deletion defect as L2-B1, independently verified before grouping. Preserve retry/discard access. |
| L2-G1 | medium | patch | Pre-verified gap: mentor text retention tests do not exercise memory content. Submit A, type B while awaiting failure, reload, assert failed content A and current draft B. |

## Current Narrow Repair Instructions

Work in the main checkout, not the previous isolated checkout. Re-derive only the faulty archive/recovery branches listed above, using the snapshot under ignored output/ios-development-2026-09-17/client-final-before/ as reference. Preserve the entire mixed working tree. KEEP all prior isolation, immutable operations, original-version corrections, explicit recovery/discard, source handling, compaction and passing checks. No backend, deployment, signing, editor or icon changes.

Agent owns MobileClientModel, MobileSettingsViews, MobileViews, MobileModels, MobilePersistence and their Core/State tests only. Parent owns MobileKeychain.swift and DiaryFlowTests.swift fixture fixes. Add a stable mobileMemory-<id> accessibility identifier to memory navigation rows for the parent UI tests. Apply findings L2-B1 through L2-B6, L2-E1 and L2-G1; use the already-supported stop control for refusal recovery and test notice/action state. Protect Keychain failure visibility from unrelated archive recovery and refresh. Scoped archive reads validate hash, endpoint and device before mutation. Destructive labels identify the scope and target without tokens or model secrets. Run only mobile Core/State tests with the CLT build plus official macOS XCTest command established in output/ios-development-2026-09-17/run-client-repair-checks.sh. Do not invoke xcodebuild, real network, Air or native UI tests. Return exact changes and checks; no subagents, commits or broad refactors.

## Spec Change Log (iteration 3)

- L2-B4/B5/B6 refine scoped local recovery: one damaged account archive must not prevent operations on another; settling old local recovery must not erase current credential failures; destructive recovery controls identify their local scope and target. Avoid global archive enumeration and localized-string error ownership. KEEP all earlier positive constraints and passing behavior; replace only these demonstrated faulty branches. Parent stabilizes synthetic UI transitions and draft-preservation assertion separately.


### Client recovery follow-up (all three layers collected)

| Finding | Verdict | Route | Evidence / required outcome |
|---|---|---|---|
| L3-B1 | medium | bad_spec | Scoped mutation works but first archive-page enumeration still fails as a whole when one file is corrupt. Return healthy states with an explicit unreadable-record warning; never overwrite/omit the damaged state silently. |
| L3-B2 | low | patch | refreshAfter restores only the storage error string; didSet removes its recovery scope. Preserve both through the refresh so successful retry/discard clears the stale warning. |
| L3-B3 | medium | bad_spec | A transient Keychain read error has no non-destructive retry within the app. Add an explicit Settings action to reload the existing credential and state; do not delete it or request pairing first. |
| L3-B4 | medium | patch | Sending becomes enabled while stop is in flight because the conversation is cleared. Check stop busy/unresolved and a currently validated sendable conversation in both the model and composer. |
| L3-B5 | low | patch | Generic interrupted status suggests continuing even for required-stop/source-change notices. Use a neutral interrupted status; the specific notice supplies the action. |
| L3-B6 | low | patch | Recovery labels omit the operation action when no body text exists. Include the safe action name as well as target/request, including privacy operations. |
| L3-B7 | low | patch | Empty pending deletion appears without the draft name. Show which draft was cleared and that discard restores its saved version. |
| L3-B8 | low | reject | The revised synthetic UI test deliberately proves version-bound draft recovery across restart; live source-change/rebase behavior is separately asserted by model tests. Additional native fixture transition machinery is more than a direct correction, and both UI variants would remain unrun under the present Xcode block. No passing live native coverage was removed. |
| L3-E1 | low | patch | Independently verified L3-B2: refreshAfter drops recoveryFailureScope before a successful local retry. |
| L3-E2 | medium | bad_spec | Independently verified L3-B1: first disconnected recovery-page load hides all good archives when one JSON is damaged. |
| L3-G1 | medium | patch | Pre-verified gap: refusal helper tests do not cover actual toolbar/composer wiring. Parent adds a synthetic refused/stop fixture and native test; native execution remains blocked, never reported as passed. |
| L3-G2 | medium | patch | Pre-verified gap: filename/body identity mismatch is only tested through scoped load. Exercise archive enumeration with a decodable mismatched record and a healthy record, preserve bytes, display healthy data and a warning, avoid duplicate-scope dictionary crash. |

## Current Repair Override (iteration 4)

This supersedes the iteration-3 action list, without changing ownership or KEEP constraints. Re-derive only failed whole-archive enumeration and unavailable credential-read retry, plus the small demonstrated guards/copy corrections above. Snapshot: ignored output/ios-development-2026-09-17/client-loop4-before/. No blanket reset or deletion. The healthy archive UI must remain reachable when one sibling is corrupt, while signaling incomplete recovery and preserving corrupt bytes. Re-reading a credential is a local explicit recovery action in Settings; it must retain drafts, reject stale account responses, and never delete/re-pair/send automatically. Preserve credential error visibility until actual successful credential access. Stop in-flight or unknown blocks sending and a refreshed valid conversation is required before send; an empty new conversation is valid. Track storage-error ownership alongside the error through refresh. Show neutral interrupted status, safe operation action names, and named unsaved draft clearing. Parent owns MobileKeychain/DiaryFlowTests fixture work for L3-G1. Keep stable IDs and all existing passing safety and behavior tests; update only expectations dictated by the verified archive partial-read contract. Add targeted tests for these actual transitions and run only mobile Core/State checks. No backend/deployment/icon/editor/signing changes, no subagents or commits.

## Spec Change Log (iteration 4)

- L3-B1/E2 make partial archive recovery actionable in the actual disconnected-page data path, with explicit unreadable count and unchanged damaged bytes. L3-B3 adds non-destructive credential re-read. KEEP identity validation, pending requests, previous account boundaries, distinct credential errors, source versions and all prior recovery semantics. Fix the demonstrated stop window and storage-error restore alongside those two branches; no broad redesign.


### Final narrow review (all three layers collected)

| Finding | Verdict | Route | Evidence / required outcome |
|---|---|---|---|
| L4-B1 | medium | patch | canSend checks mentor/stop but not pending resume control; include control busy/unresolved and exercise stopped read during held/unknown resume. |
| L4-B2 | medium | patch | Successful Keychain read clears credentialError before a failed local-state load; preserve the existing retry action until the entire recovery succeeds. |
| L4-B3 | medium | patch | retryCredentialRead invalidates displayed data but leaves old active credential/local if a new credential's state load fails. Pause old service identity before the retry, retain existing durable/unsaved drafts and show failed restoration explicitly. |
| L4-B4 | low | reject | Unreadable count cannot identify damaged records, but it accurately warns that recovery is incomplete and retains all bytes. A per-record raw-file export/repair interface is additional product behavior for uncommon corruption, beyond a direct correction; no healthy content is hidden now. |
| L4-B5 | low | reject | While an explicit draft deletion is unsaved, the archive shows its named pending deletion, with copy/export for other current text and explicit restoration of the saved state. Adding simultaneous saved-version browsing is extra recovery UI; no retained text is deleted by the current read path. |
| L4-B6 | low | patch | Synthetic refusal fixture stopped flag is global. Use a persona set and extend its UI journey to verify another mentor remains refused. |
| L4-B7 | medium | patch | Sendable-state whitelist lacks direct coverage of all supported/unknown states. Add a small data-driven model-contract test without changing the whitelist. |
| L4-E1 | medium | patch | Independently verified L4-B1 resume overlap; preserve original control operation until settled. |
| L4-E2 | medium | patch | Independently verified L4-B2 local-load failure after successful credential read; keep retry reachable. |
| L4-G1 | medium | patch | Pre-verified gap: source-invalidated conversations are not tested through canSend/send. Exercise all three supported notices, original input and completed receipt. |
| L4-G2 | medium | patch | Pre-verified gap: partial/all-corrupt archive warning has no page fixture. Parent adds isolated UUID mixed/all-corrupt archives and native assertions; execution remains explicitly unrun under Xcode's current agreement dialog. |


## Final handoff

All accepted findings from the completed three-layer reviews were repaired. The
last four implementation/test patches passed 47 State and 13 Transport tests. The
parent's final complete Core/State run passed **426 tests, zero failures** on
2026-09-17, including 47 MobileClientModel, 11 MobilePersistence and 13
MobileTransport tests. Evidence: `output/ios-development-2026-09-17/client-release-macos-full.log`.
Root also added isolated native
journeys for refusal/confirmed stop/persona isolation and mixed/all-corrupt archive
warnings. Those journeys are source/parser-checked only, not executed.

Earlier native release acceptance was blocked by Xcode: checkFirstLaunchStatus returned
69, and independent native UI inspection shows the Apple SDK agreement dialog.
The dialog was raised without accepting it. The user subsequently confirmed setup;
the final build/UI task is now complete as recorded below. The native feature tree
still overlaps earlier uncommitted voice/editor/navigation work and is not bulk
committed. Prior staged voice work and all original data/signing/icon assets are
preserved. At that stage the new iPhone installation was still pending; the later
physical installation record below supersedes that status.

Air deployment is separately complete: backend commit
5cfc762d4864748bbd4739d276a63763eea6edea, 2,020 local backend passes plus five
optional skips, 171 Air passes, verified private HTTPS/pairing/memory/privacy/real
model reply and revoked temporary device. No push was performed.

## Xcode-ready native acceptance

Xcode 27.0 (27A266a) passed first-launch checks after the user's confirmation.
The existing iOS 26.5 runtime only needed `simctl runtime scan-and-mount`; no device
replacement, erasure or new runtime download was needed. Simulator build-for-testing
and the signed iPhone build passed. All 37 native tests passed. Nine selected UI
journeys have passing evidence across the initial run and focused reruns; earlier
failures remain recorded. Test-only corrections target the combined connection label,
the system share action's actual cell type, popover dismissal and completed removal
animation. Production client inputs were unchanged. Normal Simulator data hashes
matched for all pre-existing files before restoring normal mode. Screenshots of
chat, memory correction and actual recovery export were inspected.

Evidence lives in `output/ios-development-2026-09-17/`: `client-ios27-build.log`,
`client-ios27-phone-build.log`, `client-ios27-signature-check.json`,
`client-ios27-native-ui.{log,xcresult}`, `client-ios27-ui-selector-rerun.{log,xcresult}`,
`client-ios27-recovery-final.{log,xcresult}` and `simulator-final-integrity.json`.
Ten isolated launcher checks cover the observed Xcode 27 Device Hub location while
retaining previous Simulator support and no-replacement failure behavior.
The physical iPhone subsequently reconnected and the user unlocked it. The signed
candidate was installed over the existing app on the connected iPhone, with no
uninstall. All original App data files were backed up and retained identical
hashes after installation. A normal launch succeeded and the matching Riji process
was verified. Private-network pairing, actual phone editing and new iCloud
diary-to-memory extraction remain explicit physical acceptance work. Installation
receipts and the private backup are in `output/ios-development-2026-09-17/phone-install/`.

The normal launcher subsequently completed its actual build/install/launch on the
existing Simulator. Device Hub exposed the original collection, body editor and
device-local saved state. The isolated launcher compatibility fix is committed as
`f8d73a8395dae08a85f70efd8ff8927e88de0806`; privacy hooks passed and no push occurred.
The prior staged voice specification remains staged, and the mixed native feature
worktree is retained without bundling unrelated earlier increments into that commit.
