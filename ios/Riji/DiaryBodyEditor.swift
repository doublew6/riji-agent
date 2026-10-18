import SwiftUI
import UIKit
import RijiCore

struct DiaryViewport {
    var selection = NSRange(location: 0, length: 0)
    var offset = CGPoint.zero
}

/// Session-only UI state; never serialized into a diary or its recovery records.
@MainActor final class DiaryViewports: ObservableObject {
    var documents: [JournalDay: DiaryViewport] = [:]
}

/// Text storage always contains the original Markdown. Attributes have no serialization role.
struct DiaryBodyEditor: UIViewRepresentable {
    let text: String
    let day: JournalDay
    let editable: Bool
    var foldsSavedAIResults = false
    var onProtectedEdit: () -> Void = {}
    var viewports: DiaryViewports? = nil
    var onDateNavigation: ((JournalDay, DiaryDateDirection) -> Void)? = nil
    var replacementRevision = 0
    var onEditorAvailable: ((MarkdownTextView) -> Void)? = nil
    let onChange: (JournalDay, String) -> Void
    static let protectedEditExplanation = "文档属性和来源标识受保护。请在标题或普通正文中继续编辑。"

    func makeUIView(context: Context) -> UITextView {
        context.coordinator.isUpdating = true
        defer { context.coordinator.isUpdating = false }
        let view = MarkdownTextView()
        view.foldsSavedAIResults = foldsSavedAIResults
        view.delegate = context.coordinator
        view.backgroundColor = RijiPalette.paper
        view.tintColor = RijiPalette.action
        view.textContainerInset = UIEdgeInsets(top: 24, left: 20, bottom: 40, right: 20)
        view.textContainer.lineFragmentPadding = 0
        view.alwaysBounceVertical = true
        view.keyboardDismissMode = .interactive
        view.font = .preferredFont(forTextStyle: .body)
        view.adjustsFontForContentSizeCategory = true
        view.accessibilityIdentifier = "diaryBodyEditor"
        view.accessibilityLabel = "日记正文"
        view.smartQuotesType = .no
        view.smartDashesType = .no
        view.smartInsertDeleteType = .no
        view.text = text
        view.isEditable = editable
        context.coordinator.day = day
        view.onPresentationChange = { [weak view, weak coordinator = context.coordinator] in
            if let view { coordinator?.style(view) }
        }
        context.coordinator.style(view)
        context.coordinator.configureNavigation(view)
        view.restoreViewport(viewports?.documents[day] ?? DiaryViewport())
        let toolbar = UIToolbar()
        toolbar.items = [.init(title: "文末", primaryAction: UIAction { [weak view] _ in
                            guard let view else { return }
                            view.commitTableEditing()
                            view.becomeFirstResponder()
                            view.selectedRange = NSRange(location: view.text.utf16.count, length: 0)
                            view.scrollRangeToVisible(view.selectedRange)
                         }), .init(systemItem: .undo, primaryAction: UIAction { [weak view] _ in view?.undoSource() }),
                         .init(systemItem: .redo, primaryAction: UIAction { [weak view] _ in view?.redoSource() }),
                         .flexibleSpace(), .init(title: "收起键盘", primaryAction: UIAction { [weak view] _ in view?.dismissAllEditing() })]
        toolbar.sizeToFit()
        view.inputAccessoryView = toolbar
        onEditorAvailable?(view)
        return view
    }

    func updateUIView(_ view: UITextView, context: Context) {
        guard (view as? MarkdownTextView)?.applyingTablePatch != true, !context.coordinator.isStyling else { return }
        let changingDay = context.coordinator.day != day
        let changingFolding = context.coordinator.parent.foldsSavedAIResults != foldsSavedAIResults
        let replacingSource = context.coordinator.parent.replacementRevision != replacementRevision
        let incoming = Data(text.utf8)
        let staleEcho = !changingDay && !replacingSource && context.coordinator.pendingLocalSource != nil
            && incoming == context.coordinator.modelSource && incoming != context.coordinator.pendingLocalSource
        if !staleEcho {
            context.coordinator.modelSource = incoming
            if replacingSource || incoming == context.coordinator.pendingLocalSource { context.coordinator.pendingLocalSource = nil }
        }
        context.coordinator.captureViewport(view)
        let viewport = changingDay ? viewports?.documents[day] ?? DiaryViewport() : nil
        if changingDay || !editable { (view as? MarkdownTextView)?.commitTableEditing() }
        context.coordinator.isUpdating = true
        defer { context.coordinator.isUpdating = false }
        if changingDay {
            // Resolve old-day composition using its original callback before binding the next document.
            view.unmarkText()
            context.coordinator.textViewDidChange(view)
            view.resignFirstResponder()
            (view as? MarkdownTextView)?.cancelDateGesture()
            (view as? MarkdownTextView)?.discardTableViews()
            context.coordinator.pendingLocalSource = nil
        }
        context.coordinator.parent = self
        view.isEditable = editable
        (view as? MarkdownTextView)?.foldsSavedAIResults = foldsSavedAIResults
        (view as? MarkdownTextView)?.updateTableAdmission()
        if let view = view as? MarkdownTextView { context.coordinator.configureNavigation(view) }
        guard view.markedTextRange == nil, (view as? MarkdownTextView)?.tableHasMarkedText != true else { return }
        if changingDay || (!staleEcho && !view.text.utf8.elementsEqual(text.utf8)) {
            let selection = view.selectedRange, offset = view.contentOffset
            if !changingDay { (view as? MarkdownTextView)?.discardTableViews() }
            view.undoManager?.removeAllActions()
            view.text = text
            context.coordinator.day = day
            let start = min(selection.location, view.text.utf16.count)
            let end = min(NSMaxRange(selection), view.text.utf16.count)
            view.selectedRange = changingDay ? NSRange(location: 0, length: 0) : NSRange(location: start, length: max(0, end - start))
            context.coordinator.style(view)
            if let viewport {
                view.undoManager?.removeAllActions()
                (view as? MarkdownTextView)?.restoreViewport(viewport)
            }
            else { view.setContentOffset(offset, animated: false) }
        } else if changingFolding { context.coordinator.style(view) }
    }

