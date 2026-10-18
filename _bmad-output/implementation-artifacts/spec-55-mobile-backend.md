---
title: 'Device-scoped personal mobile mentor and memory API'
type: 'feature'
created: '2026-09-17'
status: 'done'
route: 'dispatch'
baseline_commit: '77c2c9b231d4e0280b18e3a0ca7270ba4616d5b5'
review_loop_iteration: 2
context: ['PROJECT_GUIDE.md', 'AGENTS.md', 'docs/PRD.md', 'docs/architecture/mvp-architecture.md', 'docs/product/ios-personal-mvp-development-plan.md']
---

<frozen-after-approval reason="User explicitly requested Air-backed mentor and memory integration on 2026-09-17; confirmed personal MVP">

## Intent

**Problem:** Native iOS cannot safely reuse administrator/IM tokens or the existing mixed loopback API. Issue #55 introduces a private mobile boundary over the existing backend.

**Approach:** Add opt-in `/mobile/v1` device-authenticated APIs to the existing runtime and a separate, strict mobile-only loopback proxy on fixed port 8766 for Tailscale HTTPS. Reuse the existing owner, memory services and mentor worker. The parent implements the native client after the contract is verified.

## Boundaries & Constraints

**Always:** Distinct, hashed-at-rest, revocable device credentials; expiring single-use high-entropy pairing, mapped to an existing verified principal/legacy owner. Key material only written to an explicit private operator output file, never logs. Native conversations isolated by device and persona; no Feishu delivery. Revocation blocks queued model/delivery work, including restart recovery. Preserve existing purpose/target/version checks and deletion suppression. Stable operation IDs and content fingerprints; expected memory versions; durable pending/completed/failed/unknown receipts. Unknown side effects never replay automatically. No raw Markdown upload.

**Never:** Expose 8765/admin/Hermes APIs, widen existing listeners, duplicate workers, copy Review/model keys to iOS, change existing providers/consent, reopen initialization exceptions, send real messages or call real models during tests, deploy remotely, commit/stage unrelated work. Roundtable/workflows remain separate increments and capabilities explicitly mark unsupported features.

## I/O & Edge-Case Matrix

| Scenario | State | Result | Failure |
|---|---|---|---|
| Pair/auth | Valid unexpired one-time code | Device bound to verified owner; only device token returned | Expired/reused code and Review token rejected |
| Chat | Stable operation, fixed persona | Create/continue native conversation through service/worker, history with source invalidation | Duplicate same payload returns receipt; altered payload conflicts |
| Memory | Authenticated device and expected item version | Owner-only list/detail, correct/reconfirm/archive/restore/delete via MemoryService | Other owner/device, changed version, restore-in-progress denied |
| Privacy/progress | Current purpose binding | Sanitized current target/purpose/aggregate progress, grant/revoke/pause/resume/scan | No automatic new consent; stale binding fails |
| Revoke | Device has pending generation | Requests and queued generation/delivery blocked | Existing IM conversations continue |
| Unknown operation | Interrupted after intent persisted | Query same ID, preserve unknown result | No blind duplicate mutation |
| Proxy | Only fixed API route/method set | Direct loopback upstream, no environment proxy or redirects | Traversal, encoded paths, oversized body, admin paths refused |

</frozen-after-approval>

## Code Map

