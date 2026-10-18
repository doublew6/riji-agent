import UIKit
import RijiCore

private extension NSAttributedString.Key {
    static let markdownHidden = Self("RijiMarkdownHidden")
    static let markdownTableRow = Self("RijiMarkdownTableRow")
    static let markdownMarker = Self("RijiMarkdownMarker")
}

/// TextKit renders source glyphs without replacing text storage or remapping edit offsets.
final class MarkdownLayoutManager: NSLayoutManager, NSLayoutManagerDelegate {
    override init() { super.init(); delegate = self }
    required init?(coder: NSCoder) { super.init(coder: coder); delegate = self }

    func layoutManager(_ layoutManager: NSLayoutManager, shouldGenerateGlyphs glyphs: UnsafePointer<CGGlyph>,
                       properties props: UnsafePointer<NSLayoutManager.GlyphProperty>, characterIndexes: UnsafePointer<Int>,
                       font: UIFont, forGlyphRange range: NSRange) -> Int {
        guard let storage = textStorage else { return 0 }
        var properties = Array(UnsafeBufferPointer(start: props, count: range.length))
        for index in properties.indices {
            let character = characterIndexes[index]
            if storage.attribute(.markdownTableRow, at: character, effectiveRange: nil) != nil { properties[index] = .controlCharacter }
            else if storage.attribute(.markdownMarker, at: character, effectiveRange: nil) != nil { properties[index] = .controlCharacter }
            else if storage.attribute(.markdownHidden, at: character, effectiveRange: nil) != nil { properties[index] = .null }
        }
        setGlyphs(glyphs, properties: &properties, characterIndexes: characterIndexes, font: font, forGlyphRange: range)
        return range.length
    }

    func layoutManager(_ layoutManager: NSLayoutManager, shouldUse action: NSLayoutManager.ControlCharacterAction,
                       forControlCharacterAt index: Int) -> NSLayoutManager.ControlCharacterAction {
        (textStorage?.attribute(.markdownMarker, at: index, effectiveRange: nil) != nil || textStorage?.attribute(.markdownTableRow, at: index, effectiveRange: nil) != nil) ? .whitespace : action
    }

    func layoutManager(_ layoutManager: NSLayoutManager, boundingBoxForControlGlyphAt glyphIndex: Int,
                       for textContainer: NSTextContainer, proposedLineFragment: CGRect,
                       glyphPosition: CGPoint, characterIndex: Int) -> CGRect {
        if let height = textStorage?.attribute(.markdownTableRow, at: characterIndex, effectiveRange: nil) as? CGFloat {
            return CGRect(x: 0, y: 0, width: max(1, textContainer.size.width - 2 * textContainer.lineFragmentPadding - 1), height: height)
        }
        let font = textStorage?.attribute(.font, at: characterIndex, effectiveRange: nil) as? UIFont ?? .preferredFont(forTextStyle: .body)
        return CGRect(x: 0, y: -font.ascender, width: font.pointSize * 1.5, height: font.lineHeight)
    }

    func markerRect(at character: Int) -> CGRect {
        let glyph = glyphIndexForCharacter(at: character)
        let fragment = lineFragmentRect(forGlyphAt: glyph, effectiveRange: nil)
        let position = location(forGlyphAt: glyph)
        let font = textStorage?.attribute(.font, at: character, effectiveRange: nil) as? UIFont ?? .preferredFont(forTextStyle: .body)
        let size = font.pointSize
        return CGRect(x: fragment.minX + position.x, y: fragment.minY + position.y - font.capHeight,
                      width: size, height: size)
    }

    override func drawGlyphs(forGlyphRange glyphsToShow: NSRange, at origin: CGPoint) {
        super.drawGlyphs(forGlyphRange: glyphsToShow, at: origin)
        guard let storage = textStorage else { return }
        let characters = characterRange(forGlyphRange: glyphsToShow, actualGlyphRange: nil)
        storage.enumerateAttribute(.markdownMarker, in: characters) { value, range, _ in
            guard let kind = value as? String else { return }
            let rect = self.markerRect(at: range.location).offsetBy(dx: origin.x, dy: origin.y)
            self.drawMarker(kind, rect)
        }
    }

    private func drawMarker(_ kind: String, _ rect: CGRect) {
        RijiPalette.secondary.setStroke(); RijiPalette.text.setFill()
        if kind == "rule" {
            RijiPalette.border.setStroke()
            let line = UIBezierPath(); line.move(to: CGPoint(x: rect.minX, y: rect.midY))
            line.addLine(to: CGPoint(x: rect.minX + (textContainers.first?.size.width ?? rect.width), y: rect.midY))
            line.lineWidth = 1; line.stroke()
        } else if kind == "bullet" {
            UIBezierPath(ovalIn: CGRect(x: rect.midX - 2.5, y: rect.midY - 2.5, width: 5, height: 5)).fill()
        } else if kind == "quote" {
            RijiPalette.secondary.setFill()
            UIBezierPath(roundedRect: CGRect(x: rect.midX - 1.5, y: rect.minY - 2, width: 3, height: rect.height + 4), cornerRadius: 1.5).fill()
        } else {
            let outline = UIBezierPath(roundedRect: rect.insetBy(dx: 1, dy: 1), cornerRadius: 3)
            outline.lineWidth = 1.4; outline.stroke()
            if kind == "checked" {
                RijiPalette.action.setStroke()
                let check = UIBezierPath(); check.move(to: CGPoint(x: rect.minX + rect.width * 0.23, y: rect.midY))
                check.addLine(to: CGPoint(x: rect.minX + rect.width * 0.43, y: rect.minY + rect.height * 0.7))
                check.addLine(to: CGPoint(x: rect.minX + rect.width * 0.79, y: rect.minY + rect.height * 0.3))
                check.lineWidth = 2; check.lineCapStyle = .round; check.stroke()
            }
        }
    }
}

