# Build the native iOS daily journal with durable local capture

Proposed GitHub issue; not published. Implementation has not started.

## User outcome

Opening the iPhone app immediately shows today's diary. A user can type a short entry, see its target date and template section, explicitly save it once, and find the same Markdown content after closing and reopening the app. The app does not require an account or AI configuration to record locally.

This is the first implementation slice of the accepted iOS design in `docs/PRD.md`, primarily FR-01, FR-05, FR-08, FR-09 and the local part of FR-37. Weekly and monthly reports, north-star metrics and daily MIT, voice capture, mentors, memory and managed online services remain mandatory parts of the overall first release; this issue does not claim to complete them.

## Scope and files

- `ios/Riji.xcodeproj`: a native SwiftUI iPhone application, a shared build scheme and test targets, with no third-party runtime dependency.
- `ios/Riji/`: the today screen, inline text capture, target-section selection, date navigation, a diary directory, and local draft restoration. No setup wizard or server configuration on the home screen.
- `ios/RijiCore/`: app-owned Markdown storage, deterministic daily-template rendering, version-checked section appends, atomic persistence and stable operation identifiers. Internal drafts and recovery state are kept outside the user-visible diary directory.
- `ios/RijiCoreTests/` and `ios/RijiUITests/`: isolated synthetic tests of save, restart recovery, duplicate confirmation, invalid sections, changed source content and failed writes.
- `ios/README.md`: local build instructions, simulator verification, file layout and a clear list of implemented and remaining capabilities.

The initial template must be labeled as the product's initial template. The user's existing templates must not be claimed as migrated or replaced with invented personal content. The native client operates only on its own app sandbox; it does not read the existing diary vault, call a production service, or change server-side write authorization.

## Acceptance criteria

1. Given a fresh install without an account or network, when the app opens, then today's template is visible and text capture is available immediately.
2. Given typed but unconfirmed text, when the app is reopened, then the draft and its original date and section are restored without adding it to the committed diary.
3. Given a visible draft and target, when the user taps the single save action, then the exact confirmed text is appended to the selected section, the app stays on the same day, and success appears only after durable local persistence.
4. Given the same save operation is retried after interruption, when recovery runs, then the diary contains no duplicate append.
5. Given a missing or ambiguous template section, a changed file version, or a failed write, when save is attempted, then existing Markdown is preserved and the draft remains available with a useful error.
6. Given the date passes midnight with a draft open, when the app becomes active, then the existing draft keeps its original target day and the user can explicitly switch to today.
7. Given a saved diary, when the user exports it, then a standard Markdown file contains the confirmed content without credentials or internal recovery state.
8. Given the installed Xcode toolchain, when the shared scheme builds and the storage tests run, then they pass; simulator UI verification is recorded separately from compilation and device-signing readiness.

## Dependencies and boundaries

No existing GitHub issue covers this native client slice. Its local recording path does not depend on the mobile authentication gateway or AI availability. The full release still needs those contracts, account isolation, real-template migration and device validation. App Store distribution and production deployment are separate work.

Repository changes already present before this task must remain intact. The proposed implementation branch is `codex/ios-local-diary`, with an issue-number suffix if useful after the issue is created. Only this slice's new files will be included in a later code review.
