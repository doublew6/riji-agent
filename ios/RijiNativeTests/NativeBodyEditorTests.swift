import XCTest
import SwiftUI
import UIKit
import RijiCore

@MainActor
private final class BodyEditorHarness: ObservableObject {
    @Published var text: String
    @Published var status = 0
    @Published var day = try! JournalDay("2026-09-14")
    var callbacks: [(JournalDay, String)] = []
    var echoesChanges = true
    @Published var protectedFeedback = false
    @Published var showEditor = true
    @Published var editable = true
    @Published var replacementRevision = 0
    let viewports = DiaryViewports()
    var navigations: [(JournalDay, DiaryDateDirection)] = []
    init(_ text: String) { self.text = text }
}

private struct HostedBodyEditor: View {
    @ObservedObject var state: BodyEditorHarness
    var body: some View {
        VStack {
            if state.showEditor {
            DiaryBodyEditor(text: state.text, day: state.day, editable: state.editable, foldsSavedAIResults: true, onProtectedEdit: { state.protectedFeedback = true },
                            viewports: state.viewports, onDateNavigation: { state.navigations.append(($0, $1)) },
                            replacementRevision: state.replacementRevision) { day, text in
                state.callbacks.append((day, text))
                if day == state.day && state.echoesChanges { state.text = text }
            }
            }
            Text("Save state \(state.status)")
            if state.protectedFeedback { Text(DiaryBodyEditor.protectedEditExplanation) }
        }
    }
}

private struct HostedDiffFragment: View {
    @ObservedObject var state: BodyEditorHarness
    var body: some View {
        DiffFragmentEditor(text: state.text, disabled: !state.editable, identifier: "fragment", revision: state.replacementRevision) {
            state.callbacks.append((state.day, $0))
            if state.echoesChanges { state.text = $0 }
        }.frame(height: 140)
        Text("Save state \(state.status)")
    }
}

private final class CompletedDatePan: UIPanGestureRecognizer {
    var movement = CGPoint.zero
    override var state: UIGestureRecognizer.State { get { .ended } set {} }
    override func translation(in view: UIView?) -> CGPoint { movement }
}

private final class NativeBodySaveFailure: @unchecked Sendable {
    private let lock = NSLock()
    private var failing = false
    func set(_ value: Bool) { lock.lock(); failing = value; lock.unlock() }
    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        lock.lock(); defer { lock.unlock() }
        if failing && purpose == .bodyDiary && stage == .beforeReplace { throw JournalError.storage }
    }
}

private struct HostedSavingBodyEditor: View {
    @ObservedObject var model: DiaryViewModel
    let capture: (JournalDay, String) -> Void
    var body: some View {
        DiaryBodyEditor(text: model.markdown, day: model.selectedDay, editable: model.canEditBody,
                        foldsSavedAIResults: true,
                        replacementRevision: model.bodyReplacementRevision, onEditorAvailable: { view in
            model.bodyResumeAdmission = { [weak view] editable in
                // Re-enable only when updateUIView also installs the current source and callback.
                        if !editable { view?.isEditable = false; view?.updateTableAdmission() }
            }
            model.bodyRefreshAdmission = { [weak view] in
                guard let view else { return false }
                return !view.isFirstResponder && !view.tableIsEditing && view.markedTextRange == nil && !view.tableHasMarkedText
            }
        }) { [origin = model.bodyInputOrigin] day, text in
            capture(day, text)
            model.updateBody(text, for: day, origin: origin)
        }
    }
}

@MainActor private final class AppearedDiaryHost: UIHostingController<TodayDiaryView> {
    private(set) var hasAppeared = false
    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        hasAppeared = true
    }
}

