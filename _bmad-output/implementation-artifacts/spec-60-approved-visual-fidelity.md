---
title: Complete approved iOS visual hierarchy
type: bugfix
created: 2026-10-15
status: done
route: dispatch
review_loop_iteration: 0
baseline_commit: 5b87e5451e084045809b14111e57b843546df643
context:
  - PROJECT_GUIDE.md
  - docs/DESIGN.md
  - docs/design/mentor-dialogue.md
---

<frozen-after-approval reason="User approved this visual correction on 2026-10-15">

## Intent

**Problem:** The last integration applied colors and routes but left the mentor entrance as a black system list, the mentor scene too flat, and memory/workflow pages without the approved hierarchy. Functional verification was incorrectly treated as sufficient visual verification.

**Approach:** Complete the existing approved visual language: a continuous quiet forest mentor entrance and stage, warm portraits and readable dialogue, structured memory cards, and a clear direction/MIT reading hierarchy. User approved starting this correction and wants to inspect the Mac simulator before updating iPhone.

## Boundaries & Constraints

**Always:** Preserve real capabilities, account/source isolation, source validation, all existing routes and accessibility identifiers, four peer tabs, drafts, full chat history, expiry/error/offline states and explicit write confirmations. Use local existing portrait assets and approved F reference. Dynamic Type, small widths, keyboard and Reduce Motion must work. Keep diary paper dominant. One featured memory card may use the globally defined forest tokens, as approved in this correction; the original 18-screen CSS used selectedSurface, so document this distinction.

**Never:** Implement the unapproved mentor interaction proposal, change APIs/models/storage, fake goals/history/reports, alter actual journals, reset devices, deploy Air, install to iPhone or publish. No remote design generation or private artwork uploads. Do not claim pixel equivalence to an illustrative generated image.

</frozen-after-approval>

## Code Map

- Work only in `<implementation-worktree>`. It contains inherited uncommitted work; do not stage/reset/stash it. Baseline: `output/ios-visual-fidelity-2026-10-15/baseline-manifest.json` and `baseline/`. Parent owns integration and all Xcode runs.
- `ios/Riji/MobileViews.swift`: `MobileMentorsView` applies background outside NavigationStack and listRowBackground to List, leaving default surfaces. Restyle entries; preserve destinations, capabilities, polling and composer.
- `ios/Riji/MentorDialogueStyle.swift`: backdrop, portrait crop, stage and speaker cards. Add static depth, warm portraits and table composition; decoration noninteractive/accessibility-hidden. Collapse with keyboard/accessibility sizes.
- `ios/Riji/MobileRoundtableViews.swift`, `NorthStarAnchorViews.swift`: preserve invitation, north-star reference semantics, history and selection; only visual adjustments if needed.
- `ios/Riji/ApprovedPageStyle.swift`: add forestSurface/onForest/onForestSecondary matching DESIGN including dark references; reusable emphasis styling.
- `ios/Riji/MobileOverviewViews.swift`: keep expiry/pagination/filter guards; compact metadata, top judgment entry, first visible theme featured, remaining warm cards; readable summary detail and real evidence. No invented date/count/timeline.
- `ios/Riji/WorkflowViews.swift`: `DirectionAndTodayView` should show stage/period, star, title/definition, then secondary measurement metadata; group existing MIT and emphasize `directionMIT` link. No inline editing behavior changes.
- `ios/Riji/ApprovedSettingsViews.swift`, `DiaryDirectoryView.swift`: small header/selected-surface corrections only; no route changes.
- References: `docs/design/ios-pages-2026-10-14/build_design.py`; private approved F image at primary repo `output/mentor-dialogue-north-star-2026-09-27/direction-f-north-star.png` (view before implementing).

## Tasks & Acceptance

**Execution:**
- [x] Mentor entrance and scene styles: visible forest surfaces inside navigation, integrated stage and readable user/AI dialogue, same reachable actions.
- [x] Memory and direction hierarchy: explicit featured card and reading composition using real state; settings header and directory surface consistency.
- [x] Extend existing synthetic UI screenshot coverage for mentor landing, populated scenes, memory featured card and direction/MIT; keep assertions meaningful and existing flows. Parent runs tests after implementation returns.
- [x] Record precise local visual evidence and limitations; update design implementation notes without rewriting approval history. Parent handles documentation, archive alignment and manual simulator update.

