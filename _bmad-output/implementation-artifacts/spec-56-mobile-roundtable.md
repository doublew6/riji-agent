---
title: '56 - Native persistent mentor roundtable'
type: feature
created: '2026-09-20'
status: done
route: dispatch
review_loop_iteration: 0
baseline_commit: 5b87e5451e084045809b14111e57b843546df643
context:
  - '{project-root}/PROJECT_GUIDE.md'
  - '{project-root}/docs/product/PRD-ios-roundtable.md'
  - '{project-root}/docs/product/ios-visual-design-direction.md'
  - '{project-root}/docs/architecture/ios-mobile-api.md'
---

<frozen-after-approval>

## Intent and scope

User approved #56 and #57 development on 2026-09-20. Implement the full roundtable flow in the approved PRD and `docs/product/ios-feature-review-2026-09-18/wireframe-roundtable.html`, preserving existing #57 changes and all earlier work. The entry is above four mentors, named 圆桌 with subtitle 先和主持人聊聊. Open directly into persistent host chat; no title/background form. Ordinary sending invokes only host. Invite 2–4 fixed mentors into bounded reference/debate runs; all four plus host can see selected materials, explained before confirmation. Show progress, supplement, stop, summarize, continue, conditions/correction, history, and explicit local diary save. Four tabs remain. No implicit private-chat or memory sharing, Feishu mirroring, new production service, external design service, remote operations, staging or commit.

## I/O & Edge-Case Matrix

| Input | Expected behavior and tests |
| --- | --- |
| First/ordinary host message | Persistent host-only reply, zero implicit memory retrieval; API integration and native state/UI |
| Invitation and selected material | Full audience shown; exact selected artifacts/memories only in question, summary and previous context; mode/speakers/revision bound preview; API/UI |
| Another device, user, private/Feishu conversation, revoked source | Denied across read, model input, delivery and save; no Feishu delivery; API |
| Duplicate / timeout / process restart | Stable receipts, unknown reconciled without blind replay, history/drafts restored; API/State |
| Supplement / correction / stop / summarize | Old outputs rejected, old premises visibly superseded, partial summary and bounded budget; API/State |
| Changed history page / offline / account change | Reject mixed snapshots, dated bounded cached history, no known-invalid cache fallback; API/State |
| Local result save / changed file / repeated confirmation | Preview includes AI provenance/date; atomic source-bound append once, durable recovery and preserved preview on conflict; Core/State/UI |

## Acceptance

Mentors → roundtable reaches input without a setup form. Three relaunches retain accepted history/current state/unsent draft. All five synthetic discussion scenarios (planning, disagreement, changed conditions, partial stop, later feedback) exercise real orchestration and preserve advice versus fact. Result save is explicit, local, recoverable and idempotent; Air never edits the phone diary. Existing private mentor, diary/iCloud and memory tests continue to pass. Production model/phone acceptance is separately reported, never inferred from fixtures.

</frozen-after-approval>

## Code Map and decisions

Backend `mobile/service.py`, `api.py`, `proxy.py`, `store.py`; add focused roundtable module/routes. Reuse principal/device auth, `operate`, stable receipts and allowlisted proxy. Fingerprints include conversation ID, full body. Register mobile host/four apps under existing tenant; persist origin binding to device. Extend `check_conversation` narrowly for owned mobile roundtables. `MobileChannel` must persist `mobile-*` rooms and recheck devices; `mentors/local_channel.py:ChannelRouter.inspect_room` explicitly routes that prefix. Never route mobile to Feishu.

`mentors/service.py`, `lifecycle.py`, `policy.py`, `models.py`, `summary.py`, `worker.py`, `sources.py`, `history.py`: reuse current run/state revision, cancellation epoch, leases, budget, unknown request/delivery guards. Existing create auto-fetches memory in policy.freeze: add a narrow `mobile_selected` source scope and creation helper with empty initial sources, host followup and first user artifact. Do not abuse group_only or change personal/Feishu semantics. Persist selected artifact IDs; selected context must filter summary, previous and effective question, including preventing old AI from carrying deselected inputs. Revalidate source permissions/versions at preview, execution, delivery, history and result save. Corrections preserve archive and mark old results for re-evaluation.

