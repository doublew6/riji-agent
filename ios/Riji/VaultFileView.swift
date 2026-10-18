import SwiftUI
import UIKit
import QuickLook
import RijiCore

struct VaultFileView: View {
    let contents: VaultFileContents
    let close: () -> Void
    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 0) {
                Text(contents.entry.id).font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                    .padding(.horizontal, 20).padding(.vertical, 8).textSelection(.enabled)
                if contents.cloudConflict {
                    Text("此文件有待核对的 iCloud 版本，当前内容仍可只读查看。日记可从目录按日期打开“查看并处理版本”；其他文件请在系统文件中核对。")
                        .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.warning)).padding(.horizontal, 20)
                }
                if let text = contents.text {
                    VaultTextReader(text: text, markdown: ["md", "markdown"].contains((contents.entry.name as NSString).pathExtension.lowercased()))
                } else if let url = contents.previewURL { VaultQuickLook(url: url) }
            }
            .rijiPage()
            .navigationTitle(contents.entry.name).navigationBarTitleDisplayMode(.inline)
            .safeAreaInset(edge: .bottom) { Text("只读").font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary)).frame(maxWidth: .infinity).padding(8).background(.bar) }
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成", action: close).accessibilityIdentifier("closeVaultPreview") } }
        }
    }
}

private struct VaultTextReader: UIViewRepresentable {
    let text: String
    let markdown: Bool

    func makeUIView(context: Context) -> MarkdownTextView {
        let view = MarkdownTextView()
        view.isEditable = false; view.isSelectable = true
        view.backgroundColor = RijiPalette.paper; view.tintColor = RijiPalette.action; view.alwaysBounceVertical = true
        view.textContainerInset = UIEdgeInsets(top: 20, left: 20, bottom: 32, right: 20)
        view.textContainer.lineFragmentPadding = 0
        view.accessibilityIdentifier = "vaultFileReader"; view.accessibilityLabel = "文件内容"
        render(view)
        view.onPresentationChange = { [weak view] in if let view { render(view) } }
        return view
    }

    func updateUIView(_ view: MarkdownTextView, context: Context) {
        if !view.text.utf8.elementsEqual(text.utf8) { render(view) }
    }

    private func render(_ view: MarkdownTextView) {
        let selection = view.selectedRange, offset = view.contentOffset
        view.text = text; view.presentation = MarkdownPresentation(markdown ? text : "")
        view.textStorage.beginEditing(); view.applyPresentation(active: nil); view.textStorage.endEditing()
        view.layoutManager.invalidateGlyphs(forCharacterRange: NSRange(location: 0, length: view.textStorage.length), changeInLength: 0, actualCharacterRange: nil)
        view.resetTypingAttributes()
        if NSMaxRange(selection) <= view.text.utf16.count { view.selectedRange = selection }
        view.setContentOffset(offset, animated: false)
    }
}

private struct VaultQuickLook: UIViewControllerRepresentable {
    let url: URL
    func makeCoordinator() -> Coordinator { Coordinator(url: url) }
    func makeUIViewController(context: Context) -> QLPreviewController {
        let controller = QLPreviewController(); controller.dataSource = context.coordinator; controller.delegate = context.coordinator
        return controller
    }
    func updateUIViewController(_ controller: QLPreviewController, context: Context) {}
    final class Coordinator: NSObject, QLPreviewControllerDataSource, QLPreviewControllerDelegate {
        let url: URL
        init(url: URL) { self.url = url }
        func numberOfPreviewItems(in controller: QLPreviewController) -> Int { 1 }
        func previewController(_ controller: QLPreviewController, previewItemAt index: Int) -> QLPreviewItem { url as NSURL }
        func previewController(_ controller: QLPreviewController, editingModeFor previewItem: QLPreviewItem) -> QLPreviewItemEditingMode { .disabled }
    }
}
