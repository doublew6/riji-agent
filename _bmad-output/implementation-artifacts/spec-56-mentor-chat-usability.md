---
title: Mentor chat usability first batch
type: bugfix
created: 2026-10-16
status: done
route: dispatch
review_loop_iteration: 1
baseline_commit: 5b87e5451e084045809b14111e57b843546df643
context:
  - docs/DESIGN.md
  - docs/design/mentor-dialogue.md
  - docs/product/mentor-experience-proposal-2026-10-16.md
---

<frozen-after-approval reason="User requested implementation of the reviewed proposal on 2026-10-16; first batch stated in the response">

## Intent

Make ordinary mentor chatting and deliberate diary saving understandable without technical controls competing with messages. Implement first-batch UX-01/03/04/05/06/09 from the reviewed proposal: message actions on demand, one final save preview, truthful operation/connection states, continuous input and explicit group-name saving.

## Boundaries & Constraints

Preserve the home portraits, North Star, four main tabs, existing message layout, private/group identity, independent drafts, permissions, sources and legacy roundtables. User saves remain explicit: exact appended block, target date and source are visible before confirmation; edits invalidate old confirmation; atomic/idempotent writes and unknown-outcome reconciliation remain unchanged. Unknown delivery is queried with its original operation ID, never automatically replayed. Keep current single-run, maximum-five selection rule, explaining blocked additions without clearing the selection.

Do not implement second-batch message layout, group summaries, optional names, private portrait redesign, hidden main tabs, automatic goal sharing or new APIs. No Air deployment, phone installation, publication, migration or real diary mutation. Use isolated synthetic tests and the named automated simulator only.

## I/O & Edge-Case Matrix

| Scenario | Input/state | Expected behavior | Error handling |
|---|---|---|---|
| Message actions | Normal available AI reply | No selection circle; long press/more exposes copy, save, multi-select | Unavailable/superseded replies cannot be saved |
| Multi-select | Another run or sixth reply | Preserve original selection and explain limit | Cancel explicitly leaves selection mode |
| Diary preview | Selected result, date, edited text | Automatically prepare and display exact appended block; explicit confirm | Editing immediately disables stale confirmation; newest validated payload only |
| Diary recovery | Source/target changed or write outcome unknown | Reject stale save, preserve editing; read-only reconciliation first | No repeated append or silent overwrite |
| Continuous chat | Send while composer focused, then type | Keep focus and draft editable; no new automatic send queue | Original operation remains bound to originating chat |
| Reading history | New messages while viewing older content | Do not force-scroll; explicit new-reply action | Restore existing anchor without periodic reapplying over user position |
| Rename | Group details, edit name | Dedicated cancel/save screen; unchanged/invalid/busy cannot save | Failure/unknown preserves entered name; success closes only after matching receipt |
| Connection | Credentials exist but service read fails | Separate authorization, connectivity and supported capabilities | One retry action; no implicit credential replacement |

</frozen-after-approval>

## Code Map

- `ios/Riji/MobileViews.swift`: shared operation presentation and private composer/polling; retain failure export and original-operation recovery.
- `ios/Riji/MobileRoundtableViews.swift`: group cards, selection, composer, details, legacy cards and shared `RoundtableResultView`.
- `ios/Riji/MobileRoundtableState.swift`: existing selection validation and versioned commands. Preserve same-run restriction and operation targeting.
- `ios/Riji/DiaryViewModel.swift`, `ios/RijiCore/AIResultStore.swift`: prepare/restore/cancel/confirm exact preview; do not weaken persistence guards.
- `ios/Riji/MobileClientModel.swift`, `MobileSettingsViews.swift`, `ios/RijiCore/MobileModels.swift`, `MobilePersistence.swift`: truthful state and capability presentation.
- `ios/RijiUITests/DiaryFlowTests.swift`, Core/State tests: isolated fixtures; update actual action entry paths and verify changed failure boundaries.

## Tasks & Acceptance

- [x] Root: implement on-demand group/legacy message actions, explicit selection mode/limits, continuous focus, stable reading and dedicated rename flow.
- [x] Save owner: replace only the `RoundtableResultView` structure (keep in existing compiled file), necessary diary preview APIs and save-state tests.
- [x] Connection owner: connection/capability state in model/settings/Core and state tests; root owns shared `MobileOperationView` in MobileViews.
- [x] Test owner: update/add relevant UI tests only; root serializes Xcode and simulator execution.
- [x] Root: review actual delta from saved pre-task Swift baseline, run affected Core/State and UI checks, visually inspect native screens, update verification/docs.

Acceptance:
- Given normal messages, when reading and opening actions, then selection controls appear only by explicit choice and saving has an unambiguous label.
- Given an existing preview, when editing or receiving delayed verification, then stale content cannot regain confirmation eligibility.
- Given a rename draft, when cancelling, failing or confirming, then only confirmed success changes the title and no implicit dismissal loses entered text.
- Given pending requests and navigation, when replies arrive, then drafts, receipts and reading state remain in the original conversation.
- Given an unavailable/old service, when viewing settings, then status and group capability match actual evidence and local diary remains usable.

## Implementation Notes

- Continue the already authorized shared #56 branch and dirty increment. Preserve unrelated work and staging; no reset/stash/stage/commit. Per-task Swift baseline and hashes live in `output/mentor-ux-build-2026-10-16/` because HEAD predates the existing increment.
- The prior proposal plus the user's explicit start instruction supplies approval; do not add another planning approval gate. New unresolved second-batch choices remain deferred.
- Parallel file owners are recorded above; inspect shared-file changes before review. Root performs UI operations and builds, other owners do not.

## Spec Change Log

