---
title: 'Implement approved F North Star mentor dialogue'
type: 'feature'
created: '2026-10-13'
status: 'done'
route: 'dispatch'
baseline_commit: '5b87e5451e084045809b14111e57b843546df643'
review_loop_iteration: 0
context:
  - '{project-root}/PROJECT_GUIDE.md'
  - '{project-root}/docs/DESIGN.md'
  - '{project-root}/docs/design/mentor-dialogue.md'
---

<frozen-after-approval reason="User approved the design on 2026-10-12 and explicitly authorized development on 2026-10-13">

## Intent

**Problem:** Native mentor conversations still use plain lists. They do not express the approved quiet cosmic roundtable, male future-self likeness or guiding phase goal.

**Approach:** Implement the approved F visual language for both dialogue modes using reusable SwiftUI components, real conversation state and an explicit local North Star source association.

## Boundaries & Constraints

**Always:** Keep four peer tabs, all messages, source labels, invitation review, stop/summarize/resume, result selection/confirmation, drafts and failure recovery. Scope dark appearance to mentor destinations. Preserve unrelated existing working changes. Use the approved male likeness. Treat the goal as a user-chosen phase direction. Follow system text sizing, 44pt actions and Reduce Motion.

**Never:** Deploy, publish, commit, change backend/provider/permissions, send goal data implicitly, rewrite goal source files, replace real state with samples, infer a selected goal from list order or MIT history. This development authorization includes routine reversible implementation decisions; existing design approval is sufficient.

## I/O & Edge-Case Matrix

| Scenario | Input/state | Expected behavior | Error handling |
| --- | --- | --- | --- |
| Goal selection | Multiple local definitions | Explicit selection, persisted per diary source, fresh source value | Superseded version requires reselection |
| Original vault | User selects authorized text file and exact paragraph | Preview and associate its relative path/range/hash; display after reread | Changed hash, conflict, missing source or read failure hides current-goal claim and explains recovery |
| No goal | No selected association | Quiet optional selection entry; chatting works | Never display the design example as real |
| Source change | Directory/account transition during async read | Clear prior source goal and reject late results | No previous-source leakage |
| Conversation | Existing run/message/control states | Accurate speaker, full readable history, working controls | Preserve offline/unknown/failed states and retained drafts |
| Layout | Keyboard, long text, accessibility size | Collapse artwork first; goal, input and actions usable | No clipped text or forced tiny font |

</frozen-after-approval>

## Code Map

- `ios/Riji/MobileViews.swift`: mentor list and private MobileChatView; add diary dependency to single chat, preserve identifiers and recovery logic.
- `ios/Riji/MobileRoundtableViews.swift`: roundtable stage/chat/actions; preserve invitation/result sheets and reading anchors.
- `ios/Riji/RijiNavigationView.swift`: four tabs; scope styling without affecting diary/memory/settings.
- `ios/Riji/WorkflowViewModel.swift`, `WorkflowViews.swift`, `RijiCore/GoalReviewStore.swift`: local goals and existing preview-confirm editor. Original-source mode has no workflows. Do not use loadGoals as a polling display reader because it changes forms.
- `ios/Riji/VaultBrowserStore.swift`, `VaultBrowserModel.swift`: coordinated authorized reads, folder tree and identity. Reuse, never scan unrelated files.
- `ios/Riji/DiaryViewModel.swift`: source identity and directory lifecycle.
- `ios/Package.swift`, `ios/Riji.xcodeproj/project.pbxproj`: explicit new file registration; SwiftUI files excluded from State package, included in App and Native test builds.
- `ios/Riji/Assets.xcassets/MentorPortraitAtlas.imageset/`: prepared transparent four-column atlas in order gentle reviewer, blunt coach, future self, Wang Yangming. Crop/clip cells in rendering; never require asset existence in hostless tests.
- `ios/RijiUITests/DiaryFlowTests.swift`, `RijiStateTests/MobileRoundtableStateTests.swift`, `MobileClientModelTests.swift`: synthetic fixtures and stable accessibility IDs.

## Tasks & Acceptance

