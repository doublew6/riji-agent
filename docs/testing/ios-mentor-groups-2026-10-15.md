# Mentor private and persistent group chats — 2026-10-15

The user approved [PRD v0.3.1](../../_bmad-output/planning-artifacts/prds/prd-riji-agent-2026-10-14/prd.md) for development on 2026-10-15. This record covers the local native client and service implementation. It does not establish Air deployment, actual-model acceptance or physical-device installation.

## Implemented behavior

- Four interactive portraits remain the sole private-chat entry. The lower home section lists persistent groups, with an independent archived list. The F North Star visual baseline and original diary remain in place.
- Select two to four mentors to create an empty group. No model is called during creation. The automatic member-based name can be changed later. Different groups may contain identical members.
- Ordinary messages request one bounded reply from each member. An explicit member mention changes responders, while the message remains visible to the whole authorized group. No host or private-history import is introduced.
- Each group retains its identity, history, draft and reading state. Late results stay with their original chat. Archive/restore preserves content, and result saving uses the existing preview, confirmation and source checks.
- Older hosted records remain accessible; new group creation is capability-gated on older servers. Mid-chat membership changes and destructive deletion UI remain outside the approved first increment.

## Verification environment

Tests use synthetic, namespaced data in **Riji Automated Tests**, iOS 27, with unsigned Simulator builds from Xcode 27. The user's **Riji Development iPhone** simulator and phone data are not test targets. No production service, real diary or model credential is used by these fixtures.

Evidence is retained locally under `output/mentor-group-chat-2026-10-15/` and the audited runner's dated `output/ios-tests/` directories. These may contain personalized local character artwork in screenshots and are not a public artifact package.

## Results

Local implementation and regression are complete. Earlier failing runs remain as diagnostic evidence.

| Final check | Result / evidence |
|---|---|
| Full Python | **2,263 passed, five existing skips**, 198.23 seconds. `uv run --no-sync python -m pytest -o addopts='' --tb=short -q`; `python-reviewed.log` and `python-validation.json`. |
| Focused backend recovery | **192 passed**; `python-recovery-focused.log`. An independent reviewer also ran the 43 group/recovery tests successfully. |
| Full Swift Core/State/Probe | **623 passed, zero failures/skips**. `python3 scripts/test_ios.py --suite core`; `output/ios-tests/20261015T102925Z-core-d5e99638/`. All 115 audited source inputs were unchanged during the run. |
| Simulator build | `build-reviewed.log`: build-for-testing passed on the reviewed production sources. |
| Actual Simulator UI | **Nine distinct journeys have passing evidence**. `groups-ui-reviewed.xcresult` passed eight of nine; the remaining new-group save test passed in `group-result-ui-final.xcresult` after a test-only scroll correction. Both save flows were then rechecked for the final save-sheet appearance in `result-style-ui-final.xcresult` and `result-contrast-ui-final.xcresult`. |
| Whitespace | `git diff --check` passed. No staging, commit, publication, deployment or installation was performed. |

The nine UI journeys cover accessible large text/keyboard controls, North Star source continuity, group creation/rename, independent same-member groups and drafts, foreground and cold recovery, archive/restore, mention audience, new-group result preview/confirmed save/cold exact readback, private draft/reply/memory correction, older private history, and legacy result/offline recovery.

The corrected UI test originally waited for an offscreen lazy Form row before scrolling. The retained accessibility hierarchy shows a successfully generated, source-bound preview below the editor; revealing the confirmation row before waiting fixes test positioning. Its text/preview/confirmation/exact-byte assertions remain intact. The test scroll correction changes only that UI test line. Final visual inspection then applied the existing approved diary page style to the shared save Form and primary foreground to its Done button, correcting the pale inherited mentor tint over a light background. These UI styling changes affect no Core/State or backend behavior; both save journeys are rechecked on them. `final-summary.json` records the two changed UI source/test files since the full Swift audit.

Earlier runs:

| Check | Evidence before final review repairs |
|---|---|
| Full Python | `uv run python -m pytest -q`: 2,250 passed, five existing skips; `python-full-module.log`. The first console-entrypoint attempt failed collection and is retained separately. |
| Full Swift Core/State/Probe | `python3 scripts/test_ios.py --suite core`: 615 passed, zero failures/skips; `output/ios-tests/20261015T092734Z-core-34ecd357/`. |
| Simulator build | `build-final.log`: build-for-testing passed. |
| Initial selected UI | `groups-ui.xcresult`: one of five passed. The run exposed inherited identifier propagation and a legacy destination being destroyed during result preview; both were repaired and passed the reviewed run. |

## Review repairs

Three independent review lenses checked the scoped diff: missing behavior, edge cases and verification gaps. All fourteen independent findings were addressed, and focused follow-up review found no new defects. Findings and individual dispositions are recorded in the [implementation spec](../../_bmad-output/implementation-artifacts/spec-56-mentor-private-and-group-chat.md).

Repairs include version-safe metadata changes; source-chat-bound late callbacks; foreground selection; current-version paged reading restoration; actual-delivery unread cursors; structured answer fields; same-run result selection and original-topic provenance; speaker-attributed summaries; Chinese mentions; deletion metadata cleanup; and generated-but-undelivered context exclusion. Empty-group v2 creation atomically commits the group and receipt; interrupted precommit creation can safely fail without duplicate replay. Ending an unknown turn preserves its audit and allows a separate new turn without replaying the old one. Unversioned historical unknown creations without proof remain conservative unknowns.

## Actual app captures

These are native Simulator screenshots using synthetic chats, not design mockups:

| Portrait home | Empty persistent group |
|---|---|
| ![Portrait home](../../output/mentor-group-chat-2026-10-15/mentor-home.png) | ![Empty persistent group](../../output/mentor-group-chat-2026-10-15/group-empty.png) |

Additional captures: [large-text mention composer](../../output/mentor-group-chat-2026-10-15/group-mention-large-text.png), [restored original draft](../../output/mentor-group-chat-2026-10-15/group-restored.png), and [group result preview](../../output/mentor-group-chat-2026-10-15/group-result-preview.png). Local output is ignored by version control; screenshots are not included in a public package.

## Remaining acceptance boundary

Air has not been changed by this increment. A capability-gated local build cannot create the new groups against an older production service until that service is updated. Actual replies, network interruption, mobile-data use, touch/keyboard feel, VoiceOver and device installation require the separate real-service and phone acceptance path. Development completion does not imply those checks passed.

## User simulator handoff — 2026-10-15

On the user's request to test locally before phone installation, three critical native journeys were rerun successfully: group creation/rename/independent drafts/foreground/cold/archive recovery, large-text mention input, and preview-confirmed result saving with exact cold readback. Evidence: `output/mentor-simulator-review-2026-10-15/manual-preflight.xcresult`.

The normal **Riji Development iPhone / iOS 26.5** was updated through the existing signed normal launcher. Before installation, App Documents/Application Support files were backed up; all retained identical hashes after launch. No real diary editing input was issued. This does not establish that external original files stayed unchanged or that the production service supports new groups.

Device Hub was then visibly switched to **Riji Automated Tests / iOS 27.0**, with the new mentor home open in the persistent isolated roundtable demonstration namespace. Its replies are synthetic and it does not call Air or a model. The normal device remains separate with its real-data authorization. No phone installation or production update was performed; those remain conditional on the user's review.