@MainActor
final class NativeBodyEditorTests: XCTestCase {
    func testFragmentTypingIgnoresOlderEchoAndPreservesSelectionUntilExplicitReplacement() async throws {
        let source = "- [ ] Plan today\r\n", state = BodyEditorHarness("- [ ] Plan today\r\n")
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        let host = UIHostingController(rootView: HostedDiffFragment(state: state))
        window.rootViewController = host; window.makeKeyAndVisible(); defer { window.isHidden = true }
        host.view.layoutIfNeeded()
        try await waitUntil { self.findEditor(host.view) != nil }
        let editor = try XCTUnwrap(findEditor(host.view)); editor.becomeFirstResponder()
        editor.selectedRange = NSRange(location: 6, length: 0)
        let insertion = "Manual fragment 🧠 e\u{301} "
        for character in insertion {
            editor.insertText(String(character)); state.status += 1
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        let expected = source.replacingOccurrences(of: "Plan", with: insertion + "Plan")
        XCTAssertEqual(Data(editor.text.utf8), Data(expected.utf8))
        XCTAssertEqual(state.callbacks.last?.1, expected)
        let selection = editor.selectedRange
        state.echoesChanges = false; state.text = source; state.status += 1
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(Data(editor.text.utf8), Data(expected.utf8)); XCTAssertEqual(editor.selectedRange, selection)
        editor.setMarkedText("中文", selectedRange: NSRange(location: 2, length: 0)); state.status += 1
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertNotNil(editor.markedTextRange); editor.unmarkText()
        let callbacks = state.callbacks.count
        state.text = "- [x] Other choice\r\n"; state.replacementRevision += 1
        try await waitUntil { editor.text == state.text }
        XCTAssertEqual(Data(editor.text.utf8), Data(state.text.utf8)); XCTAssertEqual(state.callbacks.count, callbacks)
    }

    func testDifferenceFragmentRendersReadableTableWithoutChangingSourceOrEnablingEditing() async throws {
        let source = "## Workout\r\n| Exercise | Kg |\r\n| --- | --- |\r\n| **Bench** | 25 |\r\n"
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        let host = UIHostingController(rootView: ScrollView { DiffMarkdownReader(text: source).padding(20) })
        window.rootViewController = host; window.makeKeyAndVisible(); defer { window.isHidden = true }
        host.view.layoutIfNeeded()
        for _ in 0..<20 { try await Task.sleep(nanoseconds: 10_000_000); host.view.layoutIfNeeded() }
        let view = try XCTUnwrap(findEditor(host.view) as? MarkdownTextView)
        let grid = try XCTUnwrap(view.subviews.compactMap { $0 as? MarkdownTableView }.first)
        XCTAssertEqual(Data(view.text.utf8), Data(source.utf8))
        XCTAssertFalse(view.isEditable); XCTAssertFalse(view.isScrollEnabled)
        XCTAssertGreaterThan(grid.bounds.height, 40); XCTAssertGreaterThan(view.bounds.height, grid.bounds.height)
        grid.beginEditing(row: 1, column: 0); XCTAssertNil(grid.cellEditor)
        XCTAssertEqual(Data(view.text.utf8), Data(source.utf8))
    }

    private func editableEditors(_ view: UIView, matching bytes: Data) -> [UITextView] {
        let own = (view as? UITextView).map { editor in
            editor.isEditable && Data((editor.text ?? "").utf8) == bytes ? [editor] : []
        } ?? []
        return own + view.subviews.flatMap { editableEditors($0, matching: bytes) }
    }

    private func findEditor(_ view: UIView) -> UITextView? {
        if let value = view as? UITextView { return value }
        for child in view.subviews { if let value = findEditor(child) { return value } }
        return nil
    }

    private func mount(_ state: BodyEditorHarness) async throws -> (UIWindow, UITextView) {
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        let host = UIHostingController(rootView: HostedBodyEditor(state: state))
        window.rootViewController = host; window.makeKeyAndVisible(); host.view.layoutIfNeeded()
        for _ in 0..<30 {
            if let view = findEditor(host.view) { return (window, view) }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        throw JournalError.storage
    }

    private func waitUntil(_ context: String = "Native editor did not reach the expected document/viewport state", onTimeout: () -> Void = {}, condition: () -> Bool) async throws {
        let deadline = ProcessInfo.processInfo.systemUptime + 3
        while ProcessInfo.processInfo.systemUptime < deadline {
            if condition() { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        onTimeout()
        XCTFail(context)
        throw JournalError.storage
    }

    private func waitForDocument(_ editor: UITextView, text: String, viewport: DiaryViewport? = nil) async throws {
        try await waitUntil {
            guard editor.bounds.width > 0, editor.bounds.height > 0,
                  (editor as? MarkdownTextView)?.hasPendingViewport == false,
                  Data(editor.text.utf8) == Data(text.utf8) else { return false }
            guard let viewport else { return true }
            return editor.selectedRange == viewport.selection && abs(editor.contentOffset.y - viewport.offset.y) <= 1
        }
    }

    private func isHidden(_ index: Int, in editor: UITextView) -> Bool {
        editor.layoutManager.ensureLayout(for: editor.textContainer)
        let glyph = editor.layoutManager.glyphIndexForCharacter(at: index)
        return editor.layoutManager.propertyForGlyph(at: glyph).contains(.null)
    }

    private func savedAIReadingFixture() throws -> String {
        let metadata = """
        {"result_id":"result-synthetic","problem_id":"problem-synthetic","discussion_id":"discussion-synthetic","artifact_ids":["artifact-synthetic"],"input_revision":1,"summary_version":1,"correction_version":0,"recorded_at":"2020-01-02T03:04:05Z","mentors":["gentle_reviewer","wang_yangming"],"topic":"A synthetic saved discussion","saved_date":"2026-09-14","edited_by_user":true}
        """
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let provenance = try decoder.decode(DiscussionProvenance.self, from: Data(metadata.utf8))
        return try AIResultMarkdown.render("Keep the readable advice 🧠 e\u{301}\n> 讨论：this is advice, too", provenance: provenance)
    }

    func testSavedAIReadingCompactsMachineLinesAndPreservesSourceAccessibilitySelectionAndProtection() async throws {
        let block = try savedAIReadingFixture(), source = "# Synthetic diary\r\nBefore\r\n" + block.replacingOccurrences(of: "\n", with: "\r\n") + "\r\nAfter\r\n"
        let state = BodyEditorHarness(source)
        let (window, base) = try await mount(state); defer { window.isHidden = true }
        let editor = try XCTUnwrap(base as? MarkdownTextView)
        try await waitForDocument(editor, text: source)
        XCTAssertEqual(Data(editor.textStorage.string.utf8), Data(source.utf8))
        XCTAssertTrue(state.callbacks.isEmpty)
        XCTAssertTrue(editor.accessibilityValue?.contains("Keep the readable advice") == true)
        XCTAssertTrue(editor.accessibilityValue?.contains("AI 导师讨论结果") == true)
        XCTAssertFalse(editor.accessibilityValue?.contains("discussion-synthetic") == true)
        XCTAssertFalse(editor.accessibilityValue?.contains("gentle_reviewer") == true)
        XCTAssertTrue(editor.accessibilityValue?.contains("> 讨论：this is advice, too") == true)
        let record = try XCTUnwrap(editor.presentation.savedAIResults.first)
        for range in record.concealedRanges {
            XCTAssertTrue(isHidden(range.location, in: editor))
            let font = try XCTUnwrap(editor.textStorage.attribute(.font, at: range.location, effectiveRange: nil) as? UIFont)
            XCTAssertLessThan(font.pointSize, 1)
            let style = try XCTUnwrap(editor.textStorage.attribute(.paragraphStyle, at: range.location, effectiveRange: nil) as? NSParagraphStyle)
            XCTAssertLessThan(style.maximumLineHeight, 1)
        }
        let literal = MarkdownTextView()
        literal.bounds = editor.bounds; literal.textContainerInset = editor.textContainerInset
        literal.textContainer.lineFragmentPadding = 0; literal.text = source
        literal.applyPresentation(active: nil)
        literal.layoutIfNeeded()
        literal.layoutManager.ensureLayout(for: literal.textContainer)
        editor.layoutManager.ensureLayout(for: editor.textContainer)
        XCTAssertLessThan(editor.layoutManager.usedRect(for: editor.textContainer).height,
                          literal.layoutManager.usedRect(for: literal.textContainer).height - 100)
        editor.becomeFirstResponder()
        let metadataSelection = NSRange(location: record.range.location + 5, length: 8)
        editor.selectedRange = metadataSelection; state.status += 1
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(editor.selectedRange, metadataSelection)
        XCTAssertTrue(isHidden(record.range.location, in: editor))
        XCTAssertEqual(editor.delegate?.textView?(editor, shouldChangeTextIn: metadataSelection, replacementText: "changed"), false)
        XCTAssertEqual(Data(editor.textStorage.string.utf8), Data(source.utf8))
        editor.resignFirstResponder()
        XCTAssertTrue(state.callbacks.isEmpty, "Reading or selecting saved metadata must not report a body edit")
    }

    func testPlainReaderAccessibilityKeepsLiteralSourceAndRejectsStaleMarkdownProjection() throws {
        let source = "# Synthetic plain text\n" + (try savedAIReadingFixture()) + "\nTail\n"
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        let host = UIViewController(), editor = MarkdownTextView()
        window.rootViewController = host; window.makeKeyAndVisible(); defer { window.isHidden = true }
        editor.frame = host.view.bounds; host.view.addSubview(editor)
        editor.isEditable = false; editor.text = source
        // VaultTextReader(markdown: false) intentionally keeps an empty presentation.
        editor.applyPresentation(active: nil)
        XCTAssertEqual(editor.accessibilityValue, source)
        XCTAssertEqual(Data(editor.textStorage.string.utf8), Data(source.utf8))
        let marker = (source as NSString).range(of: "<!-- riji:ai-discussion-result").location
        XCTAssertFalse(isHidden(marker, in: editor))
        editor.presentation = MarkdownPresentation(source); editor.applyPresentation(active: nil)
        XCTAssertEqual(editor.accessibilityValue, source)
        XCTAssertFalse(isHidden(marker, in: editor), "Generic Markdown readers must keep saved sources visible")
        editor.foldsSavedAIResults = true; editor.applyPresentation(active: nil)
        XCTAssertFalse(editor.accessibilityValue?.contains("discussion-synthetic") == true)
        XCTAssertTrue(isHidden(marker, in: editor))
        // A new source must not be truncated by ranges calculated for the old source.
        let changed = "New prefix 🧠 e\u{301}\n" + source
        editor.text = changed
        XCTAssertEqual(editor.accessibilityValue, changed)
        XCTAssertEqual(Data(editor.textStorage.string.utf8), Data(changed.utf8))
        editor.presentation = MarkdownPresentation(""); editor.applyPresentation(active: nil)
        XCTAssertEqual(editor.accessibilityValue, changed)
        XCTAssertFalse(isHidden((changed as NSString).range(of: "<!-- riji:ai-discussion-result").location, in: editor))
        editor.presentation = MarkdownPresentation(changed); editor.applyPresentation(active: nil)
        editor.foldsSavedAIResults = false; editor.applyPresentation(active: nil)
        XCTAssertEqual(editor.accessibilityValue, changed)
        XCTAssertFalse(isHidden((changed as NSString).range(of: "<!-- riji:ai-discussion-result").location, in: editor))
    }

    func testSavedAIReadingSurvivesBodyUndoCompositionAndInlineTableEdits() async throws {
        let block = try savedAIReadingFixture()
        let source = "# Synthetic\n| A | B |\n| --- | --- |\n| old | keep |\n" + block + "\nOrdinary body\n"
        let state = BodyEditorHarness(source)
        let (window, base) = try await mount(state); defer { window.isHidden = true }
        let editor = try XCTUnwrap(base as? MarkdownTextView)
        editor.becomeFirstResponder(); editor.selectedRange = NSRange(location: source.utf16.count, length: 0)
        editor.insertText("Manual edit")
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertTrue(state.text.hasSuffix("Manual edit"))
        let selection = editor.selectedRange, offset = editor.contentOffset
        state.status += 1
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertEqual(editor.selectedRange, selection); XCTAssertEqual(editor.contentOffset.y, offset.y, accuracy: 1)
        editor.undoSource()
        XCTAssertEqual(Data(editor.text.utf8), Data(source.utf8))
        XCTAssertEqual(Data(state.text.utf8), Data(source.utf8))
        editor.selectedRange = (editor.text as NSString).range(of: "Ordinary body")
        editor.setMarkedText("中文组合", selectedRange: NSRange(location: 4, length: 0))
        let marked = editor.text, markerLocation = (editor.text as NSString).range(of: "<!-- riji:ai-discussion-result").location
        let oldAttributes = editor.textStorage.attributes(at: markerLocation, effectiveRange: nil)
        state.status += 1
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertNotNil(editor.markedTextRange); XCTAssertEqual(editor.text, marked)
        XCTAssertEqual((editor.textStorage.attribute(.font, at: markerLocation, effectiveRange: nil) as? UIFont), oldAttributes[.font] as? UIFont)
        editor.unmarkText(); editor.delegate?.textViewDidChange?(editor); editor.resignFirstResponder()
        XCTAssertTrue(state.text.contains("中文组合")); XCTAssertTrue(isHidden(markerLocation, in: editor))
        let grid = try XCTUnwrap(editor.subviews.compactMap { $0 as? MarkdownTableView }.first)
        grid.beginEditing(row: 1, column: 0)
        let cell = try XCTUnwrap(grid.cellEditor); cell.selectedRange = NSRange(location: 0, length: cell.text.utf16.count)
        cell.insertText("new"); grid.finishEditing()
        XCTAssertTrue(editor.text.contains("| new | keep |"))
        XCTAssertTrue(editor.text.contains(block)); XCTAssertEqual(editor.presentation.savedAIResults.count, 1)
        let movedMarker = (editor.text as NSString).range(of: "<!-- riji:ai-discussion-result").location
        XCTAssertTrue(isHidden(movedMarker, in: editor))
        editor.undoSource()
        XCTAssertTrue(editor.text.contains("| old | keep |")); XCTAssertTrue(editor.text.contains(block))
        XCTAssertEqual(Data(editor.textStorage.string.utf8), Data(state.text.utf8))
    }

    func testLivePreviewHidesSyntaxAndRendersFontsWithoutChangingSourceOrEmittingEdits() async throws {
        let raw = "# Synthetic diary\n### **Morning**\n- [ ] One important thing\n- [x] Completed\n- List item\n> A thought\n**Bold** and *italic*, ~~old~~ and `code`\n[Link](https://example.com)\nPlain 中文 e\u{301} 🧠\n"
        let state = BodyEditorHarness(raw)
        let (window, base) = try await mount(state); defer { window.isHidden = true }
        let editor = try XCTUnwrap(base as? MarkdownTextView)
        XCTAssertTrue(isHidden(0, in: editor))
        let strong = (raw as NSString).range(of: "**Morning**")
        XCTAssertTrue(isHidden(strong.location, in: editor))
        XCTAssertFalse(isHidden(strong.location + 2, in: editor))
        let font = try XCTUnwrap(editor.textStorage.attribute(.font, at: strong.location + 2, effectiveRange: nil) as? UIFont)
        XCTAssertTrue(font.fontDescriptor.symbolicTraits.contains(.traitBold))
        let italic = (raw as NSString).range(of: "italic")
        let italicFont = try XCTUnwrap(editor.textStorage.attribute(.font, at: italic.location, effectiveRange: nil) as? UIFont)
        XCTAssertTrue(italicFont.fontDescriptor.symbolicTraits.contains(.traitItalic))
        let task = try XCTUnwrap(editor.presentation.markers.first)
        XCTAssertTrue(editor.layoutManager.propertyForGlyph(at: editor.layoutManager.glyphIndexForCharacter(at: task.range.location)).contains(.controlCharacter))
        XCTAssertTrue(isHidden(task.taskState!, in: editor))
        XCTAssertEqual(Data(editor.text.utf8), Data(raw.utf8)); XCTAssertTrue(state.callbacks.isEmpty)
    }

    func testDocumentRoundTripRestoresCaretOffsetBytesAndClearsCrossDayUndo() async throws {
        let raw = (0..<150).map { "Line \($0) 中文 e\u{301} 🧠\r\n" }.joined()
        let state = BodyEditorHarness(raw), originalDay = state.day
        let (window, editor) = try await mount(state); defer { window.isHidden = true }
        try await waitForDocument(editor, text: raw)
        editor.becomeFirstResponder(); editor.selectedRange = NSRange(location: raw.utf16.count, length: 0)
        editor.insertText("Retained edit\r\n"); editor.resignFirstResponder()
        let saved = state.text
        editor.selectedRange = NSRange(location: 500, length: 0)
        editor.setContentOffset(CGPoint(x: 0, y: 700), animated: false)
        let position = editor.contentOffset
        XCTAssertTrue(editor.undoManager?.canUndo == true)
        state.day = try JournalDay("2026-09-13"); state.text = "# New day\n"
        try await waitForDocument(editor, text: state.text, viewport: DiaryViewport())
        XCTAssertEqual(editor.selectedRange, NSRange(location: 0, length: 0))
        XCTAssertEqual(editor.contentOffset.y, 0, accuracy: 1)
        XCTAssertFalse(editor.undoManager?.canUndo == true)
        state.day = originalDay; state.text = saved
        try await waitForDocument(editor, text: saved, viewport: DiaryViewport(selection: NSRange(location: 500, length: 0), offset: position))
        XCTAssertEqual(editor.selectedRange, NSRange(location: 500, length: 0))
        XCTAssertEqual(editor.contentOffset.y, position.y, accuracy: 1)
        XCTAssertEqual(Data(editor.text.utf8), Data(saved.utf8))
        XCTAssertFalse(editor.undoManager?.canUndo == true)
        state.day = try JournalDay("2026-09-13"); state.text = "# New day\n"
        try await waitForDocument(editor, text: state.text, viewport: DiaryViewport())
        state.day = originalDay; state.text = "# Shortened externally\n"
        try await waitForDocument(editor, text: state.text,
            viewport: DiaryViewport(selection: NSRange(location: state.text.utf16.count, length: 0)))
        XCTAssertEqual(editor.selectedRange, NSRange(location: state.text.utf16.count, length: 0))
        XCTAssertEqual(editor.contentOffset.y, 0, accuracy: 1)
    }

    func testUnmountedEditorRestoresSessionViewportWithoutAnyTextCallback() async throws {
        let raw = (0..<120).map { "Paragraph \($0)\n" }.joined()
        let state = BodyEditorHarness(raw)
        let (window, editor) = try await mount(state); defer { window.isHidden = true }
        try await waitForDocument(editor, text: raw)
        editor.selectedRange = NSRange(location: 260, length: 0)
        editor.setContentOffset(CGPoint(x: 0, y: 850), animated: false)
        let offset = editor.contentOffset
        state.showEditor = false
        try await waitUntil { self.findEditor(window.rootViewController!.view) == nil }
        state.showEditor = true
        try await waitUntil { self.findEditor(window.rootViewController!.view).map { $0 !== editor } == true }
        let restored = try XCTUnwrap(findEditor(window.rootViewController!.view))
        try await waitForDocument(restored, text: raw, viewport: DiaryViewport(selection: NSRange(location: 260, length: 0), offset: offset))
        XCTAssertFalse(restored === editor)
        XCTAssertEqual(restored.selectedRange, NSRange(location: 260, length: 0))
        XCTAssertEqual(restored.contentOffset.y, offset.y, accuracy: 1)
        XCTAssertEqual(Data(restored.text.utf8), Data(raw.utf8))
        XCTAssertTrue(state.callbacks.isEmpty)
    }

    func testNormalBodyDestinationSaveCommitsCompositionOnceAndRetainsFailedInputForOriginalDay() async throws {
        for failing in [false, true] {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent("BodyDestinationComposition-\(UUID())")
            defer { try? FileManager.default.removeItem(at: root) }
            let failure = NativeBodySaveFailure()
            let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"),
                stateDirectory: root.appendingPathComponent("State"), timeZone: TimeZone(secondsFromGMT: 0)!,
                failure: { try failure.check($0, $1) })
            let date = ISO8601DateFormatter().date(from: "2026-09-14T08:00:00Z")!
            let model = DiaryViewModel(now: { date }, storageFactory: { store })
            model.bodyQuietNanoseconds = 3_600_000_000_000
            model.bodyCheckpointNanoseconds = 3_600_000_000_000
            await model.start()
            let originalDay = model.selectedDay
            let raw = "# Synthetic\r\n## Notes\r\nReplace\r\n"
            model.updateBody(raw)
            let baseSaved = await model.flushBody(); XCTAssertTrue(baseSaved)
            var callbacks: [(JournalDay, String)] = []
            let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
            let host = UIHostingController(rootView: HostedSavingBodyEditor(model: model) { callbacks.append(($0, $1)) })
            window.rootViewController = host; window.makeKeyAndVisible(); host.view.layoutIfNeeded()
            defer { window.isHidden = true }
            try await waitUntil { self.findEditor(host.view) != nil }
            let editor = try XCTUnwrap(findEditor(host.view))
            try await waitForDocument(editor, text: raw)
            failure.set(failing)
            editor.becomeFirstResponder(); editor.selectedRange = (raw as NSString).range(of: "Replace")
            let composition = "中文组合 e\u{301} 🧠"
            editor.setMarkedText(composition, selectedRange: NSRange(location: composition.utf16.count, length: 0))
            XCTAssertNotNil(editor.markedTextRange)
            let expected = raw.replacingOccurrences(of: "Replace", with: composition)
            XCTAssertEqual(Data(editor.text.utf8), Data(expected.utf8))
            TodayDiaryView.commitEditing(in: host.view)
            XCTAssertNil(editor.markedTextRange)
            XCTAssertEqual(Data(model.markdown.utf8), Data(expected.utf8))
            let prepared = await model.prepareMainDestination()
            XCTAssertEqual(prepared, !failing)
            XCTAssertEqual(model.selectedDay, originalDay)
            XCTAssertEqual(Data(editor.text.utf8), Data(expected.utf8))
            if failing {
                XCTAssertTrue(model.bodyDirty); XCTAssertNotNil(model.bodyError)
                let retained = try await store.retainedBodyEdit(originalDay)
                XCTAssertEqual(retained?.content, Data(expected.utf8))
                let source = try await store.exportMarkdown(originalDay)
                XCTAssertEqual(source, Data(raw.utf8))
                failure.set(false)
                TodayDiaryView.commitEditing(in: host.view)
                let retried = await model.prepareMainDestination(); XCTAssertTrue(retried)
            }
            let repeated = await model.prepareMainDestination(); XCTAssertTrue(repeated)
            let saved = try await store.exportMarkdown(originalDay)
            XCTAssertEqual(saved, Data(expected.utf8))
            let revisions = try await store.bodyRevisions(originalDay)
            XCTAssertEqual(revisions.filter { $0.content == Data(expected.utf8) }.count, 1)
            XCTAssertFalse(model.bodyDirty); XCTAssertNil(model.bodyError)
            let nextDay = try JournalDay("2026-09-13")
            let navigated = await model.navigate(to: nextDay); XCTAssertTrue(navigated)
            try await waitForDocument(editor, text: model.markdown)
            let compositionCallbacks = callbacks.filter { $0.1.contains(composition) }
            XCTAssertFalse(compositionCallbacks.isEmpty)
            XCTAssertTrue(compositionCallbacks.allSatisfy { $0.0 == originalDay })
            XCTAssertFalse(model.markdown.contains(composition))
            let nextSource = try await store.open(nextDay)
            XCTAssertFalse(nextSource.markdown.contains(composition))
        }
    }

    func testInlineTableDestinationSaveFailureRetainsCompositionAndRecoversOriginalDay() async throws {
        for failing in [false, true] {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent("TableDestinationComposition-\(UUID())")
            defer { try? FileManager.default.removeItem(at: root) }
            let failure = NativeBodySaveFailure()
            let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"),
                stateDirectory: root.appendingPathComponent("State"), timeZone: TimeZone(secondsFromGMT: 0)!,
                failure: { try failure.check($0, $1) })
            let date = ISO8601DateFormatter().date(from: "2026-09-14T08:00:00Z")!
            let model = DiaryViewModel(now: { date }, storageFactory: { store })
            model.bodyQuietNanoseconds = 3_600_000_000_000
            model.bodyCheckpointNanoseconds = 3_600_000_000_000
            await model.start()
            let originalDay = model.selectedDay
            let raw = "# Synthetic\r\n| A | B |\r\n| --- | --- |\r\n| Replace | keep |\r\nAfter\r\n"
            model.updateBody(raw)
            let baseSaved = await model.flushBody(); XCTAssertTrue(baseSaved)
            var callbacks: [(JournalDay, String)] = []
            let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
            let host = UIHostingController(rootView: HostedSavingBodyEditor(model: model) { callbacks.append(($0, $1)) })
            window.rootViewController = host; window.makeKeyAndVisible(); host.view.layoutIfNeeded()
            defer { window.isHidden = true }
            try await waitUntil { self.findEditor(host.view) != nil }
            let editor = try XCTUnwrap(findEditor(host.view))
            try await waitForDocument(editor, text: raw)
            failure.set(failing)
            let grid = try XCTUnwrap(editor.subviews.compactMap { $0 as? MarkdownTableView }.first)
            grid.beginEditing(row: 1, column: 0)
            let cell = try XCTUnwrap(grid.cellEditor)
            cell.selectedRange = NSRange(location: 0, length: cell.text.utf16.count)
            let composition = "中文组合 e\u{301} 🧠"
            cell.setMarkedText(composition, selectedRange: NSRange(location: composition.utf16.count, length: 0))
            XCTAssertNotNil(cell.markedTextRange)
            let expected = raw.replacingOccurrences(of: "Replace", with: composition)
            XCTAssertEqual(Data(editor.text.utf8), Data(expected.utf8))
            TodayDiaryView.commitEditing(in: host.view)
            XCTAssertNil(cell.markedTextRange)
            XCTAssertEqual(Data(model.markdown.utf8), Data(expected.utf8))
            let prepared = await model.prepareMainDestination()
            XCTAssertEqual(prepared, !failing)
            XCTAssertEqual(model.selectedDay, originalDay)
            XCTAssertEqual(Data(editor.text.utf8), Data(expected.utf8))
            if failing {
                XCTAssertTrue(model.bodyDirty); XCTAssertNotNil(model.bodyError)
                let retained = try await store.retainedBodyEdit(originalDay)
                XCTAssertEqual(retained?.content, Data(expected.utf8))
                let source = try await store.exportMarkdown(originalDay)
                XCTAssertEqual(source, Data(raw.utf8))
                let reopened = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), timeZone: TimeZone(secondsFromGMT: 0)!)
                let recovered = try await reopened.retainedBodyEdit(originalDay)
                XCTAssertEqual(recovered?.content, Data(expected.utf8))
                failure.set(false)
                TodayDiaryView.commitEditing(in: host.view)
                let retried = await model.prepareMainDestination(); XCTAssertTrue(retried)
            }
            let repeated = await model.prepareMainDestination(); XCTAssertTrue(repeated)
            let saved = try await store.exportMarkdown(originalDay)
            XCTAssertEqual(saved, Data(expected.utf8))
            let revisions = try await store.bodyRevisions(originalDay)
            XCTAssertEqual(revisions.filter { $0.content == Data(expected.utf8) }.count, 1)
            XCTAssertFalse(model.bodyDirty); XCTAssertNil(model.bodyError)
            let nextDay = try JournalDay("2026-09-13")
            let navigated = await model.navigate(to: nextDay); XCTAssertTrue(navigated)
            try await waitForDocument(editor, text: model.markdown)
            let compositionCallbacks = callbacks.filter { $0.1.contains(composition) }
            XCTAssertFalse(compositionCallbacks.isEmpty)
            XCTAssertTrue(compositionCallbacks.allSatisfy { $0.0 == originalDay })
            XCTAssertFalse(model.markdown.contains(composition))
            let nextSource = try await store.open(nextDay)
            XCTAssertFalse(nextSource.markdown.contains(composition))
        }
    }

