---
title: Mentor home inline group chat
type: feature
created: 2026-10-17
status: done
route: dispatch
review_loop_iteration: 0
baseline_commit: 5b87e5451e084045809b14111e57b843546df643
context:
  - docs/DESIGN.md
  - docs/design/mentor-dialogue.md
---

<frozen-after-approval reason="User explicitly confirmed home inline group chat without page navigation on 2026-10-17">

## Intent

The current home group list still requires a navigation push before reading or sending. The user explicitly wants messages and the composer on the mentor home itself, with switching between groups in place. Keep the four mentor portraits and North Star above; portrait taps retain their independent private conversations.

## Boundaries & Constraints

Always reuse actual selected-group messages, drafts, reading state, operation receipts and preview-confirmation saves. Only one group is mounted at a time. Retain the muted warm F visual design, four main tabs, group creation/details/archive/restore and legacy roundtable identity. Adapt the header to keyboard and accessibility size so messages and composer stay reachable.

Never merge histories, widen recipients, manufacture a group, auto-send materials, alter saved diary bytes or change server behavior. This is local implementation and synthetic simulator validation, not Air deployment or phone installation. Preserve the dirty shared tree and staging. The latest user confirmation overrides the older home-list-to-chat navigation wording.

## I/O & Edge-Case Matrix

| Scenario | Input / state | Expected | Failure behavior |
|---|---|---|---|
| Home entry | Existing remembered group | Portraits, selected group messages and composer on one screen, without a back navigation step | Missing selection uses available list selection or clear empty state; no new group |
| Switching | Draft or pending reply in A, choose B | B alone appears, A draft and late reply stay in A | Stale callbacks cannot select or mutate B |
| Create | Select 2–4 mentors | New empty group becomes current inline chat | Cancel creates nothing; unknown operation retains original recovery |
| Legacy | Select existing host roundtable | Same inline placement with original host/invitation behavior | Commands and saves remain bound to that identity |
| Input | Keyboard, long text or large type | Compact upper artwork while keeping switcher and composer accessible | No overlapping nested vertical scroll views |
| Save | Select replies, open preview | Existing explicit preview and confirmation, correct group provenance | Cancel writes nothing; switch cannot reuse another group's validation |
| Availability | Empty, archive, offline or foreground return | Honest state, accessible drafts/restore, same group when possible | Keep service and diary data boundaries |

</frozen-after-approval>

## Code Map

- `ios/Riji/MobileViews.swift`: `MobileMentorsView` now owns account-scoped local group selection and a compact in-place switcher; portrait navigation remains. `MobileRoundtableDestination` loads exactly one selected ID without a group navigation push.
- `ios/Riji/MobileRoundtableViews.swift`: reuse new-group and legacy chat views, message actions, selection, composer and sheets. Add embedded presentation without repeated North Star header or child navigation title. Bind legacy operations to their fixed ID.
- `ios/Riji/MobileRoundtableState.swift`, `MobileClientModel.swift`: async selection/read guards, persistent per-ID drafts and reading state, confirmed-current-404 fallback and protection for transient, paginated and stale cases.
- `ios/RijiUITests/DiaryFlowTests.swift`: adapt group-navigation helpers and assertions, cover inline switching, private navigation, archive, large type and both save flows.
- `ios/Riji/ApprovedSettingsViews.swift`, `ios/Riji.xcodeproj/project.pbxproj`: expose actual bundle version/build in a quiet settings footer and increment build to 2, allowing this acceptance build to be distinguished.
- `docs/PRD.md`, `docs/product/PRD-mentor-dialogue-interaction.md`, `docs/design/mentor-dialogue.md`, planning PRD: record confirmed inline relationship and local validation status.

## Tasks & Acceptance

- [x] Implement a single inline group host with in-place switching and adaptive portrait header.
- [x] Preserve targeted operations, drafts, reading positions, archive and result previews across switching.
- [x] Update affected UI scenarios and verify meaningful navigation/isolation/save boundaries on the dedicated synthetic simulator.
- [x] Show build identity, update confirmed requirements and retain actual screenshots/test evidence.

Implementation and the scoped local validation are complete. User experience acceptance, live-service validation and phone installation remain separate; `done` does not assert those outcomes.

