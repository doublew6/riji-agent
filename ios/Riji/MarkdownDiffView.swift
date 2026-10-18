import SwiftUI
import UIKit
import RijiCore

/// A local comparison workspace. Only exact, expected draft bytes can be replaced.
struct MarkdownDiffView: View {
    let current: Data
    let other: Data
    var otherLabel = "另一版本"
    var summaryIdentifier = "cloudDiffSummary"
    var editable = false
    var disabled = false
    var apply: (Data, Data) -> Bool = { _, _ in false }
    @State private var comparison: MarkdownVersionDiff?
    @State private var choices: [Int: MarkdownVersionDiff.Choice] = [:]
    @State private var editorRevisions: [Int: Int] = [:]
    @State private var emitted: Data?
    @State private var error: String?
    @State private var generation = UUID()

    var body: some View {
        LazyVStack(alignment: .leading, spacing: 16) {
            if let error { Text(error).foregroundStyle(Color(uiColor: RijiPalette.error)) }
            if let comparison {
                Text(comparison.hunks.isEmpty ? "内容一致，无需逐段核对" : "\(comparison.hunks.count) 处差异 · \(comparison.identicalLines) 行相同内容已折叠")
                    .font(.headline).accessibilityIdentifier(summaryIdentifier)
                if comparison.usedCoarseComparison {
                    Text("部分差异较大，已合并成段落展示。展开可查看完整差异内容。").font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                }
                ForEach(comparison.hunks) { hunk in card(hunk) }
            } else if error == nil { ProgressView("正在比较差异…") }
        }
        .task { await rebuild() }
        .onChange(of: current) { _, value in if value != emitted { Task { await rebuild() } } }
        .onChange(of: other) { _, _ in Task { await rebuild() } }
    }

    private func card(_ hunk: MarkdownVersionDiff.Hunk) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("\(hunk.id + 1). \(hunk.section)").font(.headline).accessibilityIdentifier("cloudDiffHunk-\(hunk.id)")
                Spacer()
                Text(hunk.kind).font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
            }
            if hunk.visuallyEqual { Text("外观相同，但空白、换行或字符编码不同；可展开原文核对。").font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary)) }
            fragment(editable ? "本段原内容" : "当前文件", text: hunk.currentPreview, tint: Color(uiColor: RijiPalette.warning), raw: hunk.current)
            fragment(otherLabel, text: hunk.otherPreview, tint: Color(uiColor: RijiPalette.brand), raw: hunk.other)
            if !hunk.contextBefore.isEmpty || !hunk.contextAfter.isEmpty {
                DisclosureGroup("查看相邻上下文") {
                    Text(hunk.contextBefore + "\n⋯ 差异位置 ⋯\n" + hunk.contextAfter).font(.callout).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                }.font(.caption)
            }
            if editable {
                HStack {
                    Button(choiceIsCurrent(hunk.id) ? "✓ 已保留" : "保留这边") { select(.current, for: hunk) }
                        .buttonStyle(.bordered).accessibilityIdentifier("keepDiff-\(hunk.id)")
                    Button(choiceIsOther(hunk.id) ? "✓ 已采用" : "采用另一版") { select(.other, for: hunk) }
                        .buttonStyle(.borderedProminent).accessibilityIdentifier("useDiff-\(hunk.id)")
                }.disabled(disabled)
                DisclosureGroup("只编辑这一处") {
                    DiffFragmentEditor(text: editedText(hunk), disabled: disabled, identifier: "editDiff-\(hunk.id)", revision: editorRevisions[hunk.id] ?? 0) {
                        select(.edited(Data($0.utf8)), for: hunk, replacesEditor: false)
                    }.frame(height: 140)
                    Text("仅修改本段；完整日记的其他内容保持原样。").font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                }.font(.callout)
                if case .edited = choices[hunk.id] { Text("已采用这处手动修改").font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary)) }
            }
        }
        .padding(16).background(Color(uiColor: RijiPalette.warmSurface), in: RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color(uiColor: RijiPalette.border).opacity(0.4)))
    }

    private func fragment(_ label: String, text: String, tint: Color, raw: Data) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(label, systemImage: "text.alignleft").font(.caption.weight(.semibold)).foregroundStyle(tint)
            if raw.isEmpty { Text("此处没有内容").font(.callout).foregroundStyle(Color(uiColor: RijiPalette.secondary)) }
            else { DiffFragmentPreview(text: text, raw: raw) }
        }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
            .background(tint.opacity(0.06), in: RoundedRectangle(cornerRadius: 10))
    }

    private func editedText(_ hunk: MarkdownVersionDiff.Hunk) -> String {
        switch choices[hunk.id] ?? .current {
        case .current: return String(decoding: hunk.current, as: UTF8.self)
        case .other: return String(decoding: hunk.other, as: UTF8.self)
        case .edited(let bytes): return String(decoding: bytes, as: UTF8.self)
        }
    }
    private func choiceIsCurrent(_ id: Int) -> Bool { if case .current = choices[id] { return true }; return false }
    private func choiceIsOther(_ id: Int) -> Bool { if case .other = choices[id] { return true }; return false }
    private func select(_ choice: MarkdownVersionDiff.Choice, for hunk: MarkdownVersionDiff.Hunk, replacesEditor: Bool = true) {
        guard !disabled, let comparison else { return }
        let expected = emitted ?? comparison.current
        var next = choices; next[hunk.id] = choice
        do {
            let result = try comparison.applying(next, to: comparison.current)
            guard apply(expected, result) else { error = "此差异已过期，合并稿保持不变。请重新核对。"; return }
            choices = next; emitted = result; error = nil
            if replacesEditor { editorRevisions[hunk.id, default: 0] += 1 }
        } catch { self.error = "无法应用这处修改，原合并稿仍保留。请检查内容大小后重试。" }
    }
    @MainActor private func rebuild() async {
        let id = UUID(); generation = id
        let a = current, b = other
        comparison = nil; error = nil; choices = [:]; editorRevisions = [:]; emitted = nil
        let result = await Task.detached(priority: .userInitiated) { Result { try MarkdownVersionDiff(current: a, other: b) } }.value
        guard generation == id, !Task.isCancelled else { return }
        switch result {
        case .success(let diff): comparison = diff
        case .failure: error = "这份内容暂不能生成差异。可在“完整原文与导出”中查看；原内容保持不变。"
        }
    }
}