**Execution:**
- [x] `ios/Riji/MentorDialogueStyle.swift` (new) — shared dark tokens, quiet cosmic backdrop, stable star, portrait rendering, spatial roundtable, speaker card, composer/control treatments.
- [x] `ios/RijiCore/NorthStarAnchor.swift`, `ios/Riji/NorthStarAnchorModel.swift`, `NorthStarAnchorViews.swift` (new), `VaultBrowserStore.swift` — explicit per-source association of local goal or original-vault text; bounded readonly verification and preview-confirm linking. Store references locally, never create another goal definition.
- [x] `MobileViews.swift`, `MobileRoundtableViews.swift` — integrate stage and goal into both chats; warm identity and visual-novel dialogue; keep every existing action/state.
- [x] `ios/Package.swift`, `ios/Riji.xcodeproj/project.pbxproj` — register new source/assets correctly.
- [x] `ios/RijiCoreTests/NorthStarAnchorTests.swift`, `ios/RijiStateTests/NorthStarAnchorModelTests.swift`, `ios/RijiUITests/DiaryFlowTests.swift` — meaningful source-isolation, stale/failure, persistence and native conversation/layout regressions.
- [x] `docs/testing/ios-mentor-north-star-2026-10-13.md` — actual checks, synthetic screenshots, remaining physical-device limits.

**Acceptance Criteria:**
- Given either dialogue, when opened, then its upper stage, restrained North Star, warm male future self and readable speaker cards visibly follow approved F.
- Given a selected source goal, when reopening or switching mentor, then the same verified phase direction appears without changing or transmitting the goal.
- Given existing synthetic chats, when sending/inviting/selecting/summarizing/stopping, then existing behavior and identifiers remain usable.
- Given larger text or keyboard focus, when reading or composing, then artwork yields space and controls remain reachable.
- Given another main tab, when navigating back, then local mentor styling does not darken that tab.

## Implementation Notes

Authorization continues the known dirty checkout and current issue-56 branch. Baseline iOS working files were copied to `output/mentor-dialogue-build-2026-10-13/baseline/ios/`; compare against that snapshot to isolate this task from earlier uncommitted features. Do not stage unrelated files. GitHub #56 still has its historical design-only body; later user approval and this explicit development request authorize the UI increment. #55/native services are present; incomplete Feishu activation in #43 is not required for local UI validation.

Prefer small components and preserve existing API contracts. No additional approval is needed for source linking UI: it lets the user choose at runtime rather than guessing their actual target. Keep generation/read epochs, use precise byte/paragraph source checks, validate decoded references and handle persistence failures without claiming a saved association.

## Spec Change Log

## Review Triage Log

| ID | Verdict | Evidence and route |
| --- | --- | --- |
| B1 | medium | The Application Support reference directory lacks the backup-exclusion flag used by MobilePersistence although the UI promises a device-local association. Patch the directory policy and verify it. |
| B2 | low | Parent/direct authorization shares a normalized collection identity, while file paths are relative to the authorized root. Changing that root can require reselection; file hashes still reject different text. Reject this uncommon reauthorization inconvenience because preserving paths across different roots requires a new mapping policy. Record the limitation. |
| B3 | low | refresh clears current before every read, causing a periodic empty state on slow unchanged files. Patch preservation during read and clearing on missing/error; activation must still clear across sources. |
| B4 | medium | Successful refresh clears user-operation errors, including failed save/unlink feedback. Patch sourceError/error separation so periodic checks cannot erase operation failures. |
| B5 | medium | The unlink button is inside if-current, but unresolved references clear current while remaining on disk. Patch a reachable unlink action for the active scope when source resolution fails. |
| B6 | low | Inactive scene clears unconfirmed navigation and selection, without discarding edits or saved associations. Reject retaining deep selection across the uncommon interruption: that requires more resume state and revalidation branches. Document reselection behavior. |
| B7 | medium | The recovery button only refreshes the saved association; selectedFile/fileData stay stale after an external edit. Patch it to reopen the selected file and invalidate its preview, or rebrowse the current directory. |
| B8 | medium | Complete local-goal detail omits optional measurement and measurementSource. Patch these into readable detail. A raw UUID is not useful presentation: only current immutable versions are listed, and the source date is already shown. |
| B9 | medium | VaultReadError.tooLarge falls through to an unrelated permissions/conflict message. Patch the mapping and show the 2 MiB file limit before selection. |
| B10 | low | The four advisors respond to activeActor, but the actual host actor does not. Add the same short, Reduce Motion-aware feedback to the existing host marker. |
| E1 | medium | Same observed cause as B5: a durable reference remains when current is nil and no unlink action is rendered. Group with B5 and patch once. |
| V1 | medium | Accepted verification-gap evidence: regular CI omits the two new consumer tests because SMOKE_METHODS lacks them. Patch registration and count documentation, including the new periodic-invalidation test. |
| V2 | medium | Accepted verification-gap evidence: existing tests call refresh directly or leave UI sources unchanged. Add a real open-chat consumer test that edits the selected synthetic file and observes periodic invalidation. |
| V3 | medium | Accepted verification-gap evidence: no test changes source between pre-save and post-save reads. Add a controlled-reader test proving false confirmation, nil current and retained preview/error after a post-save mismatch. |