- 2026-10-16: independent review required explicit preservation/discard when leaving an unprepared edit, a safe cancel-preview route, an escape from unknown rename outcomes, and completed feedback outside chat. Keep exact-patch confirmation, same-run selection, original operation identity, home appearance and source guards. Repairs are applied surgically against the saved per-task baseline; reverting the shared increment would destroy earlier authorized work. No intent or second-batch scope changed.

## Review Triage Log

| Finding | Verdict / route | Verified evidence and action |
|---|---|---|
| B1 closing unprepared edits | medium / bad_spec | Done and interactive dismiss cancelled the pending task before persistence. Add explicit preservation/discard semantics; no silent loss. |
| B2 no in-page preview cancellation | medium / bad_spec | Auto-prepare establishes a date lock but Done retains it. Restore a clearly named safe cancel-save action. |
| B3 unknown rename traps navigation | medium / patch | Both Cancel and gestures were disabled while unresolved. Allow later handling; operation text/ID remain persisted and restore on re-entry. |
| B4 capabilities 4xx stuck connecting | medium / patch | Failure switch handled transport, invalid response and 5xx, not 403/404/429. End connection check with unavailable, preserving authorization distinctions. |
| B5 completed memory/privacy feedback hidden | medium / patch | Shared view hid completed operations globally. Hide ordinary chat receipts only; retain explicit action completion elsewhere. |
| B6 multi-select accessibility state | medium / patch | Selection buttons had no selected value. Add selected/unselected value and matching action labels. |
| B7 copy omitted conditions/next steps | medium / patch | Copy used only visibleText while the card also rendered uncertainties/nextSteps. Include all visible advice fields and unavailable guard. |
| B8 revalidate every editing pause | medium / patch | Editing triggered remote validation after each 350ms pause. Invalidate immediately but prepare after Finish Editing. |
| B9 cancelled preparation reported offline | medium / patch | Validation catch called show on cancelled tasks; current() does not check cancellation. Skip cancellation error publication; add regression. |
| B10 main preview exposed IDs | low / patch | Whole block included internal persona/discussion labels. Use readable source metadata; exact block remains available for review. |
| E1 capabilities 4xx | medium / patch | Independently confirms B4; same connection terminal-state repair and HTTP regression. |
| E2 stale result after preparation persistence failure | high / patch | mutate can fail before invalidateOperationReads, leaving prior validation. Clear validation before starting the selected-result attempt; regression asserts zero remote write on storage failure. |
| V1 new journeys absent from smoke | medium / patch | Fixed SMOKE_METHODS omitted three new UI methods used by CI. Register all three and update automation documentation. |
| V2 capabilities 4xx | medium / patch | Independently confirms B4; dedicated non-401 4xx tests cover terminal state and retry. |
| R1 new UI helper chose a user message | low / patch | First UI round failed two tests at AI-only menu lookup because first menu belonged to user. Give user menus a distinct identifier; product copy-only behavior is retained. |
| F1 save initialization could overwrite early input | medium / patch | Read-only final review found controls were active while restore awaited. Gate date/edit/save controls behind initialized state; reviewer recheck passed. |
| F2 legacy selection omitted missing-message guard | medium / patch | Match the group guard when selected messages are outside the current page, preserving original selection before another run can be added. |
| F3 inbox capability HTTP 4xx kept previous online state | medium / patch | Apply unavailable terminal state to background capabilities reads too; retain credentials, known capabilities and original operations. Dedicated inbox regression added. |
| R2 iOS popover omitted continue-editing action | medium / patch | Second native UI run showed confirmationDialog omitted its cancel-role button. Use an alert with explicit continue/discard actions; retain failed result for evidence. |
| R3 memory receipt check missed lazy form content | low / patch | Second native run showed completed request receipt at the top while organization state was offscreen. Assert completion, then reveal the organizer before checking pending state. No production memory behavior changed. |
| R4 XCTest activation point failed for visible cancel button | low / test patch | Third run reached the prepared edited preview; recording and hierarchy show enabled cancel fully visible. Check its complete frame/enabled state and tap observed center rather than querying the broken activation point. Subsequent run stopped earlier at first context-menu action, so cancellation/append UI assertions remain unverified. |
| R5 multiline raw text mismatched quoted Markdown | low / test patch | Unlocked follow-up reached success, but raw selectedText substring failed because every saved line is quoted. Independent exact fixture inspection proved full content and matching artifact once. Read the actual exact-preview block, validate its quoted user content, assert whole-block/marker uniqueness, retained prefix and cold-reopen equality. Final UI run passed in 111.963 seconds; production unchanged. |

## Verification

- Run project isolated Swift Core/State runner; focused UI cases for selection/save, rename, group draft isolation, private focus and legacy saving.
- Verify a rejected stale preview and idempotent recovery with temporary synthetic files; never use real diary data for destructive or write tests.
- Native screenshots for normal chat, message menu/multi-select, exact preview and name editor. Model content, real network and phone experience are separate acceptance.

Final first-batch status on 2026-10-17: 185 distinct focused Core/State tests passed; runner regression passed; eight distinct UI journeys passed across retained runs. After unlock, exact save/cancel/cold return and name/continuous-input rechecks all passed (three current-round tests). Native screens were inspected and an 11-state screenshot report generated. The auxiliary report's browser input/export verification was denied by the browser's local-file policy and is explicitly unverified; no bypass attempted. Full core remains incomplete at the unchanged voice test. Follow-up diary readability issue: raw provenance metadata is visible after saving; record separately from this completed first-batch scope. See `docs/testing/ios-mentor-usability-2026-10-17.md` and `output/mentor-ux-walkthrough-2026-10-17/ui-tests.json`. No local commit or publication from this mixed pre-existing worktree.
