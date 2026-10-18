---
title: Mentor private and persistent group chats
created: 2026-10-15
type: feature
status: done
route: dispatch
review_loop_iteration: 1
context:
  - docs/design/mentor-dialogue.md
  - docs/DESIGN.md
baseline_commit: 5b87e5451e084045809b14111e57b843546df643
---

<frozen-after-approval reason="User approved PRD v0.3.1 and explicitly requested development on 2026-10-15">

## Intent

Deliver the approved mentor experience: four warm, interactive portraits are the only private-chat entry; the lower home list contains persistent groups. Select two to four mentors, create an empty group and chat directly, without host, title, background or meeting forms. Identical members can have distinct groups. Existing chats remain accessible.

## Boundaries & Constraints

Preserve the F North Star visual baseline, four main tabs, original diary editing and existing data. Group members are the actual audience; @ changes responders only. No automatic private-chat/history/material import, hidden host or implicit extra permissions. Use the existing authenticated mobile API, operation receipts, version checks and source validation. A new service capability gates group creation on older servers. This run implements code and local tests, not Air deployment, production data mutation or phone installation. Mid-chat membership changes and complete destructive deletion UI remain outside this first release, as already unresolved in the PRD; do not expose fake actions.

## I/O & Edge-Case Matrix

| Scenario | Input/state | Expected | Failure behavior |
|---|---|---|---|
| Private chat | Tap a portrait | Restore that mentor chat, draft and reading state | Local feedback; no request on hover |
| Empty group | Select 2–4 mentors and create | Distinct persistent group, automatic name, no model call | Cancel creates nothing; same operation ID recovers same group |
| Group message | Text; optional @ member | One short response per selected responder; audience remains all members | Reject nonmember responders; preserve partial/unknown state |
| Parallel navigation | A request returns while viewing B | Result remains in A, B stays selected | No draft migration or automatic retry |
| Rename/archive | Current group/version | Same identity and history; archive/restore preserves content | Stale version rejected; active run must stop/finish |
| Context | Prior chats/private sources | Only authorized group-local material | No private-history or recipient expansion |

</frozen-after-approval>

## Code Map

- `ios/Riji/MobileViews.swift`, `MentorDialogueStyle.swift`: current mentor rows and decorative stage; make portraits interactive and replace lower section with groups.
- `ios/Riji/MobileRoundtableViews.swift`, `MobileRoundtableState.swift`: legacy host/invitation UI; reuse history, pending operations, results, isolation and recovery.
- `ios/RijiCore/MobileRoundtableModels.swift`, `MobileRoundtableCache.swift`, `MobilePersistence.swift`: compatible DTO extensions and per-group protected caches/state.
- `src/riji_agent/mobile/roundtable_api.py`, `roundtables.py`, `service.py`: backward-compatible selected-group API and device checks.
- `src/riji_agent/mentors/{models,planner,service,worker,policy,summary}.py`: explicit group-chat mode; bounded responses without host and unchanged legacy behavior.
- `ios/Riji/MobileRoundtableFixture.swift`, Swift state/core tests, Python mobile/mentor tests, `ios/RijiUITests/DiaryFlowTests.swift`: isolated synthetic fixtures and real orchestration/navigation coverage.

## Tasks & Acceptance

**Execution:**
- [x] Extend group API/domain with creation, membership audience, per-message responders, title, archive/restore and result provenance; retain legacy defaults.
- [x] Implement interactive home portraits, native create sheet, persistent group list/chat/details and reading/draft isolation.
- [x] Add meaningful backend, state and UI regressions; build the app and verify on an isolated simulator.
- [x] Synchronize PRD/API/implementation records and report remaining real-service/device validation.