API: GET/POST `/mobile/v1/roundtables`; GET `/roundtables/{id}?cursor=`; POST `/{id}/messages`, `/{id}/commands`, `/{id}/materials/preview`, `/{id}/result-preview`, `/{id}/result-validation` (all beneath roundtables). Strict models and response bounds. List items id/title/status/updated_at/revision plus current_id. Detail conversation_id/status/revision/state_revision/summary_version/correction_version/run_id/run_kind/run_personas/mode/rounds/suspended_run_id, messages (id/actor/kind/text/numeric created_at/input_revision/run_id/origin_kind/superseded/unavailable/uncertainties/next_steps/source_ids), current_summary, runs, audience, notice and versioned history cursor. Normalize unavailable messages to non-sensitive placeholders, not silently omitted. Return only sent AI artifacts. Limit summary/runs/page as necessary with honest truncation.

Create `{operation_id,text,statement_kind?}` with the same user_statement/user_plan/user_feedback enum and backwards-compatible user_statement default; messages add expected_revision and statement_kind. Commands use action start_run/stop/summarize/resume/continue/correct/archive/restore, expected_revision and relevant personas/mode/rounds/supersedes/text/preview_hash. Materials preview binds selected artifact IDs, explicit shared memory IDs and expected versions, current revision, mode, speakers and rounds. Memory candidates are owner's active cloud shared records; use existing authoritative MemorySources source/version validation. Journal-derived memory evidence is available through that picker. If adding direct journal excerpts, expose only engine.current_evidence under owner, recall consent, mentor permissions and current version, never trust uploaded raw text as an authoritative journal source. Interface must accurately describe available materials; no whole vault upload.

`mentors/handoff.py:JournalHandoff.create` supports 1–5 chosen artifacts, typed DiscussionProvenance and hashes. Reuse create and `_check` with mobile host binding for mobile preview/revalidation; do NOT call its preview/confirm (those write Air). `result-preview` returns a durable receipt with result_id only, without derived text/provenance. Fresh read-only `result-validation` returns current text/provenance/binding after device/selection/correction/source checks; never cache validation as a completed operation receipt. Thus receipt replay cannot expose revoked source text or authorize a later write. Native explicitly chooses date and may edit copy with edited_by_user flag, preserving authoritative IDs and source metadata.

Native: new Core roundtable models/API methods and focused MobileClientModel extension plus native screens; wire DiaryViewModel from RijiNavigationView. Integrate current tickets/epochs/account scopes, pending operation persistence and receipt recovery, polling only while appropriate. Add bounded dated history cache, no stale resurrection after known revocation/correction. Use #57 cache primitives if suitable, without weakening their rules. Preserve drafts and scroll/selection. Hide stale actions until refreshed; unsupported older server state remains honest.

For saving, add typed local AI result preview/transaction/receipt in RijiCore, reusing coordinated source write and full-byte preservation from BodyRevisionStore/OriginalVaultAccess. Exact format from `journal/content.py:render_ai_result`: `riji:ai-discussion-result` JSON markers, note callout, immutable provenance, AI nature. Preserve other Markdown/frontmatter; never send full diary upstream. Bind preview to account/result/day/source identity/bytes, persist before write, use same operation on recovery, no duplicate after later user edits or relaunch. Changed source requires refreshed explicit preview, not overwrite. Flush pending manual editor first; enforce cloud conflict/recovery holds. Expose helpful retry/changed-document state; do not discard preview. Register all new Swift files in Package and Xcode. Synthetic fixture transport exercises real state and UI.

## Tasks & Acceptance

Local save details: `BodyMarkdown` lives at the end of BodyRevisionStore.swift (not a separate file). Existing body receipts require the full saved bytes still match, so they alone cannot prove once-only AI insertion after later user edits. Keep a dedicated durable result/operation receipt and reconcile already-applied writes before considering a new append. A now-revoked backend result must prevent new writing but cannot make an already-applied local write disappear from recovery evidence. Preserve template anchors and original bytes; append the independent typed AI block under the existing Notes anchor where available, with an actionable retained preview if an anchor is missing. Do not add a dead Air loopback link to the phone UI; retain stable provenance IDs and usable local navigation instead.

Never register a pending AI operation as a generic body transaction: DiaryViewModel.load/flushBody automatically recover those without contacting the backend. AI recovery must remain separately owned, with source revalidation before any unapplied write, and may reuse only the coordinated write primitive. BodyMarkdown currently protects frontmatter and `riji-record` only; protect existing `riji:ai-discussion-result` provenance from ordinary editing while admitting the new marker through a narrow typed append path. Do not globally relax metadata equality to permit arbitrary new markers.