final class MarkdownTextView: UITextView, UIGestureRecognizerDelegate {
    var presentation = MarkdownPresentation("")
    var activeLines: NSRange?
    var onPresentationChange: (() -> Void)?
    var onDateNavigation: ((DiaryDateDirection) -> Void)?
    var dateNavigationEnabled = false
    var foldsSavedAIResults = false
    override var accessibilityValue: String? {
        get {
            let source = text ?? "", literal = super.accessibilityValue ?? source
            guard foldsSavedAIResults else { return literal }
            let readable = presentation.readableMarkdown(in: source)
            return readable.utf8.elementsEqual(source.utf8) ? literal : readable
        }
        set { super.accessibilityValue = newValue }
    }

    func permitsConcealedEdit(in range: NSRange, replacement: String) -> Bool {
        guard foldsSavedAIResults else { return true }
        let source = text ?? "", original = source as NSString
        guard range.location >= 0, range.length >= 0, NSMaxRange(range) <= original.length else { return false }
        if original.substring(with: range).utf8.elementsEqual(replacement.utf8) { return true }
        let display = presentation.matchesSource(source) ? presentation : MarkdownPresentation(source)
        return !display.savedAIResults.flatMap(\.concealedRanges).contains { concealed in
            range.length == 0 ? range.location >= concealed.location && range.location < NSMaxRange(concealed)
                : NSIntersectionRange(range, concealed).length > 0
        }
    }

    override func insertText(_ text: String) {
        let range = markedTextRange.map {
            NSRange(location: offset(from: beginningOfDocument, to: $0.start), length: offset(from: $0.start, to: $0.end))
        } ?? selectedRange
        guard admitsConcealedKeyInput(in: range, replacement: text) else { return }
        super.insertText(text)
    }

    override func deleteBackward() {
        let selection = selectedRange
        let range = selection.length > 0 || selection.location == 0 ? selection
            : (text as NSString).rangeOfComposedCharacterSequence(at: selection.location - 1)
        guard admitsConcealedKeyInput(in: range, replacement: "") else { return }
        super.deleteBackward()
    }

    private func admitsConcealedKeyInput(in range: NSRange, replacement: String) -> Bool {
        guard !permitsConcealedEdit(in: range, replacement: replacement) else { return true }
        // UIKeyInput entry points can bypass UITextViewDelegate. Keep the same rejection
        // feedback, while allowing ordinary typing to retain UIKit's original path.
        _ = delegate?.textView?(self, shouldChangeTextIn: range, replacementText: replacement)
        return false
    }
    private var tableViews: [(MarkdownPresentation.Table, MarkdownTableView)] = []
    private var presentedWidth: CGFloat = 0
    private var laidOutSize = CGSize.zero
    private var pendingViewport: DiaryViewport?
    var hasPendingViewport: Bool { pendingViewport != nil }
    var tableIsEditing: Bool { tableViews.contains { $0.1.cellEditor?.isFirstResponder == true } }
    var tableHasMarkedText: Bool { tableViews.contains { $0.1.cellEditor?.markedTextRange != nil } }
    func undoSource() { performSourceUndo(redo: false) }
    func redoSource() { performSourceUndo(redo: true) }
    private func performSourceUndo(redo: Bool) {
        guard isEditable else { return }
        for (_, grid) in tableViews { guard grid.finishEditing(resign: false) else { return } }
        applyingTablePatch = true
        defer { applyingTablePatch = false }
        if redo { undoManager?.redo() } else { undoManager?.undo() }
    }
    private(set) var applyingTablePatch = false
    var switchingTableCell = false
    override var canBecomeFirstResponder: Bool { !applyingTablePatch && !switchingTableCell && super.canBecomeFirstResponder }

    func refreshPalette() {
        for (_, grid) in tableViews { grid.refreshPalette() }
        setNeedsDisplay()
    }

    func commitTableEditing() { for (_, grid) in tableViews { grid.finishEditing() } }
    func finishOtherTableEditors(except active: MarkdownTableView) -> MarkdownTableView? {
        guard let index = tableViews.firstIndex(where: { $0.1 === active }) else { return nil }
        for (_, grid) in tableViews where grid !== active { guard grid.finishEditing() else { return nil } }
        // Committing an earlier table's composition can move this table's source offset.
        return tableViews.indices.contains(index) ? tableViews[index].1 : nil
    }
    func discardTableViews() {
        for (_, grid) in tableViews { grid.invalidate(); grid.removeFromSuperview() }
        tableViews = []
    }
    func updateTableAdmission() { for (_, grid) in tableViews { grid.setEditable(isEditable) } }
    func dismissAllEditing() { commitTableEditing(); resignFirstResponder() }

