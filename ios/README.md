# Riji for iPhone — local diary slice

A native SwiftUI iPhone app with an iOS 17 deployment target. It opens today's diary immediately, retains typed drafts, appends the exact confirmed text to a selected heading, browses dates and saved diaries, and exports standard Markdown. It has no account setup, networking, AI calls, credentials, vault access or third-party runtime dependencies.

The bundled template is visibly labeled **产品初始模板**. Personal templates have not been migrated. This is the first local slice of the accepted product plan, not the complete first release. Original daily/weekly/monthly templates, weekly and monthly reports, north-star metrics and MIT, voice, mentors, memory, account isolation, managed AI and controlled synchronization remain required product work.

## Build and test

Use the installed Xcode developer directory (`xcode-select -p`). No Python environment, backend service, Air connection or production data is required.

```sh
swift test --package-path ios

xcodebuild -project ios/Riji.xcodeproj -scheme Riji \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build

xcodebuild -project ios/Riji.xcodeproj -scheme Riji \
  -destination 'platform=iOS Simulator,name=Riji Development iPhone' \
  CODE_SIGNING_ALLOWED=NO test
```

The shared scheme runs `RijiUITests`; the dependency-free Swift package runs `RijiCoreTests` and `RijiStateTests` separately, compiling the same ViewModel and FileDocument adapter used by the app. A matching, installed iOS runtime and explicitly created simulator named `Riji Development iPhone` are prerequisites for UI tests. Open `ios/Riji.xcodeproj` in Xcode to run interactively. Simulator builds need no signing team; physical-device installation and App Store distribution are outside this slice.

For isolated build products, add `--scratch-path /tmp/riji-ios-swift-build` to `swift test` or `-derivedDataPath /tmp/riji-ios-derived` to `xcodebuild`. Host tools need permission to access their ordinary compiler caches and CoreSimulator services. A sandbox service denial is not evidence that the SDK is missing.

UI tests use a fresh UUID namespace inside the test app's own sandbox. Debug-only `RIJI_TEST_CONTAINER` accepts a UUID, not a filesystem path; `RIJI_TEST_DATE` injects an ISO timestamp for deterministic midnight/relaunch coverage. Release builds ignore both variables. Test data is synthetic.

## Files and persistence

All application storage stays in its own sandbox:

| Location within the app container | Content |
| --- | --- |
| `Documents/Journals/YYYY-MM-DD.md` | Confirmed user Markdown only |
| `Library/Application Support/Riji/timezone.json` | Device timezone selected on first use, then fixed |
| `Library/Application Support/Riji/selection.json` | Last selected diary date |
| `Library/Application Support/Riji/drafts/` | Unconfirmed text, day, exact heading, effective template, source version and stable operation ID |
| `Library/Application Support/Riji/transactions/` | Confirmed in-progress operations with before/after hashes and recovery bytes |
| `Library/Application Support/Riji/receipts/` | Durable results keyed by operation ID and payload hash |

The app does not enable Files in-place editing, file sharing, iCloud containers or external-vault access. Export uses the system destination picker and includes one confirmed `.md` file; drafts and recovery records are not included. Export cancellation or failure leaves the original diary and draft unchanged. OS-managed device backup behavior follows the user's system settings; this app does not implement cloud synchronization.

A single actor serializes storage calls. Before a diary changes, its draft and explicit confirmation transaction are persisted. Replacement writes a unique temporary file, flushes it, atomically renames it and flushes the containing directory. The resulting Markdown is read and verified before a receipt is persisted. Draft and transaction cleanup comes last. No success message appears until this completes. If cleanup fails, retry uses the same receipt and never appends again.

Recovery only completes an already persisted confirmation. The current diary must match the recorded before or after hash; any unknown state stops automatic writing and retains the draft/transaction. Reusing an operation ID with changed payload is rejected, including before transaction persistence when its draft already exists. Editing creates a new candidate ID while retaining the day, effective template and source version; an unchanged retry keeps its original ID. Independently confirmed identical text gets a fresh ID and is saved again. Receipt history is intentionally retained for retry detection in this slice.

Draft typing is queued in order to disk, outside the diary; superseded queued revisions are skipped before their writes. Save and navigation wait for the latest retained version. The UI only reports retained text after its write succeeds; a failed write keeps the latest visible text and reports the failure. A forced termination before a pending draft write reaches disk can only restore the last successfully persisted version. A draft's date is never silently changed at midnight or after travel; use **回到今天** or date navigation explicitly. Navigation preserves drafts for other days in the directory.

The app matches complete ATX Markdown headings outside backtick/tilde fences, HTML comments and frontmatter, including nested section boundaries. It rejects missing or duplicate destination headings, and input that would hide an existing heading or introduce a duplicate existing anchor. Such input stays in the draft. Appending inserts the original user string unchanged, with only the required surrounding line separator, while preserving all preexisting Markdown bytes. The initial template instantiates only `{{date}}` and `{{title}}`; template scripts are never executed. Existing diary bodies are never re-templated or overwritten wholesale. Reading currently presents headings and literal Markdown body text; advanced Markdown rendering and template import are separate work.

## Verification scope

Core tests cover fresh opening, durable draft restoration, fixed timezone/midnight, exact byte preservation, heading ambiguity/fences, source conflicts, concurrent retries, changed payload rejection, independent repeated text, export failures, and failures before/after draft, transaction, Markdown and receipt replacement and cleanup. All files are temporary synthetic fixtures.

UI tests separately exercise first launch, text/section/date restoration across process relaunch and midnight, same-page save with retained reading position, deliberate repeated records, directory navigation, actual Markdown export through the system destination picker, and export cancellation. Core and state tests verify the Markdown bytes and FileDocument adapter. In the UI test namespace only, the system-export destination is read back and its digest is compared against independently constructed expected Markdown; an unconfirmed draft must be excluded. Normal app exports do not depend on the destination being readable. Physical-device signing, device storage exhaustion, lock/background interruption timing, VoiceOver, and external destination-provider behavior need device validation. Compilation alone is not evidence that these device checks passed.

### Recorded local verification — 2026-09-13

- Xcode 26.6 (17F113), Swift 6.3.3, iOS Simulator SDK/runtime 26.5; deployment target remains iOS 17.
- `swift test`: **40 tests passed** (32 core and 8 ViewModel/document-adapter tests), including a 13-case persistence-boundary recovery matrix.
- Generic iOS Simulator build: **BUILD SUCCEEDED** (arm64 and x86_64 simulator compilation).
- `Riji Development iPhone` UI acceptance: **5 tests passed**, including actual system Markdown export and cancellation, unchanged reading position, and process relaunch across midnight.
- Synthetic fixtures only. No backend files, services, configuration, permissions, real diaries, production data or remote publications were changed by this implementation.

This evidence covers simulator execution, not a physical iPhone or the full product release. App Store signing/publication, real-device installation, iCloud integration and production migration were not performed.


Review fixes also cover raw UTF-8 identity, foreground midnight in the fixed journal timezone, source-review serialization and empty-draft directory filtering. The scrolling regression creates a long diary, scrolls to a middle heading, adds text to an earlier section, and checks the same heading remains within 2 points of its original position. Restoration waits for a geometry snapshot belonging to the new Markdown before adjusting the native scroll offset.
