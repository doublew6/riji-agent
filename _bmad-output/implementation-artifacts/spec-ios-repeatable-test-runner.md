---
title: Repeatable isolated iOS regression runner
type: test-infrastructure
created: 2026-09-18
status: done
route: dispatch
baseline_commit: 5b87e5451e084045809b14111e57b843546df643
context:
  - PROJECT_GUIDE.md
  - docs/PRD.md
  - docs/architecture/mvp-architecture.md
---

<frozen-after-approval>
## Intent

The owner explicitly authorized improving app automation now, alongside the #53
bidirectional swipe correction. Make existing complicated diary, editor, connection,
mentor and memory tests reliably runnable from one Mac command and in CI. Do not
implement pending roundtable or structured-memory features (#56/#57). Preserve the
entire existing mixed worktree and the manual Simulator and phone data.

## Boundaries

Use synthetic fixtures and test namespaces only. Do not call real AI/Feishu, upload
private material, change Air, inspect credentials, stage/commit/push, or erase or
uninstall any Simulator. No production source change except a demonstrated testing
bug agreed with root. No dependency downloads outside existing reputable tools.
Default runner must execute tests, not silently fall back to a build or skip.
Separate automated devices from Riji Development iPhone. Reuse one dedicated named
Riji Automated Tests device, reject duplicates/unavailable existing devices, create
only if absent and a compatible iPhone/runtime pair is available. Disable Xcode
parallel testing and clones. Never kill processes to gain a device or build path.
Use a lock or otherwise refuse concurrent runner use, retain logs and result bundle
on failures, fail on tool error, no executed tests, unexpected skips or requested
smoke tests missing. A failed command may not be converted into success by tee.

## I/O and edge-case matrix

| Given | When | Then |
|---|---|---|
| Full Xcode and available compatible iPhone runtime | default smoke | full Swift package + full native + selected risky UI tests; evidence written |
| Existing dedicated device | rerun | reuse without erase/uninstall/duplicate creation |
| Duplicate named device, unavailable existing device, absent runtime or Xcode | run | actionable nonzero failure and no destructive command |
| xcodebuild/Swift fails, zero tests, selected test absent or skipped | run | nonzero; evidence retained; not green |
| A first runner owns the lock | second run | reject without killing first process |
| CI PR/main | workflow | same runner with artifacts on success/failure; full UI selectable manually |
| Manual device and real vault exist | test | no manual-device installation, no original-vault/phone/Air access |
</frozen-after-approval>

## Code Map

- `scripts/run_ios_simulator.sh` and `tests/test_ios_simulator_launcher.py`: device-selection and fake-tool failure testing patterns; do not change manual-launch behavior.
- `ios/Package.swift`: Core/State/Probe package suites.
- `ios/Riji.xcodeproj/xcshareddata/xcschemes/Riji.xcscheme`: Native/UI suites.
- `ios/RijiUITests/DiaryFlowTests.swift`: tests use isolated UUID namespaces and injected transport fixtures. Swipe names changed in current tree; read current names.
- `.github/workflows/test.yml`: Python-only existing CI; add separate `.github/workflows/ios.yml`, preserve Python workflow.
- Existing Python CI also runs Windows and Linux. Keep runner import and fake-tool tests portable; load macOS-only locking/system facilities only inside the actual runner path or through injected boundaries. Do not break the cross-platform Python suite by importing an unavailable module at collection time.
- Official GitHub macos-15 runner image documentation checked 2026-09-18 lists Xcode 26.3 at `/Applications/Xcode_26.3.app` with iOS Simulator 26.2; pin DEVELOPER_DIR to that path and use available compatible runtime, fail clearly if absent. Source: https://github.com/actions/runner-images/blob/main/images/macos/macos-15-Readme.md . Local machine has selected Xcode 27 and available iOS 26.5/27.0. Record actual environment, do not pretend local and CI match. No preview-only APIs are needed by app.

## Tasks & Acceptance

- [x] Add readable stdlib Python runner `scripts/test_ios.py` (Python 3.9+) with `--suite core|native|smoke|full` default smoke. Core requires no device; native only full native; smoke full package+native+selected UI; full all package+native/UI. Stable dedicated device and build cache; unique run artifact directory under ignored output by default. Optional runtime constraint if useful but do not proliferate configuration.
- [x] Add meaningful pytest coverage `tests/test_ios_test_runner.py` for selection, failure propagation, result audit and safety boundary (using fake executables/injected runner; no Xcode from pytest).
- [x] Smoke selects current tests for original body editing/cold reopen, long-body confirmation viewport, bidirectional swipe & missing future file, failed save, table horizontal scroll, four peer tabs, managed installation, mentor draft/history, memory conflict and corrupt archive. Cover actual named methods; no new redundant app tests.
- [x] Add GitHub PR/push main smoke workflow and workflow_dispatch full choice, least read permissions, stable Xcode image, timeout and artifact upload always. Do not claim it ran remotely before a pushed workflow run exists.
- [x] Add `docs/testing/ios-automation.md` with one-command use, suites, evidence meanings, synthetic-vs-real boundary, real iCloud probe/phone and Air/AI as separate acceptance; link from ios/README.md after preserving swipe agent's update.
- [x] Verify pytest runner tests, execute the new smoke runner locally after root confirms exclusive Xcode access; inspect .xcresult and all requested counts. Record actual output evidence in automation doc or dated evidence file. If tests expose failure, diagnose and report; never weaken assertions just for green.

## Verification

No Xcode until the swipe implementation agent has finished. Root owns reviews,
signed phone build and optional install. Run only your new runner's pytest locally
until root releases Xcode. When available run smoke once with all selected tests,
retain logs/summary/.xcresult. Backend all-suite need not repeat if no backend
behavior changes; root may run regression separately. Raw local run logs may contain
host paths, so do not copy them into tracked docs. Report actual suite counts and
all skips honestly.

## Implementation Notes

This is a distinct testing increment on the current #53 branch authorized in the
same user message; no new production feature, API, dependency, real operation or
further approval is required. Use current files, never git reset/revert mixed prior
work. Preserve the pre-existing staged voice spec. Report changed file list and
remaining gaps. Do not invoke other skills, spawn agents, or publish anything.

## Review Triage Log


- Root review: bundle presence alone could miss individual Native/full UI cases.
  Added actual Xcode enumeration and exact requested-versus-executed case comparison,
  with missing Native/full UI regression fixtures.
- Root review: the smoke set also needs the long-document previous/next and
  destination round-trip reading-position journey. Included it; smoke selects 14 UI methods.
- Local verification: Xcode 27 accepted Swift's serial xUnit option but did not write
  XML. The first runner failed closed. Replaced that unsupported output assumption
  with discovery-to-started/passed event auditing across all package targets,
  covering both supported XCTest identifier formats and malformed result cases.
- Safety correction: a preliminary enumeration schema probe mistakenly targeted
  the manual Simulator; enumeration loads its test runner. Formal runner operations
  use only the dedicated automated device. Root rechecked the manual device's
  20 retained data-file hashes and found them unchanged. The dated evidence records
  this deviation; it is not counted as an executed acceptance test.

### Independent review triage (2026-09-18)

All three review layers have returned. Each finding was considered separately:

| Finding | Verdict | Evidence and route |
|---|---|---|
| Blind 1: discovery ignores unsupported test identifiers | medium | The regex drops a synthetic Swift Testing-style entry while the three XCTest targets remain present. Fail closed on unrecognized test-shaped discovery lines; patch, without claiming Swift Testing support. |
| Blind 2: target omitted from identifier key | low | Two legal targets with the same class/method collide in package_inventory. Directly retain the full target/class/method identity in discovery and event audit; patch. |
| Blind 3: DEVELOPER_DIR app bundle rejected | low | Local xcode-select manual explicitly accepts an Xcode .app or Contents/Developer, but the runner accepts only the latter. Normalize the valid .app form and test both; patch. |
| Blind 4: commands can wait indefinitely | medium | subprocess.run has no timeout, including bootstatus and test execution. An unresponsive owned child prevents a local final failure record. Add bounded stage waits with retained timeout evidence; patch. |
| Blind 5: job deadline leaves no upload window | medium | Test and artifact upload share the same 60-minute deadline. Add a shorter test-step deadline and document cancellation limits; patch. |
| Blind 6: evidence not bound to source contents | low | This checkout includes untracked source; commands/SDK/test names cannot identify its exact bytes. Add a scoped SHA-256 manifest of runner and build/test inputs and compare after execution; patch. |
| Blind 7: child lock inheritance untested | medium | Existing fake flock checks contention only; removing pass_fds escapes the tests. Add a bounded POSIX synthetic child/competitor regression, with a Windows skip; patch. |
| Blind 8: SIGTERM leaves running summary | medium | Default signal termination bypasses finally and leaves the persisted status running. Record controlled termination and active command failure using a synthetic signal test; patch. SIGKILL cannot be intercepted and must remain documented. |
| Edge 1: unbounded child wait | medium | Independently confirms Blind 4 at subprocess.run; same bounded-wait patch, no killing unrelated processes. |
| Verification 1: filtered env never checked at child boundary | medium | Reviewer mutation env=os.environ left all 56 tests green. Extend the existing check to execute a synthetic child and verify prohibited values absent and allowed values present; patch. |
| Verification 2: inherited lock not exercised | medium | Reviewer removal of pass_fds left all 56 tests green. Same synthetic POSIX inheritance regression as Blind 7; patch. |

No production app source or feature decision changes are required. The successful
505-test run remains valid evidence for the unchanged app/test tree; patched runner
auditors will be checked against those saved results, with focused process tests
and a fresh package run rather than an unnecessary repeat of all UI journeys.

- Final local smoke: 451 package tests (Core 230, State 216, Probe support 5),
  all 40 Native and all 14 requested UI journeys passed, zero failures/skips.
  All 13 tool commands exited zero; actual Xcode discovery, requested inventory
  and result case tree agree. Evidence: `output/ios-tests/20260918T030058Z-smoke-ab439c3b/`.
  Runner pytest: 56 passing; unchanged launcher pytest: 10 passing.
  The actual second runner was rejected while the first held its lock.
  Local Xcode 27/iOS 27 evidence does not establish Xcode 26.3 GitHub CI success;
  the unpushed workflow and full UI/physical-service acceptance remain separate.

- Review repair verification: the focused runner suite now passes 76 tests. The
  package auditor retains target-qualified identities and rejects unsupported
  test-shaped discovery/event lines. Actual synthetic child processes verify
  environment filtering, bounded timeouts, SIGTERM terminal evidence, and POSIX
  lock inheritance after the owner dies. Xcode .app paths are normalized.
- Fixed stage/whole-run deadlines and a shorter CI test-step deadline reserve
  normal artifact-upload time; only directly owned children are stopped. SIGKILL,
  host loss and forced platform cancellation remain documented limitations.
- Scoped source SHA-256 snapshots now guard new runs; synthetic mutations fail.
  Local/private signing config, secret directories and generated/user settings
  are excluded. A later 80-input snapshot and offline 451+54 result replay are
  recorded separately; the old smoke did not have a preflight manifest and its
  retained evidence is unchanged. No real Swift/Xcode rerun occurred in this repair.

- The final unsupported-discovery correction also rejects unknown-target two-part
  identifiers. Its six parameterized cases passed (one newly added; 77 runner
  cases now exist, after the earlier 76-case passing run). Saved final smoke and
  root's later 451-case core logs replay successfully with the final parser.
  Root owns final whole-suite verification and review closure; status stays in-review.

### Root closure

Final runner pytest: **77 passed**, no failures/skips, with exact log and JUnit
evidence under `output/ios-tests/runner-review-fixes-20260918/root-pytest.*`.
Root also executed the bounded runner's real `--suite core`: **451 passed**, all
five tool commands passed and all 80 scoped inputs were unchanged during the run
(`output/ios-tests/20260918T033710Z-core-b60c2006/`). The final one-line discovery
format correction followed that run; only the runner input hash differs, with all
iOS inputs unchanged. Final 77-case pytest and independent replay of both saved
package runs and the 54-case Xcode result passed with this final parser. Replay
is recorded as later auditing, never another iOS run. All review findings are
resolved; this test-infrastructure increment adds no deferred defect.

No commit or push was made: this checkout still contains substantial prior iOS
work, including required untracked build inputs and an unrelated staged voice
specification. The reviewed narrow increment must not be published as a broken
partial tree or sweep those prior changes into a new commit. Existing staging
was preserved. CI, full UI and real phone/Air acceptance remain explicitly
separate from the completed local smoke and test-runner verification.
