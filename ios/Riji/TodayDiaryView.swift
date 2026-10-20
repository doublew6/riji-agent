import SwiftUI
import UIKit
import UniformTypeIdentifiers
import RijiCore

struct TodayDiaryView: View {
    @ObservedObject var model: DiaryViewModel
    @StateObject private var viewports = DiaryViewports()
    @State private var showDirectory = false
    @State private var showFolderPicker = false
    @State private var showVoice = false
    @State private var showDate = false
    @State private var showLegacy = false
    @State private var showAIRecovery = false
    @StateObject private var aiSources: DiaryAISourceSession
    @State private var showConflict = false
    @State private var showFullBodyVersions = false
    @State private var showCloudConflict = false
    @State private var dateSelection = Date()
    @State private var document: MarkdownDocument?
    @State private var showExporter = false
    @State private var protectedEditFeedback = false
    #if DEBUG && targetEnvironment(simulator)
    @State private var syntheticConflictPrepared = false
    #endif
    // DatePicker carries a calendar label, not a local midnight instant.
    private let pickerTimeZone = TimeZone(secondsFromGMT: 0)!

    init(model: DiaryViewModel, aiSources: DiaryAISourceSession? = nil) {
        self.model = model
        _aiSources = StateObject(wrappedValue: aiSources ?? DiaryAISourceSession())
    }

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 0) {
                Text(model.templateLabel).font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                    .padding(.horizontal, 20).padding(.top, 8).accessibilityIdentifier("templateLabel")
                    .accessibilityLabel(diaryStatus.isEmpty ? model.templateLabel : "\(model.templateLabel)，\(diaryStatus)")
                    .accessibilityValue(exportTestValue)
                if model.needsFolder {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(model.needsFolder ? "打开原日记文件夹" : "原目录暂时无法读取").font(.headline)
                        Text("选择包含 daily 与 templates 的原目录，或它的上一级目录。").font(.callout).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                        Button("选择原日记文件夹", systemImage: "folder.badge.plus") { showFolderPicker = true }
                            .accessibilityIdentifier("chooseHomeVaultFolder")
                        Button("打开原有 App 本地日记") { Task { await model.useLocalDiaries() } }
                            .accessibilityIdentifier("openOldLocalDiaries")
                        if model.sourceUnavailable { Button("重新读取原目录") { Task { if model.isOriginalSource { await model.useOriginalDiaries() } else { await model.start() } } } }
                    }.padding(20)
                }
                #if DEBUG && targetEnvironment(simulator)
                if offersSyntheticConflictPreparation {
                    Button("Prepare synthetic conflict") {
                        guard offersSyntheticConflictPreparation, model.isReady,
                              model.isOriginalSource, !model.isBusy else { return }
                        syntheticConflictPrepared = true
                        endEditing()
                        model.simulateExternalRefreshFixture(divergent: true)
                    }
                    .disabled(!model.isReady || !model.isOriginalSource || model.isBusy)
                    .accessibilityIdentifier("prepareSyntheticBodyConflict")
                }
                #endif
                DiaryBodyEditor(text: model.markdown, day: model.selectedDay, editable: model.canEditBody,
                                foldsSavedAIResults: true,
                                onProtectedEdit: { protectedEditFeedback = true }, viewports: viewports,
                                onDateNavigation: model.canNavigateAdjacentDay ? { day, direction in
                    Task { if model.selectedDay == day { _ = await model.navigateToAdjacentDay(direction, from: day) } }
                } : nil, replacementRevision: model.bodyReplacementRevision, onEditorAvailable: { view in
                    model.bodyResumeAdmission = { [weak view] editable in
                        // Re-enable only when updateUIView also installs the current source and callback.
                        if !editable { view?.isEditable = false; view?.updateTableAdmission() }
                    }
                    model.bodyRefreshAdmission = { [weak view, weak model] in
                        guard let view, let model else { return false }
                        return !view.tableIsEditing && view.markedTextRange == nil && !view.tableHasMarkedText
                            && view.text.utf8.elementsEqual(model.markdown.utf8)
                    }
                }) { [origin = model.bodyInputOrigin] day, text in model.updateBody(text, for: day, origin: origin) }
                .id(model.sourceIdentity)
            }
            .rijiPage()
            .onChange(of: model.sourceIdentity) { _, _ in viewports.documents = [:]; aiSources.invalidateIfChanged(model) }
            .fileImporter(isPresented: $showFolderPicker, allowedContentTypes: [.folder]) { result in
                switch result {
                case .success(let url): endEditing(); Task { await model.selectOriginalFolder(url) }
                case .failure(let error): model.sourcePickerFailed(error)
                }
            }
            .onChange(of: Data(model.markdown.utf8)) { _, _ in protectedEditFeedback = false; aiSources.invalidateIfChanged(model) }
            .onChange(of: model.selectedDay) { _, _ in protectedEditFeedback = false; aiSources.invalidateIfChanged(model) }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if needsRecoveryNotice { recoveryNotice }
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { navigationItems }
            .sheet(isPresented: $showDirectory, onDismiss: { Task { await model.directoryClosed() } }) { DiaryDirectoryView(model: model) }
            .sheet(isPresented: $showDate) { datePicker }
            .sheet(isPresented: $showLegacy) { legacyRecovery }
            .sheet(isPresented: $showAIRecovery) { localAIRecovery }
            .sheet(item: $aiSources.document) { document in
                DiaryAISourcesView(document: document) { aiSources.document = nil }
            }
            .task(id: model.sourceIdentity + ":" + model.selectedDay.value) { await model.refreshLocalAIRecovery() }
            .sheet(isPresented: $showCloudConflict) { CloudVersionReviewView(model: model) { showCloudConflict = false } }
            .sheet(isPresented: $showConflict, onDismiss: model.closeBodyConflict) { conflictReview }
            .sheet(isPresented: $showVoice) {
                if let voice = model.voice { VoiceSheet(diary: model, model: voice) }
            }
            .fileExporter(isPresented: $showExporter, document: document,
                          contentType: MarkdownDocument.readableContentTypes[0],
                          defaultFilename: document?.filename ?? "Diary.md") { result in
                if let document { model.exportCompleted(result, document: document) }
            }
        }
    }

    #if DEBUG && targetEnvironment(simulator)
    private var offersSyntheticConflictPreparation: Bool {
        let environment = ProcessInfo.processInfo.environment
        return !syntheticConflictPrepared && model.isExternalRefreshFixture
            && environment["RIJI_TEST_PREPARE_BODY_CONFLICT"] == "1"
            && environment["RIJI_TEST_VAULT_BROWSER"] == "1"
            && environment["RIJI_TEST_MOBILE"] == nil
    }
    #endif

    private var localAIRecovery: some View {
        NavigationStack {
            RijiList {
                Text("仅检查本机记录。取消未写入的预览可恢复这一天的编辑；新的保存需要回到原账户联网核验。")
                ForEach(model.localAIResultPreviews) { preview in
                    Section("AI 导师讨论结果 · " + preview.day.value) {
                        Text(preview.text).textSelection(.enabled)
                        Text("保存不代表采纳建议或已经执行。").font(.caption)
                        Button("取消尚未写入的预览", role: .destructive) { Task { await model.cancelLocalAIResult(preview) } }
                            .accessibilityIdentifier("cancelLocalAIResult")
                    }
                }
                if let status = model.aiResultStatus { Text(status).font(.callout) }
                Button("重新检查本地记录") { Task { await model.refreshLocalAIRecovery() } }
            }.navigationTitle("本地保存恢复").toolbar { Button("完成") { showAIRecovery = false } }
        }
    }

    func endEditing() {
        Self.commitVisibleInput()
    }

    static func commitVisibleInput() {
        for scene in UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }) {
            for window in scene.windows { Self.commitEditing(in: window) }
        }
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
    }

    /// Resolve the input method before a command captures the SwiftUI binding.
    static func commitEditing(in view: UIView) {
        if let editor = view as? UITextView, editor.isFirstResponder {
            if let coordinator = editor.delegate as? DiaryBodyEditor.Coordinator { coordinator.textViewDidEndEditing(editor) }
            else { editor.unmarkText(); editor.delegate?.textViewDidChange?(editor) }
            editor.resignFirstResponder()
        }
        for child in view.subviews { commitEditing(in: child) }
    }

    private var diaryStatus: String {
        model.status == "Markdown 已导出" || model.status.hasPrefix("已保存版本 Markdown")
            || model.status.hasPrefix("本机恢复草稿已导出") ? model.status : model.bodyStatus
    }

    private var needsRecoveryNotice: Bool {
        model.aiResultPending || model.cloudBlocksEditing || model.cloudError != nil || protectedEditFeedback || model.bodyMergeError != nil || model.bodyError != nil
            || model.errorMessage != nil || model.hasRetainedBodyMerge || model.hasLegacyDraft
    }

    private var recoveryNotice: some View {
        VStack(alignment: .leading, spacing: 6) {
            if model.aiResultPending {
                HStack {
                    Text("这一天有待核对的 AI 保存预览").font(.caption)
                    Button("检查本地预览") { Task { await model.refreshLocalAIRecovery(); showAIRecovery = true } }
                        .font(.caption).accessibilityIdentifier("localAIRecovery")
                }
            }
            if model.cloudBlocksEditing {
                HStack {
                    Text("有冲突副本或未完成的版本处理 · 正文只读").font(.caption)
                    Button("查看并处理版本") {
                        endEditing()
                        Task { await model.reviewCloudVersions(); showCloudConflict = model.cloudReviewPresented }
                    }.font(.caption).disabled(model.isBusy).accessibilityIdentifier("reviewCloudVersions")
                }
                if let error = model.cloudError { Text(error).font(.caption).foregroundStyle(Color(uiColor: RijiPalette.error)) }
            }
            if protectedEditFeedback {
                Text(DiaryBodyEditor.protectedEditExplanation).font(.caption)
                    .accessibilityIdentifier("protectedBodyEditFeedback")
            }
            if let error = model.bodyMergeError ?? model.bodyError ?? model.errorMessage,
               !(model.cloudConflict != nil && model.bodySourceIssue == .cloudConflict && model.bodyMergeError == nil && model.errorMessage == nil) {
                Text(error).font(.caption).foregroundStyle(Color(uiColor: RijiPalette.error)).accessibilityIdentifier("storageError")
                if model.isOriginalSource && model.needsSourceReauthorization {
                    Button("重新选择原目录") { endEditing(); showFolderPicker = true }
                        .font(.caption).disabled(model.isBusy).accessibilityIdentifier("reauthorizeOriginal")
                }
                HStack {
                    if model.bodyRecoveryUnavailable {
                        Button("重新读取恢复记录") { Task { await model.retryBodyRecoveryRead() } }
                            .disabled(model.isBusy).accessibilityIdentifier("retryBodyRecoveryRead")
                    } else if model.isOriginalSource && (model.sourceUnavailable || model.bodySourceIssue == .cloudConflict) {
                        Button("重新读取文件") { endEditing(); Task { await model.retryOriginalBodySource() } }
                            .disabled(model.isBusy).accessibilityIdentifier("retryExternalRefresh")
                    } else if model.bodyMergeDirty {
                        Button("重试保留合并文字") { endEditing(); Task { _ = await model.flushBodyMerge() } }
                            .disabled(model.isBusy).accessibilityIdentifier("retryBodyMergeRetention")
                    } else if model.bodyDirty || model.bodyRecoveryPending {
                        Button("重试保存正文") { endEditing(); Task { _ = await model.flushBody() } }.disabled(model.isBusy).accessibilityIdentifier("retryBodySave")
                        if model.canReviewBodyVersions { Button("核对双方版本") {
                            endEditing()
                            Task {
                                await model.reviewBodyConflict()
                                if model.bodyConflict != nil { endEditing(); showConflict = true }
                            }
                        }.disabled(model.isBusy).accessibilityIdentifier("reviewBodyConflict") }
                    } else if model.isOriginalSource && model.isReady {
                        Button("重新读取文件") { endEditing(); Task { await model.retryOriginalBodySource() } }
                            .disabled(model.isBusy).accessibilityIdentifier("retryExternalRefresh")
                    } else if !model.isReady {
                        Button("重试存储") { Task { await model.start() } }
                    }
                }.font(.caption)
            }
            if model.isOriginalSource && (model.bodyDirty || model.sourceUnavailable || (model.bodySourceIssue == .cloudConflict && model.cloudConflict == nil)) {
                Button(model.bodyDirty ? "导出本机草稿" : "导出当前正文") {
                    endEditing()
                    Task { if let retained = await model.exportRetainedBody() { document = retained; showExporter = true } }
                }.font(.caption).disabled(model.isBusy || model.bodyRecoveryUnavailable).accessibilityIdentifier("exportRetainedBody")
            }
            if model.hasRetainedBodyMerge && model.canReviewBodyVersions {
                Button("恢复未完成的合并") {
                    endEditing()
                    Task { await model.reviewBodyConflict(); if model.bodyConflict != nil { showConflict = true } }
                }.font(.caption).disabled(model.isBusy || model.bodyRecoveryUnavailable).accessibilityIdentifier("recoverBodyMerge")
            }
            if model.hasLegacyDraft {
                Button("恢复未完成的文字／插入") { endEditing(); showLegacy = true }
                    .font(.caption).disabled(model.isBusy || model.bodyRecoveryUnavailable).accessibilityIdentifier("recoverLegacyDraft")
            }
        }.padding(.horizontal, 16).padding(.vertical, 6).background(.regularMaterial)
    }

    private var exportTestValue: String {
        #if DEBUG
        if let value = ProcessInfo.processInfo.environment["RIJI_TEST_CONTAINER"], UUID(uuidString: value) != nil {
            return model.exportVerificationDigest
        }
        #endif
        return ""
    }

    var legacyRecovery: some View {
        NavigationStack {
            RijiForm {
                Section("尚未追加的原输入") {
                    Text("原草稿与录音凭据保留。核对目标与文字后，可明确追加到正文。")
                    Text(model.selectedDay.value)
                    Picker("原目标区块", selection: Binding(get: { model.selectedSectionID }, set: model.selectSectionID)) {
                        Text(model.section).tag(model.selectedSectionID)
                        ForEach(model.sectionChoices.filter { $0.id != model.selectedSectionID }) { Text($0.title).tag($0.id) }
                    }.disabled(!model.canEditLegacy).accessibilityIdentifier("legacySectionPicker")
                    TextEditor(text: Binding(get: { model.text }, set: model.updateText))
                        .frame(minHeight: 180).disabled(!model.canEditLegacy)
                        .accessibilityIdentifier("legacyDraftEditor")
                }
                if model.isInsertionUnknown {
                    Button("核对上次插入") { endEditing(); Task { await model.retryVoiceInsertion() } }.disabled(model.isBusy)
                    Button("打开录音恢复") { endEditing(); showLegacy = false; showVoice = true }.disabled(model.isBusy)
                } else {
                    Button("核对当前正文") { endEditing(); Task { await model.reviewCurrentSource() } }.disabled(!model.canEditLegacy).accessibilityIdentifier("refreshLegacySource")
                    DisclosureGroup("当前正文") { Text(model.markdown).textSelection(.enabled) }
                    Button(model.isPending ? "重试原保存" : "确认追加恢复的文字") {
                        endEditing()
                        Task {
                            if model.isPending { await model.retryLegacySave() } else { await model.save() }
                            if !model.hasLegacyDraft { showLegacy = false }
                        }
                    }.disabled(model.isBusy || (!model.isPending && !model.canSave)).accessibilityIdentifier("confirmLegacyDraft")
                }
                #if DEBUG
                if let harness = RecoveryControlTestHarness.shared { RecoveryControlTestGate(harness: harness) }
                #endif
                if let error = model.bodyMergeError ?? model.bodyError ?? model.errorMessage {
                    Text(error).foregroundStyle(Color(uiColor: RijiPalette.error)).accessibilityIdentifier("legacyRecoveryError")
                    if model.bodyError != nil || model.bodyMergeError != nil {
                        Text("请重试；如需核对正文双方版本，可完成返回正文后处理。原草稿继续保留。")
                    }
                }
            }.navigationTitle("恢复原草稿").navigationBarTitleDisplayMode(.inline)
                .interactiveDismissDisabled(true)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { endEditing(); showLegacy = false }.disabled(model.isBusy).accessibilityIdentifier("closeLegacyRecovery") } }
        }
    }

    var conflictReview: some View { bodyConflictReview(fullVersions: $showFullBodyVersions) }

    func bodyConflictReview(fullVersions: Binding<Bool>) -> some View {
        NavigationStack {
            RijiForm {
                Section {
                    Text("文件已在其他编辑器中更新，本机也有修改。选择后会再次核对文件，双方内容保留在恢复版本中。")
                    Button("使用文件最新版本") {
                        endEditing(); Task { if await model.useLatestBodyVersion() { showConflict = false } }
                    }.disabled(!model.canEditBodyMerge || model.bodyConflict?.source == nil).accessibilityIdentifier("useLatestBodyVersion")
                    Button("保留本机编辑") {
                        endEditing(); Task { if await model.keepLocalBodyVersion() { showConflict = false } }
                    }.disabled(!model.canEditBodyMerge || model.localBodyChoiceExplanation != nil).accessibilityIdentifier("keepLocalBodyVersion")
                    if let explanation = model.localBodyChoiceExplanation {
                        Text(explanation).font(.caption).accessibilityIdentifier("keepLocalMetadataNotice")
                    }
                    Button("重新读取当前版本（保留合并文字）") { endEditing(); Task { await model.reviewBodyConflict(refreshingSource: true) } }
                        .disabled(!model.canEditBodyMerge).accessibilityIdentifier("refreshBodyMergeSource")
                }
                Section("只比较不同的地方") {
                    MarkdownDiffView(current: Data(model.bodyMergeText.utf8), other: model.bodyConflict?.source ?? Data(),
                                     otherLabel: "当前文件版本", summaryIdentifier: "bodyDiffSummary", editable: true,
                                     disabled: !model.canEditBodyMerge) { expected, replacement in
                        applyBodyDifference(expected: expected, replacement: replacement)
                    }
                }
                DisclosureGroup(isExpanded: fullVersions) {
                    ScrollView {
                        Text(model.bodyConflict?.source.map { String(decoding: $0, as: UTF8.self) } ?? "文件尚不存在")
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled).accessibilityIdentifier("bodyConflictSource")
                    }.frame(height: 260).accessibilityIdentifier("bodyConflictSourceScroll")
                    TextEditor(text: Binding(get: { model.bodyMergeText }, set: model.updateBodyMerge)).frame(height: 260)
                        .disabled(!model.canEditBodyMerge).accessibilityIdentifier("bodyConflictMerge").id("bodyConflictMerge")
                    Text("双方原文和合并结果均保留版本。确认时会再次核对当前文件。")
                        .font(.caption)
                } label: {
                    Text("完整原文与整篇合并").accessibilityIdentifier("bodyFullVersions")
                }.id("bodyFullVersions")
                #if DEBUG
                if let harness = RecoveryControlTestHarness.shared { RecoveryControlTestGate(harness: harness) }
                #endif
                if let error = model.bodyMergeError ?? model.bodyError { Text(error).foregroundStyle(Color(uiColor: RijiPalette.error)).accessibilityIdentifier("bodyMergeError") }
                if model.isOriginalSource && !model.canReviewBodyVersions {
                    Button("重新读取文件") { endEditing(); Task { await model.retryOriginalBodySource() } }
                        .disabled(model.isBusy).accessibilityIdentifier("retryConflictSource")
                }
                DisclosureGroup {
                    MarkdownDiffView(current: model.bodyConflict?.source ?? Data(), other: Data(model.bodyMergeText.utf8),
                                     otherLabel: "拟保存结果", summaryIdentifier: "bodyFinalDiffSummary")
                } label: { Text("最终保存差异").accessibilityIdentifier("bodyFinalDiff") }
                Button("确认保存以上合并结果") {
                    endEditing()
                    Task { if await model.resolveBodyConflict(with: model.bodyMergeText) { showConflict = false } }
                }.disabled(!model.canEditBodyMerge).accessibilityIdentifier("confirmBodyMerge")
            }.accessibilityIdentifier("bodyConflictReviewForm")
                .navigationTitle("核对正文版本").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("稍后处理") { endEditing(); Task { if await model.dismissBodyConflict() { showConflict = false } } }.disabled(model.isBusy).accessibilityIdentifier("closeBodyMerge") } }
                .interactiveDismissDisabled(true)
        }
    }

    func applyBodyDifference(expected: Data, replacement: Data) -> Bool {
        guard model.canEditBodyMerge, Data(model.bodyMergeText.utf8) == expected,
              let text = String(data: replacement, encoding: .utf8) else { return false }
        model.updateBodyMerge(text)
        return Data(model.bodyMergeText.utf8) == replacement
    }

    @ToolbarContentBuilder private var navigationItems: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            Button { endEditing(); Task { if await model.prepareDirectory() { showDirectory = true } } } label: { Image(systemName: "books.vertical") }
                .accessibilityLabel("日记目录").accessibilityIdentifier("diaryDirectory")
        }
        ToolbarItem(placement: .principal) {
            Button {
                endEditing()
                dateSelection = model.selectedDay.date(in: pickerTimeZone)
                showDate = true
            } label: {
                HStack(spacing: 5) { Text(model.selectedDay.value); Image(systemName: "chevron.down").font(.caption2) }
            }
            .foregroundStyle(Color(uiColor: RijiPalette.text)).accessibilityIdentifier("selectedDate")
        }
        ToolbarItem(placement: .topBarTrailing) {
            if model.selectedDay != model.today {
                Button("回到今天", systemImage: "calendar") { endEditing(); Task { _ = await model.navigate(to: model.today) } }
                    .labelStyle(.iconOnly).accessibilityIdentifier("returnToday")
            }
        }
        ToolbarItem(placement: .topBarTrailing) {
            Menu {
                #if DEBUG
                if model.isExternalRefreshFixture {
                    Button("Synthetic external edit") { endEditing(); model.simulateExternalRefreshFixture(divergent: false) }
                        .accessibilityIdentifier("simulateExternalEdit")
                    Button("Synthetic competing edits") { endEditing(); model.simulateExternalRefreshFixture(divergent: true) }
                        .accessibilityIdentifier("simulateCompetingEdits")
                }
                #endif
                Button("AI 讨论来源", systemImage: "text.bubble") {
                    aiSources.open(model, committing: endEditing)
                }.accessibilityIdentifier("diaryAISources")
                if model.hasCloudRecovery {
                    Button("查看版本备份", systemImage: "clock.arrow.circlepath") {
                        endEditing()
                        Task { await model.reviewCloudVersions(); showCloudConflict = model.cloudReviewPresented }
                    }.disabled(model.isBusy).accessibilityIdentifier("cloudVersionBackups")
                }
                Button("录音记录", systemImage: "waveform") {
                    endEditing()
                    Task { if await model.prepareDirectory() { showVoice = true } }
                }
                    .accessibilityIdentifier("voiceLibrary").disabled(model.isOriginalSource || !model.isReady)
                Button(model.bodyRecoveryUnavailable ? "导出已保存版本 Markdown" : "导出 Markdown", systemImage: "square.and.arrow.up") {
                    endEditing()
                    let day = model.selectedDay
                    Task {
                        if let captured = await (model.bodyRecoveryUnavailable ? model.exportSavedBodyVersion() : model.export(day: day)) { document = captured; showExporter = true }
                    }
                }.disabled(!model.isSaved && !model.bodyDirty).accessibilityIdentifier("exportMarkdown")
            } label: { Image(systemName: "ellipsis") }
            .accessibilityLabel("更多").accessibilityIdentifier("moreMenu")
        }
    }

    private var datePicker: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                DatePicker("选择日记日期", selection: $dateSelection, displayedComponents: .date)
                    .datePickerStyle(.graphical).environment(\.timeZone, pickerTimeZone)
                Button("回到今天") {
                    Task { if await model.navigate(to: model.today) { showDate = false } }
                }.buttonStyle(RijiPrimaryButtonStyle()).disabled(model.isBusy).accessibilityIdentifier("dateSheetToday")
                Text("没有文件的日期会先预览原模板，开始编辑才创建文件。")
                    .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                }.padding()
            }.rijiPage().navigationTitle("选择日期").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("取消") { showDate = false } }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("打开") {
                            Task {
                                let day = JournalDay(date: dateSelection, timeZone: pickerTimeZone)
                                if await model.navigate(to: day) { showDate = false }
                            }
                        }.disabled(model.isBusy)
                    }
                }
        }.presentationDetents([.large])
    }

}