    func testProductionGestureAdmissionReservesEditingSelectionAndTableRows() async throws {
        let raw = "# Diary\nOrdinary text\n| Column | Value |\n| --- | --- |\nText below\n"
        let state = BodyEditorHarness(raw)
        let (window, base) = try await mount(state); defer { window.isHidden = true }
        let editor = try XCTUnwrap(base as? MarkdownTextView)
        try await waitForDocument(editor, text: raw)
        func point(_ text: String) -> CGPoint {
            let range = (raw as NSString).range(of: text)
            editor.layoutManager.ensureLayout(for: editor.textContainer)
            let glyphs = editor.layoutManager.glyphRange(forCharacterRange: range, actualCharacterRange: nil)
            let rect = editor.layoutManager.boundingRect(forGlyphRange: glyphs, in: editor.textContainer)
            return CGPoint(x: rect.midX + editor.textContainerInset.left, y: rect.midY + editor.textContainerInset.top)
        }
        let ordinary = point("Ordinary text")
        XCTAssertTrue(editor.admitsDateGesture(at: ordinary))
        // UIKit may ask to begin at only a few points; the 80-point completion
        // threshold, not an additional begin threshold, admits a deliberate swipe.
        for sign: CGFloat in [-1, 1] {
            XCTAssertTrue(editor.admitsInitialDatePan(CGPoint(x: sign * 3.3, y: 0), at: ordinary))
            XCTAssertFalse(editor.admitsInitialDatePan(CGPoint(x: sign * 3.3, y: 2), at: ordinary))
            XCTAssertFalse(editor.admitsInitialDatePan(CGPoint(x: sign * 160, y: 0), at: point("| Column | Value |")))
        }
        XCTAssertFalse(editor.admitsInitialDatePan(CGPoint(x: 0, y: 3.3), at: ordinary))
        XCTAssertFalse(editor.admitsDateGesture(at: point("| --- | --- |")))
        let gesture = CompletedDatePan()
        for (movement, expected) in [(CGPoint(x: 80, y: 24), DiaryDateDirection.previous), (CGPoint(x: 80, y: -24), .previous),
                                     (CGPoint(x: -80, y: 24), .next), (CGPoint(x: -80, y: -24), .next)] {
            XCTAssertEqual(MarkdownTextView.dateDirection(for: movement), expected)
            gesture.movement = movement; editor.handleDatePan(gesture)
            XCTAssertEqual(state.navigations.last?.0, state.day)
            XCTAssertEqual(state.navigations.last?.1, expected)
        }
        XCTAssertEqual(state.navigations.count, 4)
        state.navigations.removeAll()
        for movement in [CGPoint(x: 0, y: 150), CGPoint(x: -79, y: 0), CGPoint(x: 79, y: 0),
                         CGPoint(x: 80, y: 24.1), CGPoint(x: 80, y: -24.1),
                         CGPoint(x: -80, y: 24.1), CGPoint(x: -80, y: -24.1),
                         CGPoint(x: -150, y: 70), CGPoint(x: 150, y: -70)] {
            XCTAssertNil(MarkdownTextView.dateDirection(for: movement))
            gesture.movement = movement; editor.handleDatePan(gesture)
        }
        assertEditingRejectsDateCompletion(editor, at: ordinary, gesture: gesture)
        XCTAssertTrue(state.navigations.isEmpty)
    }

    private func assertEditingRejectsDateCompletion(_ editor: MarkdownTextView, at point: CGPoint, gesture: CompletedDatePan) {
        for sign: CGFloat in [-1, 1] {
            gesture.movement = CGPoint(x: sign * 160, y: 0)
            editor.dateNavigationEnabled = false; editor.handleDatePan(gesture)
            editor.dateNavigationEnabled = true
            editor.selectedRange = NSRange(location: 8, length: 5)
            XCTAssertFalse(editor.admitsInitialDatePan(gesture.movement, at: point))
            editor.handleDatePan(gesture)
            editor.selectedRange = NSRange(location: 8, length: 0); editor.becomeFirstResponder()
            XCTAssertFalse(editor.admitsInitialDatePan(gesture.movement, at: point))
            editor.handleDatePan(gesture)
            editor.setMarkedText("中文", selectedRange: NSRange(location: 2, length: 0))
            XCTAssertFalse(editor.admitsInitialDatePan(gesture.movement, at: point))
            editor.handleDatePan(gesture)
            editor.unmarkText(); editor.resignFirstResponder()
        }
    }

    func testRuleAndEmptyBulletProjectionCanBeSelectedEditedAndUndone() async throws {
        let raw = "# Heading\n\n---\n-\n"
        let state = BodyEditorHarness(raw)
        let (window, base) = try await mount(state); defer { window.isHidden = true }
        let editor = try XCTUnwrap(base as? MarkdownTextView)
        XCTAssertEqual(editor.presentation.markers.map(\.kind), [.rule, .bullet])
        let rule = (raw as NSString).range(of: "---")
        XCTAssertTrue(isHidden(rule.location + 1, in: editor))
        editor.becomeFirstResponder(); editor.selectedRange = rule
        XCTAssertFalse(isHidden(rule.location + 1, in: editor))
        editor.insertText("***"); editor.resignFirstResponder()
        XCTAssertEqual(state.text, raw.replacingOccurrences(of: "---", with: "***"))
        XCTAssertEqual(editor.presentation.markers.first?.kind, .rule)
        editor.undoManager?.undo(); XCTAssertEqual(Data(state.text.utf8), Data(raw.utf8))
    }

    func testSelectAllReformatsAfterTextStorageEditingEndsAndPreservesUndo() async throws {
        let raw = "# Heading\n**Bold**\n- [ ] Task\n"
        let state = BodyEditorHarness(raw)
        let (window, editor) = try await mount(state); defer { window.isHidden = true }
        editor.becomeFirstResponder(); editor.selectAll(nil)
        editor.layoutManager.ensureLayout(for: editor.textContainer)
        XCTAssertEqual(editor.selectedRange, NSRange(location: 0, length: raw.utf16.count))
        XCTAssertFalse(isHidden(0, in: editor))
        editor.insertText("# Replacement\n**New**\n")
        XCTAssertEqual(state.text, "# Replacement\n**New**\n")
        editor.undoManager?.undo(); XCTAssertEqual(Data(state.text.utf8), Data(raw.utf8))
    }

    func testActiveLineRevealsAndCollapsesOnSelectionAndKeyboardDismissal() async throws {
        let raw = "# Heading\r\n**First**\r\n**Second**\r\n"
        let state = BodyEditorHarness(raw)
        let (window, editor) = try await mount(state); defer { window.isHidden = true }
        let first = (raw as NSString).range(of: "**First**"), second = (raw as NSString).range(of: "**Second**")
        editor.becomeFirstResponder(); editor.selectedRange = NSRange(location: first.location + 2, length: 0)
        XCTAssertFalse(isHidden(first.location, in: editor)); XCTAssertTrue(isHidden(second.location, in: editor))
        editor.selectedRange = NSRange(location: second.location + 2, length: 0)
        XCTAssertTrue(isHidden(first.location, in: editor)); XCTAssertFalse(isHidden(second.location, in: editor))
        editor.resignFirstResponder()
        XCTAssertTrue(isHidden(first.location, in: editor)); XCTAssertTrue(isHidden(second.location, in: editor))
        XCTAssertEqual(Data(editor.text.utf8), Data(raw.utf8))
    }

    func testTaskToggleUsesOneCharacterEditAndNativeUndoWithUnicodeAndCRLF() async throws {
        let raw = "---\r\nunknown: keep\r\n---\r\n# 日记 🧠\r\n- [ ] 中文 e\u{301}\r\n<!-- riji-record:mit:synthetic -->\r\n"
        let state = BodyEditorHarness(raw)
        let (window, base) = try await mount(state); defer { window.isHidden = true }
        let editor = try XCTUnwrap(base as? MarkdownTextView)
        let marker = try XCTUnwrap(editor.presentation.markers.first)
        editor.toggleTask(marker)
        let changed = raw.replacingOccurrences(of: "- [ ]", with: "- [x]")
        XCTAssertEqual(Data(state.text.utf8), Data(changed.utf8))
        XCTAssertEqual(editor.presentation.markers.first?.kind, .task(true))
        editor.undoManager?.undo()
        XCTAssertEqual(Data(editor.text.utf8), Data(raw.utf8))
        XCTAssertEqual(Data(state.text.utf8), Data(raw.utf8))
        editor.undoManager?.redo()
        XCTAssertEqual(Data(state.text.utf8), Data(changed.utf8))
        editor.isEditable = false
        editor.toggleTask(try XCTUnwrap(editor.presentation.markers.first))
        XCTAssertEqual(Data(editor.text.utf8), Data(changed.utf8))
    }

    func testLivePreviewRelayoutLeavesComposedTextCaretAndUndoIntact() async throws {
        let raw = "# Heading\n**Replace**\n- [ ] Keep\n"
        let state = BodyEditorHarness(raw)
        let (window, editor) = try await mount(state); defer { window.isHidden = true }
        editor.becomeFirstResponder(); editor.selectedRange = (raw as NSString).range(of: "Replace")
        editor.setMarkedText("中文组合", selectedRange: NSRange(location: 4, length: 0))
        let marked = editor.text; state.status += 1
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertNotNil(editor.markedTextRange); XCTAssertEqual(editor.text, marked)
        editor.unmarkText(); editor.delegate?.textViewDidChange?(editor)
        let caret = editor.selectedRange
        editor.resignFirstResponder()
        XCTAssertTrue(isHidden((editor.text as NSString).range(of: "**").location, in: editor))
        XCTAssertEqual(editor.selectedRange, caret)
        XCTAssertEqual(Data(state.text.utf8), Data(raw.replacingOccurrences(of: "Replace", with: "中文组合").utf8))
    }

    func testHeadingAttributesPreserveRawBytesAndCaretReplacementAcrossSaveUpdates() async throws {
        let raw = "---\r\nunknown: keep\r\n---\r\n# Synthetic\r\n## **Notes**\r\nA 原文 e\u{301} tail\r\n<% unsupported %>\r\n"
        let state = BodyEditorHarness(raw)
        let (window, editor) = try await mount(state); defer { window.isHidden = true }
        XCTAssertEqual(Data(editor.text.utf8), Data(raw.utf8))
        editor.becomeFirstResponder()
        let range = (editor.text as NSString).range(of: "原文")
        editor.selectedRange = range
        editor.insertText("修改")
        let expected = raw.replacingOccurrences(of: "原文", with: "修改")
        XCTAssertEqual(Data(state.text.utf8), Data(expected.utf8))
        let caret = editor.selectedRange
        state.status += 1
        try await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertEqual(editor.selectedRange, caret)
        XCTAssertEqual(Data(editor.text.utf8), Data(expected.utf8))
        let heading = (editor.text as NSString).range(of: "# Synthetic")
        let headingFont = editor.textStorage.attribute(.font, at: heading.location, effectiveRange: nil) as? UIFont
        XCTAssertGreaterThan(headingFont?.pointSize ?? 0, UIFont.preferredFont(forTextStyle: .body).pointSize)
        editor.undoManager?.undo()
        XCTAssertEqual(Data(editor.text.utf8), Data(raw.utf8), "Autosave presentation must retain the native undo operation")
    }

