---
title: '56 - Reachable roundtable invitation with ten-round debates'
type: bugfix
created: '2026-09-21'
status: done
route: oneshot
review_loop_iteration: 2
---

<frozen-after-approval>

## Intent

Repair the invitation flow reported by the user: long selected chat and memory lists must not hide the next/confirm action. Keep the primary action pinned inside the safe area, separate material review from selection, and retain explicit confirmation before sharing. Increase the iOS debate round selector and accepted backend range to ten, with bounded execution that can actually complete ten rounds. Reference mode remains one pass of independent opinions and comparison and must not imply it repeats ten times. Preserve source permissions, existing 1–2 round behavior, stop/recovery, receipt idempotency, and the user's unrelated work and phone data. The user explicitly requested both corrections as this invitation repair; no further product decision is needed.

</frozen-after-approval>

## Implementation Notes

- Continue the current feature branch and preserve the known dirty worktree/index from earlier authorized development. The captured baseline is `output/ios-roundtable-invitation-2026-09-21/baseline.json`; compare this increment against it, not all historical work against HEAD.
- `MobileRoundtableViews.swift`: selection Form, dedicated review Form, pinned next/confirm actions, operation-in-progress guard, and explicit reference/debate description. Exact existing source-preview validation remains in `MobileRoundtableState.swift`.
- Backend: mobile request validation and shared command validation accept 1–10. Existing desktop/Feishu entry limits stay at two; do not widen unrelated ingress. Scale only >2-round mobile debates' immutable request/time budgets; retain private/followup 7/120 and reference/legacy 24/600. Four mentors × ten rounds requires 46 successful stages. Source 900/4000 limits remain unchanged.
- `planner.visible_history`: preserve the initial opinions and comparison when selecting the most recent bounded 24-artifact context; preserve run/revision isolation and empty independent-opinion history.
- Verify synthetic API/orchestration ten-round completion, out-of-range requests, preview binding, budget persistence/source limits, pinned buttons under long content, separate review, return to modification and final receipt. Use project test simulator only; no phone connection is assumed. No real diary/memory changes or publication.
- Existing external design skill is not needed for a focused native interaction repair; no Sleek service or user screenshot upload is used.

## Review Triage and Final Verification

- Fixed the consensus wording to match initial-comparison behavior; no new midpoint consensus mechanism was added.
- Added visible 48-chat / 10-memory / 4000-character admission limits, bounded default chat selection, and removal access after reaching limits.
- Kept read-only preview cancellable; final submission alone locks dismissal. Existing ticket invalidation rejects late responses.
- Show controlled incomplete-run reasons; hide conversation-level stale notices when a later run succeeds. Added state coverage.
- Isolated material preview errors so successful retry removes them without erasing unrelated storage failures. Added failure/retry assertions.
- Strengthened synthetic UI verification to select four speakers and ten memories, verify the ten-round ceiling, preserve selections through return-to-edit, scroll long review content and confirm from the pinned action.
- Fixed shared Form scroll retention using distinct selection/review identity; parent selection state is preserved. Independent final review found no remaining substantive issue.
- Python full suite: 2220 passed / 5 optional skipped. Final Swift core/state: 588 passed / no skips. Ordinary host→invite→save→cold-reopen UI flow passed; final dark XXXL long-material flow passed separately. Earlier failed/controlled-interrupted UI runs are retained as evidence, not counted as passes.
- Air: verified identity/ownership and Fleet checks; exact backup and five-file transfer; 390 runtime tests passed / 1 optional skipped; doctor and controlled main-service restart; health and immutable source revision verified. Phone proxies, Mem0, Hermes and existing configuration preserved.
- Signed iPhone build matches the final tested 105-input manifest and verifies successfully. The user subsequently connected the known phone; this increment is now installed and normally launched, with all app-owned durable files hash-identical before/after overlay installation. No diary or memory data was modified by testing; fixtures use isolated synthetic namespaces.
- No commit/push performed: preserve the previously staged unrelated voice specification and existing dirty feature work. Full handoff: `docs/testing/ios-roundtable-invitation-2026-09-21.md`.