**Acceptance Criteria:**
- Given a mentor draft, when returning through the portrait, then the same draft/history reappears and no duplicate contact rows exist.
- Given the same selected members, when creating twice with different operation IDs, then two empty groups persist, while retrying one operation does not duplicate it.
- Given a two-member group, when sending normal or @ text, then only intended member responders run, no host is invoked and all group members remain the audience.
- Given A in flight, when opening B before A finishes, then B remains visible and each draft/result retains its original target.
- Given old records or an old server, when opening mentors, then old history remains identifiable and unsupported new-group creation fails visibly.
- Given selected AI output, when saving to diary, then existing preview/confirmation and source revalidation still apply.

## Implementation Notes

- User authorization to develop the reviewed PRD governs implementation; no repeated build approval checkpoint is added. The relevant dirty branch is an existing shared increment and is preserved without reset/stash/staging.
- Reuse `/roundtables`; add `chat_kind=group`, `personas` and optional empty text on create. Details/list expose kind/title/personas; messages accept `reply_personas`; rename uses versioned commands. Legacy requests keep legacy behavior.

## Spec Change Log

- 2026-10-15, final review repair: make recovery requirements concrete with commit-backed empty-group receipts, explicit ending of an unverifiable turn without replay, actual-delivery unread cursors and batched reading-anchor restoration. KEEP the approved portrait/group model, fixed member audience, unknown-operation audit, operation-target ownership, bounded model context and preview/confirmation. Repairs are applied surgically in the shared dirty tree: reverting the increment would destroy earlier user work, so no reset or broad re-derivation is used.

## Review Triage Log

All three independent review layers reported before triage. Every finding is recorded separately below; final regression results are recorded under Verification.

| ID / lens | Verdict / route | Evidence and resolution |
|---|---|---|
| B1 / blind | high / bad_spec | Creating a group and crashing before the mobile receipt could leave an unknown operation blocking later creation. Recover only from a device/operation-bound committed group proof; do not infer success from an arbitrary matching group or replay unknown creation. |
| B2 / blind | high / bad_spec | `resume` correctly refuses unknown model/delivery attempts, but offered no safe way to continue the group. Add an explicit end-current-turn action that preserves unknown audit records and never replays that turn; late receipts remain bound to the old run. |
| B3 / blind | medium / patch | The legacy history sheet changed model selection without changing its parent destination ID. Use the single home history list for routing, and retain a stable legacy view while asynchronous operations temporarily invalidate its data. |
| B4 / blind | medium / bad_spec | Restoring only an anchor ID while fetching the newest page cannot restore an older position. Find the anchor using current-version pages in bounded batches, offer continuation, and distinguish an exhausted/missing anchor from a successful restore. |
| B5 / blind | medium / bad_spec | Home had neither actual-delivery unread metadata nor an explicit jump to a new reply. Add last-delivered reply IDs, local per-chat read cursors and foreground metadata polling; rename/revision changes cannot create false unread markers. |
| B6 / blind | medium / patch | Group bubbles omitted the returned `uncertainties` and `nextSteps` fields. Render those fields as visible parts of the answer, retaining the existing legacy behavior. |
| B7 / blind | medium / patch | The group picker allowed messages from multiple runs but result validation requires one discussion. Constrain selection to one run with a visible explanation; keep the existing maximum of five and provenance checks. |
| B8 / blind | high / patch | Persistent groups replace `Conversation.question` on each new message, causing an old saved answer to inherit a newer topic. Derive the saved topic from the user artifact belonging to the selected run. |
| B9 / blind | medium / patch | Summary conversion dropped the actor while the summary can outlive the raw recent-message window. Preserve the actor in compatible summary records and model context. |
| B10 / blind | medium / patch | The mention regex excluded continuous Chinese and Chinese brackets after a known name. Recognize exact known member names without requiring a Western delimiter; reject responders outside membership as before. |
| E1 / edge | high / patch | Suspend cleared the selected group while the destination task depended only on its unchanged ID. Re-select that same destination on foreground epoch changes and restart only its own polling. |
| E2 / edge | high / patch | Existing history deletion cleared the question but retained the newly introduced title and mobile room snapshot. Clear both on authorized deletion, retaining the existing tombstone/audit boundary. |
| V1 / verification | medium / patch | The member-tampering test wrote `local_room`, while mobile authorization reads `mobile_room`. Write the same illegal actual snapshot and grant, then assert both detail rejection and zero model calls for queued work. This gap was pre-verified by the independent reviewer. |
| V2 / verification | medium / patch | Tests resumed generated-but-unsent output but did not inspect a subsequent new message's model context. Stop before delivery, send a new message and assert the cancelled AI ID/text appears in neither `previous` nor `working_summary`. This gap was pre-verified by the independent reviewer. |