    func testLongDiaryCompositionCommitKeepsWritingVisibleAfterLayoutAndStatusRefresh() async throws {
        let table = "| Activity | Notes |\r\n| --- | --- |\r\n| Walk | Synthetic |\r\n"
        let raw = "# Synthetic diary\r\n" + (0..<90).map { "## Section \($0)\r\n**Paragraph** 中文 e\u{301} 🧠\r\n" }.joined()
            + table + "\r\n## Evening\r\nWriting here\r\n"
        let state = BodyEditorHarness(raw)
        let (window, editor) = try await mount(state); defer { window.isHidden = true }
        try await waitForDocument(editor, text: raw)
        editor.becomeFirstResponder()
        editor.selectedRange = (raw as NSString).range(of: "Writing here")
        editor.scrollRangeToVisible(editor.selectedRange)
        try await Task.sleep(nanoseconds: 350_000_000)
        editor.layoutManager.ensureLayout(for: editor.textContainer)
        editor.layoutIfNeeded()
        let startingSelection = try XCTUnwrap(editor.selectedTextRange)
        let startingCaret = editor.caretRect(for: startingSelection.end)
        editor.setContentOffset(CGPoint(x: 0, y: max(0, startingCaret.maxY - editor.bounds.height + 80)), animated: false)
        let initialOffset = editor.contentOffset.y
        XCTAssertGreaterThan(initialOffset, 1000)
        XCTAssertTrue(editor.bounds.intersects(startingCaret))
        editor.setMarkedText("zhongwen", selectedRange: NSRange(location: 8, length: 0))
        XCTAssertNotNil(editor.markedTextRange)
        state.status += 1
        try await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertNotNil(editor.markedTextRange)
        editor.insertText("中文确认")
        XCTAssertNil(editor.markedTextRange)
        let committed = raw.replacingOccurrences(of: "Writing here", with: "中文确认")
        XCTAssertEqual(Data(state.text.utf8), Data(committed.utf8))
        let caret = editor.selectedRange
        state.status += 1
        for _ in 0..<6 {
            try await Task.sleep(nanoseconds: 100_000_000)
            editor.layoutIfNeeded()
            XCTAssertEqual(editor.selectedRange, caret)
            XCTAssertGreaterThan(editor.contentOffset.y, initialOffset - 300, "Confirmed text must not jump to the document start: offset=\(editor.contentOffset), size=\(editor.contentSize)")
            let selection = try XCTUnwrap(editor.selectedTextRange)
            XCTAssertTrue(editor.bounds.intersects(editor.caretRect(for: selection.end)), "The insertion point must remain visible after deferred layout")
        }
        editor.insertText("\nContinue")
        try await Task.sleep(nanoseconds: 150_000_000)
        XCTAssertEqual(Data(state.text.utf8), Data(committed.replacingOccurrences(of: "中文确认", with: "中文确认\nContinue").utf8))
        XCTAssertGreaterThan(editor.contentOffset.y, initialOffset - 300)
        let continuedSelection = try XCTUnwrap(editor.selectedTextRange)
        XCTAssertTrue(editor.bounds.intersects(editor.caretRect(for: continuedSelection.end)), "Continuing onto a new line must keep the insertion point visible")
        editor.resignFirstResponder()
        try await Task.sleep(nanoseconds: 350_000_000)
        XCTAssertGreaterThan(editor.contentOffset.y, initialOffset - 600, "Dismissing the keyboard must retain the reading location")
    }

    func testTypicalLongDiaryContinuousTypingReportsLayoutCostAndRetainsExactText() async throws {
        try await checkContinuousTyping(sectionCount: 90)
    }

    func testLongDiaryContinuousTypingReportsLayoutCostAndRetainsExactText() async throws {
        try await checkContinuousTyping(sectionCount: 2000)
    }