Acceptance:
- Given an existing group, when opening Mentors or switching groups, then messages and input are visible under the portraits and no group back button appears.
- Given drafts in two groups and a mentor private chat, when switching among them and cold launching, then each remains attached to its original conversation.
- Given a selected group, when creating, renaming, archiving or restoring, then the home remains the chat surface and the operation affects only that group.
- Given a keyboard or large text, when composing and selecting another group, then input and selection controls remain usable.
- Given selected AI suggestions, when cancelling or confirming, then cancellation appends nothing and confirmation writes the exact selected block once with original provenance.

## Implementation Notes

- Execution ownership: the dispatched implementation agent owns only `MobileViews.swift`, `MobileRoundtableViews.swift`, and state files if necessary. It reports the accessibility IDs and local verification without running Xcode or operating the simulator. Root coordinates the test agent, build metadata, documentation, builds, UI testing and final review. Preserve stable message/composer IDs; expose `mobileGroupSwitcher` and `mobileGroup-<id>` in the in-place switch menu, and `mobileCurrentGroup` on the selected title. No group-level back navigation remains.

- No unresolved intent gap after the user's explicit answer. No irreversible action. Footprint is native presentation, focused tests, build metadata and requirements.
- Existing authorization covers continuing on the shared Issue #56/#57 branch. Baseline files are saved under `output/mentor-inline-chat-2026-10-17/baseline`; no reset, staging or publication.
- Issue #56's historical design-only text is superseded by repeated explicit development authorization and today's layout confirmation. Existing #55 identity and mentor dependencies are reused, not deployed or reimplemented.

## Spec Change Log

## Review Triage Log

All 16 findings from the blind, edge-case and verification reviews are recorded independently below. Duplicate reports retain their own row and link to the underlying correction. Severity reflects the review finding, not a claim of unresolved release impact. Scoped implementation and local validation are complete, with coverage limitations and two pre-existing deferred findings preserved.