- `src/riji_agent/main.py`: composition and CLI. Mount mobile only when enabled; reuse `gateway.mentor_runtime`, `gateway.memory_service`; no second gateway.
- `config.py`: new disabled-by-default switch only. `mobile/` owns enrollment/credential store, operation receipts, facade, strict proxy and CLI helpers.
- `mentors/service.py`, `identity.py`, `models.py`: create/apply/current conversations. Add native local applications and ChatBindings bound server-side to existing principal; do not call Feishu identity resolution.
- `mentors/policy.py`, `local_channel.py`, `runtime.py`: policy/source checks and channel registry. Add a narrow optional device authorization hook at freeze/execution/delivery boundaries without changing legacy behavior. Mobile origin/device binding must be durable before work is runnable.
- `memory/service.py`, `journal_backend.py`: authoritative lifecycle, source decoration and deletion suppression; use public services, not raw Mem0.
- `memory/journal_privacy.py`, `journal_store.py`: four purposes, exact binding, progress/control. Project only safe fields; no absolute paths, internal owner IDs or arbitrary metadata to client.
- `tests/test_mentor_runtime.py`, `test_private_mentor_context.py`, `test_journal_memory.py`: real composed synthetic fixtures.

## Tasks & Acceptance

**Execution:**
- [x] `mobile/`, `main.py`, `config.py` -- opt-in facade, private credential/operation SQLite, single-use pairing CLI, device revoke/list and isolated proxy CLI.
- [x] `mentors/` -- smallest compatible native identity/channel and revocation integration; continuous per-persona messages, get/list history and stop/restart controls.
- [x] `mobile/` -- memory list/detail/versioned mutation and extraction/privacy controls, all calls owner derived; explicit safe allowlisted response schemas.
- [x] `tests/test_mobile_*.py` -- matrix and composed generation/memory/legacy-route coverage, including crash/unknown/no-repeat and revocation during queued work.
- [x] `docs/architecture/ios-mobile-api.md`, `docs/deployment.md`, `.env.example`, `ops/launchd/ai.riji-agent.mobile-proxy.plist` -- exact request/response contract, operator setup, fixed ports, no private values.

**Acceptance Criteria:**
- Given mobile disabled, when existing services run, then their routes and workers behave unchanged.
- Given a paired owner device, when sending to one fixed mentor, then the existing worker generates a source-validated response visible only in that native conversation, with no IM delivery.
- Given a memory mutation, when its response is lost, then querying the same operation distinguishes committed/failed/unknown and never silently retries a side effect.
- Given a private network proxy, when requesting an administrative or escaped URL, then no such request reaches the existing service.

## Implementation Notes

Existing mixed work is authorized and must remain. Restrict this implementation to Python backend, its tests, and listed API/deployment files; no Swift, app icon, existing iOS specs or production writes. Parent has verified Air/Tailscale/Fleet read-only but actual deployment and device acceptance remain separate. Use `uv run pytest`; synthetic data only. Define the complete JSON contract in `docs/architecture/ios-mobile-api.md` for the next native client increment. User-authored chat is sent only after pressing Send; memory sources continue existing authorization.

## Spec Change Log

- Review iteration 2: retain rejected user input until explicit local discard; distinguish active-scope persistence warnings from old-scope recovery and provide safe retry/discard. Keep immutable pending requests, version-bound corrections, successful receipt compaction, TLS/device isolation and all earlier KEEP constraints. Add isolated UI fixtures for history, rebase and recovery without asserting unrun native passes. Backend narrow lifecycle patches collect historical dependencies on delete and bound history including the prospective next user artifact. Re-derive only the demonstrated branches; preserve the shared mixed tree.

- Review iteration 1: refine the non-frozen implementation details and verification for the individually triaged findings below. Keep device-bound authentication, existing providers and explicit consent, strict proxy/TLS, source freshness, original operation deduplication, offline diary editing, four tabs, icon/signing, all pre-existing unrelated work. Re-derive only the demonstrated faulty incremental branches; preserve the baseline and passing paths. Snapshot: ignored output/ios-development-2026-09-17/review-loop1-before/. Do not reset the mixed working tree.

- Deployment acceptance: Fleet global rule requires immutable actual runtime version/revision. Add a startup-frozen loopback-only `GET /.well-known/fleet` with a nonsecret optional deployment revision setting; never forward this route through the mobile proxy. The existing runtime behavior remains unchanged when unset. Verify revision response and proxy rejection.

## Review Triage Log