**Acceptance Criteria:**
- Actual simulator screenshots visibly replace black/gray mentor entrance with the quiet green family, and show stage depth, portrait and dialogue organization consistent with F.
- Memory shows title/status hierarchy and one readable forest emphasis card, with filtering and source drill-down still working; expired or unsupported data is never pictured as current.
- Direction uses paper, star and continuous goal definition; MIT confirmation still uses existing workflow with mismatch warnings.
- Large text and keyboard do not trap composer/actions; switching tabs preserves diary state and appearance. Review actual images in addition to test results.

## Implementation Notes

- No intent gaps or irreversible steps. User already approved this scope; isolated filesystem baseline preserves inherited changes.
- Sleek generation is unnecessary for existing local approved designs.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Evidence and route |
| --- | --- | --- |
| B1 | low | The first filtered summary is positional, not an importance ranking. Rename its accessibility value to “首个主题”; patch. |
| B2 | low | Actual screenshots show the low-contrast native chevron beside the explicit footer arrow. Hide only the native indicator using the iOS 17 API; patch. |
| B3 | false | MobileRoundtableView immediately follows the decorative stage with explicit “本轮发言” participant names. Membership is not conveyed by name color alone; keep warm portraits. |
| B4 | low | Actual stage screenshots show rectangular side/bottom edges of the static landscape. Apply a static edge fade to that decorative layer; patch. |
| B5 | low | The original file tree has no active-file identity highlight before this change; only the existing App date list carries selection. Reliable file identity is a separate pre-existing issue; defer. |
| B6 | low | The new selected-day background/font has no corresponding selected accessibility trait. Add the conditional trait to the same existing date comparison; patch. |
| B7 | medium | Direction sections became plain VStack text, losing native Section heading semantics. Restore heading traits for the direction and MIT headings; patch. |
| B8 | low | Single-summary screenshots cannot demonstrate one featured card among multiple categories. The large-text final capture does show its title/body/footer after scrolling; add multi-theme/filter assertions with V2, patch. |
| E1 | medium | The 54-point table is drawn after portraits with only 49-point bottom padding, hiding the active-speaker capsule. Raise portraits to 56-point bottom padding; patch. |
| V1 | medium | The new direction hierarchy UI case is not registered in SMOKE_METHODS, so default CI misses it. Add the case to the existing selection; patch. |
| V2 | low | The overview fixture only returns one summary. Add a dedicated synthetic multi-summary fixture and assert the first displayed card changes after category filtering; patch with B8. |

All reviewer findings were classified before grouping. No intent gaps or spec re-derivation are required; fixes are direct presentation, accessibility and existing test-fixture corrections. No API, model or storage behavior changes.

## Verification

- Parent runs the existing locked targeted runner with relevant mentor north-star, accessibility keyboard, roundtable invitation, memory filter/invalidation and MIT reconciliation UI cases. Build must succeed with no skipped/missing requested cases.
- Inspect synthetic screenshots at normal/large text against approved references; record differences.
- Hash-guard primary integration; `bash scripts/run_ios_simulator.sh --normal`; preserve files/auth and leave iPhone unchanged.

### Actual verification

- Three targeted UI runs passed 8/8, 5/5 and 5/5 with zero failed/skipped/missing tests and 115 unchanged source inputs per run; these cover eight unique flows, not eighteen unique tests.
- Related runner tests: 80 passed. Default smoke now includes the direction hierarchy flow.
- Actual screenshots inspected; review patches confirmed. Multicategory first-card reassignment and normal evidence invalidation both pass. Original-tree current-file highlighting remains deferred as a pre-existing issue.
- No local commit: the files depend on inherited uncommitted implementation, and the primary tree contains unrelated staged user work. Preserve the task-only filesystem diff instead of bundling unrelated history or creating a non-buildable partial commit.
- Guarded integration completed for 15 task files; all 115 primary build inputs match the tested tree, Git index unchanged.
- Normal manual-simulator build/install/launch succeeded; all existing data files unchanged. Last native foreground inspection was blocked by macOS lock screen; user asked to unlock. Actual automated screenshots are reviewed; iPhone/Air remain unchanged.

- After the user unlocked the Mac, native foreground inspection confirmed diary, mentor entrance, single mentor, roundtable and settings visuals. Connection revalidation succeeded. The real overview returned an incomplete organization state with no valid summaries; populated cards remain verified with synthetic tests. The Mac locked again before the final return to Mentors; last confirmed foreground page is Memory.