Suggested AIResultStore API: prepare/retain preview; inspect/reconcile already-applied operation; confirm with fresh validation. Preview binds operation/account/result/source/day/before bytes/edited copy/rendered block/after bytes. Transaction freezes that payload; receipt permanently binds its hash/result/block independently of future diary edits. Keep a stable result→operation binding, including across date edits; no new UUID on a retry. Add pending AI state to both sides of body/legacy append/workflow/voice write exclusion and cloud holds, not just UI controls. Recovery may supplement a missing receipt when current bytes equal after, or a uniquely occurring complete exact block proves the applied write after later manual edits. Ambiguous/modified/missing/duplicate blocks in changed bytes stay unresolved. An existing receipt never recreates content the user later deleted. A still-before transaction needs explicit retry and fresh backend validation. Use MarkdownSectionAppender's parsed unique Notes anchor and preserve CRLF; do not guess a missing/duplicate anchor. Test all write/receipt/cleanup boundaries and provenance injection/preservation, including Python content_spans recognizing generated Swift blocks as AI.

- [x] Backend mobile room/identity, host creation, bounded lifecycle/history/materials and result handoff APIs plus proxy/capability.
- [x] Native full chat/invitation/progress/correction/history/offline/receipt flows, local preview and idempotent save.
- [x] Synthetic orchestration, API, Core/State and focused UI tests for every matrix row and five scenarios.
- [x] Extend the existing `scripts/run_ios_simulator.sh` with explicit synthetic feature-review modes (memory overview / roundtable), preserving normal/sample namespaces and dedicated development Simulator. Add a simple local launch entry and exact instructions to `docs/testing/ios-mentor-memory-manual.md` so the user can manually inspect both features on the Mac without Air or real diary data. Clear all fixture flags in normal mode; test argument and namespace isolation with existing launcher tests.
- [x] Update mobile API/test documentation and actual verification report.

## Verification and working tree

Baseline before both features is `output/ios-mentor-memory-2026-09-20/baseline.json` / `baseline/`; #57 changes now belong to this approved batch and must remain. Large earlier dirty work and staged voice spec are unrelated: preserve them. No additional implementation delegation needed. Run relevant pytest then coordinate source freeze with parent for native runner `scripts/test_ios.py`; never edit iOS files while its audit is running. Parent performs integrated review/full verification. No real diary/Air changes; no staging/publication. Finish local implementation rather than placeholders; document narrow technical adjustments without changing frozen intent.

## Execution notes

Implementation started after #57 completed local backend, native and focused UI verification.


Local implementation handoff: backend and native source frozen for parent integrated verification/review. The focused native run exposed an invitation sheet dismissal bug after instant completion; corrected to use the accepted start-run receipt and awaiting rerun. Final new Core/State cases also await the parent runner. Actual results and production boundaries are recorded in `docs/testing/ios-roundtable-2026-09-20.md`; the test-matrix task remains open until that final verification finishes.

## Review Triage Log

Every reviewer finding is preserved before grouping. Evidence was checked against current callers, receipt compaction, request tickets, API selection, rendering and tests. All fixes stay within the approved flows; no prior dirty work is reverted.