    func applyTableEdit(_ grid: MarkdownTableView, operation: MarkdownTableEditing.Operation) -> Bool {
        guard isEditable, !applyingTablePatch, tableViews.contains(where: { $0.1 === grid }),
              grid.source.utf8.elementsEqual((text ?? "").utf8) else { grid.showFailure(); return false }
        let additionalCells: Int
        switch operation {
        case .cell: additionalCells = 0
        case .insertRow: additionalCells = grid.table.alignments.count
        case .appendColumn: additionalCells = grid.table.rows.count
        }
        let allocatedCells = tableViews.reduce(0) { $0 + $1.0.rows.count * $1.0.alignments.count }
        guard allocatedCells + additionalCells <= MarkdownTableEditing.maximumCells else { grid.showFailure(); return false }
        do {
            let patch = try MarkdownTableEditing.patch(source: text, table: grid.table, operation: operation)
            let next = try patch.applying(to: text)
            guard next.utf8.elementsEqual(text.utf8) == false else { grid.clearFailure(); return true }
            guard delegate?.textView?(self, shouldChangeTextIn: patch.range, replacementText: patch.replacement) ?? true else { return false }
            replaceTableRange(patch.range, with: patch.replacement)
            grid.clearFailure()
            return true
        } catch { grid.showFailure(); return false }
    }

    private func replaceTableRange(_ range: NSRange, with replacement: String) {
        guard isEditable, BodyMarkdown.permitsEdit(in: text, range: range, replacement: replacement) else { return }
        applyingTablePatch = true
        defer { applyingTablePatch = false }
        let offset = contentOffset
        if selectedRange != range { selectedRange = range }
        insertText(replacement)
        setContentOffset(offset, animated: false)
    }