    private func checkContinuousTyping(sectionCount: Int) async throws {
        let raw = (0..<sectionCount).map { "## Section \($0)\nSynthetic 中文 e\u{301} 🧠\n" }.joined()
        let state = BodyEditorHarness(raw)
        let (window, editor) = try await mount(state); defer { window.isHidden = true }
        try await waitForDocument(editor, text: raw)
        editor.becomeFirstResponder(); editor.selectedRange = NSRange(location: raw.utf16.count, length: 0)
        editor.scrollRangeToVisible(editor.selectedRange)
        try await Task.sleep(nanoseconds: 150_000_000)
        var durations: [Double] = [], expected = raw
        for value in ["中", "文", "确", "认", "\n", "c", "o", "n", "t", "i", "n", "u", "e"] {
            let start = ProcessInfo.processInfo.systemUptime
            editor.insertText(value); editor.layoutIfNeeded()
            durations.append((ProcessInfo.processInfo.systemUptime - start) * 1000)
            expected += value
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTAssertEqual(Data(state.text.utf8), Data(expected.utf8))
        XCTAssertEqual(editor.selectedRange, NSRange(location: expected.utf16.count, length: 0))
        let report = "Synthetic \(sectionCount)-heading diary input: samples=\(durations.count), mean_ms=\(durations.reduce(0, +) / Double(durations.count)), max_ms=\(durations.max() ?? 0)"
        let evidence = XCTAttachment(string: report); evidence.name = "Long diary input timing"; evidence.lifetime = .keepAlways; add(evidence)
        print(report)
    }

    func testSameDaySourceReplacementKeepsSelectedRangeAndClampsBothBounds() async throws {
        let raw = "# Synthetic\r\nBefore 中文\r\nSelected phrase\r\nOther body\r\n"
        let state = BodyEditorHarness(raw)
        let (window, editor) = try await mount(state); defer { window.isHidden = true }
        editor.becomeFirstResponder()
        let selected = (raw as NSString).range(of: "Selected phrase")
        editor.selectedRange = selected
        let changed = raw.replacingOccurrences(of: "Other body", with: "Other source changed 中 e\u{301}")
        state.text = changed
        try await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertEqual(editor.selectedRange, selected)
        XCTAssertEqual(Data(editor.text.utf8), Data(changed.utf8))
        XCTAssertEqual(state.day, try JournalDay("2026-09-14"))
        let shortened = (changed as NSString).substring(to: selected.location + 3)
        state.text = shortened
        try await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertEqual(editor.selectedRange, NSRange(location: selected.location, length: 3))
        XCTAssertEqual(Data(editor.text.utf8), Data(shortened.utf8))
        state.text = "# Short\n"
        try await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertEqual(editor.selectedRange, NSRange(location: state.text.utf16.count, length: 0))
        XCTAssertEqual(Data(editor.text.utf8), Data(state.text.utf8))
    }

    func testChineseMarkedTextAndMetadataProtectionSurviveStatusRefresh() async throws {
        let raw = "---\nunknown: keep\n---\n# Notes\nText\n<!-- riji-record:ai:synthetic -->\n"
        let state = BodyEditorHarness(raw)
        let (window, editor) = try await mount(state); defer { window.isHidden = true }
        editor.becomeFirstResponder()
        editor.selectedRange = (raw as NSString).range(of: "Text")
        editor.setMarkedText("中文输入", selectedRange: NSRange(location: 4, length: 0))
        XCTAssertNotNil(editor.markedTextRange)
        let marked = editor.text
        state.status += 1
        try await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertNotNil(editor.markedTextRange)
        XCTAssertEqual(editor.text, marked)
        editor.unmarkText()
        editor.delegate?.textViewDidChange?(editor)
        XCTAssertTrue(state.text.contains("中文输入"))
        let metadata = (editor.text as NSString).range(of: "unknown: keep")
        XCTAssertEqual(editor.delegate?.textView?(editor, shouldChangeTextIn: metadata, replacementText: ""), false)
        let provenance = (editor.text as NSString).range(of: "<!-- riji-record:ai:synthetic -->")
        XCTAssertEqual(editor.delegate?.textView?(editor, shouldChangeTextIn: provenance, replacementText: ""), false)
    }

    func testCompositionKeepsSourceDayIdentityWhenAnotherDocumentIsPublished() async throws {
        let oldDay = try JournalDay("2026-09-14"), newDay = try JournalDay("2026-09-15")
        let state = BodyEditorHarness("# Old day\nText\n")
        let (window, editor) = try await mount(state); defer { window.isHidden = true }
        editor.becomeFirstResponder()
        editor.selectedRange = (editor.text as NSString).range(of: "Text")
        editor.setMarkedText("中文组合", selectedRange: NSRange(location: 4, length: 0))
        XCTAssertNotNil(editor.markedTextRange)
        let newText = "# New day\nUntouched\n"
        state.day = newDay; state.text = newText
        try await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertNil(editor.markedTextRange)
        XCTAssertEqual(editor.text, newText); XCTAssertEqual(state.text, newText)
        XCTAssertTrue(state.callbacks.contains { $0.0 == oldDay && $0.1.contains("中文组合") })
        XCTAssertFalse(state.callbacks.contains { $0.0 == newDay && $0.1.contains("中文组合") })
        editor.becomeFirstResponder(); editor.selectedRange = NSRange(location: editor.text.utf16.count, length: 0)
        editor.insertText("New input")
        XCTAssertEqual(state.callbacks.last?.0, newDay)
    }

    func testNativeAdmissionProtectsMetadataPositionAndProvenanceNewlineEndpoints() async throws {
        let raw = "---\r\nunknown: keep\r\n---\r\n# Body\r\n<!-- riji-record:mit:synthetic -->\r\nNext\r\n"
        let state = BodyEditorHarness(raw)
        let (window, editor) = try await mount(state); defer { window.isHidden = true }
        let record = (raw as NSString).range(of: "<!-- riji-record:mit:synthetic -->")
        let edits: [(NSRange, String, Bool)] = [
            (.init(location: 0, length: 0), "x", false),
            (.init(location: record.location, length: 0), "x", false),
            (.init(location: record.location - 2, length: 2), "", false),
            (.init(location: NSMaxRange(record), length: 0), "x", false),
            (.init(location: NSMaxRange(record), length: 2), "", false),
            (.init(location: NSMaxRange(record), length: 0), "\r\n", true),
            ((raw as NSString).range(of: "Next"), "Replacement", true)
        ]
        for (range, replacement, allowed) in edits {
            XCTAssertEqual(editor.delegate?.textView?(editor, shouldChangeTextIn: range, replacementText: replacement), allowed)
            let next = (raw as NSString).replacingCharacters(in: range, with: replacement)
            XCTAssertEqual((try? BodyMarkdown.validatePreservation(before: Data(raw.utf8), after: Data(next.utf8))) != nil, allowed)
        }
        XCTAssertEqual(Data(editor.text.utf8), Data(raw.utf8))
    }

    func testDenseHeadingFormattingRetainsSelectionAndExactUnicodeBytes() async throws {
        let raw = (0..<2000).map { "## Heading \($0) 中文 e\u{301} 🧠\r\nBody\r\n" }.joined()
        let state = BodyEditorHarness(raw)
        let (window, editor) = try await mount(state); defer { window.isHidden = true }
        editor.selectedRange = (raw as NSString).range(of: "Body", options: .backwards)
        let selection = editor.selectedRange
        state.status += 1
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(editor.selectedRange, selection); XCTAssertEqual(Data(editor.text.utf8), Data(raw.utf8))
    }

    func testNativeEmptyLFAndCRLFFrontmatterIsProtectedWhileBodyRemainsEditable() async throws {
        for newline in ["\n", "\r\n"] {
            let raw = "---\(newline)---\(newline)# Body\(newline)Editable\(newline)"
            let state = BodyEditorHarness(raw)
            let (window, editor) = try await mount(state); defer { window.isHidden = true }
            editor.becomeFirstResponder()
            XCTAssertEqual(editor.delegate?.textView?(editor, shouldChangeTextIn: NSRange(location: 0, length: 3), replacementText: ""), false)
            editor.selectedRange = (raw as NSString).range(of: "Editable")
            editor.insertText("中文修改")
            XCTAssertEqual(Data(state.text.utf8), Data(raw.replacingOccurrences(of: "Editable", with: "中文修改").utf8))
            XCTAssertTrue(state.text.hasPrefix("---\(newline)---\(newline)"))
        }
    }

    func testNativeAdmissionExplainsRejectedEditWithoutDirtyCallbackOrUndoLoss() async throws {
        let raw = "---\r\nunknown: keep\r\n---\r\n# Notes\r\nEditable\r\n"
        let state = BodyEditorHarness(raw)
        let (window, editor) = try await mount(state); defer { window.isHidden = true }
        editor.becomeFirstResponder()
        editor.selectedRange = (raw as NSString).range(of: "Editable")
        editor.insertText("Accepted")
        let accepted = state.text, callbacks = state.callbacks.count
        editor.selectedRange = (editor.text as NSString).range(of: "unknown: keep")
        let selection = editor.selectedRange
        XCTAssertEqual(editor.delegate?.textView?(editor, shouldChangeTextIn: selection, replacementText: "Rejected"), false)
        XCTAssertTrue(state.protectedFeedback)
        XCTAssertEqual(editor.text, accepted); XCTAssertEqual(state.text, accepted)
        XCTAssertEqual(state.callbacks.count, callbacks); XCTAssertEqual(editor.selectedRange, selection)
        XCTAssertTrue(DiaryBodyEditor.protectedEditExplanation.contains("来源标识"))
        editor.undoManager?.undo()
        XCTAssertEqual(Data(editor.text.utf8), Data(raw.utf8))
    }

    func testActualRecoveryEditorsCommitNativeCompositionBeforeExplicitCapture() async throws {
        for merge in [false, true] {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent("RecoveryComposition-\(UUID())")
            defer { try? FileManager.default.removeItem(at: root) }
            let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"))
            let date = ISO8601DateFormatter().date(from: "2026-09-14T08:00:00Z")!
            let day = JournalDay(date: date, timeZone: store.timeZone)
            let model = DiaryViewModel(now: { date }, storageFactory: { store }); await model.start()
            model.updateBody("# Synthetic\n## Notes\nBase\n"); let baseSaved = await model.flushBody(); XCTAssertTrue(baseSaved)
            if merge {
                try Data("# External\n## Notes\n".utf8).write(to: root.appendingPathComponent("Journals/\(day.value).md"))
                model.updateBody("# Local\n## Notes\n")
                _ = await model.flushBody(); await model.reviewBodyConflict()
            } else { model.updateSection("Notes"); model.updateText("Legacy input"); await model.flushDraft() }
            let draft = Data((merge ? model.bodyMergeText : model.text).utf8)
            let (window, rootView, editor) = try await mountRecoveryEditor(model, merge: merge, matching: draft)
            defer { window.isHidden = true }
            XCTAssertTrue(editor.isEditable); XCTAssertEqual(Data(editor.text.utf8), draft)
            XCTAssertTrue(editor.becomeFirstResponder(), "The mounted recovery editor must accept focus once")
            XCTAssertTrue(editor.isFirstResponder)
            editor.selectedRange = NSRange(location: editor.text.utf16.count, length: 0)
            editor.setMarkedText("中文组合", selectedRange: NSRange(location: 4, length: 0))
            XCTAssertNotNil(editor.markedTextRange)
            let expected = editor.text ?? ""
            XCTAssertTrue(editor.window === window && editor.isDescendant(of: rootView))
            XCTAssertTrue(editor.isFirstResponder, "Composition must belong to the active mounted editor before capture")
            TodayDiaryView.commitEditing(in: rootView)
            try await Task.sleep(nanoseconds: 50_000_000)
            XCTAssertNil(editor.markedTextRange)
            if merge {
                XCTAssertEqual(model.bodyMergeText, expected)
                let saved = await model.resolveBodyConflict(with: model.bodyMergeText); XCTAssertTrue(saved)
                XCTAssertEqual(model.markdown, expected)
            } else {
                XCTAssertEqual(model.text, expected)
                await model.save(); XCTAssertFalse(model.hasLegacyDraft)
                XCTAssertTrue(model.markdown.contains(expected))
            }
        }
    }

    private func mountRecoveryEditor(_ model: DiaryViewModel, merge: Bool, matching draft: Data) async throws -> (UIWindow, UIView, UITextView) {
        let layout = RecoveryEditorLayout()
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        let host = UIHostingController(rootView: HostedRecoveryEditor(model: model, merge: merge, layout: layout))
        window.rootViewController = host; window.makeKeyAndVisible()
        var mounted = false
        defer { if !mounted { window.isHidden = true } }
        var states: [String] = []
        func record(_ detail: String) {
            let state = "merge=\(merge) ready=\(layout.scrollReady) requested=\(layout.scrollRequested) applied=\(layout.scrollApplied) \(detail)"
            guard states.last != state else { return }
            states.append(state); if states.count > 12 { states.removeFirst() }
        }
        let attachFailure = { self.attachRecoveryReadiness(states) }
        try await waitUntil("The recovery Form must mount before its single scroll request", onTimeout: attachFailure) {
            host.view.layoutIfNeeded()
            record("phase=mount key=\(window.isKeyWindow) sameWindow=\(host.view.window === window)")
            return host.view.window === window && (!merge || layout.scrollReady)
        }
        if merge { layout.scrollRequested = true }
        var previousEditor: UITextView?
        var previousFrame: CGRect?
        var captured: UITextView?
        try await waitUntil("The recovery editor must remain mounted and visible after scrolling", onTimeout: attachFailure) {
            host.view.layoutIfNeeded()
            guard !merge || layout.scrollApplied else { record("phase=editor reject=scroll-not-applied count=not-read"); return false }
            let found = self.editableEditors(host.view, matching: draft)
            var detail = "count=\(found.count)"
            guard found.count == 1, let editor = found.first,
                  let frame = self.recoveryEditorFrame(editor, in: host.view, window: window, detail: &detail) else {
                record("phase=editor stable=false \(detail)")
                previousEditor = nil; previousFrame = nil; return false
            }
            let stable = previousEditor === editor && previousFrame == frame
            record("phase=editor stable=\(stable) sameEditor=\(previousEditor === editor) sameFrame=\(previousFrame == frame) \(detail)")
            previousEditor = editor; previousFrame = frame
            if stable { captured = editor }
            return stable
        }
        // TextEditor exposes its identifier through AX, not the backing UIKit property.
        XCTAssertEqual(editableEditors(host.view, matching: draft).count, 1,
                       merge ? "bodyConflictMerge" : "legacyDraftEditor")
        let editor = try XCTUnwrap(captured)
        mounted = true
        return (window, host.view, editor)
    }

    private func attachRecoveryReadiness(_ states: [String]) {
        let attachment = XCTAttachment(string: states.joined(separator: "\n"))
        attachment.name = "Recovery editor readiness — last 12 distinct states"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    private func recoveryEditorFrame(_ editor: UITextView, in root: UIView, window: UIWindow, detail: inout String) -> CGRect? {
        detail += " key=\(window.isKeyWindow) sameWindow=\(editor.window === window) descendant=\(editor.isDescendant(of: root))"
        guard window.isKeyWindow, editor.window === window, editor.isDescendant(of: root) else { return nil }
        let frame = editor.convert(editor.bounds, to: window)
        detail += " frame=\(frame) finite=\([frame.minX, frame.minY, frame.width, frame.height].allSatisfy { $0.isFinite }) window=\(window.bounds)"
        guard [frame.minX, frame.minY, frame.width, frame.height].allSatisfy({ $0.isFinite }), !frame.isEmpty else { return nil }
        var visible = window.bounds.intersection(frame)
        var ancestor: UIView? = editor
        var firstClip = visible.isEmpty || visible.isNull ? "window" : "none"
        while let view = ancestor {
            guard !view.isHidden, view.alpha > 0 else {
                detail += " hiddenAncestor=\(type(of: view)) hidden=\(view.isHidden) alpha=\(view.alpha) firstClip=\(firstClip) visible=\(visible)"
                return nil
            }
            if view.clipsToBounds {
                visible = visible.intersection(view.convert(view.bounds, to: window))
                if firstClip == "none", visible.isEmpty || visible.isNull { firstClip = String(describing: type(of: view)) }
            }
            ancestor = view.superview
        }
        detail += " hiddenAncestor=none firstClip=\(firstClip) visible=\(visible)"
        return visible.isEmpty || visible.isNull ? nil : frame
    }

}

@MainActor private final class RecoveryEditorLayout: ObservableObject {
    @Published var scrollRequested = false
    var scrollReady = false
    var scrollApplied = false
}

private struct HostedRecoveryEditor: View {
    @ObservedObject var model: DiaryViewModel
    let merge: Bool
    @ObservedObject var layout: RecoveryEditorLayout
    var body: some View {
        if merge {
            ScrollViewReader { proxy in
                TodayDiaryView(model: model).bodyConflictReview(fullVersions: .constant(true))
                    .onAppear { layout.scrollReady = true }
                    .onChange(of: layout.scrollRequested) {
                        guard layout.scrollRequested else { return }
                        proxy.scrollTo("bodyConflictMerge", anchor: .center)
                        layout.scrollApplied = true
                    }
            }
        }
        else { TodayDiaryView(model: model).legacyRecovery }
    }
}


extension NativeBodyEditorTests {
    func testTableCellsRenderInlineStylesAndLongTableCanScrollToFollowingBody() async throws {
        let source = "# Heading\n| A | B |\n| --- | --- |\n| **Bold** | \\| `a|b` [[note|别名]] |\n" +
            (0..<40).map { "| row \($0) | value |\n" }.joined() + "Following body\n"
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        view.layoutIfNeeded()
        let grid = try XCTUnwrap(view.subviews.first { $0.accessibilityIdentifier == "markdownTable" } as? UIScrollView)
        func labels(_ view: UIView) -> [UILabel] {
            if let label = view as? UILabel { return [label] }
            return view.subviews.flatMap(labels)
        }
        let cells = labels(grid)
        let bold = try XCTUnwrap(cells.first { $0.attributedText?.string == "Bold" })
        let font = bold.attributedText?.attribute(.font, at: 0, effectiveRange: nil) as? UIFont
        XCTAssertTrue(font?.fontDescriptor.symbolicTraits.contains(.traitBold) == true)
        XCTAssertTrue(cells.contains { $0.attributedText?.string == "| a|b 别名" })
        XCTAssertGreaterThan(view.contentSize.height, grid.frame.maxY)
        XCTAssertGreaterThan(view.contentSize.height, view.bounds.height)
        view.setContentOffset(CGPoint(x: 0, y: 600), animated: false)
        XCTAssertEqual(view.contentOffset.y, 600, accuracy: 1)
        XCTAssertEqual(Data(view.text.utf8), Data(source.utf8)); XCTAssertTrue(state.callbacks.isEmpty)
    }

    func testLargeTableFallsBackToSourceWithoutAllocatingUnboundedCellViews() async throws {
        let source = "| A | B |\n| --- | --- |\n" + (0..<600).map { "| row \($0) | value |\n" }.joined()
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        XCTAssertFalse(view.subviews.contains { $0.accessibilityIdentifier == "markdownTable" })
        XCTAssertFalse(isHidden(0, in: view))
        XCTAssertEqual(Data(view.text.utf8), Data(source.utf8)); XCTAssertTrue(state.callbacks.isEmpty)
    }

    func testTableGridKeepsBytesWrapsScrollsAndRetainsInlineComposition() async throws {
        let source = "Intro 🧠\r\n| 名称 | 数值 | 备注 | 更多 |\r\n| --- | ---: | --- | --- |\r\n| **e\u{301}** | | 中文长单元格 | 🧠 |\r\nAfter\r\n"
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        let grid = try XCTUnwrap(view.subviews.compactMap { $0 as? MarkdownTableView }.first)
        XCTAssertGreaterThan(grid.contentSize.width, grid.bounds.width)
        grid.beginEditing(row: 1, column: 0)
        let cell = try XCTUnwrap(grid.cellEditor)
        XCTAssertEqual(cell.text, "**e\u{301}**")
        XCTAssertTrue(cell.isFirstResponder); XCTAssertFalse(view.isFirstResponder)
        XCTAssertEqual(Data(view.text.utf8), Data(source.utf8)); XCTAssertTrue(state.callbacks.isEmpty)
        cell.selectedRange = NSRange(location: 0, length: cell.text.utf16.count)
        cell.setMarkedText("拼音 🧠", selectedRange: NSRange(location: 5, length: 0))
        cell.delegate?.textViewDidChange?(cell)
        XCTAssertNotNil(cell.markedTextRange)
        state.status += 1
        window.frame.size.width = 320; window.rootViewController?.view.frame = window.bounds
        window.rootViewController?.view.layoutIfNeeded(); view.layoutIfNeeded()
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertTrue(grid === view.subviews.compactMap { $0 as? MarkdownTableView }.first)
        XCTAssertTrue(cell === grid.cellEditor); XCTAssertTrue(cell.isFirstResponder); XCTAssertNotNil(cell.markedTextRange)
        let selected = try XCTUnwrap(cell.selectedTextRange)
        XCTAssertTrue(view.bounds.intersects(cell.convert(cell.caretRect(for: selected.end), to: view)))
        XCTAssertFalse(view.admitsDateGesture(at: CGPoint(x: 20, y: 25)))
        TodayDiaryView.commitEditing(in: view)
        XCTAssertNil(cell.markedTextRange)
        let expected = source.replacingOccurrences(of: "**e\u{301}**", with: "拼音 🧠")
        XCTAssertEqual(Data(state.text.utf8), Data(expected.utf8))
        try await Task.sleep(nanoseconds: 20_000_000)
        view.undoSource()
        XCTAssertEqual(Data(view.text.utf8), Data(source.utf8))
    }

    func testInlineTableReturnAndPlusControlsRetainGridFocusAndExactUndo() async throws {
        let source = "Before\r\n| A | B |\r\n| :--- | ---: |\r\n| one | two |\r\nAfter\r\n"
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        let grid = try XCTUnwrap(view.subviews.compactMap { $0 as? MarkdownTableView }.first)
        grid.beginEditing(row: 0, column: 1)
        let header = try XCTUnwrap(grid.cellEditor)
        XCTAssertEqual(header.delegate?.textView?(header, shouldChangeTextIn: header.selectedRange, replacementText: "\n"), false)
        let added = source.replacingOccurrences(of: "| :--- | ---: |\r\n", with: "| :--- | ---: |\r\n|  |  |\r\n")
        XCTAssertEqual(Data(view.text.utf8), Data(added.utf8))
        XCTAssertTrue(grid.superview === view, "The same grid must survive Return; root responder=\(view.isFirstResponder), table count=\(view.presentation.tables.count)")
        XCTAssertEqual(grid.cellEditor?.accessibilityLabel, "表格第2行第2列")
        XCTAssertTrue(grid.cellEditor?.isFirstResponder == true)
        grid.cellEditor?.insertText("新增")
        XCTAssertEqual(view.text, added.replacingOccurrences(of: "|  |  |", with: "|  | 新增 |"))
        grid.finishEditing()
        try await Task.sleep(nanoseconds: 20_000_000)
        for _ in 0..<12 where view.undoManager?.canUndo == true { view.undoSource() }
        XCTAssertEqual(Data(view.text.utf8), Data(source.utf8))
        func buttons(_ view: UIView) -> [UIButton] { (view as? UIButton).map { [$0] } ?? view.subviews.flatMap(buttons) }
        let addColumn = try XCTUnwrap(buttons(grid).first { $0.accessibilityIdentifier == "tableAddColumn" })
        let columnAction = try XCTUnwrap(addColumn.actions(forTarget: grid, forControlEvent: .touchUpInside)?.first)
        grid.perform(NSSelectorFromString(columnAction), with: addColumn)
        XCTAssertEqual(view.presentation.tables.first?.alignments.count, 3)
        XCTAssertEqual(grid.cellEditor?.accessibilityLabel, "表格第1行第3列")
        try await Task.sleep(nanoseconds: 20_000_000)
        view.undoSource()
        XCTAssertEqual(Data(view.text.utf8), Data(source.utf8))
        view.redoSource()
        XCTAssertEqual(view.presentation.tables.first?.alignments.count, 3)
        let beforeRow = view.text!
        try await Task.sleep(nanoseconds: 20_000_000)
        let addRow = try XCTUnwrap(buttons(grid).first { $0.accessibilityIdentifier == "tableAddRow" })
        let rowAction = try XCTUnwrap(addRow.actions(forTarget: grid, forControlEvent: .touchUpInside)?.first)
        grid.perform(NSSelectorFromString(rowAction), with: addRow)
        XCTAssertEqual(view.presentation.tables.first?.rows.count, 3)
        XCTAssertEqual(grid.cellEditor?.accessibilityLabel, "表格第3行第1列")
        try await Task.sleep(nanoseconds: 20_000_000)
        view.undoSource()
        XCTAssertEqual(Data(view.text.utf8), Data(beforeRow.utf8))
    }

    func testInlineTableCompositionKeepsOriginalDayAndRejectsDetachedCallbacks() async throws {
        let source = "| A | B |\n| --- | --- |\n| old | keep |\n"
        let state = BodyEditorHarness(source), originalDay = state.day
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        let grid = try XCTUnwrap(view.subviews.compactMap { $0 as? MarkdownTableView }.first)
        grid.beginEditing(row: 1, column: 0)
        let cell = try XCTUnwrap(grid.cellEditor)
        cell.selectedRange = NSRange(location: 0, length: cell.text.utf16.count)
        cell.setMarkedText("中文组合", selectedRange: NSRange(location: 4, length: 0))
        state.day = try JournalDay("2026-09-15"); state.text = "# New day\nUntouched\n"
        try await waitForDocument(view, text: state.text)
        XCTAssertTrue(state.callbacks.contains { $0.0 == originalDay && $0.1.contains("中文组合") })
        XCTAssertFalse(state.callbacks.contains { $0.0 == state.day && $0.1.contains("中文组合") })
        let callbackCount = state.callbacks.count
        grid.textViewDidChange(cell)
        XCTAssertEqual(state.callbacks.count, callbackCount)
        XCTAssertEqual(view.text, "# New day\nUntouched\n")
    }

    func testInlineTableReadOnlyAndUnsupportedStructureRetainSourceWithVisibleFeedback() async throws {
        let source = "| A | B |\n| --- | --- |\n| one | two | hidden |\n"
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        let grid = try XCTUnwrap(view.subviews.compactMap { $0 as? MarkdownTableView }.first)
        XCTAssertFalse(view.applyTableEdit(grid, operation: .appendColumn))
        func errors(_ view: UIView) -> [UIView] { view.subviews.flatMap { [$0] + errors($0) } }
        XCTAssertTrue(errors(grid).contains { $0.accessibilityIdentifier == "tableEditError" && !$0.isHidden })
        XCTAssertEqual(view.text, source); XCTAssertTrue(state.callbacks.isEmpty)
        XCTAssertTrue(view.applyTableEdit(grid, operation: .insertRow(after: 1)))
        XCTAssertFalse(errors(grid).contains { $0.accessibilityIdentifier == "tableEditError" && !$0.isHidden })
        view.undoSource()
        XCTAssertEqual(view.text, source)
        let callbackCount = state.callbacks.count
        state.editable = false
        try await Task.sleep(nanoseconds: 30_000_000)
        grid.beginEditing(row: 1, column: 0)
        XCTAssertNil(grid.cellEditor)
        XCTAssertFalse(view.applyTableEdit(grid, operation: .insertRow(after: 0)))
        XCTAssertEqual(view.text, source); XCTAssertEqual(state.callbacks.count, callbackCount)
    }

    func testOriginalTableAutosavePreservesNativeUndoAndRedoBytes() async throws {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("OriginalTableUndo-\(UUID())")
        defer { try? FileManager.default.removeItem(at: base) }
        let root = base.appendingPathComponent("riji")
        try FileManager.default.createDirectory(at: root.appendingPathComponent("daily"), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: root.appendingPathComponent("templates"), withIntermediateDirectories: true)
        try Data("# Original {{date}}\r\n".utf8).write(to: root.appendingPathComponent("templates/daily.md"))
        let source = "---\r\nunknown: keep\r\n---\r\n# Synthetic\r\n| 名称 | 数值 | 备注 |\r\n| --- | ---: | --- |\r\n| 中文 🧠 0 | | e\u{301} |\r\nAfter\r\n"
        let file = root.appendingPathComponent("daily/2026-09-16.md")
        try Data(source.utf8).write(to: file)
        let date = ISO8601DateFormatter().date(from: "2026-09-16T12:00:00Z")!
        let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base)
        let model = DiaryViewModel(now: { date }, useOriginalCollection: true, vaultStore: browser)
        model.bodyQuietNanoseconds = 25_000_000
        await model.start(); await model.selectOriginalFolder(root)
        XCTAssertTrue(model.canEditBody); XCTAssertEqual(Data(model.markdown.utf8), Data(source.utf8))
        var callbacks: [(JournalDay, String)] = []
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        let host = UIHostingController(rootView: HostedSavingBodyEditor(model: model) { callbacks.append(($0, $1)) })
        window.rootViewController = host; window.makeKeyAndVisible(); host.view.layoutIfNeeded()
        defer { window.isHidden = true }
        try await waitUntil { self.findEditor(host.view) != nil }
        let editor = try XCTUnwrap(findEditor(host.view))
        try await waitForDocument(editor, text: source)
        let grid = try XCTUnwrap(editor.subviews.compactMap { $0 as? MarkdownTableView }.first)
        grid.beginEditing(row: 1, column: 0)
        let cell = try XCTUnwrap(grid.cellEditor)
        cell.selectedRange = NSRange(location: 0, length: 0)
        let position = (source as NSString).range(of: "中文 🧠 0").location
        cell.insertText("Changed")
        let changed = (source as NSString).replacingCharacters(in: NSRange(location: position, length: 0), with: "Changed")
        XCTAssertEqual(Data(model.markdown.utf8), Data(changed.utf8))
        try await waitUntil { !model.bodyDirty && model.bodyStatus == "已保存在此设备" }
        XCTAssertEqual(try Data(contentsOf: file), Data(changed.utf8))
        XCTAssertEqual(cell.selectedRange, NSRange(location: 7, length: 0))
        let undo = try XCTUnwrap(editor.undoManager)
        XCTAssertTrue(undo.canUndo, "Original-file autosave and attributed table presentation must retain native undo")
        (editor as? MarkdownTextView)?.undoSource()
        XCTAssertEqual(Data(editor.text.utf8), Data(source.utf8))
        XCTAssertEqual(Data(model.markdown.utf8), Data(source.utf8))
        try await waitUntil { !model.bodyDirty && model.bodyStatus == "已保存在此设备" }
        XCTAssertEqual(try Data(contentsOf: file), Data(source.utf8))
        XCTAssertTrue(undo.canRedo)
        (editor as? MarkdownTextView)?.redoSource()
        XCTAssertEqual(Data(editor.text.utf8), Data(changed.utf8))
        try await waitUntil { !model.bodyDirty && model.bodyStatus == "已保存在此设备" }
        XCTAssertEqual(try Data(contentsOf: file), Data(changed.utf8))
        XCTAssertNil(model.bodyError)
        XCTAssertTrue(callbacks.contains { Data($0.1.utf8) == Data(source.utf8) })
    }

    func testTableWidthRelayoutDoesNotRewriteSourceOrEmitBodyEdits() async throws {
        let source = "| A | B |\n| --- | --- |\n| 中文 | |\n"
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        view.layoutIfNeeded()
        let originalGrid = try XCTUnwrap(view.subviews.first { $0.accessibilityIdentifier == "markdownTable" })
        let originalWidth = originalGrid.frame.width
        window.frame.size.width = 280; window.rootViewController?.view.frame = window.bounds
        window.rootViewController?.view.layoutIfNeeded(); view.layoutIfNeeded()
        XCTAssertEqual(Data(view.text.utf8), Data(source.utf8)); XCTAssertTrue(state.callbacks.isEmpty)
        let narrowGrid = try XCTUnwrap(view.subviews.first { $0.accessibilityIdentifier == "markdownTable" })
        XCTAssertLessThan(narrowGrid.frame.width, originalWidth)
        XCTAssertEqual(narrowGrid.frame.width, view.bounds.width - view.textContainerInset.left - view.textContainerInset.right, accuracy: 1)
    }
}

extension NativeBodyEditorTests {
    func testInlineTableSpacesSurviveResizeAndNativeUndoWithoutBypassingReadOnly() async throws {
        let source = "| A | B |\n| --- | --- |\n|  old     | keep |\nAfter\n"
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        let grid = try XCTUnwrap(view.subviews.compactMap { $0 as? MarkdownTableView }.first)
        grid.beginEditing(row: 1, column: 0)
        let cell = try XCTUnwrap(grid.cellEditor)
        cell.selectedRange = NSRange(location: 0, length: cell.text.utf16.count)
        cell.insertText("")
        let manager = try XCTUnwrap(view.undoManager)
        XCTAssertTrue(manager.canUndo, "Initial deletion undo must exist")
        for text in [" ", "N", "e", "w", " "] {
            cell.insertText(text)
            XCTAssertTrue(view.undoManager === manager, "Source undo manager identity must stay stable")
            XCTAssertTrue(manager.canUndo, "Cell keystroke must retain its undo; groups=\(manager.groupingLevel), name=\(manager.undoActionName)")
        }
        let caret = cell.selectedRange
        state.status += 1
        window.frame.size.width = 320; window.rootViewController?.view.frame = window.bounds
        window.rootViewController?.view.layoutIfNeeded(); view.layoutIfNeeded()
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertEqual(cell.text, " New "); XCTAssertEqual(cell.selectedRange, caret)
        cell.insertText("row")
        XCTAssertEqual(view.text, source.replacingOccurrences(of: "old", with: " New row"))
        try await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertTrue(view.undoManager === manager)
        XCTAssertTrue(manager.canUndo, "Undo after resize must exist; groups=\(manager.groupingLevel)")
        var undone = 0
        while view.text != source, manager.canUndo, undone < 12 { view.undoSource(); undone += 1 }
        XCTAssertEqual(view.text, source)
        for _ in 0..<undone { view.redoSource() }
        let edited = view.text
        XCTAssertEqual(edited, source.replacingOccurrences(of: "old", with: " New row"))
        state.editable = false
        try await Task.sleep(nanoseconds: 20_000_000)
        view.undoSource()
        XCTAssertEqual(view.text, edited)
    }
}


extension NativeBodyEditorTests {
    func testInlineTableExternalReplacementInvalidatesStaleSourceUndo() async throws {
        let source = "| A | B |\n| --- | --- |\n| old | keep |\n"
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        let grid = try XCTUnwrap(view.subviews.compactMap { $0 as? MarkdownTableView }.first)
        grid.beginEditing(row: 1, column: 0); grid.cellEditor?.insertText("edited")
        try await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertTrue(view.undoManager?.canUndo == true)
        state.text = "# External replacement\nUntouched\n"
        try await waitForDocument(view, text: state.text)
        XCTAssertFalse(view.undoManager?.canUndo == true)
        view.undoSource()
        XCTAssertEqual(view.text, state.text)
    }
}


extension NativeBodyEditorTests {
    func testInlineTableStaleModelEchoPreservesCellFocusAndNativeUndo() async throws {
        let source = "| A | B |\n| --- | --- |\n| old | keep |\n"
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        let grid = try XCTUnwrap(view.subviews.compactMap { $0 as? MarkdownTableView }.first)
        grid.beginEditing(row: 1, column: 0)
        let cell = try XCTUnwrap(grid.cellEditor)
        state.echoesChanges = false
        cell.insertText("new")
        let changed = view.text!
        XCTAssertNotEqual(changed, source)
        state.status += 1
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertEqual(state.text, source)
        XCTAssertEqual(view.text, changed)
        XCTAssertTrue(grid.cellEditor === cell); XCTAssertTrue(cell.isFirstResponder)
        XCTAssertTrue(view.undoManager?.canUndo == true)
        state.echoesChanges = true; state.text = changed
        try await Task.sleep(nanoseconds: 30_000_000)
        view.undoSource()
        XCTAssertEqual(Data(view.text.utf8), Data(source.utf8))
    }

