---
title: '57 - Resume organization after evidence invalidation'
type: bugfix
created: '2026-10-15'
status: done
route: dispatch
review_loop_iteration: 1
baseline_commit: 5b87e5451e084045809b14111e57b843546df643
context:
  - '{project-root}/PROJECT_GUIDE.md'
  - '{project-root}/docs/codex-provider.md'
---

<frozen-after-approval>

## Intent

The user authorized fixing server-side memory organization and running real memory on Air. A working model runtime alone does not restore complete structured coverage: an unchanged seed remains in covered_seeds even when its only report group was invalidated by a changed neighbor, so the worker incorrectly reports no remaining work while mobile correctly shows unorganized memories.

Restore bounded ordinary organization for current authorized memories whose valid group coverage disappeared. Preserve valid results and existing consent, owner, source-version, budget and stopped-history boundaries. The runtime path repair uses an independently installed audited, pinned official CLI and leaves models and credentials unchanged.

## Boundaries & Constraints

Always validate coverage using current versions and surviving groups; continue through the existing five-seed queue and shared daily ledger. Successful historical seeds may be rebuilt as ordinary work after invalidation, with no initialization allowance. Preserve fixed-batch failed/spent/retry_failed/retry_spent records, recovery registration and sent_at values. Existing valid historical report groups remain reusable. Production runs stay on verified Air, with backups, scoped rsync dry-run, runtime tests, controlled main-service restart and private metadata evidence.

Never reset covered reports, budgets, seeds, recovery ledgers or source caches to force progress. Never reopen historical initialization or one-time failed retries; never widen consent or change model/provider. Never synchronize the dirty working tree wholesale, modify production secrets beyond the authorized executable path, restart Mem0/Colima, or alter unrelated iOS/mentor work. No Git publication in this task.

## I/O & Edge-Case Matrix

| Scenario | Input / state | Expected behavior | Failure handling |
| --- | --- | --- | --- |
| Orphaned coverage | Unchanged seed's group contains changed/deleted neighbor | Seed becomes ordinary candidate; current report no longer claims it complete without group coverage | Existing bounded queue and error classification |
| Surviving group | Covered seed also exists in another valid group | Preserve coverage and avoid redundant model request | Current versions remain authoritative |
| Historical success | done or retry_done seed loses group | Rebuild within daily budget without a recovery token | Preserve all historical terminal records |
| Stopped history | failed/spent/retry_failed/retry_spent exact versions | Excluded from automatic candidates | No new recovery registration |
| Existing pending initialization | pending/retry_pending exact authorized versions | Retain existing dedicated ledger and at-most-once send behavior | Current claim and consent checks remain |
| Budget exhaustion | Ordinary rebuild exceeds daily allowance | Defer through existing queue; no ledger reset or unlimited override | Explicit remaining work remains visible |

</frozen-after-approval>

## Code Map

- `src/riji_agent/memory/journal_organization.py`: `_organize` restores compatible report and seed-ledger groups, derives covered/stopped/candidates, sends at most five seeds and enqueues continuation. Reconcile coverage after all group restoration; distinguish pending initialization identity from ordinary rebuilds when calling `_call`.
- `src/riji_agent/memory/journal_store.py`: existing initialization/recovery membership, terminal exclusions and reserve_daily semantics; reuse without schema changes or ledger resets.
- `src/riji_agent/memory/organization_store.py`: add guarded `record_checkpoint` and explicit `latest_report` over existing report_json, preserving `record_input` and `latest(ready_only=True)` semantics for legacy consumers; preserve processing/pending/failed state while making validated journal progress reusable/readable. Legacy input manifests never count as progress; no schema migration.
- `src/riji_agent/mobile/overview.py`: consume explicit `latest_report` for the report snapshot while `latest` independently supplies real job status; keep all stricter group/coverage/source validation unchanged. Confirm stable full-pagination projection using an operator-only read-only audit.
- `tests/test_journal_organization.py`, related initialization/recovery tests: synthetic in-memory Mem0 backend, temporary vault/SQLite and bounded provider doubles. Add a focused regression module if it keeps the tests clear.
- `docs/codex-provider.md`: document stable independently managed pinned runtime and recovery behavior without deployment identities, private counts or diary text.

## Tasks & Acceptance