    static func dismantleUIView(_ view: UITextView, coordinator: Coordinator) {
        (view as? MarkdownTextView)?.commitTableEditing()
        coordinator.captureViewport(view)
        (view as? MarkdownTextView)?.discardTableViews()
    }

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    final class Coordinator: NSObject, UITextViewDelegate {
        var parent: DiaryBodyEditor
        var day: JournalDay?
        var isUpdating = false
        var modelSource: Data
        var pendingLocalSource: Data?
        private var styledSource: Data?
        private var styledLines: NSRange?
        private var styledCategory: UIContentSizeCategory?
        private var styledWidth: CGFloat?
        private var styledFolding: Bool?
        var isStyling = false
        init(parent: DiaryBodyEditor) { self.parent = parent; modelSource = Data(parent.text.utf8) }

        func configureNavigation(_ view: MarkdownTextView) {
            view.dateNavigationEnabled = parent.onDateNavigation != nil
            view.onDateNavigation = { [weak self, weak view] direction in
                guard let self, let view, let day = self.day else { return }
                self.captureViewport(view)
                self.parent.onDateNavigation?(day, direction)
            }
        }

        func captureViewport(_ view: UITextView) {
            guard !isUpdating, !isStyling, let day,
                  (view as? MarkdownTextView)?.hasPendingViewport != true else { return }
            parent.viewports?.documents[day] = DiaryViewport(selection: view.selectedRange, offset: view.contentOffset)
        }

        func scrollViewDidScroll(_ scrollView: UIScrollView) {
            if let view = scrollView as? UITextView { captureViewport(view) }
        }

        func textView(_ view: UITextView, shouldChangeTextIn range: NSRange, replacementText text: String) -> Bool {
            let allowed = view.isEditable && BodyMarkdown.permitsEdit(in: view.text, range: range, replacement: text)
                && ((view as? MarkdownTextView)?.permitsConcealedEdit(in: range, replacement: text) ?? true)
            if !allowed {
                parent.onProtectedEdit()
                UIAccessibility.post(notification: .announcement, argument: DiaryBodyEditor.protectedEditExplanation)
            }
            return allowed
        }

        func textViewDidChange(_ view: UITextView) {
            pendingLocalSource = Data((view.text ?? "").utf8)
            // Marked text belongs to the input method until committed; no formatting interrupts it.
            if view.markedTextRange == nil { style(view) }
            guard let day else { return }
            parent.onChange(day, view.text)
        }

        func textViewDidBeginEditing(_ view: UITextView) {
            style(view)
        }

        func textViewDidEndEditing(_ view: UITextView) {
            view.unmarkText()
            let bytes = Data((view.text ?? "").utf8)
            if bytes != (pendingLocalSource ?? modelSource) { textViewDidChange(view) }
            else { style(view) }
        }

        func textViewDidChangeSelection(_ view: UITextView) {
            guard (view as? MarkdownTextView)?.applyingTablePatch != true else { return }
            if view.markedTextRange == nil { style(view) }
            captureViewport(view)
        }

        func style(_ view: UITextView) {
            guard let view = view as? MarkdownTextView, view.markedTextRange == nil, !isStyling else { return }
            let source = view.text ?? "", bytes = Data(source.utf8)
            if bytes != styledSource { view.presentation = MarkdownPresentation(source) }
            let active = view.isFirstResponder ? MarkdownPresentation.activeLines(in: source, selection: view.selectedRange, tables: view.presentation.tables) : nil
            let category = view.traitCollection.preferredContentSizeCategory
            let width = view.bounds.width - view.textContainerInset.left - view.textContainerInset.right
            let resizedTable = !view.presentation.tables.isEmpty && styledWidth != width
            guard bytes != styledSource || active != styledLines || category != styledCategory || resizedTable || styledFolding != view.foldsSavedAIResults else { return }
            isStyling = true
            defer { isStyling = false }
            styledSource = bytes; styledLines = active; styledCategory = category; styledWidth = width
            styledFolding = view.foldsSavedAIResults
            let selection = view.selectedRange, offset = view.contentOffset
            let undo = view.undoManager
            undo?.disableUndoRegistration()
            view.textStorage.beginEditing()
            view.applyPresentation(active: active)
            view.textStorage.endEditing()
            // Custom glyph attributes must invalidate shaping, not just color/display.
            view.layoutManager.invalidateGlyphs(forCharacterRange: NSRange(location: 0, length: view.textStorage.length), changeInLength: 0, actualCharacterRange: nil)
            undo?.enableUndoRegistration()
            // Resolve TextKit's new document height before restoring the visible position.
            // Otherwise UIKit clamps the offset against a partially laid-out document.
            view.layoutManager.ensureLayout(for: view.textContainer)
            view.layoutIfNeeded()
            view.selectedRange = selection
            view.setContentOffset(offset, animated: false)
            view.resetTypingAttributes()
        }
    }
}