    func testInlineTableGrowthAtDocumentCellBudgetKeepsSourceAndEditor() async throws {
        let first = "| A | B |\n| --- | --- |\n" + String(repeating: "| a | b |\n", count: 499)
        let second = "| C | D |\n| --- | --- |\n" + String(repeating: "| c | d |\n", count: 11)
        let source = first + "\n" + second
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        let grids = view.subviews.compactMap { $0 as? MarkdownTableView }
        XCTAssertEqual(grids.count, 2)
        let grid = try XCTUnwrap(grids.last)
        grid.beginEditing(row: 1, column: 0)
        let cell = try XCTUnwrap(grid.cellEditor)
        XCTAssertFalse(view.applyTableEdit(grid, operation: .insertRow(after: 1)))
        XCTAssertFalse(view.applyTableEdit(grid, operation: .appendColumn))
        XCTAssertEqual(Data(view.text.utf8), Data(source.utf8)); XCTAssertTrue(state.callbacks.isEmpty)
        XCTAssertTrue(grid.cellEditor === cell); XCTAssertTrue(cell.isFirstResponder)
        XCTAssertTrue(grid.subviews.flatMap(\.subviews).contains { $0.accessibilityIdentifier == "tableEditError" && !$0.isHidden })
    }
}


extension NativeBodyEditorTests {
    func testInlineTableUnchangedCellSwitchKeepsPreviousDisplayAndBodyEditingAvailable() async throws {
        let source = "| A | B |\n| --- | --- |\n| **first** | second |\nAfter\n"
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        let grid = try XCTUnwrap(view.subviews.compactMap { $0 as? MarkdownTableView }.first)
        func labels(_ view: UIView) -> [UILabel] { (view as? UILabel).map { [$0] } ?? view.subviews.flatMap(labels) }
        let first = try XCTUnwrap(labels(grid).first { $0.attributedText?.string == "first" })
        grid.beginEditing(row: 1, column: 0)
        XCTAssertTrue(first.isHidden)
        grid.beginEditing(row: 1, column: 1)
        XCTAssertFalse(first.isHidden)
        XCTAssertEqual(grid.cellEditor?.text, "second"); XCTAssertTrue(grid.cellEditor?.isFirstResponder == true)
        XCTAssertEqual(Data(view.text.utf8), Data(source.utf8)); XCTAssertTrue(state.callbacks.isEmpty)
        view.commitTableEditing()
        view.becomeFirstResponder()
        view.selectedRange = NSRange(location: view.text.utf16.count, length: 0)
        view.insertText("Body")
        XCTAssertTrue(view.isFirstResponder)
        XCTAssertEqual(Data(view.text.utf8), Data((source + "Body").utf8))
        view.undoSource()
        XCTAssertEqual(Data(view.text.utf8), Data(source.utf8))
    }
}


extension NativeBodyEditorTests {
    func testInlineTableEncodedCellSessionsKeepExactSourceNeighborsAndFocus() async throws {
        let sessions: [(String, [(String, String)])] = [
            ("", [("|\u{301}", "\\|\u{301}"), ("x", "\\|\u{301}x"), ("DELETE", "\\|\u{301}"), ("z", "\\|\u{301}z")]),
            ("`unfinished", [(" more", "`unfinished more"), ("DELETE", "`unfinished mor"), ("|", "`unfinished mor\\|"), ("x", "`unfinished mor\\|x")]),
            ("[[unfinished", [(" more", "[[unfinished more"), ("DELETE", "[[unfinished mor"), ("|", "[[unfinished mor\\|"), ("x", "[[unfinished mor\\|x")]),
            ("", [("one\n二", "one<br>二"), ("x", "one<br>二x"), ("DELETE", "one<br>二"), ("DELETE", "one<br>"), ("续", "one<br>续")])
        ]
        for (initial, edits) in sessions {
            let prefix = "Before 🧠\r\n| A | B |\r\n| --- | --- |\r\n| "
            let suffix = " | keep |\r\nAfter\r\n"
            let source = prefix + initial + suffix
            let state = BodyEditorHarness(source)
            let (window, raw) = try await mount(state); defer { window.isHidden = true }
            let view = try XCTUnwrap(raw as? MarkdownTextView)
            try await waitForDocument(view, text: source)
            let grid = try XCTUnwrap(view.subviews.compactMap { $0 as? MarkdownTableView }.first)
            grid.beginEditing(row: 1, column: 0)
            let cell = try XCTUnwrap(grid.cellEditor)
            for (input, expected) in edits {
                if input == "DELETE" { cell.deleteBackward() } else { cell.insertText(input) }
                XCTAssertEqual(Data(view.text.utf8), Data((prefix + expected + suffix).utf8))
                XCTAssertEqual(view.presentation.tables.first?.rows[1].sourceCells.count, 2)
                XCTAssertTrue(grid.cellEditor === cell); XCTAssertTrue(cell.isFirstResponder)
                state.status += 1
                try await Task.sleep(nanoseconds: 10_000_000)
                XCTAssertTrue(grid.cellEditor === cell); XCTAssertTrue(cell.isFirstResponder)
            }
        }
    }

    func testInlineTableZeroWidthRestyleKeepsOneValidGridAndEditor() async throws {
        let source = "| A | B |\n| --- | --- |\n| old | keep |\n"
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        let grid = try XCTUnwrap(view.subviews.compactMap { $0 as? MarkdownTableView }.first)
        grid.beginEditing(row: 1, column: 0)
        let cell = try XCTUnwrap(grid.cellEditor)
        let frame = view.frame
        view.frame.size.width = 0
        view.applyPresentation(active: nil)
        view.frame = frame
        view.applyPresentation(active: nil); view.layoutIfNeeded()
        let grids = view.subviews.compactMap { $0 as? MarkdownTableView }
        XCTAssertEqual(grids.count, 1); XCTAssertTrue(grids.first === grid)
        XCTAssertTrue(grid.cellEditor === cell); XCTAssertTrue(cell.isFirstResponder)
        cell.insertText("new")
        XCTAssertEqual(view.text, source.replacingOccurrences(of: "old", with: "oldnew"))
    }

