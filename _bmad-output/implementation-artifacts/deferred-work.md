
- source_spec: `_bmad-output/implementation-artifacts/spec-ios-inline-diary-editor.md`
  summary: Validate background departure while the latest body buffer is still writing (unverified medium).
  evidence: L2-B5: buffer retention is queued immediately and full revisions are coalesced, but no real lifecycle/held-storage trace establishes loss or a bounded background guarantee; test suspension with exact durable/visible bytes before changing lifecycle behavior.

- source_spec: `_bmad-output/implementation-artifacts/spec-ios-inline-diary-editor.md`
  summary: Measure actual native large-document typing responsiveness (unverified medium).
  evidence: L2-B8: linear full-string admission/style/buffer work is present; correctness and paced storage-growth tests are not a per-keystroke latency or memory benchmark. Measure specified document sizes on a specified device before introducing caches or a performance budget.

- source_spec: `_bmad-output/implementation-artifacts/spec-ios-inline-diary-editor.md`
  summary: Keep late transcript and history errors bound to their original recording.
  evidence: L1-E3/L2-B9: unchanged pre-editor VoiceViewModel.showTranscript and showHistory check identity on success but not in catch; a late old-record error can alter the new selection. These nine original voice source boundaries remained unchanged in this body-editor correction.

- source_spec: `_bmad-output/implementation-artifacts/spec-ios-inline-diary-editor.md`
  summary: Coordinate supported external document writers across the shared atomic-file storage boundary.
  evidence: L3-B1: the original append writer and new body writer both read source separately from POSIX rename; an independent uncoordinated writer can change the path between them. Actor serialization covers only app-owned operations. Establish supported coordinated-writer behavior and a deterministic cross-process test before shared external-vault editing; no universal compare-and-swap guarantee is claimed.

- source_spec: `_bmad-output/implementation-artifacts/spec-ios-inline-diary-editor.md`
  summary: Verify attributed Dynamic Type refresh without editing (unverified medium).
  evidence: L3-B6: native UITextView enables adjustsFontForContentSizeCategory and uses preferred fonts, while the style cache keys on bytes. Measure actual body, heading and protected-run fonts across content-size changes in the mounted editor before deciding whether extra invalidation is required.

- source_spec: `_bmad-output/implementation-artifacts/spec-53-ios-diary-navigation.md`
  summary: Verify large-font Settings footer hit targets and VoiceOver date/return focus before final personal-MVP accessibility acceptance (medium, unverified).
  evidence: The new navigation uses native labels, links and a date picker, but current UI tests use default typography and do not enable VoiceOver. Actual large-font and VoiceOver runs must verify reachable Settings, previous-date navigation via the picker, and focus restoration; code inspection alone cannot establish those behaviors.
- source_spec: `_bmad-output/implementation-artifacts/spec-54-ios-editor-viewport.md`
  summary: Optimize whole-document Markdown presentation for exceptionally long diaries while preserving native input, source bytes and viewport stability.
  evidence: Actual 13-insertion Simulator timings on 2000 headings averaged 589 ms before the correction and 682 ms with complete layout before viewport restoration; the 90-heading corrected case averaged 60 ms. The pre-existing full-body formatting bottleneck remains, and the added layout cost is measurable. A separate incremental-presentation change needs its own evidence rather than weakening the proven scroll correction.

- source_spec: `_bmad-output/implementation-artifacts/spec-53-ios-bidirectional-date-swipe.md`
  summary: Existing JournalDay uses Foundation's historical Gregorian cutover rather than a proleptic Gregorian domain.
  evidence: Review reproduced 1582-10-04 to 1582-10-15 and Julian leap behavior in 1500; parser/formatter and previous-day behavior predate this increment. Modern diary dates are unaffected; any change requires consistent parsing, arithmetic and storage compatibility, not only a navigation override.
- source_spec: `_bmad-output/implementation-artifacts/spec-53-ios-bidirectional-date-swipe.md`
  summary: Add a direct adjacent-target read-failure regression case to the existing original-vault failure suite.
  evidence: Source failures and candidate folder reads are tested, but no direct next/previous swipe destination placeholder or unreadable-template failure followed by retry was found. The shared load/navigation implementation is unchanged.
- source_spec: `_bmad-output/implementation-artifacts/spec-53-ios-bidirectional-date-swipe.md`
  summary: Unverified medium-risk mid-gesture document replacement should receive an actual UIKit lifecycle regression test.
  evidence: Existing updateUIView cancels date gestures and source-day validation rejects stale callbacks; no defect was reproduced. A pan begun on one day, externally replaced document and subsequent UIKit cancellation/completion would settle whether stale completion is possible.