Additional actual-Simulator failures: an ancestor accessibility identifier replaced all four portrait IDs; remove that inherited identifier and assert all four real buttons. A conditional legacy destination destroyed the result-preview view during mutation; stable destination identity preserves selection and sheet state. Earlier isolated review fixes covered stale metadata revisions, archiving active runs, generated-but-unsent resume, explicit interrupted recovery and late callback isolation in both directions.

Final visual inspection: the diary save sheet inherited a pale mentor tint over a light native Form, making enabled actions appear disabled. Apply the existing approved diary `rijiPage` style only to that shared save sheet. This direct visual correction changes no save logic; both new-group and legacy save flows are rechecked after it.

## Verification

- `uv run pytest` for selected-group API, legacy roundtables, permissions and orchestration regressions; broaden to full backend suite when focused tests pass.
- Swift package Core/State tests through the project runner/scratch configuration.
- Xcode simulator build and focused mentor/group UI tests using a synthetic isolated container, including large type and cold draft recovery.
- Simulator screenshots inspect portrait warmth, group list, selection, keyboard and group details. Synthetic services are identified; no real-service or device acceptance is inferred.

- Backend and native client work are dispatched to separate file owners in parallel, with the shared contract recorded above. Existing user-approved worktree content is preserved; implementation never resets or stages unrelated work.

- Pre-review fixes: versioned rename/archive, stop-before-delivery replay, explicit group resume, operation-target-bound chat invalidation, and expected conversation guards. Targeted Swift tests passed 75 cases; full Python suite passed 2250 with 5 existing skips. Final Core passed; UI verification is running.

- Final verification after review repairs: **2,263 Python passed / five existing skips**, **623 Swift Core/State/Probe passed / zero failures or skips**, Simulator build-for-testing passed, and **nine distinct UI journeys** have passing evidence. Eight passed in `groups-ui-reviewed.xcresult`; the new group result-save journey passed in `group-result-ui-final.xcresult` after correcting only a test's lazy-Form scroll order. No production source changed between these UI runs. The full Swift runner verified 115 unchanged source inputs; the later UI test-only change is recorded separately.
- Follow-up independent reviews found no additional defects in unknown-turn isolation, provenance, deletion or v2 creation; the verification reviewer independently passed 43 group/recovery tests. `git diff --check` passed.
- Evidence: `docs/testing/ios-mentor-groups-2026-10-15.md`, `output/mentor-group-chat-2026-10-15/`, and `output/ios-tests/20261015T102925Z-core-d5e99638/`. Initial failing diagnostics remain intact. The scoped delta was rewritten after repairs; no portrait bytes or real data are included in that delta.
- Existing shared staging and unrelated/untracked increments were preserved. No local commit was created from this mixed worktree; no publication, Air operation, phone installation or production migration is part of this completed local increment. Real-service/model and physical-device acceptance remain explicitly separate.
- Final visual-only follow-up applies the approved diary page palette and readable Done label to the shared result sheet. Both affected new-group and legacy save journeys passed again in `result-contrast-ui-final.xcresult` (two tests, zero failures); Core/State/backend logic is unchanged. The final source-audit delta and screenshot are retained in `final-summary.json` and the verification record.