/// Keep native selection and marked text intact when the parent publishes draft changes.
struct DiffFragmentEditor: UIViewRepresentable {
    let text: String
    let disabled: Bool
    let identifier: String
    let revision: Int
    let change: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }
    func makeUIView(context: Context) -> UITextView {
        let view = UITextView()
        view.font = .preferredFont(forTextStyle: .body)
        view.backgroundColor = RijiPalette.warmSurface
        view.textColor = RijiPalette.text; view.tintColor = RijiPalette.action
        view.autocorrectionType = .no; view.autocapitalizationType = .none
        view.smartQuotesType = .no; view.smartDashesType = .no
        view.text = text; view.delegate = context.coordinator
        view.accessibilityIdentifier = identifier; view.isEditable = !disabled
        return view
    }
    func updateUIView(_ view: UITextView, context: Context) {
        context.coordinator.parent = self
        if view.isEditable == disabled { view.isEditable = !disabled }
        guard context.coordinator.revision != revision else { return }
        context.coordinator.revision = revision
        context.coordinator.applying = true
        defer { context.coordinator.applying = false }
        view.unmarkText()
        let selection = view.selectedRange, offset = view.contentOffset
        view.text = text
        let start = min(selection.location, text.utf16.count)
        view.selectedRange = NSRange(location: start, length: min(selection.length, text.utf16.count - start))
        view.setContentOffset(offset, animated: false)
    }
    final class Coordinator: NSObject, UITextViewDelegate {
        var parent: DiffFragmentEditor
        var revision: Int
        var applying = false
        init(parent: DiffFragmentEditor) { self.parent = parent; revision = parent.revision }
        func textViewDidChange(_ view: UITextView) { if !applying { parent.change(view.text) } }
    }
}

private struct DiffFragmentPreview: View {
    let text: String
    let raw: Data
    @State private var expanded = false
    private var shortened: String { String(text.prefix(1600)) }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            DiffMarkdownReader(text: expanded ? text : shortened)
            if text.count > 1600 { Button(expanded ? "收起长段落" : "展开本处全部内容") { expanded.toggle() }.font(.caption) }
            DisclosureGroup("原文细节") {
                Text(String(decoding: raw, as: UTF8.self)).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                Text(newlineDescription).font(.caption2).foregroundStyle(Color(uiColor: RijiPalette.secondary))
            }.font(.caption2).foregroundStyle(Color(uiColor: RijiPalette.secondary))
        }
    }
    private var newlineDescription: String {
        let text = String(decoding: raw, as: UTF8.self)
        return "\(raw.count) 字节 · " + (text.contains("\r\n") ? "CRLF 换行" : "LF 换行")
            + (text.hasSuffix("\n") || text.hasSuffix("\r") ? " · 末尾有换行" : " · 末尾无换行")
    }
}

/// Reuse the diary's Markdown projection, including table grids, without nested vertical scrolling.
struct DiffMarkdownReader: UIViewRepresentable {
    let text: String
    func makeUIView(context: Context) -> MarkdownTextView {
        let view = MarkdownTextView()
        view.isEditable = false; view.isSelectable = true; view.isScrollEnabled = false
        view.backgroundColor = .clear; view.textContainerInset = .zero; view.textContainer.lineFragmentPadding = 0
        view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        render(view); return view
    }
    func updateUIView(_ view: MarkdownTextView, context: Context) {
        if !view.text.utf8.elementsEqual(text.utf8) { render(view) }
    }
    func sizeThatFits(_ proposal: ProposedViewSize, uiView view: MarkdownTextView, context: Context) -> CGSize? {
        guard let width = proposal.width, width > 0 else { return nil }
        if view.bounds.width != width { view.bounds.size.width = width; render(view) }
        view.layoutIfNeeded()
        return CGSize(width: width, height: max(24, view.sizeThatFits(CGSize(width: width, height: .greatestFiniteMagnitude)).height))
    }
    private func render(_ view: MarkdownTextView) {
        view.text = text; view.presentation = MarkdownPresentation(text)
        view.textStorage.beginEditing(); view.applyPresentation(active: nil); view.textStorage.endEditing()
        view.resetTypingAttributes()
    }
}