All three review layers completed before triage. The remaining entries are direct corrections to demonstrated paths or verification registration; no intent or spec loopback is needed. The index is untouched.

### Review outcome

B1, B3–B5, B7–B10, E1 and V1–V3 are corrected and verified. B2 and B6 remain documented low-impact interaction limits; no entry is deferred. The implementation agent supplied the patches and 16 passing targeted package tests plus two passing UI journeys. Its final UI failure was a lazy List confirmation button below a long synthetic paragraph; the parent inspected the saved accessibility hierarchy, added the existing bounded scroll helper and completed the remaining run. No product assertion or timeout was weakened.

## Verification

- `python3 scripts/test_ios.py --suite core`: Core/State tests, including new association failures.
- Generic iOS Simulator Xcode build: all SwiftUI code and resources compile.
- Run focused existing mentor/roundtable UI journeys plus new North Star and large-text tests on the dedicated automated-test simulator, following `docs/testing/ios-automation.md`. Do not use the manual development device.
- Inspect synthetic screenshots against the approved board; no real diary/service input, device installation or Air deployment.

### Executed verification and matrix audit

- Package evidence: `output/ios-tests/20261013T111715Z-smoke-349d5f5a/summary.json`, 599 passed (299 Core, 295 State, 5 Probe), no skipped tests. All 4 NorthStarAnchor Core and 7 State tests are in its executed inventory.
- Final SwiftUI/Native/UI evidence: `output/ios-tests/20261013T113532Z-smoke-865ae7e4/`, 59 Native + 2 new UI passed; 113 source inputs unchanged. The earlier five existing conversation UI journeys passed in the preceding focused run. This is not the default full smoke suite.
- Matrix: explicit local choice/supersession → `testExplicitChoiceReopensAcrossMentorsAndSupersededGoalRequiresReselection`; original paragraph/stale/conflict/missing → both original-file State tests and Core byte-range tests; no-goal chat → maximum-accessibility UI; source/account change → late-read State test and source-switch UI; conversation controls/drafts/history → five existing UI journeys; layout/keyboard → maximum-accessibility UI plus seven inspected native screenshots. Every covering test executed and passed.
- Generic Simulator build passed. Final native images reviewed against the approved board: larger centered male future self, restrained star, roundtable participants, latest speaker side portrait, real software keyboard and isolated light diary appearance. The stage scrolls with full history and collapses for input or accessibility sizes.
- Full evidence, failed runs and remaining physical-device limits: `docs/testing/ios-mentor-north-star-2026-10-13.md`.

### Post-review verification

- `output/ios-tests/20261013T124809Z-smoke-e8ee3957/`: all 16 targeted package tests and the existing two new UI journeys passed. The additional external-change UI failed because its confirmation button was below the visible List viewport; the failure and hierarchy remain preserved.
- `output/ios-tests/20261013T131808Z-smoke-ea5af139/`: all 604 package tests passed (300 Core, 299 State, 5 Probe), plus the corrected external-change UI, with zero failures/skips and all 113 source inputs unchanged. It verifies the production 15-second loop while the conversation stays open, invalidation feedback, unlink and cold reopen. The same Xcode run builds the app and all selected UI code.
- `uv run --no-sync pytest tests/test_ios_test_runner.py -q`: passed. The three new mentor journeys are registered in the default 30-method smoke suite. The full default smoke and remote CI were not run in this task.
- Reuse the unchanged 59 Native and five prior conversation UI results listed above; the two existing new UI methods passed again after the review patches. This gives eight distinct passing focused UI journeys across the recorded runs.
- The specification, repository design copy and Obsidian master record local implementation and simulator validation separately from physical-device acceptance. Local repository recording and external publication remain excluded by the frozen constraint.