    func testInlineTableFailureKeepsAddRowAndShowsFeedbackInVisibleLongTable() async throws {
        let source = "| A | B | C | D |\n| --- | --- | --- | --- |\n| first | keep | third | fourth | hidden |\n" + String(repeating: "| a | b | c | d |\n", count: 60)
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        let grid = try XCTUnwrap(view.subviews.compactMap { $0 as? MarkdownTableView }.first)
        func descendants(_ view: UIView) -> [UIView] { view.subviews.flatMap { [$0] + descendants($0) } }
        let row = try XCTUnwrap(descendants(grid).first { $0.accessibilityIdentifier == "tableAddRow" } as? UIButton)
        let column = try XCTUnwrap(descendants(grid).first { $0.accessibilityIdentifier == "tableAddColumn" } as? UIButton)
        func activate(_ button: UIButton) throws {
            let action = try XCTUnwrap(button.actions(forTarget: grid, forControlEvent: .touchUpInside)?.first)
            grid.perform(NSSelectorFromString(action), with: button)
        }
        grid.setContentOffset(CGPoint(x: grid.contentSize.width - grid.bounds.width, y: 0), animated: false)
        try activate(column)
        let feedback = try XCTUnwrap(descendants(grid).first { $0.accessibilityIdentifier == "tableEditError" })
        XCTAssertFalse(feedback.isHidden); XCTAssertFalse(row.isHidden); XCTAssertTrue(row.isEnabled)
        XCTAssertTrue(view.bounds.contains(feedback.convert(feedback.bounds, to: view)))
        XCTAssertFalse(feedback.frame.intersects(row.frame))
        XCTAssertEqual(view.text, source); XCTAssertTrue(state.callbacks.isEmpty)
        try activate(row)
        XCTAssertEqual(view.text, source + "|  |  |  |  |\n")
        XCTAssertTrue(feedback.isHidden); XCTAssertTrue(grid.cellEditor?.isFirstResponder == true)
    }

    func testInlineTableDismissalAndCrossTableFocusRestoreFormattedLabels() async throws {
        let source = "| A | B |\n| --- | --- |\n| **first** | keep |\n\n| C | D |\n| --- | --- |\n| **second** | stays |\n"
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        let grids = view.subviews.compactMap { $0 as? MarkdownTableView }
        XCTAssertEqual(grids.count, 2)
        func labels(_ view: UIView) -> [UILabel] { (view as? UILabel).map { [$0] } ?? view.subviews.flatMap(labels) }
        grids[0].beginEditing(row: 1, column: 0)
        view.dismissAllEditing()
        XCTAssertNil(grids[0].cellEditor)
        XCTAssertTrue(labels(grids[0]).contains { $0.attributedText?.string == "first" && !$0.isHidden })
        XCTAssertEqual(view.text, source)
        grids[0].beginEditing(row: 1, column: 0)
        let cell = try XCTUnwrap(grids[0].cellEditor)
        cell.selectedRange = NSRange(location: 0, length: cell.text.utf16.count)
        cell.setMarkedText("**中文**", selectedRange: NSRange(location: 4, length: 0))
        state.status += 1
        try await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertTrue(grids[0].cellEditor === cell); XCTAssertNotNil(cell.markedTextRange); XCTAssertTrue(cell.isFirstResponder)
        let currentTarget = try XCTUnwrap(view.subviews.compactMap { $0 as? MarkdownTableView }.last)
        currentTarget.beginEditing(row: 1, column: 0)
        XCTAssertNil(grids[0].cellEditor)
        let second = try XCTUnwrap(view.subviews.compactMap { $0 as? MarkdownTableView }.last)
        XCTAssertTrue(second.cellEditor?.isFirstResponder == true)
        XCTAssertEqual(second.cellEditor?.accessibilityLabel, "表格第2行第1列")
        XCTAssertEqual(view.text, source.replacingOccurrences(of: "**first**", with: "**中文**"))
        XCTAssertTrue(labels(grids[0]).contains { $0.attributedText?.string == "中文" && !$0.isHidden })
        view.dismissAllEditing()
        XCTAssertNil(second.cellEditor)
        XCTAssertTrue(labels(second).contains { $0.attributedText?.string == "second" && !$0.isHidden })
    }
}

extension NativeBodyEditorTests {
    func testExplicitReplacementRevisionOverridesPendingNativeEchoAndInvalidatesTableUndo() async throws {
        let source = "| A | B |\r\n| --- | --- |\r\n| old | keep |\r\n"
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        let grid = try XCTUnwrap(view.subviews.compactMap { $0 as? MarkdownTableView }.first)
        grid.beginEditing(row: 1, column: 0)
        let cell = try XCTUnwrap(grid.cellEditor)
        state.echoesChanges = false
        cell.insertText("Unconfirmed")
        let pending = view.text!
        XCTAssertNotEqual(pending, source)
        TodayDiaryView.commitEditing(in: view)
        state.status += 1
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertEqual(view.text, pending, "A routine stale SwiftUI echo must still be ignored")
        state.replacementRevision += 1
        try await waitForDocument(view, text: source)
        XCTAssertFalse(view.undoManager?.canUndo == true)
        XCTAssertFalse(view.undoManager?.canRedo == true)
        let count = state.callbacks.count
        grid.textViewDidChange(cell)
        XCTAssertEqual(state.callbacks.count, count, "The superseded table must not reinsert its draft")
        XCTAssertEqual(Data(view.text.utf8), Data(source.utf8))
    }

    func testProductionLifecycleRetainsBodyAndTableCompositionWhenExternalFileChanged() async throws {
        for tableInput in [false, true] {
            let base = FileManager.default.temporaryDirectory.appendingPathComponent("NativeRefresh-\(UUID())")
            defer { try? FileManager.default.removeItem(at: base) }
            let root = base.appendingPathComponent("riji")
            for folder in ["daily", "templates"] { try FileManager.default.createDirectory(at: root.appendingPathComponent(folder), withIntermediateDirectories: true) }
            try Data("# Template {{date}}\r\n".utf8).write(to: root.appendingPathComponent("templates/daily.md"))
            let source = "# Synthetic\r\nReplace\r\n| A | B |\r\n| --- | --- |\r\n| old | keep |\r\n"
            let target = root.appendingPathComponent("daily/2026-09-16.md")
            try Data(source.utf8).write(to: target)
            let date = ISO8601DateFormatter().date(from: "2026-09-16T12:00:00Z")!
            let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base)
            let model = DiaryViewModel(now: { date }, useOriginalCollection: true, vaultStore: browser)
            model.bodyQuietNanoseconds = 3_600_000_000_000; model.bodyCheckpointNanoseconds = 3_600_000_000_000
            await model.start(); await model.selectOriginalFolder(root)
            let day = model.selectedDay
            let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
            let host = UIHostingController(rootView: TodayDiaryView(model: model))
            window.rootViewController = host; window.makeKeyAndVisible(); host.view.layoutIfNeeded()
            defer { window.isHidden = true }
            try await waitUntil { self.findEditor(host.view) != nil }
            let editor = try XCTUnwrap(findEditor(host.view) as? MarkdownTextView)
            try await waitForDocument(editor, text: source)
            let input: UITextView
            if tableInput {
                let grid = try XCTUnwrap(editor.subviews.compactMap { $0 as? MarkdownTableView }.first)
                grid.beginEditing(row: 1, column: 0); input = try XCTUnwrap(grid.cellEditor)
                input.selectedRange = NSRange(location: 0, length: input.text.utf16.count)
            } else {
                input = editor; input.becomeFirstResponder(); input.selectedRange = (source as NSString).range(of: "Replace")
            }
            input.setMarkedText("组合 🧠", selectedRange: NSRange(location: 2, length: 0))
            XCTAssertNotNil(input.markedTextRange); XCTAssertFalse(model.bodyRefreshAdmission())
            let external = source + "External update\r\n"; try Data(external.utf8).write(to: target)
            let revision = model.bodyReplacementRevision
            await model.refreshOriginalBody()
            XCTAssertEqual(model.bodyReplacementRevision, revision); XCTAssertNotNil(input.markedTextRange)
            model.scenePhaseChanged(.inactive) {
                TodayDiaryView.commitEditing(in: host.view)
                TodayDiaryView.commitVisibleInput()
            }
            XCTAssertNil(input.markedTextRange)
            let local = source.replacingOccurrences(of: tableInput ? "old" : "Replace", with: "组合 🧠")
            try await waitUntil { model.bodyError != nil }
            XCTAssertEqual(Data(model.markdown.utf8), Data(local.utf8)); XCTAssertEqual(model.selectedDay, day)
            XCTAssertEqual(try Data(contentsOf: target), Data(external.utf8))
            let accessValue = try await browser.originalAccess(), access = try XCTUnwrap(accessValue)
            let store = try await browser.homeStore(access), retained = try await store.retainedBodyEdit(day)
            XCTAssertEqual(retained?.content, Data(local.utf8))
            model.scenePhaseChanged(.active) { XCTFail() }
            XCTAssertFalse(editor.isEditable, "Resume must synchronously fence the stale native view")
            await model.reviewBodyConflict()
            let resolved = await model.useLatestBodyVersion(); XCTAssertTrue(resolved)
            try await waitForDocument(editor, text: external)
            XCTAssertFalse(editor.undoManager?.canUndo == true)
            let cold = DiaryViewModel(now: { date }, useOriginalCollection: true,
                vaultStore: VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base))
            await cold.start(); XCTAssertEqual(Data(cold.markdown.utf8), Data(external.utf8))
        }
    }
}

extension NativeBodyEditorTests {
    func testMountedNativeCallbackAfterExternalPublicationPreservesOriginalBase() async throws {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("NativePublishedRefresh-\(UUID())")
        defer { try? FileManager.default.removeItem(at: base) }
        let root = base.appendingPathComponent("riji")
        for folder in ["daily", "templates"] { try FileManager.default.createDirectory(at: root.appendingPathComponent(folder), withIntermediateDirectories: true) }
        try Data("# Template {{date}}\r\n".utf8).write(to: root.appendingPathComponent("templates/daily.md"))
        let before = "# Source\r\nOriginal e\u{301} 🧠\r\n", target = root.appendingPathComponent("daily/2026-09-16.md")
        try Data(before.utf8).write(to: target)
        let date = ISO8601DateFormatter().date(from: "2026-09-16T12:00:00Z")!
        let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base)
        let model = DiaryViewModel(now: { date }, useOriginalCollection: true, vaultStore: browser)
        model.bodyQuietNanoseconds = 3_600_000_000_000; model.bodyCheckpointNanoseconds = 3_600_000_000_000
        await model.start(); await model.selectOriginalFolder(root)
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        let host = UIHostingController(rootView: TodayDiaryView(model: model))
        window.rootViewController = host; window.makeKeyAndVisible(); host.view.layoutIfNeeded()
        defer { window.isHidden = true }
        try await waitUntil { self.findEditor(host.view) != nil }
        let editor = try XCTUnwrap(findEditor(host.view)), coordinator = try XCTUnwrap(editor.delegate as? DiaryBodyEditor.Coordinator)
        try await waitForDocument(editor, text: before)
        let oldNativeCallback = coordinator.parent.onChange, day = model.selectedDay
        let external = before + "External published\r\n"; try Data(external.utf8).write(to: target)
        await model.refreshOriginalBody()
        XCTAssertEqual(model.markdown, external)
        oldNativeCallback(day, before)
        XCTAssertEqual(model.markdown, external); XCTAssertFalse(model.bodyDirty)
        oldNativeCallback(day, before + "First native input")
        XCTAssertEqual(model.markdown, before + "First native input")
        oldNativeCallback(day, before + "First and second native input")
        XCTAssertEqual(model.markdown, before + "First and second native input")
        oldNativeCallback(day, before)
        XCTAssertEqual(model.markdown, before, "A genuine net-zero undo after late input must be retained")
        let local = before + "Native input before view update\r\n"
        oldNativeCallback(day, local)
        try await waitForDocument(editor, text: local)
        let saved = await model.flushBody(); XCTAssertFalse(saved)
        XCTAssertEqual(try Data(contentsOf: target), Data(external.utf8))
        let accessValue = try await browser.originalAccess(), access = try XCTUnwrap(accessValue)
        let store = try await browser.homeStore(access), retained = try await store.retainedBodyEdit(day)
        XCTAssertEqual(retained?.before, Data(before.utf8)); XCTAssertEqual(retained?.content, Data(local.utf8))
    }

    func testCleanFocusedEditorRefreshesExternalBytesAndContinuesTypingWithoutConflict() async throws {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("NativeFocusedRefresh-\(UUID())")
        defer { try? FileManager.default.removeItem(at: base) }
        let root = base.appendingPathComponent("riji")
        for folder in ["daily", "templates"] { try FileManager.default.createDirectory(at: root.appendingPathComponent(folder), withIntermediateDirectories: true) }
        try Data("# Template {{date}}\r\n".utf8).write(to: root.appendingPathComponent("templates/daily.md"))
        let before = "# Source\r\nOriginal e\u{301} 🧠\r\n", target = root.appendingPathComponent("daily/2026-09-16.md")
        try Data(before.utf8).write(to: target)
        let date = ISO8601DateFormatter().date(from: "2026-09-16T12:00:00Z")!
        let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base)
        let model = DiaryViewModel(now: { date }, useOriginalCollection: true, vaultStore: browser)
        model.bodyQuietNanoseconds = 3_600_000_000_000; model.bodyCheckpointNanoseconds = 3_600_000_000_000
        await model.start(); await model.selectOriginalFolder(root)
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        let host = UIHostingController(rootView: TodayDiaryView(model: model))
        window.rootViewController = host; window.makeKeyAndVisible(); host.view.layoutIfNeeded()
        defer { window.isHidden = true }
        try await waitUntil { self.findEditor(host.view) != nil }
        let editor = try XCTUnwrap(findEditor(host.view))
        try await waitForDocument(editor, text: before)
        editor.becomeFirstResponder()
        editor.selectedRange = NSRange(location: before.utf16.count, length: 0)
        XCTAssertTrue(editor.isFirstResponder); XCTAssertNil(editor.markedTextRange)
        XCTAssertTrue(model.bodyRefreshAdmission(), "Keyboard focus alone must not block clean external updates")
        let selection = editor.selectedRange, external = before + "External arrived while focused\r\n"
        try Data(external.utf8).write(to: target)
        await model.refreshOriginalBody()
        try await waitForDocument(editor, text: external)
        XCTAssertTrue(editor.isFirstResponder); XCTAssertEqual(editor.selectedRange, selection)
        editor.selectedRange = NSRange(location: external.utf16.count, length: 0)
        editor.insertText("Continued from latest 🧠\r\n")
        let expected = external + "Continued from latest 🧠\r\n"
        try await waitUntil { model.markdown == expected }
        let saved = await model.flushBody(); XCTAssertTrue(saved)
        XCTAssertNil(model.bodyError); XCTAssertFalse(model.bodyDirty)
        XCTAssertEqual(try Data(contentsOf: target), Data(expected.utf8))
    }
}