| Finding | Verdict | Route | Evidence and resolution |
| --- | --- | --- | --- |
| B1 | high | patch | Completed operation compaction removes result_id before refreshAfterRoundtable; roundtable-ui-3 fails before local preview. Preserve only typed recovery IDs, never derived text. |
| B2 | medium | patch | _selection and result_preview derive eligible IDs from default first history page; older-page reproduction rejects legitimate visible IDs. Validate selected IDs against full authorized current history. |
| B3 | high | patch | Invitation onChange clears only the value while previewMaterials accepts by conversation revision; changed options can receive an older selection. Invalidate the request ticket and bind the options snapshot. |
| B4 | medium | patch | MobileMentorsView gates the entire entry on fetched capabilities. Offline cold launch has valid credentials/cache but nil capabilities. Expose validated offline history without enabling mutations. |
| B5 | high | patch | hasAIResultPending locks the target day, but recovery is account-gated in roundtable; global aiResultPreview also blocks other days. Add local day-bound recovery/cancel independent of a live account, with no new write authorization. |
| B6 | medium | patch | Completed discussion is never polled; expiry clears it without initiating a fresh read. Refresh idle content before expiry and keep offline expiry explicit. |
| B7 | medium | patch | Cache save throws before assigning live roundtable data; roundtableFailure then invalidates it. Retain a validated online response and report cache failure separately. |
| B8 | medium | patch | New-chat state sends only text and creation schema lacks the content kind already offered by the composer. Preserve the existing plan/feedback classification through creation and test the first artifact. |
| B9 | medium | patch | Theme/observation/comparison views call partial-page absence empty even with nextCursor. Use an explicit not-yet-loaded state plus the existing load-more action. |
| B10 | medium | patch | Backend caps conversation list at100 and UI ignores truncated; older conversations have no navigation. Keep bounded pages and expose a cursor/read-more route with an honest truncation notice. |
| E1 | high | patch | invalidateRoundtable leaves read/material/result tickets live; refreshMemory/privacy can invalidate while old replies still satisfy current(). Revoke admission tickets before clearing cache. |
| E2 | high | patch | Same concrete selection race as B3, independently traced; changed invitation options must invalidate pending preview admission. |
| E3 | medium | patch | Same concrete first-page validation restriction as B2; full source authorization must not depend on presentation page. |
| E4 | medium | patch | Same first-message provenance loss as B8; preserve plan and feedback intent through create. |
| E5 | medium | patch | Backend supports summarize(stopped), but action bar shows summarize only active/waiting_user. Make partial stopped summary reachable. |
| V1 | high | patch | Pre-verified test gap: both success and error set aiResultStatus; UI asserting status existence cannot prove insertion. Check success, exact target bytes and cold-reopen once-only content. |
| V2 | medium | patch | Pre-verified regression gap: AI tests call commitBodyEdit but do not exercise legacy append/daily workflow shared guard. Add actual calls with pending AI and cancellation. |
| V3 | high | patch | Same completed-receipt result-ID loss as B1, independently confirmed by UI-3 evidence. |
| V4 | high | patch | Same stale-response resurrection as E1; existing test covers sequential/cold invalidation only. Add held responses released after privacy invalidation. |
| V5 | high | patch | AI preview enumerations decode all files; atomic writes use hidden .tmp files. Real kill can leave partial tmp despite exception tests cleaning them. Filter recognized preview JSON files and test orphan temp bytes. |
| V6 | medium | patch | Two inserted raw-memory jumps contradict original test preconditions (service inactive / tab switch rejected by unsaved body). Remove only those inapplicable jumps; preserve underlying assertions. |

Review runtime limitation: only the blind layer was a fresh context-free agent. The runtime refused more new agents; edge review reused the earlier planning agent, and test-evidence review reused the implementer. These are not represented as three independent context-free reviews. Full HEAD audit and dirty-baseline increment are preserved separately in the private output folder.

Late integrated UI findings (not covered by the earlier review): date round-trip lost its viewport because `load` suspended after publishing the date but before replacing the body; move all reads ahead of publication. Audit also found stale local AI recovery could publish after same-date source replacement. Bind all AI admission to store/source/day/body/lifecycle/action generations, atomically publish recovery state, clear old previews only after a committed source change, and reconcile uncertain writes before reporting failure. Eight new controlled State cases passed with the existing sixteen roundtable cases. A limited follow-up review found one pre-existing unknown-voice recovery read still in `load`'s publication block; it was likewise moved ahead without changing recovery behavior. Keep the first failed UI batch as evidence and rerun the final source.

## Spec Change Log

- Review integration clarification: existing first-message content-kind control must survive creation, completed receipts retain only typed recovery IDs, and bounded conversation history must offer subsequent pages. Keep the approved host-first interaction, strict device/source checks, local-only typed AI writes and permanent idempotency receipts. Repair these narrow integration contracts in place while preserving the captured dirty baseline and all existing diary/voice/iCloud work.

## Remediation verification

The original blind reviewer rechecked B1/B3/B5 and grouped E1/V4/V5 after patches, read-only, without widening the scope. It found typed identifier-only receipts, pre-change invitation invalidation, revoked admission tickets, account-independent local recovery limited to the target day, and orphan-temporary-file filtering closed with corresponding tests. A later narrow review checked the AI admission repair; its remaining pre-existing voice await was moved before state publication and confirmed closed. These are follow-up checks, not additional independent reviewers.

Local verification completed on 2026-09-21: backend 2211 passed / 5 optional skips; full Swift 587 passed / 0 skipped; Native components 59 passed; all 27 distinct UI journeys have passing sealed evidence. The final memory-detail focus fix and stronger keyboard/tab assertion reran all seven mobile-service flows; the twenty unaffected diary/roundtable journeys retain their passing evidence. The summary explicitly records those two input snapshots and earlier failures, rather than claiming one green monolithic smoke. Test runner regression: 80 passed. Full details, screenshots and stage-specific source boundaries are in docs/testing/ios-mentor-memory-2026-09-20.md.

Both local implementation specs are done; Air deployment and physical-device acceptance remain separate. Fleet preflight timed out, so no production sync/restart, phone install, commit, staging or push occurred. Desktop demo launchers and the manual acceptance guide are ready.