| ID | Severity | Classification | Action / disposition | Evidence and verification boundary |
|---|---|---|---|---|
| B1 | medium | Confirmed; fixed | On a current conversation's confirmed 404, clear only that unavailable selection and fall back to another available group or an empty state; preserve its draft and reading record. Do not treat a missing list page, transport error or stale response as deletion. | `MobileRoundtableState.swift`: `preferredRoundtableSelection`, `dismissUnavailableRoundtable`, guarded `refreshRoundtable` catch; `MobileViews.swift`: account-bound selection and unavailable-ID change handling. The four missing-group / transport / stale-404 cases in `MobileGroupChatStateTests` passed in the focused 24-test state run. |
| B2 | medium | Confirmed; fixed and verified | Compact the fixed portrait header for compact vertical size class and measured available height below 580 points, as well as keyboard focus and accessibility text. | `MobileMentorsView.compactHeader` reads `verticalSizeClass` and measured `availableHeight`. `testMobileInlineGroupLandscapeKeepsMessagesAndComposerReachable` passed (37.231 seconds), checking the actual landscape frame, messages, switcher, composer and four mentors. Accessibility-size reachability also passed, with final manual large-text inspection. Related duplicate: E2. |
| B3 | medium | Confirmed; fixed, fault-specific visual coverage limited | Move group-creation operation recovery out of the fixed artwork header into the selected conversation's scrolling messages; show it in the scrollable empty state when no conversation is selected. | `MobileMentorsView.header` no longer mounts `MobileOperationView`; both embedded chat message stacks mount the creation target once, and the unknown-chat fallback has its own scrolling recovery area. Code review and state recovery tests support this change; this round did not separately inject a populated creation-recovery notice for a visual fault scenario. Normal, keyboard, large-text and landscape reachability were exercised. |
| B4 | low | Confirmed; fixed | Remove the duplicate group-creation recovery notice from the fixed header, leaving the empty state's single scrolling instance. | `MobileViews.swift`: `header` contains the environment notice, North Star and portrait directory; `emptyGroups` contains one creation-target `MobileOperationView`. Shares B3's correction but addresses the distinct duplicate-empty-state finding. |
| B5 | low | Confirmed; fixed and verified | Give compact portraits short visible mentor names and a visible unread dot while retaining the full accessible name and unread description. | `MobileMentorsView.compactName` and compact `directoryStage` render names and conditional dots; `accessibilityLabel` retains the full mentor identity. Large-text / keyboard UI regression passed; `large-type-final.png` records the final manual compact-header inspection. |
| B6 | low | Confirmed; fixed and verified | Surface unread replies in other groups on the closed group switcher, with matching accessibility text; preserve each menu item's own unread state. | `MobileMentorsView.otherGroupsUnread`, `switchMenu` and `groupChoice` use existing `hasUnreadReply` state. Existing unread-state regressions passed. Final manual accessibility-size inspection confirmed the unread dot and switcher arrow remain visible; ordinary size retains the text cue. Final build and installed binary hashes match. |
| B7 | medium | Pre-existing usability issue; deferred | Record a separate follow-up for differentiating multiple groups with identical members. Keep explicit group renaming available; do not invent an automatic naming policy in this layout correction. | `src/riji_agent/mobile/group_chats.py` derives default names by joining member names. The task-start `MobileViews.swift` list also displayed `item.title` without a unique-name policy. `MobileGroupNameView` still permits explicit renaming. Added a source-linked entry to `deferred-work.md`. |
| B8 | medium | Confirmed; fixed and verified | Restore the retained AI preview for the selected conversation, rather than allowing the account's first pending result to hide another group's preview; retain recovery locks across all pending dates. | `DiaryViewModel.restorePendingAIResults(account:conversationID:)` filters matching provenance; both chat views pass their fixed identifier. The different-date restore case passed in the 18-test recovery run. The native exact-preview test passed A → B → legacy → A, confirmation and cold-reopen assertions (218.192 seconds). |
| B9 | medium | Pre-existing recovery-surface gap; deferred | Record a follow-up to expose the legacy conversation's reading-restoration notice and continuation action when the bounded initial history fetch does not reach the saved anchor. | The task-start `MobileRoundtableViews.swift` already rendered `roundtableReadingNotice` and “继续找回阅读位置” only in `MobileGroupChatView`; the legacy `MobileRoundtableView` lacked that presentation. The state layer's bounded repaging and continuation tests remain intact. Added a source-linked entry to `deferred-work.md`; no new loss of stored reading state is claimed. |
| B10 | medium | Confirmed verification gap; covered and passed | Extend the group selection UI test to send two turns, select an earlier reply, switch to the legacy conversation, and return. Assert restored selection and a visible earlier control without scrolling after return, while a later reply remains outside that viewport. | `DiaryFlowTests.testMobileGroupMessageSelectionOnlyAppearsAfterExplicitActionAndCanCancel` and `verifySelectionAndReadingAcrossGroups`; assertions cover no cross-group selection, preserved selected value and actual restored viewport. The final UI run passed the added path after correcting message-layout restoration; the first failing run remains retained. Related verification report: V2. |
| E1 | medium | Duplicate confirmed finding; fixed with B1 | Apply B1's confirmed-404 fallback and stale-response protections; retain this independent edge-case report. | The same four `MobileGroupChatStateTests` cover available-group fallback, last-group empty state, transient / paginated omission and stale / older-page 404; focused state run passed. |
| E2 | medium | Duplicate confirmed finding; fixed and verified with B2 | Apply B2's height and vertical-size-class compaction for landscape and shorter viewports. | `MobileMentorsView.compactHeader`; the native landscape geometry test passed with actual message and composer viewport assertions. Separate keyboard / large-type UI scenarios passed; this does not claim physical-device usability. |
| E3 | medium | Confirmed preview/date edge case; fixed with B8 | Restore by conversation across different target dates without discarding another group's pending transaction or releasing its day lock. | `testConversationRestoreSelectsEachRetainedPreviewAcrossDifferentDates` prepares two groups on different days, leaves one write unresolved, restores each exact preview, checks both pending records and locks, and verifies no diary file or receipt was created. Focused 18-test recovery run passed. |
| V1 | medium | Confirmed verification gap; covered and passed | Exercise edited retained preview A → new group B → legacy conversation → A, then continue the existing confirmed-save and cold-reopen flow. | `DiaryFlowTests.testMobileGroupResultPreviewRequiresConfirmationAndKeepsExactSelection` and `verifyRetainedPreviewAcrossGroups` check that B and the legacy page/menu expose no A recovery entry, then compare restored exact Markdown bytes, target date and provenance before saving once. The native path passed (218.192 seconds); exact-file assertions were retained. Linked implementation findings: B8 / E3. |
| V2 | medium | Duplicate verification finding; covered and passed with B10 | Retain the independent report and use B10's real UI selection / reading round trip, not only model-persistence assertions. | `verifySelectionAndReadingAcrossGroups` waits for the original selected control to become hittable, checks its frame against `roundtableMessages`, and never calls a reveal or scrolling helper after returning. Final native execution passed with no reveal call on return. |
| V3 | medium | Duplicate missing-case verification finding; covered by B1 | Add the confirmed-missing-conversation and failure-classification state cases rather than treating any list omission as a missing group. | `testConfirmedMissingGroupFallsBackWithoutDiscardingItsDraftOrReadingState`, `testConfirmedMissingLastGroupLeavesEmptySelectionAndKeepsItsDraft`, `testMissingListPageTransportAndMissingCacheDoNotDeclareGroupUnavailable`, and `testOldPageOrStaleGroup404CannotClearCurrentGroup`; all included in the passed 24-test state run. |