extension NativeBodyEditorTests {
    func testAppearanceChangePreservesSourceSelectionViewportAndUndo() async throws {
        let source = "# Synthetic diary\n" + String(repeating: "A quiet paragraph for reading.\n\n", count: 80)
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        window.overrideUserInterfaceStyle = .light
        view.becomeFirstResponder()
        view.selectedRange = NSRange(location: 35, length: 0)
        view.insertText("Changed ")
        try await Task.sleep(nanoseconds: 100_000_000)
        view.setContentOffset(CGPoint(x: 0, y: 240), animated: false)
        let bytes = Data(view.text.utf8), selection = view.selectedRange, offset = view.contentOffset
        let callbacks = state.callbacks.count
        XCTAssertTrue(view.undoManager?.canUndo == true)
        window.overrideUserInterfaceStyle = .dark
        try await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertEqual(Data(view.text.utf8), bytes)
        XCTAssertEqual(view.selectedRange, selection)
        XCTAssertEqual(view.contentOffset.y, offset.y, accuracy: 1)
        XCTAssertEqual(state.callbacks.count, callbacks)
        XCTAssertEqual(view.backgroundColor?.resolvedColor(with: view.traitCollection), RijiPalette.paper.resolvedColor(with: view.traitCollection))
        XCTAssertTrue(view.undoManager?.canUndo == true)
        view.undoSource()
        XCTAssertEqual(Data(view.text.utf8), Data(source.utf8))
    }

    func testAppearanceChangeRefreshesTableBordersWithoutReplacingComposition() async throws {
        let source = "| A | B |\n| --- | --- |\n| first | keep |\n"
        let state = BodyEditorHarness(source)
        let (window, raw) = try await mount(state); defer { window.isHidden = true }
        let view = try XCTUnwrap(raw as? MarkdownTextView)
        try await waitForDocument(view, text: source)
        let grid = try XCTUnwrap(view.subviews.compactMap { $0 as? MarkdownTableView }.first)
        window.overrideUserInterfaceStyle = .light
        grid.beginEditing(row: 1, column: 0)
        let cell = try XCTUnwrap(grid.cellEditor)
        cell.setMarkedText("中文", selectedRange: NSRange(location: 2, length: 0))
        try await Task.sleep(nanoseconds: 50_000_000)
        let text = cell.text, selection = cell.selectedRange, bytes = Data(view.text.utf8)
        window.overrideUserInterfaceStyle = .dark
        try await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertTrue(grid.cellEditor === cell)
        XCTAssertTrue(cell.isFirstResponder); XCTAssertNotNil(cell.markedTextRange)
        XCTAssertEqual(cell.text, text); XCTAssertEqual(cell.selectedRange, selection)
        XCTAssertEqual(Data(view.text.utf8), bytes)
        XCTAssertEqual(cell.layer.borderColor, RijiPalette.border.resolvedColor(with: cell.traitCollection).cgColor)
        cell.unmarkText(); grid.finishEditing()
        XCTAssertTrue(view.text.contains("中文")); XCTAssertTrue(view.text.contains("keep"))
    }
}


extension NativeBodyEditorTests {
    func testOrdinaryBodyDiffRejectsStaleDraftAndSourceBeforeWriting() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("BodyDiff-\(UUID())")
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"))
        let date = ISO8601DateFormatter().date(from: "2026-09-14T08:00:00Z")!
        let model = DiaryViewModel(now: { date }, storageFactory: { store }); await model.start()
        let base = "# Synthetic\r\n## Notes\r\nKeep e\u{301} 🧠\r\n"
        model.updateBody(base); let saved = await model.flushBody(); XCTAssertTrue(saved)
        let file = root.appendingPathComponent("Journals/2026-09-14.md")
        let external = base + "External\r\n", local = base + "Local\r\n"
        try Data(external.utf8).write(to: file)
        model.updateBody(local); _ = await model.flushBody(); await model.reviewBodyConflict()
        let page = TodayDiaryView(model: model)
        let replacement = base + "Reviewed\r\n"
        XCTAssertFalse(page.applyBodyDifference(expected: Data(base.utf8), replacement: Data(replacement.utf8)))
        XCTAssertEqual(Data(model.bodyMergeText.utf8), Data(local.utf8))
        XCTAssertTrue(page.applyBodyDifference(expected: Data(local.utf8), replacement: Data(replacement.utf8)))
        XCTAssertEqual(Data(model.bodyMergeText.utf8), Data(replacement.utf8))
        let changedAgain = external + "Another external change\r\n"
        try Data(changedAgain.utf8).write(to: file)
        let committed = await model.resolveBodyConflict(with: model.bodyMergeText)
        XCTAssertFalse(committed)
        XCTAssertEqual(try Data(contentsOf: file), Data(changedAgain.utf8))
        XCTAssertEqual(Data(model.bodyMergeText.utf8), Data(replacement.utf8))
    }
}

extension NativeBodyEditorTests {
    func testConcealedAISourceFieldsRejectEditsAndKeepFocusWithoutCallbacks() async throws {
        let source = "Ordinary body\n" + (try savedAIReadingFixture()) + "\nTail\n"
        let state = BodyEditorHarness(source)
        let (window, base) = try await mount(state); defer { window.isHidden = true }
        let editor = try XCTUnwrap(base as? MarkdownTextView)
        try await waitForDocument(editor, text: source)
        editor.becomeFirstResponder()
        for prefix in ["> 问题：", "> 讨论时间：", "> 参与导师：", "> 讨论："] {
            let start = (source as NSString).range(of: prefix).location + (prefix as NSString).length
            let range = NSRange(location: start, length: 3)
            XCTAssertEqual(editor.delegate?.textView?(editor, shouldChangeTextIn: range, replacementText: "changed"), false)
            XCTAssertEqual(editor.delegate?.textView?(editor, shouldChangeTextIn: range, replacementText: ""), false)
            XCTAssertEqual(editor.delegate?.textView?(editor, shouldChangeTextIn: NSRange(location: start, length: 0), replacementText: "new"), false)
            editor.selectedRange = range; editor.insertText("changed")
            XCTAssertEqual(editor.selectedRange, range)
            editor.deleteBackward()
            editor.selectedRange = NSRange(location: start + 1, length: 0); editor.insertText("new")
            XCTAssertEqual(Data(editor.textStorage.string.utf8), Data(source.utf8))
            XCTAssertTrue(editor.isFirstResponder)
            let same = (source as NSString).substring(with: range)
            XCTAssertEqual(editor.delegate?.textView?(editor, shouldChangeTextIn: range, replacementText: same), true)
            XCTAssertEqual(editor.delegate?.textView?(editor, shouldChangeTextIn: NSRange(location: start, length: 0), replacementText: ""), true)
        }
        let selection = editor.selectedRange
        TodayDiaryView.commitEditing(in: window.rootViewController!.view)
        editor.becomeFirstResponder()
        XCTAssertEqual(editor.selectedRange, selection)
        XCTAssertTrue(state.callbacks.isEmpty, "Focus, rejected edits and a no-change source command must not report body edits")
        XCTAssertFalse(editor.undoManager?.canUndo == true)
        XCTAssertEqual(Data(state.text.utf8), Data(source.utf8))
        XCTAssertFalse(editor.accessibilityValue?.contains("discussion-synthetic") == true)
    }

    func testSavedAIAdviceReturnDeleteAndUndoKeepConcealedSource() async throws {
        let source = "# Synthetic\n" + (try savedAIReadingFixture()) + "\nTail\n"
        let state = BodyEditorHarness(source)
        let (window, base) = try await mount(state); defer { window.isHidden = true }
        let editor = try XCTUnwrap(base as? MarkdownTextView)
        editor.becomeFirstResponder()
        let advice = (source as NSString).range(of: "Keep the readable advice")
        editor.selectedRange = NSRange(location: NSMaxRange(advice), length: 0)
        editor.insertText("\nNew ordinary paragraph")
        try await Task.sleep(nanoseconds: 30_000_000)
        let afterReturn = editor.text!
        XCTAssertTrue(afterReturn.contains("\nNew ordinary paragraph"))
        XCTAssertEqual(editor.presentation.savedAIResults.count, 1)
        XCTAssertFalse(editor.accessibilityValue?.contains("discussion-synthetic") == true)
        let quote = (editor.text as NSString).range(of: "> Keep the readable advice")
        editor.selectedRange = NSRange(location: quote.location, length: 2); editor.deleteBackward()
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertEqual(editor.presentation.savedAIResults.count, 1)
        XCTAssertFalse(editor.accessibilityValue?.contains("discussion-synthetic") == true)
        XCTAssertTrue(editor.text.contains("\nKeep the readable advice"))
        editor.undoSource()
        XCTAssertEqual(Data(editor.text.utf8), Data(afterReturn.utf8))
        XCTAssertEqual(editor.presentation.savedAIResults.count, 1)
        editor.undoSource()
        XCTAssertEqual(Data(editor.text.utf8), Data(source.utf8))
        XCTAssertEqual(Data(state.text.utf8), Data(source.utf8))
        XCTAssertTrue(isHidden((editor.text as NSString).range(of: "<!-- riji:ai-discussion-result").location, in: editor))
    }

    private struct AISourceDiaryFixture {
        let base: URL
        let target: URL
        let model: DiaryViewModel
        let sources: DiaryAISourceSession
        let window: UIWindow
        let host: UIHostingController<TodayDiaryView>
        let editor: MarkdownTextView
        let source: String
    }

    private func mountAISourceDiary() async throws -> AISourceDiaryFixture {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("NativeAISources-\(UUID())")
        let root = base.appendingPathComponent("riji")
        for folder in ["daily", "templates"] { try FileManager.default.createDirectory(at: root.appendingPathComponent(folder), withIntermediateDirectories: true) }
        try Data("# Template {{date}}\n".utf8).write(to: root.appendingPathComponent("templates/daily.md"))
        let source = "# Synthetic\nReplace\n| A | B |\n| --- | --- |\n| old | keep |\n\n## Notes\n" + (try savedAIReadingFixture()) + "\nTail\n"
        let target = root.appendingPathComponent("daily/2026-09-14.md")
        try Data(source.utf8).write(to: target)
        let date = ISO8601DateFormatter().date(from: "2026-09-14T12:00:00Z")!
        let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base)
        let model = DiaryViewModel(now: { date }, useOriginalCollection: true, vaultStore: browser)
        model.bodyQuietNanoseconds = 3_600_000_000_000; model.bodyCheckpointNanoseconds = 3_600_000_000_000
        await model.start(); await model.selectOriginalFolder(root)
        let sources = DiaryAISourceSession(), window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        let host = AppearedDiaryHost(rootView: TodayDiaryView(model: model, aiSources: sources))
        window.rootViewController = host; window.makeKeyAndVisible(); host.view.layoutIfNeeded()
        try await waitUntil("AI source diary host must appear before opening a sheet") { host.hasAppeared && self.findEditor(host.view) != nil }
        let editor = try XCTUnwrap(findEditor(host.view) as? MarkdownTextView)
        try await waitForDocument(editor, text: source)
        return AISourceDiaryFixture(base: base, target: target, model: model, sources: sources, window: window, host: host, editor: editor, source: source)
    }

    func testAISourceCommandCapturesBodyAndTableCompositionWithoutInvalidatingMatchingSnapshot() async throws {
        for tableInput in [false, true] {
            let f = try await mountAISourceDiary()
            defer { f.host.dismiss(animated: false); f.window.isHidden = true; try? FileManager.default.removeItem(at: f.base) }
            let input: UITextView
            if tableInput {
                let grid = try XCTUnwrap(f.editor.subviews.compactMap { $0 as? MarkdownTableView }.first)
                grid.beginEditing(row: 1, column: 0); input = try XCTUnwrap(grid.cellEditor)
                input.selectedRange = NSRange(location: 0, length: input.text.utf16.count)
            } else {
                input = f.editor; input.becomeFirstResponder(); input.selectedRange = (f.source as NSString).range(of: "Replace")
            }
            input.setMarkedText("组合 🧠", selectedRange: NSRange(location: 2, length: 0))
            XCTAssertNotNil(input.markedTextRange)
            f.sources.open(f.model, committing: { TodayDiaryView.commitEditing(in: f.host.view) })
            XCTAssertNil(input.markedTextRange)
            let captured = try XCTUnwrap(f.sources.document)
            let expected = f.source.replacingOccurrences(of: tableInput ? "old" : "Replace", with: "组合 🧠")
            XCTAssertEqual(Data(captured.markdown.utf8), Data(expected.utf8))
            XCTAssertEqual(captured.day, f.model.selectedDay); XCTAssertEqual(captured.sourceIdentity, f.model.sourceIdentity)
            // Hostless Native verifies the production session; full App UI tests verify presentation.
            XCTAssertNotNil(f.sources.document)
            try await Task.sleep(nanoseconds: 100_000_000)
            XCTAssertEqual(f.sources.document?.id, captured.id, "The commit's pending onChange must keep the matching new snapshot")
            XCTAssertEqual(Data(f.editor.text.utf8), Data(expected.utf8))
            let saved = await f.model.flushBody(); XCTAssertTrue(saved)
            XCTAssertEqual(try Data(contentsOf: f.target), Data(expected.utf8))
            f.sources.document = nil
            XCTAssertNil(f.sources.document)
        }
    }

    func testAISourceSessionInvalidatesOnExternalOriginalRefreshAndReopensNewSnapshot() async throws {
        let f = try await mountAISourceDiary()
        defer { f.host.dismiss(animated: false); f.window.isHidden = true; try? FileManager.default.removeItem(at: f.base) }
        f.editor.becomeFirstResponder()
        let revision = f.model.bodyReplacementRevision
        f.sources.open(f.model, committing: { TodayDiaryView.commitEditing(in: f.host.view) })
        XCTAssertNotNil(f.sources.document)
        XCTAssertFalse(f.model.bodyDirty); XCTAssertEqual(f.model.bodyReplacementRevision, revision)
        let initial = try XCTUnwrap(f.sources.document)
        XCTAssertEqual(Data(initial.markdown.utf8), Data(f.source.utf8))
        let updated = f.source + "External original update 🧠 e\u{301}\n"
        try Data(updated.utf8).write(to: f.target)
        await f.model.refreshOriginalBody()
        try await waitUntil("External source changes must invalidate the snapshot through the mounted Today view") { f.sources.document == nil }
        try await waitForDocument(f.editor, text: updated)
        XCTAssertFalse(f.model.bodyDirty)
        f.sources.open(f.model, committing: { TodayDiaryView.commitEditing(in: f.host.view) })
        XCTAssertNotNil(f.sources.document)
        let refreshed = try XCTUnwrap(f.sources.document)
        XCTAssertEqual(Data(refreshed.markdown.utf8), Data(updated.utf8))
        XCTAssertEqual(try Data(contentsOf: f.target), Data(updated.utf8))
        f.sources.document = nil
        XCTAssertNil(f.sources.document)
    }
}
