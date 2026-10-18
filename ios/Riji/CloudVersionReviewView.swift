import SwiftUI
import RijiCore

struct CloudVersionReviewView: View {
    @ObservedObject var model: DiaryViewModel
    let close: () -> Void
    @State private var choice: Data?
    @State private var keepingCurrent = false
    @State private var document: MarkdownDocument?
    @State private var exporting = false
    @State private var comparisonID: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text("这里对比的是当前日记与系统保留的冲突副本，不是本地与云端的实时同步状态。正常编辑由 iCloud 自动同步。")
                        .font(.callout).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                    if let error = model.cloudError { Text(error).foregroundStyle(Color(uiColor: RijiPalette.error)).accessibilityIdentifier("cloudReviewError") }
                    if let warning = model.cloudBackupWarning { Text(warning).foregroundStyle(Color(uiColor: RijiPalette.secondary)) }
                    if let draft = model.cloudDraft {
                        if let issue = draft.review.resolutionIssue { Text(issue).foregroundStyle(Color(uiColor: RijiPalette.secondary)) }
                        if draft.review.canResolve {
                            Button("整份保留当前日记…") {
                                TodayDiaryView.commitVisibleInput(); keepingCurrent = false; choice = draft.review.current
                            }.buttonStyle(.borderedProminent).disabled(!canConfirm).accessibilityIdentifier("keepEntireCurrentDiary")
                            Text("当前日记已经完整时，整份确认一次即可。下方差异只用于按需取回副本中的文字，不必逐条点选。").font(.callout).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                        }
                        if draft.review.canKeepCurrent && model.cloudRecoveryPending {
                            Button("核对当前文件并结束旧恢复…") { keepingCurrent = true; choice = draft.review.current }
                                .disabled(model.isBusy || model.isOriginalReadOnly).accessibilityIdentifier("reviewKeepCloudCurrent")
                        }
                        comparison(draft)
                        DisclosureGroup("完整原文与导出") {
                            version("当前文件", content: draft.review.current, id: "current")
                            ForEach(Array(draft.review.versions.enumerated()), id: \.element.id) { index, version in
                                self.version("冲突副本 \(index + 1)", content: version.content, id: "version-\(index)", metadata: (version.modifiedAt, version.exceedsSizeLimit == true))
                            }
                            ForEach(Array(model.cloudLocalVersions.enumerated()), id: \.offset) { index, content in
                                version("独立本机草稿 \(index + 1)", content: content, id: "local-\(index)")
                            }
                            Button("导出合并文字") { export(Data(model.cloudMergeText.utf8)) }
                        }
                        DisclosureGroup("手动编辑整篇") {
                            TextEditor(text: Binding(get: { model.cloudMergeText }, set: model.updateCloudMerge))
                                .font(.system(.body, design: .monospaced)).frame(minHeight: 260)
                                .disabled(model.isBusy).accessibilityIdentifier("cloudMergeEditor")
                        }
                    }
                    Button("刷新并重新核对版本") { TodayDiaryView.commitVisibleInput(); Task { await model.reviewCloudVersions(refresh: true) } }
                        .disabled(model.isBusy).accessibilityIdentifier("refreshCloudVersions")
                    if model.cloudRecoveryPending {
                        Button("重试已确认的版本处理") { Task { if await model.confirmCloudVersion(retry: true) { close() } } }
                            .disabled(model.isBusy).accessibilityIdentifier("retryCloudResolution")
                    }
                    if !model.cloudBackups.isEmpty {
                        Text("本机版本备份").font(.headline)
                        ForEach(Array(model.cloudBackups.enumerated()), id: \.offset) { index, backup in
                            DisclosureGroup("备份 \(index + 1)") {
                                version("原当前文件", content: backup.review.current, id: "backup-\(index)-current", selectable: false)
                                ForEach(Array(backup.review.versions.enumerated()), id: \.offset) { number, version in
                                    self.version("原冲突副本 \(number + 1)", content: version.content, id: "backup-\(index)-\(number)", selectable: false, metadata: (version.modifiedAt, version.exceedsSizeLimit == true))
                                }
                                version("保留的结果／合并文字", content: backup.content, id: "backup-\(index)-proposal", selectable: false)
                            }
                        }
                    }
                }.padding(20)
            }
            .navigationTitle("查看并处理版本").navigationBarTitleDisplayMode(.inline)
            .rijiPage()
            .safeAreaInset(edge: .bottom) {
                if model.cloudDraft != nil && model.cloudBlocksEditing {
                    Button("核对合并结果") { TodayDiaryView.commitVisibleInput(); keepingCurrent = false; choice = Data(model.cloudMergeText.utf8) }
                        .buttonStyle(.borderedProminent).frame(maxWidth: .infinity).padding(12).background(.bar)
                        .disabled(!canConfirm).accessibilityIdentifier("reviewCloudMerge")
                }
            }
            .toolbar { ToolbarItem(placement: .confirmationAction) {
                Button("稍后处理") { TodayDiaryView.commitVisibleInput(); Task { if await model.closeCloudReview() { close() } } }
                    .disabled(model.isBusy).accessibilityIdentifier("closeCloudReview")
            } }
            .interactiveDismissDisabled()
            .sheet(isPresented: Binding(get: { choice != nil }, set: { if !$0 { choice = nil } })) { confirmation }
            .fileExporter(isPresented: $exporting, document: document, contentType: MarkdownDocument.readableContentTypes[0],
                          defaultFilename: document?.filename ?? "Version.md") { result in
                if let document { model.exportCompleted(result, document: document) }
            }
        }
    }

    private var canConfirm: Bool { model.cloudBlocksEditing && model.cloudDraft?.review.canResolve == true && !model.isBusy && !model.isOriginalReadOnly }

    private struct ComparedVersion: Identifiable {
        let id: String, label: String, content: Data
    }
    private var comparedVersions: [ComparedVersion] {
        guard let draft = model.cloudDraft else { return [] }
        var values = draft.review.versions.enumerated().compactMap { index, version -> ComparedVersion? in
            guard let content = version.content, String(data: content, encoding: .utf8) != nil else { return nil }
            return ComparedVersion(id: version.id, label: "冲突副本 \(index + 1)", content: content)
        }
        values += model.cloudLocalVersions.enumerated().map { index, content in
            ComparedVersion(id: "local-\(index)", label: "本机草稿 \(index + 1)", content: content)
        }
        return values
    }
    @ViewBuilder private func comparison(_ draft: CloudRecoveryDraft) -> some View {
        let options = comparedVersions
        let selected = options.first(where: { $0.id == comparisonID }) ?? options.first
        if let selected {
            if options.count > 1 {
                Picker("与哪个版本比较", selection: Binding(get: { selected.id }, set: { comparisonID = $0 })) {
                    ForEach(options) { Text($0.label).tag($0.id) }
                }.pickerStyle(.menu).disabled(model.isBusy)
            } else { Text("待保存内容 ↔ \(selected.label)").font(.subheadline).foregroundStyle(Color(uiColor: RijiPalette.secondary)) }
            MarkdownDiffView(current: Data(model.cloudMergeText.utf8), other: selected.content, otherLabel: selected.label,
                             editable: model.cloudBlocksEditing, disabled: model.isBusy || model.isOriginalReadOnly,
                             apply: { model.applyCloudDifference(reviewID: draft.review.id, expected: $0, result: $1) })
                .id(draft.review.id.uuidString + selected.id)
        }
    }

    private func version(_ label: String, content: Data?, id: String, selectable: Bool = true, metadata: (Date?, Bool) = (nil, false)) -> some View {
        DisclosureGroup(label) {
            if let modifiedAt = metadata.0 { Text(modifiedAt.formatted(date: .abbreviated, time: .standard)).font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary)) }
            if let content {
                if let text = String(data: content, encoding: .utf8) {
                    Text(text).font(.system(.body, design: .monospaced)).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading).accessibilityIdentifier("cloudRaw-" + id)
                    if selectable {
                        Button("使用此版本…") { keepingCurrent = false; choice = content }.disabled(!canConfirm)
                            .accessibilityIdentifier("chooseCloud-" + id)
                    }
                } else { Text("此版本不是有效 UTF-8 文本，可导出原始字节；不能直接作为 Markdown 确认。") }
                Button("导出此版本") { export(content) }.accessibilityIdentifier("exportCloud-" + id)
            } else { Text(metadata.1 ? "此版本超过 2 MiB 的读取上限，原系统版本保持不变；请在原编辑器中处理。" : "此版本暂不可读取。请刷新重试；当前文件仍可查看、导出。") }
        }
    }

    private var confirmation: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text(choice == model.cloudDraft?.review.current
                         ? "当前日记正文保持原样。确认后会先备份本次冲突副本，再结束这次版本处理，无需逐条确认。"
                         : "以下是确认后日记实际会发生的变化。所有已核对版本会先备份在本机。").font(.callout).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                    MarkdownDiffView(current: model.cloudDraft?.review.current ?? Data(), other: choice ?? Data(), otherLabel: "确认后的日记", summaryIdentifier: "cloudResultDiffSummary")
                    DisclosureGroup("查看完整结果（原文）") {
                        Text(String(decoding: choice ?? Data(), as: UTF8.self))
                            .font(.system(.body, design: .monospaced)).textSelection(.enabled)
                            .accessibilityIdentifier("cloudChosenFullSource")
                    }
                }.padding(20)
            }
            .rijiPage()
            .navigationTitle("核对保存结果").navigationBarTitleDisplayMode(.inline)
            .safeAreaInset(edge: .bottom) {
                VStack {
                    if let error = model.cloudError { Text(error).font(.caption).foregroundStyle(Color(uiColor: RijiPalette.error)) }
                    if keepingCurrent { Text("保留上方刚核对的当前文件，不重放旧保存。原意图、合并文字、备份和独立正文草稿全部保留。").font(.caption) }
                    Button(keepingCurrent ? "确认保留当前文件并结束旧恢复" : "确认保存并处理已审阅版本") {
                        Task { if await model.confirmCloudVersion(choice, keepCurrent: keepingCurrent) { choice = nil; close() } }
                    }.disabled(keepingCurrent ? model.isBusy || model.isOriginalReadOnly : !canConfirm).accessibilityIdentifier("confirmCloudResolution")
                }.padding().background(.bar)
            }
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("返回核对") { choice = nil }.disabled(model.isBusy) } }
            .interactiveDismissDisabled(model.isBusy)
        }
    }

    private func export(_ content: Data) {
        document = MarkdownDocument(day: model.selectedDay, data: content, recoveryDraft: true)
        exporting = true
    }
}