Review-fix verification: 62 focused state tests passed (24 group, 18 AI-result recovery, 20 legacy roundtable). Ten distinct relevant UI scenarios obtained passing results across targeted batches; these are not a single ten-test pass or the whole-app suite. B3's injected creation-recovery visual scenario remains explicitly unverified, while normal, keyboard, accessibility-size and landscape layout checks passed. Final ordinary-size home and accessibility-size unread-dot rendering were manually checked and captured after the last build. No production deployment, phone installation, Git staging or publication is implied.

## Verification

- Final focused state regression: 62 passed, 0 failures (18 AI recovery, 24 group state, 20 legacy roundtable state), `output/mentor-inline-chat-2026-10-17/final-state-tests.log`.
- Final native build: `final-build.log` reports `BUILD SUCCEEDED`. The installed App is 0.1.0 (2); `installed-build.json` confirms the installed `Riji` and `Riji.debug.dylib` hashes match final build products. `source-final-installed.json` identifies the only product-source change after the last UI batch: the accessibility-size unread indicator in `MobileViews.swift`; that presentation delta received final manual inspection rather than a claimed suite rerun.
- The new B10/V2 native UI scenario revealed a real mounted-view restoration issue: stored selection and anchor were correct, but assigning an anchor before message layout did not scroll on return. Both embedded chat views now explicitly restore through `ScrollViewReader` after messages are available, preserving the pending anchor during loading. Final native re-run passed the full two-turn selection and group-return test (109.647 seconds), including a direct visible-control assertion without scrolling after return; see `inline-final-ui.log`.
- The V1/B8/E3 cross-group preview journey passed (218.192 seconds): A's edited preview remains exact across B and legacy visits, with no A recovery entry in those conversations. Confirmed saving and cold-start exact-file assertions remained intact.
- Ten successful native scenarios cover group lifecycle and independent drafts, private navigation, North Star source isolation, explicit selection and reading return, exact new-group saves, mention/large-text input, landscape viewport, consecutive-input focus, accessibility controls, legacy invitation/save and stopped/offline recovery. The report lists each of the ten test names, durations and logs; some scenarios cover more than one behavior.
- `inline-final-ui.log` retained one legacy activation-point failure. The test now checks the visible enabled confirmation button's actual frame before tapping its observed center; save/content/provenance/deduplication/cold-start assertions remain. A subsequent batch's legacy test was interrupted during repeated iOS animation waits and is not counted as passed. The standalone final legacy test passed in 99.911 seconds (`inline-legacy-final-ui.log`, `TEST EXECUTE SUCCEEDED`).
- The stable synthetic diary remains 1169 bytes with the same SHA-256 as before this task (`diary-after.json`). Only Riji Automated Tests, UDID `<SIMULATOR_UDID>`, was used; the real-data Development simulator was not reset or changed.
- Shared-tree changes and staging predate this task and remain intact. Review used task-start file snapshots against the current tree, including task edits to previously untracked files. Task-specific diffs, original failed logs and result bundles are retained; no commit or publication was made.
- Final actual screenshots are `home-final.png` and `large-type-final.png`. The ordinary-size mentor home is open with North Star, four portraits, messages and composer, without a group navigation step or selection overlay. See [the validation report](../../docs/testing/ios-mentor-inline-chat-2026-10-17.md) for the screenshot comparison, exact UI matrix, failed-run history, two pre-existing deferred findings and remaining real-service / iCloud / VoiceOver / physical-device boundaries. User experience acceptance remains pending.
- Additional manual evidence: `switcher-final.png` shows the in-place menu; `landscape-final.png` confirms visible controls but a substantially shorter message viewport. Landscape geometry is reachable; long messages require scrolling and reading comfort awaits user experience review. The simulator was returned to portrait.
