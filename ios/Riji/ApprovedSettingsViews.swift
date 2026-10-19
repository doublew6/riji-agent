import SwiftUI
import RijiCore

struct DiarySettingsView: View {
    @ObservedObject var model: DiaryViewModel
    @ObservedObject var mobile: MobileClientModel
    private var appVersion: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "—"
        return "Riji \(version) · 构建 \(build)"
    }
    var body: some View {
        RijiList {
            Section("日记与文件") {
                NavigationLink { DiaryFilesSettingsView(model: model, mobile: mobile) } label: {
                    RijiRow(title: "文件与模板", detail: model.sourceLabel, symbol: "folder")
                }.accessibilityIdentifier("settingsFiles")
            }
            Section("资料与智能") {
                NavigationLink { DiaryAISettingsView(mobile: mobile) } label: {
                    RijiRow(title: "资料与 AI", detail: mobile.connectionStatus, symbol: "checkmark.shield")
                }.accessibilityIdentifier("settingsDataAI")
            }
            Section("任务") {
                NavigationLink { DiaryTasksView(model: model, mobile: mobile) } label: {
                    RijiRow(title: "任务与处理", detail: "日记工作流与记忆处理", symbol: "clock")
                }.accessibilityIdentifier("settingsTasks")
            }
            Section {
                NavigationLink { DiaryExportRecoveryView(model: model, mobile: mobile) } label: {
                    RijiRow(title: "导出与恢复", detail: "当前 Markdown、录音与保留内容", symbol: "square.and.arrow.up")
                }.accessibilityIdentifier("settingsExportRecovery")
            } header: {
                Text("数据")
            } footer: {
                Text(appVersion).accessibilityIdentifier("settingsAppVersion")
            }
        }.listSectionSpacing(20).navigationTitle("设置").navigationBarTitleDisplayMode(.inline)
    }
}

struct DiaryAISettingsView: View {
    @ObservedObject var mobile: MobileClientModel
    var body: some View {
        RijiList {
            Section("本人连接") {
                NavigationLink { MobileConnectionView(model: mobile) } label: {
                    RijiRow(title: "AI 服务", detail: mobile.connectionStatus, symbol: "network")
                }.accessibilityIdentifier("settingsConnection")
            }
            Section("来源、用途与去向") {
                NavigationLink { MobilePrivacyView(model: mobile) } label: {
                    RijiRow(title: "资料与 AI 使用", detail: "核对四项用途及实际处理去向", symbol: "hand.raised")
                }.accessibilityIdentifier("settingsPrivacy")
                Text("目录授权、iCloud 文件同步、Air 本地存储与模型处理分别管理。模型接收的内容以当前授权和服务返回的去向为准。")
                    .font(.callout).foregroundStyle(Color(uiColor: RijiPalette.secondary))
            }
        }.navigationTitle("资料与 AI").navigationBarTitleDisplayMode(.inline)
    }
}

struct DiaryTasksView: View {
    @ObservedObject var model: DiaryViewModel
    @ObservedObject var mobile: MobileClientModel
    var body: some View {
        RijiList {
            Section("日记任务") {
                RijiUnavailable(title: "自动任务暂不可管理", detail: "当前连接没有提供 Air 原任务的时间、时区与运行结果，不能在此修改或运行。原任务保持原配置。")
                    .accessibilityIdentifier("settingsTasksUnavailable")
                if let workflows = model.workflows {
                    NavigationLink("手动准备周记与月记") { ReviewsView(model: workflows, diary: model) }
                    NavigationLink("手动确认今天的 MIT") { MITView(model: workflows, diary: model) }
                }
            }
            Section("记忆处理") {
                RijiUnavailable(title: "记忆周期任务尚未接入", detail: "当前可查看既有提取状态；周复盘计划与远端任务管理尚不可用。")
                NavigationLink("查看提取状态与用途") { MobilePrivacyView(model: mobile) }
            }
        }.navigationTitle("任务与处理").navigationBarTitleDisplayMode(.inline)
    }
}