| Finding | Verdict | Route | Evidence / required outcome |
|---|---|---|---|
| B1 | high | bad_spec | Review reads metadata before the raw backend guard; its later reconfirm can restore an older cloud permission. Guard the complete cooperating read/modify/write operation. |
| B2 | high | bad_spec | MemoryService.retrieve filters permissions only when journal exists. The new permission action is reachable without journal, so all model recall must enforce local/none. |
| B3 | high | bad_spec | Mobile grant publishes consent and invokes callbacks before setting paused; a pending extraction can start. Pause before grant publication. |
| B4 | high | bad_spec | Mobile send reuses current conversation and supplement without refreezing sources; a corrected source leaves the continuing chat interrupted on future sends. Re-derive a safe current-source continuation while retaining historic dependency boundaries. |
| B8 | medium | bad_spec | Conversation response has only a 200-message bound, which can exceed the 4 MiB proxy limit. Bound serialized size and offer retrievable older history. |
| E1 | high | bad_spec | Independently reproduced the B1 race: permission none is overwritten by a concurrent Review reconfirm stale metadata write. |
| E2 | high | bad_spec | Independently reproduced B3: grant notification processes a queued extraction before the later paused write. |
| E4 | medium | bad_spec | Without journal, list_memories caps at 1000; the new cursor then falsely reports end of history. Enumerate authoritative exported owner records. |
| G1 | high | patch | Verified testing gap: disabling the mutation guard still passes mobile tests because the competing write completes before mobile begins. Add event-controlled interleavings in both directions. |
| G4 | high | patch | Verified testing gap: API fixture manually wraps the backend; factory composition has no memory service. Exercise build_memory_runtime with mobile enabled and a successful versioned correction. |


### Integration review follow-up

| Finding | Verdict | Route | Evidence / required outcome |
|---|---|---|---|
| L1-B2 | high | patch | Continuation rotates source tuples; delete only checks those current tuples after erasing artifacts. Collect all historic dependencies before deletion and retain other conversations historical references. |
| L1-B3 | medium | patch | Preflight bounds history before the pending user artifact; an oversized prior answer can reject a valid short next message. Use the exact prospective user artifact when bounding. |
| L1-B4 | medium | patch | Preflight can retain a historical dependency that actual bounded request omits after adding new user input. Compute dependencies against that same final bounded history. |
| L1-B9 | low | reject | Full history hashing and repeated bounded-page encoding can grow costly, but no current personal-MVP latency failure was demonstrated. Replacing storage/paging with indexes and caches exceeds a direct correction; retain current exact-snapshot validation. |
| L1-E1 | high | patch | Independent in-memory reproduction of L1-B2: prior source remains after _stop(delete=True) removes artifacts and current sources. |
| L1-G1 | high | patch | Independent fixture reproduction of L1-B2 through DiscussionHistory.delete after correction and second reply; stale memory text remains in mentor_records. |

## Verification

- `uv run pytest tests/test_mobile_api.py tests/test_mobile_proxy.py tests/test_mentor_runtime.py tests/test_private_mentor_context.py` (adjust exact new test filenames in notes) -- all run/passed, not skipped.
- Parent runs full backend regression before any Air deployment. No actual phone or cloud pass inferred from synthetic checks.


## Verified result

Local full regression: 2,020 passed, five optional skips; 39 related cases passed
again after synthetic-publication placeholders were clarified. Backend-only local
commit `5cfc762d4864748bbd4739d276a63763eea6edea` passed publication guard. No push
was performed. Parent completed separate authorized Air rollout after seven Fleet
preflight passes, actual identity/ownership checks, source/config/13 SQLite backups,
30-file rsync whitelist without delete and 171 passing Air tests. Doctor and restart
passed; immutable running revision and source hashes matched. A private HTTPS smoke
proved pairing, four routes, paged memory, privacy and real model response, then
revoked its synthetic device. No native-phone or diary-to-memory acceptance inferred.
