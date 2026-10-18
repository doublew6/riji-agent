---
title: '57 - Structured mobile memory overview'
type: feature
created: '2026-09-20'
status: done
route: dispatch
review_loop_iteration: 0
baseline_commit: 5b87e5451e084045809b14111e57b843546df643
context:
  - '{project-root}/PROJECT_GUIDE.md'
  - '{project-root}/docs/product/PRD-ios-memory-overview.md'
  - '{project-root}/docs/product/ios-visual-design-direction.md'
---

<frozen-after-approval>

## Intent and scope

User approved development of #56 and #57 on 2026-09-20. Implement #57 completely first; #56 follows separately. This approval supersedes earlier discussion-only gates. Deliver native memory overview → theme → summary/evidence → existing source and correction controls, with separate persona observations, comparison/review and recent changes entries. Four main tabs remain. Follow approved prototype in `docs/product/ios-feature-review-2026-09-18/wireframe-memory.html`, using native quiet system styling. Do not use external design services.

Reuse current authoritative memory, owner/persona isolation, journal consent, source lifecycle, mobile authentication and operation receipts. GET must never call a model or schedule work. A user-requested organization action uses the normal bounded queue; never reset historical initialization or stopped/failed versions. No graph database, automatic fact invention, production changes, remote operations, commit or staging. Existing unrelated work must be preserved.

## I/O & Edge-Case Matrix

| Input | Expected behavior / required verification |
| --- | --- |
| Valid legacy or journal report | Real six-theme overview, current evidence, persona boundaries; API and native rendering tests |
| Partial / unorganized / empty / permission denied / failed | Distinct honest state and actual coverage; no fabricated summaries; API and State tests |
| Changed/deleted/withdrawn/corrected evidence | Hide dependent summaries, revalidate before return, retain correction drafts on failure; API and State tests |
| Observations and comparison | Separate non-fact presentation, actual independent supporting dates and counterevidence; API tests |
| Pagination / timeout / relaunch / duplicate organize | Reject mixed versions; reconcile receipts, no duplicate enqueue, drafts survive; API and State tests |
| Offline / account change / revocation | Bounded dated snapshot only, expired or known-invalid content never resurrects; Core/State tests |

## Acceptance

Given available reports, entering Memory shows themes first and evidence is at most three taps away. Original records remain accessible beneath overview and old servers show an honest unsupported state with raw-record access. Given correction/revocation, invalid summaries disappear immediately and a failed write preserves edits. Given a changed snapshot cursor, accumulated pages are discarded and reloaded. No raw reports, foreign records or protected secrets escape through projection.

</frozen-after-approval>

## Code Map and implementation contract

`src/riji_agent/mobile/{service,api,proxy,memory}.py`: current principal/device checks, strict bodies, allowlisted proxy, receipt `operate`, `memory_view`, privacy binding and existing mutations. Add focused `mobile/overview.py`; register GET `/mobile/v1/memory-overview?cursor=` and POST `/memory-overview/actions`, capability `memory_overview`. Review both local/public proxy admission.

GET fields: `version`, ISO `updated_at`, optional ISO `organized_at`, epoch `cache_expires_at` (≤900 seconds); `state` ready/partial/unorganized/empty/permission_denied/failed/invalid; `job_status`; `coverage {eligible,organized,unorganized,stopped,invalid}`; six `categories [{id,title,summary_count}]`; `summaries [{id,category,title,summary,scope,persona_id?,states,evidence_ids,updated_at}]`; `observations [{id,scope,persona_id?,summary,limitation,support_ids,counter_ids}]`; `comparisons [{id,scope,persona_id?,kind,reason,evidence_ids}]`; `evidence` as existing memory view plus observed_at/kind/certainty; `next_cursor?`, `can_organize`, `organize_binding`, `organize_reason?`.

Page at most 20 combined entries; return only referenced evidence, cap 20 refs/entry and response below client limit. Version hashes owner, permissions/binding, catalog epoch, current decorated memory versions/state, full report and latest job, excluding request clock. Version+offset cursor changes return 409 `memory_overview_changed`.

POST `{operation_id,action:"organize",expected_version,binding,acknowledged:true}` uses `operate` kind `memory_organize`; result `{accepted:true,run_id?,state:"pending"|"already_pending"}`. Validate current version and consent binding. Use `OrganizationStore.request_if_idle` to preserve backoff/pending/processing. Disable action for paused/restoring/unauthorized/pending/no eligible work. Existing worker can continue bounded batches: copy must not promise a single batch.