struct DiaryExportRecoveryView: View {
    @ObservedObject var model: DiaryViewModel
    @ObservedObject var mobile: MobileClientModel
    @State private var exportRequest: SettingsMarkdownExportRequest?
    @State private var isPreparingExport = false
    @State private var showVoice = false
    @State private var showVersions = false
    private var exportTestValue: String {
        #if DEBUG
        if let value = ProcessInfo.processInfo.environment["RIJI_TEST_CONTAINER"], UUID(uuidString: value) != nil {
            return model.exportVerificationDigest
        }
        #endif
        return ""
    }
    var body: some View {
        RijiList {
            Section("当前日记 · " + model.selectedDay.value) {
                Text("仅导出当前 Markdown 正文，不包含附件、其他日期、服务数据库或授权。原文件保留。")
                    .font(.callout)
                Button(model.bodyRecoveryUnavailable ? "导出已保存版本 Markdown" : "导出当前 Markdown") {
                    let day = model.selectedDay
                    isPreparingExport = true
                    Task {
                        defer { isPreparingExport = false }
                        if let document = await (model.bodyRecoveryUnavailable ? model.exportSavedBodyVersion() : model.export(day: day)) {
                            exportRequest = SettingsMarkdownExportRequest(document: document)
                        }
                    }
                }.disabled(model.isBusy || isPreparingExport || exportRequest != nil || (!model.isSaved && !model.bodyDirty))
                    .accessibilityIdentifier("settingsExportMarkdown")
                if model.status.contains("Markdown") && model.status.contains("已导出") {
                    Text("上次导出：" + model.status).font(.callout).accessibilityIdentifier("settingsExportStatus")
                        .accessibilityValue(exportTestValue)
                }
                if let error = model.errorMessage ?? model.bodyError { Text(error).foregroundStyle(Color(uiColor: RijiPalette.error)) }
                if let error = model.cloudError {
                    Text(error).foregroundStyle(Color(uiColor: RijiPalette.error)).accessibilityIdentifier("settingsVersionError")
                    Button("重试读取版本备份") { Task { await model.reviewCloudVersions(); showVersions = model.cloudReviewPresented } }
                        .disabled(model.isBusy).accessibilityIdentifier("settingsRetryVersionBackups")
                }
                if model.hasCloudRecovery {
                    Button("查看版本备份") { Task { await model.reviewCloudVersions(); showVersions = model.cloudReviewPresented } }
                        .disabled(model.isBusy).accessibilityIdentifier("settingsVersionBackups")
                }
            }
            Section("本机保留内容") {
                if let _ = model.voice, !model.isOriginalSource {
                    Button("录音记录") { showVoice = true }.accessibilityIdentifier("settingsRecordings")
                } else {
                    NavigationLink("打开原有 App 日记与录音入口") { DiaryFilesSettingsView(model: model, mobile: mobile) }
                    Text("原录音保留在 App 本地日记中。切换到原有 App 日记后，可在这里查看录音；不会自动迁移到原目录。")
                        .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                }
                NavigationLink("未发草稿与原操作") { MobileDraftArchiveView(model: mobile) }
                    .accessibilityIdentifier("settingsRetainedDrafts")
                NavigationLink("恢复原文件夹授权") { DiaryFilesSettingsView(model: model, mobile: mobile) }
            }
            Section("完整备份与恢复") {
                RijiUnavailable(title: "完整备份恢复暂不可用", detail: "当前没有经过核验的全量导出与恢复接口。可先导出当前正文或保留草稿，回到日记核对具体冲突；不会覆盖原件或恢复旧授权。")
            }
        }.navigationTitle("导出与恢复").navigationBarTitleDisplayMode(.inline)
            .background {
                if let request = exportRequest {
                    SettingsMarkdownExporter(document: request.document) { result in
                        guard exportRequest?.id == request.id else { return }
                        if let result { model.exportCompleted(result, document: request.document) }
                        exportRequest = nil
                    }.id(request.id)
                }
            }
            .sheet(isPresented: $showVoice) { if let voice = model.voice { VoiceSheet(diary: model, model: voice) } }
            .sheet(isPresented: $showVersions) { CloudVersionReviewView(model: model) { showVersions = false } }
    }
}

private struct SettingsMarkdownExportRequest: Identifiable {
    let id = UUID()
    let document: MarkdownDocument
}

private struct SettingsMarkdownExporter: View {
    let document: MarkdownDocument
    let finished: (Result<URL, Error>?) -> Void
    @State private var isPresented = false
    @State private var started = false

    var body: some View {
        Color.clear.frame(width: 0, height: 0)
            .fileExporter(isPresented: $isPresented, document: document,
                          contentTypes: MarkdownDocument.readableContentTypes, defaultFilename: document.filename,
                          onCompletion: { finished($0) }, onCancellation: { finished(nil) })
            .onAppear {
                // Build the exporter with its immutable document before presenting it.
                guard !started else { return }
                started = true; isPresented = true
            }
    }
}