#if DEBUG
private struct RecoveryControlTestGate: View {
    @ObservedObject var harness: RecoveryControlTestHarness
    var body: some View {
        Section("Synthetic test gate") {
            Text(harness.reached ? "held" : "ready").accessibilityIdentifier("recoveryGateState")
            Button("Release injected failure") { harness.release() }.accessibilityIdentifier("releaseRecoveryGate")
        }
    }
}
#endif

/// An immutable, local reading snapshot; the parent dismisses it when document identity or bytes change.
struct DiaryAISourceDocument: Identifiable {
    let id = UUID()
    let sourceIdentity: String
    let day: JournalDay
    let markdown: String
}

@MainActor final class DiaryAISourceSession: ObservableObject {
    @Published var document: DiaryAISourceDocument?

    func open(_ model: DiaryViewModel, committing commit: () -> Void) {
        commit()
        document = DiaryAISourceDocument(sourceIdentity: model.sourceIdentity, day: model.selectedDay, markdown: model.markdown)
    }

    func invalidateIfChanged(_ model: DiaryViewModel) {
        guard let document else { return }
        if document.sourceIdentity != model.sourceIdentity || document.day != model.selectedDay
            || !document.markdown.utf8.elementsEqual(model.markdown.utf8) { self.document = nil }
    }
}

