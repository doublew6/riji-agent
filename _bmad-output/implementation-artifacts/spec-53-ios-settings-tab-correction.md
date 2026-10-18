---
title: 'Place Settings beside the main tabs and clarify recordings'
type: 'bugfix'
created: '2026-09-15'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

The user clarified with a screenshot that Settings belongs beside Diary, Mentors and Memory in the main bottom navigation. The previously implemented fixed diary-directory footer misunderstood that placement. Add Settings as the fourth peer tab, remove that footer, retain the existing Settings content and subpage navigation, and preserve diary input through the existing save-before-leaving gate.

The More entry called “恢复的录音” opens the existing local voice library, including ordinary records and unfinished work; it does not indicate that a failure just occurred. Call this entry and its library section “录音记录”, and explain that saved audio and unfinished text can be viewed there. Preserve all recordings, recovery actions and storage identities. Final Simulator handoff uses normal personal mode with the user's selected template unchanged.

</frozen-after-approval>

## Implementation Notes

- This is a correction within claimed Issue #53. The latest explicit user instruction supersedes its original directory-footer acceptance. No new backend, file-writing or voice scope is introduced.
- Intent gaps: none. Irreversible operations: none. Footprint: four SwiftUI view files, relevant existing UI flows, and current product/manual documentation. No storage model or public API changes.
- Preserve the dirty baseline and index. Exact working files and personal/sample Simulator data were backed up under ignored `output/ios-settings-tab-2026-09-15/baseline`. Do not commit mixed prior implementation or unrelated Python changes.
- Reuse `RijiNavigationView.select` and `DiaryViewModel.prepareMainDestination` for the fourth tab, including IME commit and failed-save retention. Wrap existing Settings in its own NavigationStack. Settings directs users to the diary directory to inspect their selected external folder and access state; do not add a second browser instance or pretend folder permissions were verified by Settings.
- Remove only the fixed inset in `DiaryDirectoryView`; preserve browser startup, closure and list-refresh lifecycle. Rename labels in `TodayDiaryView` and `VoiceViews`, keeping accessibility IDs and all voice operations unchanged.
- Given a saved diary, opening Settings shows the fourth selected tab and real existing preferences; subpages return within Settings. Given pending input, navigating to Settings saves it first; on failed save the diary and text remain. Given existing recordings, the renamed entry opens the same library without starting capture. Existing read-only directory preview and saved date/position navigation remain regression coverage.
- Verification: focused Simulator UI flows for Settings/subpages, save success/failure, date/position and timezone routing, plus recording entry; build and normal-mode visual inspection. No Air operations or external vault writes.

- Independent review found that moving Settings bypassed the former directory-sheet dismissal refresh. Returning from any non-diary tab now awaits the same safe `directoryClosed()` reload before showing Diary, so a template change remains visible even when returning via another tab. The existing store preserves saved/draft-bound templates; focused coverage checks both fresh and fixed content. The linked confirmation record was corrected as well.

## Review Triage Log

- medium / patch: The fourth tab bypassed the former directory-dismiss reload, so a new daily template or weekday preference could leave an unwritten diary stale. Returning to Diary now awaits the existing safe reload, including indirect returns through other tabs. Added actual UI coverage for imported template, weekday changes and saved-content/relaunch preservation; existing state regressions cover saved/draft-bound templates and workflow refresh ownership.
- low / patch: The PRD-linked confirmation record still presented the superseded footer as final. Its S01 conclusion now records the latest screenshot clarification. Other historical records retain their earlier evidence with an explicit correction link.
- Follow-up independent read-only review verified both fixes, switching ownership, session viewport retention and saved/draft priority. No remaining findings or new deferrals.


- Final verification: six actual Simulator UI flows, one native composition/save test, and two existing template/state regressions passed with zero failures. Final Xcode result is `Test-Riji-2026.09.15_16-39-24-+0800.xcresult`; frozen code hashes matched. The initial keyboard-obscured test click was corrected by explicit dismissal and hittability assertions before rerun.
- Normal-mode build/launch succeeded; actual Simulator screenshot shows the selected imported personal template and four bottom tabs. All 22 prior personal/sample files remained byte-identical. Only the new test Runner was uninstalled after its 38 files were archived and verified; main App/data retained. Original index and unrelated Python files remain unchanged. No mixed-tree commit or remote publication was made.