- [x] Correct orphaned coverage and ordinary-vs-initialization send identity in `journal_organization.py`, persisting validated progress in the existing organization run after reconciliation and each successful seed, with focused synthetic regressions.
- [x] Verify unchanged valid reuse, changed/deleted neighbors, stopped history, one-time retry boundaries, daily-budget deferral and bounded continuation.
- [x] Run relevant tests and complete local Python suite; independently review the focused diff.
- [x] Back up and synchronize only verified changed source/tests/docs to Air, run Air runtime checks before controlled main-service restart, then request one ordinary run.
- [x] Observe real metadata and exact mobile projection; report ready/partial/budget/remaining/stopped honestly in private output.

Given a current seed with no surviving valid group, when ordinary organization runs, then it is either rebuilt, remains counted as pending, or is explicitly stopped/deferred; it cannot disappear from both pending work and valid coverage.

Given a recovered successful historical seed, when its current group becomes invalid, then ordinary rebuilding charges the daily ledger and does not reuse the exhausted recovery registration.

## Implementation Notes

Current primary tree has inherited unrelated edits and a parallel mentor task. User explicitly authorized this recovery; isolate the patch to previously untouched organization source/tests and generic docs, preserving all other files. Full canonical baseline above is captured from Git; Air source is an existing non-Git mirror. Runtime-only recovery and its preflight evidence are already recorded privately; this spec adds the discovered coverage bug within the same requested outcome. No unresolved intent choices remain, and existing authorization covers the bounded deploy/run.

Implementation handoff owns only source, focused synthetic tests and generic documentation. The parent owns complete-suite source freeze, independent review, all SSH/production operations, private runtime reports and final acceptance. Implementation must not stage, commit, push, deploy, contact Air or modify other agents' files. Parent already has a separate synthetic reproduction in progress; reuse its findings when available, while keeping implementation based on this spec.

Review adjustment: persist the already-validated base report after restoring compatible groups and reconciling coverage, and persist each successful seed before the next send. A later budget wait, malformed response, restart or explicit retry must reuse this progress without re-sending completed seeds. Keep true job_status pending/processing/failed; mobile may read valid partial progress without claiming completion. Explicit `latest_report` admits a narrowly marked/versioned, complete journal checkpoint distinct from legacy input_batches; retain existing `latest(ready_only=True)` semantics for other callers, per-group source/privacy/version validation and fallback to compatible old ready reports. Do not save raw model output, invalid groups, source text or a new successful ledger claim. Add synthetic mid-round budget and provider-failure/reopen tests, checkpoint shape/state/legacy and real mobile projection tests, an overlapping multi-memory group case, and ordinary-success send-guard mutation cases. Read stopped versions from the current ledger in test projection helpers; existing full observation/source-guard suites remain part of the release gate.

Focused tests may extend `tests/test_mobile_overview.py`, `tests/test_model_failure_diagnostics.py` and existing organization schema/recovery tests only where trusted checkpoints change the old expectation of absent report_json on failure: assert bounded validated progress and absence of rejected model output/CANARY, preserving safe error classification. The first patch is privately retained under ignored `output/memory-runtime-recovery-2026-10-15/first-coverage-patch/`; its three task-owned source/tests/docs were reverted for this re-derivation. Pre-existing mobile source and affected existing tests were captured separately in ignored `coverage-baseline/` for exact scoped diff review; never replace them wholesale from Git HEAD.

Final verification: local complete Python suite 2410 passed / 5 optional skips with 350 stable input hashes; focused 11-module suite 343 passed. Air complete mirror suite 2271 passed / 12 pre-existing unrelated failures / 10 skips; exact failed-name set matches pre-recovery baseline, new regression modules 126 passed with no failure/skip and stable input hashes. Scoped nine-file backup, dry-run/sync, doctor, idle controlled main-service restart, health/ownership and private real-data projection checks completed. Real processing reached the unchanged daily allowance; ordinary work remains queued rather than exceeding it. Valid prior groups were reconciled locally into the existing waiting run with guarded report-only CAS, without sending, changing backoff or touching historical ledgers. Current real counts and operational details remain only in ignored private output. No automatic Git commit: the inherited mixed tree contains staged work and uncommitted prerequisite modules, so coherent staging would include unrelated work; exact diff and manifest are retained for later scoped publication.

## Spec Change Log