    func revealTableEditor(_ grid: MarkdownTableView) {
        guard let editor = grid.cellEditor else { return }
        grid.scrollRectToVisible(editor.frame, animated: false)
        layoutIfNeeded()
        let caret = editor.selectedTextRange.map { editor.caretRect(for: $0.end) } ?? editor.bounds
        scrollRectToVisible(editor.convert(caret, to: self).insetBy(dx: -12, dy: -24), animated: false)
    }
    #if DEBUG
    private static let exposesTestViewport: Bool = {
        let environment = ProcessInfo.processInfo.environment
        return environment["RIJI_TEST_NAVIGATION"] == "1"
            && environment["RIJI_TEST_CONTAINER"].flatMap(UUID.init(uuidString:)) != nil
    }()
    override var accessibilityLabel: String? {
        get {
            guard Self.exposesTestViewport else { return super.accessibilityLabel }
            return "日记正文 · y=\(Int(contentOffset.y.rounded())) · caret=\(selectedRange.location)"
        }
        set { super.accessibilityLabel = newValue }
    }
    #endif
    private lazy var taskTap = UITapGestureRecognizer(target: self, action: #selector(handleTaskTap(_:)))
    private(set) lazy var datePan = UIPanGestureRecognizer(target: self, action: #selector(handleDatePan(_:)))

    init() {
        let storage = NSTextStorage(), layout = MarkdownLayoutManager()
        let container = NSTextContainer(size: CGSize(width: 0, height: CGFloat.greatestFiniteMagnitude))
        container.widthTracksTextView = true
        storage.addLayoutManager(layout); layout.addTextContainer(container)
        super.init(frame: .zero, textContainer: container)
        taskTap.delegate = self; addGestureRecognizer(taskTap)
        datePan.maximumNumberOfTouches = 1
        datePan.delegate = self; datePan.cancelsTouchesInView = false
        addGestureRecognizer(datePan)
        NotificationCenter.default.addObserver(self, selector: #selector(keyboardFrameDidChange), name: UIResponder.keyboardDidChangeFrameNotification, object: nil)
        tintColor = RijiPalette.action
        registerForTraitChanges([UITraitUserInterfaceStyle.self]) { (view: MarkdownTextView, _: UITraitCollection) in
            // Dynamic text/UIColors update themselves. Refresh only static layer colors:
            // replacing text or rebuilding a grid would disturb selection, undo or IME.
            view.refreshPalette()
        }
        registerForTraitChanges([UITraitPreferredContentSizeCategory.self]) { (view: MarkdownTextView, _: UITraitCollection) in
            view.onPresentationChange?()
        }
    }
    required init?(coder: NSCoder) { fatalError("Use init()") }
    deinit { NotificationCenter.default.removeObserver(self) }

    @objc private func keyboardFrameDidChange(_ notification: Notification) {
        guard tableIsEditing, let window else { return }
        window.layoutIfNeeded()
        if let grid = tableViews.first(where: { $0.1.cellEditor?.isFirstResponder == true })?.1 { revealTableEditor(grid) }
    }

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
        if gestureRecognizer === datePan { return admitsDateGesture(at: touch.location(in: self)) }
        if gestureRecognizer === taskTap { return isEditable && markedTextRange == nil && task(at: touch.location(in: self)) != nil }
        // Cell controls own taps/selection; the diary still owns vertical panning.
        if gestureRecognizer !== panGestureRecognizer,
           tableViews.contains(where: { touch.view?.isDescendant(of: $0.1) == true }) { return false }
        return true
    }

    override func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
        if gestureRecognizer === datePan {
            // UIKit has already crossed its pan threshold; inspect direction here.
            // Completion below still requires a deliberate 80-point movement.
            return admitsInitialDatePan(datePan.translation(in: self), at: datePan.location(in: self))
        }
        return super.gestureRecognizerShouldBegin(gestureRecognizer)
    }

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool {
        (gestureRecognizer === datePan && other === panGestureRecognizer)
            || (other === datePan && gestureRecognizer === panGestureRecognizer)
    }

    static func dateDirection(for translation: CGPoint, minimum: CGFloat = 80) -> DiaryDateDirection? {
        guard abs(translation.x) >= minimum, abs(translation.y) <= abs(translation.x) * 0.3 else { return nil }
        return translation.x > 0 ? .previous : .next
    }

    func admitsInitialDatePan(_ translation: CGPoint, at point: CGPoint) -> Bool {
        admitsDateGesture(at: point) && Self.dateDirection(for: translation, minimum: 0.1) != nil
    }

    func admitsDateGesture(at point: CGPoint) -> Bool {
        guard dateNavigationEnabled, !isFirstResponder, !tableIsEditing, !tableHasMarkedText, markedTextRange == nil, selectedRange.length == 0 else { return false }
        let source = (text ?? "") as NSString
        guard source.length > 0 else { return true }
        let local = CGPoint(x: point.x - textContainerInset.left, y: point.y - textContainerInset.top)
        let index = min(layoutManager.characterIndex(for: local, in: textContainer, fractionOfDistanceBetweenInsertionPoints: nil), source.length - 1)
        if tableViews.contains(where: { table, grid in
            let glyphs = layoutManager.glyphRange(forCharacterRange: table.range, actualCharacterRange: nil)
            let sourceBounds = layoutManager.boundingRect(forGlyphRange: glyphs, in: textContainer)
                .offsetBy(dx: textContainerInset.left, dy: textContainerInset.top)
            return grid.frame.union(sourceBounds).contains(point)
        }) { return false }
        // Source-revealed tables also keep ordinary selection and horizontal interaction.
        return !source.substring(with: source.lineRange(for: NSRange(location: index, length: 0))).contains("|")
    }

    @objc func handleDatePan(_ gesture: UIPanGestureRecognizer) {
        guard gesture.state == .ended, dateNavigationEnabled, !isFirstResponder, !tableIsEditing, !tableHasMarkedText,
              markedTextRange == nil, selectedRange.length == 0,
              let direction = Self.dateDirection(for: gesture.translation(in: self)) else { return }
        onDateNavigation?(direction)
    }

    func cancelDateGesture() {
        datePan.isEnabled = false; datePan.isEnabled = true
    }

    func restoreViewport(_ viewport: DiaryViewport) {
        pendingViewport = viewport
        setNeedsLayout()
        layoutIfNeeded()
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        let width = bounds.width - textContainerInset.left - textContainerInset.right
        if width > 0, abs(presentedWidth - width) > 0.5, markedTextRange == nil {
            presentedWidth = width
            // The owner preserves selection, undo and viewport while restyling.
            // Ordinary paragraphs do not need a presentation change on resize.
            if !presentation.tables.isEmpty { onPresentationChange?() }
        }
        positionTables()
        let resized = laidOutSize != bounds.size
        laidOutSize = bounds.size
        if resized, bounds.width > 0, bounds.height > 0,
           let grid = tableViews.first(where: { $0.1.cellEditor?.isFirstResponder == true })?.1 {
            revealTableEditor(grid)
        }
        guard let viewport = pendingViewport, bounds.width > 0, bounds.height > 0 else { return }
        layoutManager.ensureLayout(for: textContainer)
        let count = text.utf16.count, start = min(viewport.selection.location, count)
        selectedRange = NSRange(location: start, length: max(0, min(NSMaxRange(viewport.selection), count) - start))
        let minimumY = -adjustedContentInset.top
        let maximumY = max(minimumY, contentSize.height - bounds.height + adjustedContentInset.bottom)
        let maximumX = max(-adjustedContentInset.left, contentSize.width - bounds.width + adjustedContentInset.right)
        let offset = CGPoint(x: min(max(-adjustedContentInset.left, viewport.offset.x), maximumX),
                             y: min(max(minimumY, viewport.offset.y), maximumY))
        setContentOffset(offset, animated: false)
        pendingViewport = nil
        delegate?.scrollViewDidScroll?(self)
    }

    private func task(at point: CGPoint) -> MarkdownPresentation.Marker? {
        guard let layout = layoutManager as? MarkdownLayoutManager else { return nil }
        let local = CGPoint(x: point.x - textContainerInset.left, y: point.y - textContainerInset.top)
        let visible = layout.glyphRange(forBoundingRect: bounds.offsetBy(dx: -textContainerInset.left, dy: -textContainerInset.top), in: textContainer)
        let characters = layout.characterRange(forGlyphRange: visible, actualGlyphRange: nil)
        return presentation.markers.first { marker in
            guard marker.taskState != nil, NSLocationInRange(marker.range.location, characters),
                  activeLines.map({ NSIntersectionRange($0, marker.range).length == 0 }) ?? true else { return false }
            return layout.markerRect(at: marker.range.location).insetBy(dx: -5, dy: -6).contains(local)
        }
    }

    @objc private func handleTaskTap(_ gesture: UITapGestureRecognizer) {
        guard let marker = task(at: gesture.location(in: self)) else { return }
        toggleTask(marker)
    }

    /// Uses the ordinary native edit admission, undo stack, and body-save callback.
    func toggleTask(_ marker: MarkdownPresentation.Marker) {
        guard isEditable, markedTextRange == nil, let index = marker.taskState,
              case .task(let checked) = marker.kind else { return }
        let range = NSRange(location: index, length: 1), replacement = checked ? " " : "x"
        guard delegate?.textView?(self, shouldChangeTextIn: range, replacementText: replacement) ?? true else { return }
        let selection = selectedRange, offset = contentOffset
        selectedRange = range; insertText(replacement)
        selectedRange = selection; setContentOffset(offset, animated: false)
        onPresentationChange?()
    }

    func applyPresentation(active: NSRange?) {
        activeLines = active
        let storage = textStorage, full = NSRange(location: 0, length: storage.length)
        let body = UIFont.preferredFont(forTextStyle: .body, compatibleWith: traitCollection)
        let paragraph = NSMutableParagraphStyle(); paragraph.lineSpacing = 3
        storage.setAttributes([.font: body, .foregroundColor: RijiPalette.text, .paragraphStyle: paragraph], range: full)
        for span in presentation.spans { apply(span) }
        var hidden = presentation.hidden
        if let active { hidden.remove(integersIn: active.location..<NSMaxRange(active)) }
        for range in hidden.rangeView { storage.addAttribute(.markdownHidden, value: true, range: NSRange(range)) }
        for marker in presentation.markers where hidden.contains(marker.range.location) {
            let kind: String
            switch marker.kind { case .bullet: kind = "bullet"; case .quote: kind = "quote"; case .rule: kind = "rule"; case .task(let checked): kind = checked ? "checked" : "unchecked" }
            storage.addAttribute(.markdownMarker, value: kind, range: NSRange(location: marker.range.location, length: 1))
        }
        projectTables(body: body, active: active)
        for range in BodyMarkdown.protectedRanges(in: text) {
            storage.addAttributes([.foregroundColor: RijiPalette.secondary,
                                   .font: UIFont.preferredFont(forTextStyle: .caption1, compatibleWith: traitCollection)], range: range)
        }
        // Saved machine lines stay folded even when selected. Apply this last so active-line
        // revelation and the protected-marker caption font cannot restore JSON-sized layout.
        if foldsSavedAIResults && presentation.matchesSource(text) {
            let collapsed = NSMutableParagraphStyle()
            collapsed.minimumLineHeight = 0.1; collapsed.maximumLineHeight = 0.1
            for range in presentation.savedAIResults.flatMap(\.concealedRanges) {
                storage.removeAttribute(.markdownMarker, range: range)
                storage.addAttributes([.markdownHidden: true, .foregroundColor: UIColor.clear,
                                       .font: UIFont.systemFont(ofSize: 0.1), .paragraphStyle: collapsed], range: range)
            }
        }
    }

    private func projectTables(body: UIFont, active: NSRange?) {
        let width = max(1, bounds.width - textContainerInset.left - textContainerInset.right)
        guard width > 1 else { return }
        let oldViews = tableViews
        tableViews = []
        var remainingCells = MarkdownTableEditing.maximumCells
        for table in presentation.tables where active.map({ NSIntersectionRange($0, table.range).length == 0 }) ?? true {
            let cells = table.rows.count * table.alignments.count
            guard cells <= remainingCells else { continue }
            remainingCells -= cells
            let grid = oldViews.first { $0.0.range.location == table.range.location }?.1 ?? MarkdownTableView(owner: self)
            grid.configure(table: table, source: textStorage, font: body, width: width)
            // Reserve each original row in TextKit. Source characters and offsets never change.
            for (index, row) in table.rows.enumerated() {
                let paragraph = NSMutableParagraphStyle()
                paragraph.minimumLineHeight = grid.heights[index]; paragraph.maximumLineHeight = grid.heights[index]
                textStorage.addAttributes([.markdownHidden: true, .paragraphStyle: paragraph], range: row.range)
                textStorage.addAttribute(.markdownTableRow, value: grid.heights[index], range: NSRange(location: row.range.location, length: 1))
            }
            let collapsed = NSMutableParagraphStyle(); collapsed.minimumLineHeight = 0.1; collapsed.maximumLineHeight = 0.1
            textStorage.addAttributes([.markdownHidden: true, .font: UIFont.systemFont(ofSize: 0.1), .paragraphStyle: collapsed], range: (text as NSString).lineRange(for: table.delimiter))
            tableViews.append((table, grid)); addSubview(grid)
        }
        for (_, grid) in oldViews where !tableViews.contains(where: { $0.1 === grid }) {
            grid.invalidate(); grid.removeFromSuperview()
        }
        setNeedsLayout()
    }

    private func positionTables() {
        guard !tableViews.isEmpty else { return }
        layoutManager.ensureLayout(for: textContainer)
        for (table, grid) in tableViews {
            let glyph = layoutManager.glyphIndexForCharacter(at: table.range.location)
            let line = layoutManager.lineFragmentRect(forGlyphAt: glyph, effectiveRange: nil)
            grid.frame = CGRect(x: textContainerInset.left, y: line.minY + textContainerInset.top,
                                width: max(1, bounds.width - textContainerInset.left - textContainerInset.right), height: grid.heights.reduce(0, +))
            grid.positionFailure()
        }
    }

    func resetTypingAttributes() {
        let paragraph = NSMutableParagraphStyle(); paragraph.lineSpacing = 3
        typingAttributes = [.font: UIFont.preferredFont(forTextStyle: .body, compatibleWith: traitCollection),
                            .foregroundColor: RijiPalette.text, .paragraphStyle: paragraph]
    }

    private func apply(_ span: MarkdownPresentation.Span) {
        let storage = textStorage
        switch span.style {
        case .heading(let level):
            let style: UIFont.TextStyle = level == 1 ? .title1 : level == 2 ? .title3 : .headline
            let base = UIFont.preferredFont(forTextStyle: style, compatibleWith: traitCollection)
            let font = UIFont(descriptor: base.fontDescriptor.withSymbolicTraits(.traitBold) ?? base.fontDescriptor, size: 0)
            storage.addAttribute(.font, value: font, range: span.range)
            let paragraph = NSMutableParagraphStyle(); paragraph.paragraphSpacingBefore = 8; paragraph.paragraphSpacing = 4; paragraph.lineSpacing = 3
            storage.addAttribute(.paragraphStyle, value: paragraph, range: span.range)
            if (text as NSString).substring(with: span.range).contains("AI") { storage.addAttribute(.foregroundColor, value: RijiPalette.secondary, range: span.range) }
        case .bold, .italic:
            storage.enumerateAttribute(.font, in: span.range) { value, range, _ in
                guard let font = value as? UIFont else { return }
                let traits = font.fontDescriptor.symbolicTraits.union(span.style == .bold ? .traitBold : .traitItalic)
                storage.addAttribute(.font, value: UIFont(descriptor: font.fontDescriptor.withSymbolicTraits(traits) ?? font.fontDescriptor, size: 0), range: range)
            }
        case .strike: storage.addAttribute(.strikethroughStyle, value: NSUnderlineStyle.single.rawValue, range: span.range)
        case .code:
            let size = UIFont.preferredFont(forTextStyle: .body, compatibleWith: traitCollection).pointSize
            storage.addAttributes([.font: UIFont.monospacedSystemFont(ofSize: size * 0.9, weight: .regular), .backgroundColor: RijiPalette.warmSurface], range: span.range)
        case .link: storage.addAttributes([.foregroundColor: RijiPalette.action, .underlineStyle: NSUnderlineStyle.single.rawValue], range: span.range)
        }
    }
}

/// One persistent cell editor sits over an otherwise display-only native grid.
final class MarkdownTableView: UIScrollView, UITextViewDelegate {
    private(set) var table: MarkdownPresentation.Table!
    private(set) var source = ""
    private(set) var heights: [CGFloat] = []
    private(set) var cellEditor: MarkdownTableCellEditor?
    private weak var owner: MarkdownTextView?
    private let canvas = UIView()
    private var cells: [[UIView]] = []
    private var cellWidth: CGFloat = 0
    private var row = 0, column = 0
    private var committing = false
    private var valid = true
    private var cellValue = ""
    private var storedCell = ""
    private var cellContentOffset = 0
    private var cellTrailingPadding = 0
    private let failureLabel = UILabel()
    private let addRow = UIButton(type: .system), addColumn = UIButton(type: .system)