`memory/organization.py`: CATEGORIES, `_parse_report`, `memory_version`, `active_records`; legacy report groups plus top-level versions. `memory/journal_organization.py`: per-group versions, observations, covered/stopped seeds and catalog epoch. Revalidate entire group against current owner/scope/persona/versions; apply authoritative journal `can_send(item,"organization")` and current decorated sources. Reuse `_validate_observations` for independent dates/counterexamples. Do not trust stored JSON or loosen validation based on existing HTML review. Use mutation guard and recheck before returning. Never expose arbitrary report metadata. Existing corrections invalidate epoch and reports.

`ios/RijiCore/MobileModels.swift`, `MobilePersistence.swift`, `ios/Riji/MobileClientModel.swift`, `MobileViews.swift`, `MobileKeychain.swift`: add typed models and focused state/view files; update explicit source registration in Package.swift and Xcode project. Make only necessary existing private helpers accessible to extensions. Keep existing authenticated request tickets, epoch and operation reconciliation. Add safely scoped dated read cache; invalidate persistently on account/permission/source changes, while preserving pending writes/drafts. Never use offline data to authorize mutation. Use existing memory detail/correction UI below new overview; add native theme, observation, comparison and refresh states. Fixture data must be synthetic and exercise real new APIs/state.

## Tasks & Acceptance

- [x] Implement strict projection, organization action, capability, API and proxy routes.
- [x] Implement typed native data/state/cache and complete overview navigation and evidence/correction flow.
- [x] Add meaningful synthetic API, Core/State and focused UI tests covering every matrix row; register tests.
- [x] Update mobile API documentation and a local verification report with actual results and unverified boundaries.

## Verification and working tree

Baseline before this feature: `output/ios-mentor-memory-2026-09-20/baseline.json` and `baseline/` snapshots. The worktree has large unrelated prior edits, and an already staged voice spec; never revert or stage them. Compare against these copies as well as HEAD. Work only on this feature. No additional implementation delegation needed. Parent handles final cross-feature verification and review; report tests actually run, not assumed. Run focused Python tests using `.venv/bin/python -m pytest`; native runner is `scripts/test_ios.py` with global lock/source audit, so avoid editing any iOS input while it runs. Ask parent to coordinate a source freeze before long native suites. All tests use synthetic temporary state, no real Air or diary files. Finish all local implementation, not merely a scaffold; if reality differs, preserve the frozen intent and record a narrow technical adjustment.

## Execution notes

Completed local implementation on 2026-09-20. The strict projection reuses existing legacy/journal validators, source lifecycle and receipts. Native navigation reaches current evidence and the existing correction controls; typed pagination, expiry and independently admitted persistent cache prevent known-invalid content from returning. Raw search explicitly covers the loaded raw page. Idle overview polling is 60 seconds, pending/processing polling is 15 seconds, with foreground and pull refresh retained.

Relevant Python regression: 108 passed. Full Swift Core/State/ProbeSupport: 552 passed, zero failed/skipped. Four focused UI paths passed in `memory-overview-ui-2`; the overview correction/organization path passed after a final View-only receipt-placement fix in `memory-overview-ui-3`. Both test runs retained unchanged source-input audits. Earlier failures and their concrete fixes are preserved in the verification report; no failed run is claimed as passing. The final View-only change did not alter the tested Core/State/backend logic.

See `docs/testing/ios-memory-overview-2026-09-20.md` for exact commands, results, screenshot links and remaining manual boundaries. No Air/production operation, model call, Git commit or staging occurred. Actual phone/Air acceptance, large-text/dark/VoiceOver checks and user assessment of real summary quality remain unverified; parent handles the combined smoke and review after #56.

## Review Triage Log

| Finding | Verdict | Route | Evidence |
| --- | --- | --- | --- |
| B9 | medium | patch | Partial overview pages show a definitive empty state; distinguish unloaded categories using nextCursor and the existing load-more action. |
| V6 | medium | patch | Two raw-memory UI jumps assume a connected/mounted page where tests deliberately prevent navigation; remove those jumps and retain original behavior checks. |
| Late UI focus regression | medium | patch | After correction, the retained input focus reopened the keyboard over the peer tabs. Clear correction focus before showing comparison/confirmation; assert no keyboard and a hittable Settings tab after submission. All seven affected mobile-service UI journeys passed after the fix. |

Other batch review findings are individually recorded in spec-56-mobile-roundtable.md.

## Final integration verification

Completed locally on 2026-09-21. Combined verification: 2211 Python passed with 5 explicit optional skips; 587 Swift package passed; 59 Native components passed; 27 distinct UI flows have sealed passing evidence. Final memory correction focus repair reran all seven mobile-service UI flows, including theme/evidence/correction/invalidation/organization and cross-tab navigation with no obscuring keyboard. Earlier failures, scoped source changes and review limitations remain in docs/testing/ios-mentor-memory-2026-09-20.md. Air deployment is pending its Fleet preflight; no phone installation or real-memory/model acceptance is claimed.