- Review loop 1: a successful ordinary rebuild followed by budget/provider failure discarded charged progress because only final reports were saved. Add durable validated journal checkpoints without changing terminal history, queue/backoff or budgets. KEEP: surviving-group coverage reconciliation, ordinary None send identity, valid-group reuse, stopped-version exclusion and the existing twelve regression scenarios. Re-derive only this task's owned source/tests/docs, preserving unrelated dirty work.

## Review Triage Log

| Finding | Verdict | Route | Evidence |
| --- | --- | --- | --- |
| Blind 1 | medium | bad_spec | Ordinary results were only persisted after all five calls; a later daily-budget/provider failure loses paid progress. Address durability in this requested recovery path. |
| Blind 2 | low | patch | Synthetic mobile helper used report stopped_seeds; read the live temporary initialization ledger to match production. |
| Blind 3 | medium | bad_spec | Singleton search cases do not exercise overlap/replacement of multi-memory groups in rebuilt ordinary coverage; add a targeted scenario. |
| Blind 4 | medium | bad_spec | Existing guards are unchanged and prior tests cover initialization, but the newly enabled successful-history ordinary branch needs a focused guarded-provider ledger-preservation test. |
| Blind 5 | false | reject | Existing cross-batch observation and full mobile projection tests exercise independent dates, counterevidence and source invalidation; the new pure coverage helper is intentionally used only for empty-observation groups. No production observation validator is bypassed. |
| Edge 1 | medium | bad_spec | Same lost-progress defect as Blind 1; persist success before later budget failure and resume without repeated send. |
| Verification | false | no action | Independent verification reviewer found no additional gaps; no finding to patch. |
| Round 2 blind 1 | medium | defer | Latest status still parses JSON before latest_report fallback. This pre-existing corrupted-database path is not produced by validated checkpoint writes; track separate corruption recovery rather than widen this runtime repair. |
| Round 2 blind 2 | medium | defer | Malformed ready container fields could already stop the old `.items()`/group iteration path. Current normal writers serialize valid report shapes; arbitrary ready-report corruption remains separate recovery work. |
| Round 2 blind 3 | medium | defer | Malformed initialization ledger JSON was also loaded directly before this patch; per-row cache corruption recovery remains separate. |
| Round 2 blind 4 | medium | defer | Existing stale-processing claim marks interrupted runs failed and requires an explicit organization action. This patch preserves validated progress across that action but does not change crash retry policy; controlled deployment waits for idle. |
| Round 2 blind 5 | false | reject | All actual checkpoint writers use the report builder, which derives counts/manifests from current records. Worker re-derives candidates and Mobile independently computes coverage from validated groups/current records, so the injected inconsistent metadata does not fabricate user-visible organized coverage or grant sending authority. |
| Round 2 blind 6 | false | reject | organized_at already denotes selected report publication/update time: old zero-send ready reconciliation also refreshed it. Source/event dates remain separate evidence fields; the API does not claim this is the last cloud-inference timestamp. |
| Round 2 blind 7 | medium | defer | Non-seed observations were already excluded by the worker but counted unorganized by Mobile before this task; preserve truthful counts and track actionable coverage separately. |
| Round 2 blind 8 | low | defer | Existing observation validation accepts nonempty whitespace; checkpoint storage deliberately preserves that compatibility. A shared semantic text-validation change is separate from coverage/durability recovery. |
| Round 2 blind 9 | low | defer | Only worker restoration and Mobile intentionally consume partial checkpoints; Review and derived-observation recall retain completed-report semantics. Documented private limitation; confirmed raw-memory retrieval is unchanged. |
| Round 2 edge | false | no action | Independent edge reviewer found no confirmed new regression. |
| Round 2 verification | false | no action | Independent verification reviewer found no additional gaps. |

## Verification

- `.venv/bin/python -m pytest tests/test_journal_organization.py` plus new coverage regression module and initialization/recovery/mobile-overview suites: all relevant checks pass with no real diary/model data.
- Complete local Python suite: record exact passed/failed/skipped totals and input hashes. Air release gate uses its actual source mirror; retain and disclose its pre-existing unrelated failures.
- Real Air read-only projection: matching catalog epoch, stable pagination, actual organized/unorganized/stopped coverage and historical ledger digest. Real run metadata retains request counts and budget usage without publishing identifiers or content.