struct DiaryAISourcesView: View {
    let document: DiaryAISourceDocument
    let close: () -> Void
    @State private var showRaw = false

    var body: some View {
        let sources = SavedAIResult.records(in: document.markdown)
        NavigationStack {
            RijiList {
                Section {
                    Text("这是日记中保存的本地来源记录，未经在线核验。保存 AI 建议不代表采纳或已经执行。")
                        .font(.callout).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                    Text("日记日期：" + document.day.value).font(.caption)
                }
                if sources.isEmpty {
                    Text("这份日记中没有可识别的 AI 讨论来源记录。")
                }
                ForEach(Array(sources.enumerated()), id: \.offset) { index, source in
                    Section("来源 \(index + 1)") {
                        Text(source.provenance.topic).font(.headline)
                        Text("参与导师：" + source.mentorNames)
                        Text("讨论时间：" + readableDate(source.provenance.recordedAt))
                        Text("保存日期：" + source.provenance.savedDate)
                        Text("保存前用户编辑：" + (source.provenance.editedByUser ? "是" : "否"))
                    }
                }
                Section {
                    Button { showRaw.toggle() } label: {
                        HStack {
                            Text("查看原始 Markdown")
                            Spacer()
                            Image(systemName: showRaw ? "chevron.down" : "chevron.right")
                        }
                    }.accessibilityIdentifier("diaryAIRawDisclosure")
                        .accessibilityValue(showRaw ? "已展开" : "已收起")
                    if showRaw {
                        Text(document.markdown).font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled).accessibilityIdentifier("diaryAIRawMarkdown")
                    }
                } footer: {
                    Text("原文只读，包含完整来源标识与内部编号。")
                }
            }.navigationTitle("AI 讨论来源").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("完成", action: close).accessibilityIdentifier("closeDiaryAISources")
                    }
                }
        }
    }

    private func readableDate(_ value: String) -> String {
        let formatter = ISO8601DateFormatter()
        var date = formatter.date(from: value)
        if date == nil { formatter.formatOptions.insert(.withFractionalSeconds); date = formatter.date(from: value) }
        return date?.formatted(date: .abbreviated, time: .shortened) ?? value
    }
}