- source_spec: `_bmad-output/implementation-artifacts/spec-54-ios-inline-table-editing.md`
  summary: Verify explicit same-day restore to the last model bytes while a local editor echo is pending (medium, unverified).
  evidence: Byte-only echo suppression has no origin token, but real model updates accept synchronously and restore/refresh paths serialize dirty input. Reproduce through DiaryViewModel with a delayed acknowledgement and an explicit same-value replacement before changing the protocol.

- source_spec: `_bmad-output/implementation-artifacts/spec-54-ios-inline-table-editing.md`
  summary: Measure continuous table typing latency near the 1,024 visible-cell limit (medium, unverified).
  evidence: Input reparses source and rebuilds display labels; no reliable observed delay was established. Collect controlled per-keystroke latency and allocations rather than using anomalous simulator XCTest durations.
- source_spec: `_bmad-output/implementation-artifacts/spec-54-ios-external-file-refresh.md`
  summary: Measure large-vault foreground refresh cost before replacing repeated identity scans (medium, unverified user impact).
  evidence: refreshBodySnapshot calls bodySourceID twice plus open, which scan daily paths. Enumeration is bounded, but no timing or device energy evidence establishes an everyday regression. Synthetic large-directory timing and physical-device profiling would settle impact; retain source identity validation meanwhile.

- source_spec: `_bmad-output/implementation-artifacts/spec-60-approved-visual-fidelity.md`
  summary: Add reliable active-file highlighting to the original vault directory tree.
  evidence: B5: the pre-existing file tree highlights only its root and does not carry the active diary file identity; the App date list already has a selected-day comparison. Determine identity without inferring it from arbitrary filenames.
- source_spec: `spec-57-organization-coverage-recovery.md`
  summary: Handle corrupted latest report JSON and malformed ready report containers without blocking safe report fallback.
  evidence: Pre-existing status JSON parsing and covered/groups iteration can fail on manually corrupted stored reports; validated production writers do not generate these forms.
- source_spec: `spec-57-organization-coverage-recovery.md`
  summary: Isolate malformed initialization report cache rows during restoration.
  evidence: Existing json.loads on a damaged cached row blocks the whole restoration; add per-row corruption recovery with current evidence validation.
- source_spec: `spec-57-organization-coverage-recovery.md`
  summary: Define bounded automatic recovery of interrupted processing tasks with saved progress.
  evidence: Current stale claim marks interrupted work failed; saved checkpoints survive but an explicit organization action is still required after a processing crash.
- source_spec: `spec-57-organization-coverage-recovery.md`
  summary: Align Mobile actionable organization coverage with non-seed observation exclusions.
  evidence: Existing worker excludes observation records while Mobile can count them as unorganized and enable an action with no ordinary seeds.
- source_spec: `spec-57-organization-coverage-recovery.md`
  summary: Reject blank observation summaries and limitations consistently in shared validators.
  evidence: Existing observation validation accepts whitespace of positive length; checkpoint storage currently retains compatibility rather than independently changing that rule.
- source_spec: `spec-57-organization-coverage-recovery.md`
  summary: Specify partial-checkpoint consumption across Review and derived-observation recall.
  evidence: Worker and Mobile now use valid partial reports; Review and derived-observation recall intentionally wait for completed reports, while raw confirmed memory remains available.

- source_spec: `_bmad-output/implementation-artifacts/spec-56-mentor-inline-group-chat.md`
  summary: Differentiate multiple groups with the same members without requiring users to infer identity from identical default titles (medium, pre-existing usability issue).
  evidence: B7: `src/riji_agent/mobile/group_chats.py` creates default titles by joining mentor names, and the task-start iOS group list already displayed those identical titles. The inline switcher preserves separate conversation IDs and explicit group renaming. A follow-up should define a concise distinguishing label or naming policy with user review, without merging conversations or changing existing group identities.

- source_spec: `_bmad-output/implementation-artifacts/spec-56-mentor-inline-group-chat.md`
  summary: Surface bounded reading-position recovery and a continue-loading action in legacy roundtable conversations (medium, pre-existing recovery-surface gap).
  evidence: B9: the task-start `MobileRoundtableViews.swift` renders `roundtableReadingNotice` and “继续找回阅读位置” for `MobileGroupChatView`, but not for legacy `MobileRoundtableView`. State tests already cover bounded repaging and a second restoration batch. Add the legacy presentation with an actual history-pagination UI regression; preserve existing anchors and do not treat an unloaded earlier page as lost history.