    init(owner: MarkdownTextView) {
        self.owner = owner
        super.init(frame: .zero)
        backgroundColor = RijiPalette.paper
        showsHorizontalScrollIndicator = true; isDirectionalLockEnabled = true
        addSubview(canvas)
        accessibilityIdentifier = "markdownTable"
        addRow.setImage(UIImage(systemName: "plus"), for: .normal)
        addRow.accessibilityLabel = "添加表格行"; addRow.accessibilityIdentifier = "tableAddRow"
        addColumn.setImage(UIImage(systemName: "plus"), for: .normal)
        addColumn.accessibilityLabel = "添加表格列"; addColumn.accessibilityIdentifier = "tableAddColumn"
        addRow.addTarget(self, action: #selector(appendRow), for: .touchUpInside)
        addColumn.addTarget(self, action: #selector(appendColumn), for: .touchUpInside)
        failureLabel.text = "无法安全修改表格结构，已保留原文。"
        failureLabel.font = .preferredFont(forTextStyle: .caption1); failureLabel.textColor = RijiPalette.error
        failureLabel.numberOfLines = 2; failureLabel.isHidden = true
        failureLabel.backgroundColor = RijiPalette.warmSurface
        failureLabel.accessibilityIdentifier = "tableEditError"
        canvas.addSubview(addRow); canvas.addSubview(addColumn); canvas.addSubview(failureLabel)
        registerForTraitChanges([UITraitUserInterfaceStyle.self]) { (view: MarkdownTableView, _: UITraitCollection) in
            // Resolve static CGColors after this grid has received its new traits.
            view.refreshPalette()
        }
    }
    required init?(coder: NSCoder) { fatalError("Use init") }

    func configure(table: MarkdownPresentation.Table, source: NSAttributedString, font: UIFont, width: CGFloat) {
        self.table = table; self.source = source.string
        cellWidth = max(font.pointSize * 5.5, (width - 44) / CGFloat(table.alignments.count))
        for line in cells { for cell in line { cell.removeFromSuperview() } }; cells = []
        heights = []
        var y: CGFloat = 0
        for (r, line) in table.rows.enumerated() {
            let values = line.cells.map { Self.cellText(source, range: $0) }
            var height = max(font.lineHeight + 16, values.map { $0.boundingRect(with: CGSize(width: cellWidth - 16, height: .greatestFiniteMagnitude), options: [.usesLineFragmentOrigin, .usesFontLeading], context: nil).height + 16 }.max() ?? 0).rounded(.up)
            if r == row, let editor = cellEditor { height = max(height, editor.sizeThatFits(CGSize(width: cellWidth, height: .greatestFiniteMagnitude)).height) }
            var views: [UIView] = []
            for (c, value) in values.enumerated() {
                let cell = UIView(frame: CGRect(x: CGFloat(c) * cellWidth, y: y, width: cellWidth, height: height))
                cell.layer.borderWidth = 0.5; cell.layer.borderColor = RijiPalette.border.resolvedColor(with: traitCollection).cgColor
                cell.backgroundColor = r == 0 ? RijiPalette.warmSurface : RijiPalette.paper
                let label = UILabel(frame: cell.bounds.insetBy(dx: 8, dy: 8))
                label.numberOfLines = 0; label.font = font; label.attributedText = value
                label.textAlignment = alignment(c)
                label.isAccessibilityElement = true; label.accessibilityLabel = value.length == 0 ? "空白单元格" : value.string
                label.isHidden = cellEditor != nil && r == row && c == column
                cell.addSubview(label); canvas.addSubview(cell); views.append(cell)
                cell.tag = r * 64 + c
                cell.addGestureRecognizer(UITapGestureRecognizer(target: self, action: #selector(tapCell(_:))))
            }
            cells.append(views); heights.append(height); y += height
        }
        let gridWidth = CGFloat(table.alignments.count) * cellWidth
        canvas.frame = CGRect(x: 0, y: 0, width: gridWidth + 44, height: y + 44)
        addRow.frame = CGRect(x: 0, y: y, width: gridWidth, height: 44)
        addColumn.frame = CGRect(x: gridWidth, y: 0, width: 44, height: 44)
        heights[heights.count - 1] += 44
        contentSize = canvas.bounds.size; alwaysBounceHorizontal = contentSize.width > width
        if let editor = cellEditor {
            if table.rows.indices.contains(row), table.alignments.indices.contains(column) {
                if !committing, editor.markedTextRange == nil {
                    let raw = (self.source as NSString).substring(with: table.rows[row].cells[column])
                    let value = MarkdownTableEditing.cellText(source: self.source, range: table.rows[row].cells[column])
                    if !raw.utf8.elementsEqual(storedCell.utf8) {
                        let caret = editor.selectedRange
                        editor.text = value; cellValue = value; storedCell = raw
                        cellContentOffset = Self.contentOffset(raw, value: value)
                        cellTrailingPadding = raw.utf16.count - cellContentOffset - value.utf16.count
                        editor.selectedRange = NSRange(location: min(caret.location, value.utf16.count), length: 0)
                    }
                }
                editor.frame = cells[row][column].frame; canvas.bringSubviewToFront(editor)
            } else { invalidateEditor() }
        }
        setEditable(owner?.isEditable == true)
        positionFailure()
    }

    func refreshPalette() {
        let border = RijiPalette.border.resolvedColor(with: traitCollection).cgColor
        for line in cells { for cell in line { cell.layer.borderColor = border } }
        cellEditor?.layer.borderColor = border
    }

    private func alignment(_ column: Int) -> NSTextAlignment {
        table.alignments[column] == .center ? .center : table.alignments[column] == .right ? .right : .left
    }
    func setEditable(_ editable: Bool) {
        addRow.isEnabled = editable; addColumn.isEnabled = editable; cellEditor?.isEditable = editable
    }
    func clearFailure() { failureLabel.isHidden = true }
    func showFailure() {
        failureLabel.isHidden = false
        positionFailure()
        if let owner { owner.scrollRectToVisible(failureLabel.convert(failureLabel.bounds, to: owner).insetBy(dx: 0, dy: -4), animated: false) }
        UIAccessibility.post(notification: .announcement, argument: failureLabel.text)
    }
    func positionFailure() {
        guard !failureLabel.isHidden, let owner else { return }
        let visible = owner.convert(owner.bounds, to: canvas).intersection(canvas.bounds)
        guard !visible.isNull else { return }
        let grid = CGRect(x: 0, y: 0, width: addColumn.frame.minX, height: addRow.frame.minY)
        let usable = visible.intersection(grid).insetBy(dx: 8, dy: 8)
        guard !usable.isNull, usable.width > 0 else { return }
        let y = max(0, min(usable.minY, grid.maxY - 44))
        failureLabel.frame = CGRect(x: usable.minX, y: y, width: usable.width, height: 44)
        canvas.bringSubviewToFront(failureLabel)
    }
    override func layoutSubviews() { super.layoutSubviews(); positionFailure() }
    func invalidate() { valid = false; invalidateEditor() }
    private func invalidateEditor() {
        if cells.indices.contains(row), cells[row].indices.contains(column) { cells[row][column].subviews.first?.isHidden = false }
        cellEditor?.delegate = nil; cellEditor?.resignFirstResponder(); cellEditor?.removeFromSuperview(); cellEditor = nil
    }
    @discardableResult func finishEditing(resign: Bool = true) -> Bool {
        guard let editor = cellEditor else { return true }
        editor.unmarkText()
        guard commitCell(editor) else { return false }
        if resign {
            let wasSwitching = owner?.switchingTableCell ?? false
            owner?.switchingTableCell = true
            invalidateEditor()
            owner?.switchingTableCell = wasSwitching
        }
        return true
    }
    @objc private func tapCell(_ tap: UITapGestureRecognizer) {
        guard let tag = tap.view?.tag else { return }
        beginEditing(row: tag / 64, column: tag % 64)
    }
    func beginEditing(row: Int, column: Int) {
        guard valid, let owner, owner.isEditable, owner.markedTextRange == nil,
              table.rows.indices.contains(row), table.alignments.indices.contains(column) else { return }
        owner.switchingTableCell = true
        defer { owner.switchingTableCell = false }
        guard let target = owner.finishOtherTableEditors(except: self) else { return }
        if target !== self { target.beginEditing(row: row, column: column); return }
        if cellEditor != nil { guard finishEditing(resign: false) else { return }; invalidateEditor() }
        owner.resignFirstResponder(); owner.cancelDateGesture()
        self.row = row; self.column = column
        let editor = MarkdownTableCellEditor()
        editor.frame = cells[row][column].frame
        editor.font = .preferredFont(forTextStyle: .body); editor.textColor = RijiPalette.text
        editor.backgroundColor = RijiPalette.selectedSurface; editor.isScrollEnabled = false
        editor.layer.borderWidth = 0.5; editor.layer.borderColor = RijiPalette.border.resolvedColor(with: traitCollection).cgColor
        editor.adjustsFontForContentSizeCategory = true
        editor.textContainerInset = UIEdgeInsets(top: 8, left: 3, bottom: 8, right: 3)
        editor.textAlignment = alignment(column)
        editor.smartQuotesType = .no; editor.smartDashesType = .no; editor.smartInsertDeleteType = .no
        editor.accessibilityIdentifier = "markdownTableCellEditor"
        editor.accessibilityLabel = "表格第\(row + 1)行第\(column + 1)列"
        cellValue = MarkdownTableEditing.cellText(source: source, range: table.rows[row].cells[column])
        storedCell = (source as NSString).substring(with: table.rows[row].cells[column])
        cellContentOffset = Self.contentOffset(storedCell, value: cellValue)
        cellTrailingPadding = storedCell.utf16.count - cellContentOffset - cellValue.utf16.count
        editor.text = cellValue; editor.delegate = self; editor.inputAccessoryView = owner.inputAccessoryView
        cellEditor = editor; canvas.addSubview(editor)
        cells[row][column].subviews.first?.isHidden = true
        editor.becomeFirstResponder(); editor.selectedRange = NSRange(location: editor.text.utf16.count, length: 0)
        owner.revealTableEditor(self)
    }
    func textView(_ textView: UITextView, shouldChangeTextIn range: NSRange, replacementText text: String) -> Bool {
        guard valid, owner?.isEditable == true else { return false }
        if text == "\n", textView.markedTextRange == nil { insertRow(after: row, focusColumn: column); return false }
        let value = (textView.text as NSString).replacingCharacters(in: range, with: text)
        guard source.utf8.elementsEqual((owner?.text ?? "").utf8),
              (try? MarkdownTableEditing.patch(source: source, table: table, operation: .cell(row: row, column: column, text: value, previousSourceText: previousSourceText(), contentOffset: cellContentOffset))) != nil else { showFailure(); return false }
        return true
    }
    func textViewDidChange(_ textView: UITextView) { _ = commitCell(textView) }
    private func commitCell(_ textView: UITextView) -> Bool {
        guard valid, let owner, owner.isEditable else { return false }
        // An untouched editor may still show a raw spelling whose safe encoding differs.
        if textView.text.utf8.elementsEqual(cellValue.utf8) { return true }
        let wasMissing = table.rows[row].sourceCells.count <= column
        committing = true
        let accepted = owner.applyTableEdit(self, operation: .cell(row: row, column: column, text: textView.text, previousSourceText: previousSourceText(), contentOffset: cellContentOffset))
        committing = false
        if accepted {
            if wasMissing { cellContentOffset = 1; cellTrailingPadding = 1 }
            cellValue = textView.text
            storedCell = (source as NSString).substring(with: table.rows[row].cells[column])
        }
        if textView.markedTextRange == nil { owner.revealTableEditor(self) }
        return accepted
    }
    func textViewDidEndEditing(_ textView: UITextView) {
        guard valid else { return }
        textView.unmarkText()
        if commitCell(textView), cellEditor === textView { invalidateEditor() }
    }
    func textViewDidChangeSelection(_ textView: UITextView) {
        if textView.markedTextRange == nil { owner?.revealTableEditor(self) }
    }
    @objc private func appendRow() { insertRow(after: table.rows.count - 1, focusColumn: 0) }
    private func insertRow(after: Int, focusColumn: Int) {
        guard finishEditing(resign: false) else { return }
        guard owner?.applyTableEdit(self, operation: .insertRow(after: after)) == true else { return }
        beginEditing(row: after + 1, column: focusColumn)
    }
    @objc private func appendColumn() {
        guard finishEditing(resign: false) else { return }
        let next = table.alignments.count
        guard owner?.applyTableEdit(self, operation: .appendColumn) == true else { return }
        beginEditing(row: 0, column: next)
    }
    private func previousSourceText() -> String {
        let length = max(0, storedCell.utf16.count - cellContentOffset - cellTrailingPadding)
        return (storedCell as NSString).substring(with: NSRange(location: cellContentOffset, length: length))
    }

    private static func contentOffset(_ raw: String, value: String) -> Int {
        value.isEmpty ? raw.utf16.count / 2 : (raw as NSString).range(of: value).location
    }

    private static func cellText(_ source: NSAttributedString, range: NSRange) -> NSAttributedString {
        let result = NSMutableAttributedString(attributedString: source.attributedSubstring(from: range))
        var hidden: [NSRange] = []
        result.enumerateAttribute(.markdownHidden, in: NSRange(location: 0, length: result.length)) { value, range, _ in
            if value != nil { hidden.append(range) }
        }
        for range in hidden.reversed() { result.deleteCharacters(in: range) }
        let plain = result.string as NSString
        let first = plain.rangeOfCharacter(from: .whitespacesAndNewlines.inverted)
        guard first.location != NSNotFound else { return NSAttributedString(string: "") }
        let last = plain.rangeOfCharacter(from: .whitespacesAndNewlines.inverted, options: .backwards)
        return result.attributedSubstring(from: NSRange(location: first.location, length: NSMaxRange(last) - first.location))
    }
    override func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
        if gestureRecognizer === panGestureRecognizer {
            let velocity = panGestureRecognizer.velocity(in: self)
            return abs(velocity.x) > abs(velocity.y)
        }
        return super.gestureRecognizerShouldBegin(gestureRecognizer)
    }
}

final class MarkdownTableCellEditor: UITextView {
    override var undoManager: UndoManager? { nil }
}
