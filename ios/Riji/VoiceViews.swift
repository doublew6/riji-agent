import SwiftUI
import UniformTypeIdentifiers
import RijiCore

struct VoiceSheet: View {
    @ObservedObject var diary: DiaryViewModel
    @ObservedObject var model: VoiceViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var document: VoiceAudioDocument?
    @State private var exporting = false
    @State private var importing = false
    @State private var confirmingDelete = false
    @State private var syntheticPauseCount = 0
    @State private var syntheticEndCount = 0
    @State private var confirmingAbandon = false
    @State private var exportedDigest = ""
    @State private var fileError: String?
    @State private var showPieces = true
    @FocusState private var correcting: Bool

    var body: some View {
        NavigationStack {
            Form {
                if diary.requiresSectionSelection {
                    Section("选择语音插入位置") {
                        Text("原区块已移除或存在同名标题。录音和文字保留；可回正文恢复唯一的原标题后重新预览，或选择区块新建录音。")
                            .accessibilityIdentifier("voiceTargetRecovery")
                        Picker("目标区块", selection: Binding<Data?>(get: { diary.requiresSectionSelection ? nil : diary.selectedSectionID }, set: { if let id = $0 { diary.selectSectionID(id) } })) {
                            Text("请选择").tag(Optional<Data>.none)
                            ForEach(diary.sectionChoices) { Text($0.title.isEmpty ? "空标题" : $0.title).tag(Optional($0.id)) }
                        }
                    }
                }
                if diary.isInsertionUnknown { insertionControls }
                if model.recording == nil, let error = model.errorMessage {
                    Section("读取录音失败") { Text(error).foregroundStyle(.red).accessibilityIdentifier("voiceSelectionError") }
                }
                if model.recording == nil && model.insertionVerifying {
                    Section("准备语音记录") {
                        ProgressView("正在分批核验保存记录…")
                        Button("取消本次准备") { model.cancelInsertionVerification() }
                    }
                }
                if let value = model.recording {
                    recordingHeader(value)
                    if !value.segments.isEmpty { audioPieces(value) }
                    correctionEditor(value)
                    if !diary.isInsertionUnknown { insertionControls }
                    if !value.segments.isEmpty { deletionControls(value) }
                    #if DEBUG
                    if Self.isTestNamespace { syntheticControls(value) }
                    #endif
                }
                recordingLibrary
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: model.recording?.segments.count) { _, count in if (count ?? 0) > 3 { showPieces = false } }
            .navigationTitle("语音记录").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { Task { if await model.dismiss() { dismiss() } } }
                        .disabled(model.preventsDismissal || diary.isBusy).accessibilityIdentifier("closeVoice")
                }
            }
            .interactiveDismissDisabled(true)
            .task { await model.loadLibrary() }
            .confirmationDialog("确认删除原音频？", isPresented: $confirmingDelete, titleVisibility: .visible) {
                Button("确认只删除原音频", role: .destructive) { Task { await model.deleteAudio() } }
            }
            .confirmationDialog("放弃这次尚未写入的插入确认？", isPresented: $confirmingAbandon, titleVisibility: .visible) {
                Button("确认放弃这次插入") { Task { await diary.abandonVoiceInsertion() } }
            } message: {
                Text("目标：\(diary.abandonableInsertion?.target.day.value ?? "") · \(diary.abandonableInsertion?.target.section ?? "")。不会删除日记输入、校正文字或原音频；以后需要重新预览确认。")
            }
            .fileExporter(isPresented: $exporting, document: document,
                          contentType: document?.contentType ?? .wav, defaultFilename: document?.filename) { result in
                exportFinished(result)
            }
            .fileImporter(isPresented: $importing, allowedContentTypes: [.wav], onCompletion: importFinished)
        }
    }

    private func recordingHeader(_ value: VoiceRecording) -> some View {
        Section("\(value.target.day.value) · \(value.target.section)") {
            Text("原录音与文字先保存在此设备。")
            if diary.needsOriginalVoiceTarget(value.target) {
                Button("打开录音的原日期与区块") {
                    Task {
                        if await diary.navigate(to: value.target.day) {
                            diary.updateSection(value.target.section); await diary.flushDraft()
                        }
                    }
                }.disabled(diary.isBusy || diary.isInsertionUnknown || model.preventsDismissal).accessibilityIdentifier("recoverOriginalVoiceTarget")
            }
            Text(String(format: "已核验 %.1f 秒 · 当前段 %.1f 秒", value.duration, model.liveDuration))
                .accessibilityIdentifier("voiceDuration")
            if let piece = value.lastPlayable, let index = value.segments.firstIndex(where: { $0.id == piece.id }) {
                Text(String(format: "最后已核验：第 %d 段 · %.1f 秒", index + 1, piece.duration ?? 0))
                    .font(.caption).accessibilityIdentifier("voiceLastPlayable")
            }
            if let warning = model.integrityWarning { Text(warning).accessibilityIdentifier("voiceIntegrityWarning") }
            Text(model.status).font(.caption).accessibilityIdentifier("voiceStatus")
                .accessibilityValue(model.playbackVerified ? "playback-verified" : exportedDigest)
            if let error = model.errorMessage ?? fileError { Text(error).foregroundStyle(.red) }
            if let _ = model.insertion {
                Text("这份录音已插入日记输入区。原音频与文字仍可查看；继续记录请新建录音。")
                    .accessibilityIdentifier("voiceAlreadyInserted")
                Button("新建录音") { Task { _ = await diary.captureVoice() } }
                    .disabled(model.busy || diary.isBusy || diary.isInsertionUnknown).accessibilityIdentifier("newVoiceRecording")
            } else if !model.insertionKnown {
                if model.insertionVerifying {
                    ProgressView("正在分批核验保存记录…").accessibilityIdentifier("voiceInsertionVerifying")
                    Button("取消本次核验") { model.cancelInsertionVerification() }.accessibilityIdentifier("cancelVoiceInsertionVerification")
                } else {
                    Text("插入记录尚未核验，编辑暂不可用。")
                    Button("重试核验插入记录") { Task { await model.refreshInsertionState() } }
                }
            }
            VoiceCaptureSafetyControls(active: model.hardwareStopAvailable, pending: model.captureStopPending,
                pause: { Task { await model.pause() } }, end: { Task { await model.pause(ending: true) } })
            if model.stopNeedsRetry {
                Text("采集已停止，原件或结束状态尚待保留。请重试核验；不会自动恢复录音。")
                Button("重试保留停止状态") { Task { await model.retryRecovery() } }
            }
            if !value.deletionRequested && model.canEditRecording { recordingControls.disabled(diary.isBusy || diary.isInsertionUnknown) }
            if value.deletionRequested {
                Text(model.audioRemovalComplete ? "音频已删除，文字仍保留。" : "音频已申请删除，移除尚待核验；文字仍保留。")
            }
            if model.playbackState == .verifying {
                Text("正在核验原录音，尚未开始回放。").accessibilityIdentifier("voicePlaybackVerifying")
            }
            if model.playbackState == .playing || model.playbackState == .verifying {
                Button("停止回放") { model.stopPlayback() }.accessibilityIdentifier("stopVoicePlayback")
            }
        }
    }

    private func correctionEditor(_ value: VoiceRecording) -> some View {
        Section("校正文字") {
            if let notice = model.correctionOriginNotice { Text(notice).accessibilityIdentifier("voiceCorrectionOrigin") }
            if value.currentCorrectionUnavailable == true {
                Text("当前校正版本暂不可读，已保留其身份；恢复前不能改写或插入。原音频和历史可独立查看。")
                    .accessibilityIdentifier("voiceCurrentCorrectionError")
                Button("重试读取当前文字") { Task { await model.select(value) } }
            }
            TextEditor(text: Binding(get: { model.correction }, set: model.updateCorrection))
                .frame(minHeight: 120).focused($correcting).accessibilityIdentifier("voiceCorrection")
                .disabled(!model.canEditRecording || model.busy || diary.isBusy || diary.isInsertionUnknown)
            Text("已保留 \(model.retainedPieceCount) 段设备转写").font(.caption).accessibilityIdentifier("voiceTranscriptionProgress")
            Button("在此设备转写（中文）") { model.transcribe() }
                .disabled(!model.canEditRecording || model.transcribing || model.busy || diary.isBusy || diary.isInsertionUnknown || model.phase == .recording || value.deletionRequested)
                .accessibilityIdentifier("transcribeVoice")
            if model.currentInventory.contains(where: { !$0.usable }) {
                Text(VoiceSourceNotices.text(model.currentInventory))
                Button("仅继续转写当前可用原件") { model.transcribe(allowingUnavailable: true) }
                    .disabled(!model.canEditRecording || model.busy || model.transcribing || model.phase == .recording || diary.isBusy || diary.isInsertionUnknown)
            }
            if model.transcribing { Button("取消转写") { model.cancelTranscription() } }
            if let missing = model.emptyPiece, let index = value.segments.firstIndex(where: { $0.id == missing.segmentID }) {
                Text("第 \(index + 1) 段转写完成但没有文字，不能据此判断为静音。可重试、手填，或明确略过此段。")
                Button("略过这段并继续转写") { Task { await model.skipAndContinueTranscription() } }
                    .disabled(model.busy || !model.canEditRecording || diary.isBusy || diary.isInsertionUnknown)
            }
            ForEach(Array((value.segmentTranscripts ?? []).enumerated()), id: \.offset) { _, piece in
                DisclosureGroup("已保留的第 \((value.segments.firstIndex { $0.id == piece.media.segmentID } ?? 0) + 1) 段原稿") {
                    Text(piece.text).textSelection(.enabled)
                    Button("采用此段原稿") { Task { await model.adoptPiece(piece) } }.disabled(!model.canEditRecording || model.busy || diary.isBusy || diary.isInsertionUnknown)
                }
            }
            ForEach(value.skippedTranscripts ?? [], id: \.segmentID) { reference in
                Button("重新尝试曾略过的第 \((value.segments.firstIndex { $0.id == reference.segmentID } ?? 0) + 1) 段") {
                    Task { await model.retrySkippedPiece(reference) }
                }.disabled(!model.canEditRecording || model.busy || diary.isBusy || diary.isInsertionUnknown)
            }
            if model.correctionCleanupPending {
                Text("当前校正已保留；旧临时副本清理待重试，可继续关闭或插入。")
                Button("重试整理旧临时副本") { Task { await model.retryCorrectionCleanup() } }
            }
            if value.unavailableHistory?.isEmpty == false {
                Text("部分历史版本暂不可读，原引用保留。可以分别读取其他版本和原音频。")
            }
            ForEach(value.transcripts) { proposal in
                DisclosureGroup("原始转写 \(proposal.id.uuidString.prefix(6))") {
                    proposalContent(proposal, value: value)
                }
            }
            DisclosureGroup("所有转写版本与逐段原稿") {
                Button("转写历史第一页") { Task { await model.loadTranscriptHistory() } }
                Button("逐段原稿第一页") { Task { await model.loadTranscriptHistory(segments: true) } }
                ForEach(Array(model.transcriptHistory.enumerated()), id: \.offset) { _, reference in
                    Button("读取原稿 \(reference.id.uuidString.prefix(6))") { Task { await model.showTranscript(reference) } }
                }
                if let next = model.nextTranscriptOffset {
                    Button("下一页原稿") { Task { await model.loadTranscriptHistory(segments: model.transcriptHistoryIsSegments, offset: next) } }
                }
                if let proposal = model.selectedTranscript { proposalContent(proposal, value: value) }
                if let piece = model.selectedPiece {
                    Text("原件 \(piece.media.segmentID.uuidString.prefix(6)) · \(piece.media.hash.prefix(8))")
                    Text(piece.text).textSelection(.enabled)
                    Button("采用选中原稿") { Task { await model.adoptPiece(piece) } }
                        .disabled(!model.canEditRecording || model.busy || diary.isBusy || diary.isInsertionUnknown)
                }
                if let error = model.historyError { Text(error).foregroundStyle(.red).accessibilityIdentifier("voiceHistoryError") }
            }
            DisclosureGroup("已保留的校正版本") {
                Button("查看／返回历史第一页") { Task { await model.loadHistory() } }
                if model.historyRebuildingCount > 0 {
                    Text("历史版本仍在恢复，尚有 \(model.historyRebuildingCount) 份待核对；当前列表未完成。")
                    Button("继续恢复校正历史") { Task { await model.loadHistory() } }
                }
                if model.historyUnavailableCount > 0 { Text("有 \(model.historyUnavailableCount) 份旧版本不可读，原文件保留。") }
                ForEach(Array(model.history.enumerated()), id: \.offset) { _, reference in
                    Button("读取版本 \(reference.id.uuidString.prefix(6))") { Task { await model.showHistory(reference) } }
                }
                if let next = model.nextHistoryOffset {
                    Button("下一页校正版本") { Task { await model.loadHistory(offset: next) } }
                }
                if let version = model.selectedHistory { Text(version.text).textSelection(.enabled) }
                if let error = model.historyError { Text(error).foregroundStyle(.red) }
            }
        }
    }

    private func proposalContent(_ proposal: VoiceTranscript, value: VoiceRecording) -> some View {
        let sources = proposal.media.map { reference in
            String(reference.segmentID.uuidString.prefix(6)) + ":" + String(reference.hash.prefix(8))
        }.joined(separator: "、")
        return VStack(alignment: .leading) {
            Text("原稿 \(proposal.id.uuidString.prefix(6)) · 原件 " + sources)
                .font(.caption)
            if let inventory = proposal.inventory {
                let notice = VoiceSourceNotices.text(inventory)
                if !notice.isEmpty { Text(notice).accessibilityIdentifier("voiceProposalNotice") }
            } else if let skipped = proposal.skipped, !skipped.isEmpty {
                Text("不完整转写，略过片段：" + skipped.map { ref in String((value.segments.firstIndex { $0.id == ref.segmentID } ?? 0) + 1) }.joined(separator: "、"))
            }
            Text(proposal.text).textSelection(.enabled).accessibilityIdentifier("voiceProposalText")
            Button("采用这份原稿") { model.adopt(proposal) }
                .disabled(!model.canEditRecording || model.busy || diary.isBusy || diary.isInsertionUnknown)
        }
    }

    private func deletionControls(_ value: VoiceRecording) -> some View {
        Section {
            Button(value.deletionRequested ? "重试删除原音频" : "删除原音频", role: .destructive) { confirmingDelete = true }
                .disabled(model.busy || model.phase == .recording || diary.isInsertionUnknown)
            Text("只删除这份录音的原音频；已保存日记、校正文字及插入记录保留，已有链接不会自动改写。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    #if DEBUG
    private func syntheticControls(_ value: VoiceRecording) -> some View {
        Section("合成音频验收") {
            Text("纯音 WAV 用于文件与回放验证，不代表中文识别成功。")
            if ProcessInfo.processInfo.environment["RIJI_TEST_VOICE_ACTIONS"] == "1" {
                VoiceCaptureSafetyControls(active: true, pending: false, pause: { syntheticPauseCount += 1 }, end: { syntheticEndCount += 1 })
                Text("pause=\(syntheticPauseCount),end=\(syntheticEndCount)").accessibilityIdentifier("voiceActionCounts")
            }
            if ProcessInfo.processInfo.environment["RIJI_TEST_VOICE_HISTORY"] == "1" {
                Button("准备合成历史原稿") { Task { await model.prepareSyntheticHistory(VoiceAudioDocument.syntheticWAV) } }
                    .accessibilityIdentifier("prepareSyntheticVoiceHistory")
            }
            Button("导出合成 WAV") {
                document = VoiceAudioDocument(data: VoiceAudioDocument.syntheticWAV, filename: "Riji-Synthetic.wav")
                exporting = true
            }.accessibilityIdentifier("exportSyntheticVoice")
            Button("从文件导入测试 WAV") { importing = true }
                .disabled(!model.canEditRecording || model.busy || diary.isBusy || diary.isInsertionUnknown || value.ended || value.deletionRequested)
                .accessibilityIdentifier("importSyntheticVoice")
        }
    }
    #endif

    private var recordingLibrary: some View {
        Section("录音记录") {
            Text("查看本机保存的录音和未完成的文字记录。").font(.caption)
            Button("新建待整理记录") { Task { _ = await diary.captureVoice() } }
                .disabled(model.preventsDismissal || diary.isBusy || diary.isInsertionUnknown)
                .accessibilityIdentifier("prepareVoiceRecord")
            Text("先整理文字或导入音频；需要采集时再点开始录音。").font(.caption)
            if model.recording == nil, let error = diary.errorMessage { Text(error).foregroundStyle(.red) }
            if model.derivedCleanupPending {
                Text("本地音频工作副本清理尚未完成，原件与文字保留。")
                Button("重试清理音频工作副本") { Task { await model.retryDerivedCleanup() } }
            }
            if let error = model.libraryError { Text(error).foregroundStyle(.red).accessibilityIdentifier("voiceLibraryError") }
            if model.libraryUnreadableCount > 0 {
                Text("有 \(model.libraryUnreadableCount) 份录音状态暂不可读，原文件保留。")
                    .accessibilityIdentifier("voiceLibraryUnreadable")
            }
            if model.libraryRebuildingCount > 0 {
                Text("录音索引仍在恢复，尚有 \(model.libraryRebuildingCount) 份待核对；当前列表尚非完整排序。")
                Button("继续恢复录音索引") { Task { await model.loadLibrary() } }.disabled(model.libraryLoading)
            }
            if model.libraryLimitedCount > 0 { Text("本页有 \(model.libraryLimitedCount) 份记录超过读取上限，文件保留；可以继续下一页。") }
            HStack {
                Button("上一页录音") { Task { await model.previousLibraryPage() } }.disabled(model.libraryPageNumber <= 1)
                Text("第 \(model.libraryPageNumber) 页")
                Button("下一页录音") { Task { await model.nextLibraryPage() } }.disabled(model.nextLibraryCursor == nil)
            }.buttonStyle(.borderless).disabled(model.busy || model.libraryLoading)
            if model.libraryError != nil || model.libraryUnreadableCount > 0 || model.libraryLimitedCount > 0 {
                Button("重试读取录音库") { Task { await model.loadLibrary() } }
            } else if model.library.isEmpty && model.libraryRebuildingCount == 0 { Text("暂无其他录音") }
            Button("刷新并返回录音第一页") { Task { await model.restartLibrary() } }
                .disabled(model.libraryLoading).accessibilityIdentifier("refreshVoiceLibrary")
            ForEach(model.library) { recording in
                Button {
                    Task { await model.selectSummary(recording) }
                } label: {
                    VStack(alignment: .leading) {
                        Text("\(recording.day.value) · \(recording.sectionLabel)")
                        Text(recording.createdAt, format: .dateTime.year().month().day().hour().minute().second()).font(.caption)
                        Text(String(format: "%.1f 秒 · %@", recording.duration,
                                    recording.deletionRequested ? "已申请删除音频，文字保留" : "\(recording.segmentCount) 段待查看"))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }.disabled(model.preventsDismissal || diary.isInsertionUnknown)
                    .accessibilityIdentifier("voice-library-\(recording.id.uuidString)")
            }
        }
    }

    private func importFinished(_ result: Result<URL, Error>) {
        guard case .success(let url) = result else { return }
        let access = url.startAccessingSecurityScopedResource()
        defer { if access { url.stopAccessingSecurityScopedResource() } }
        do {
            let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? Int.max
            guard size <= 32 * 1024 * 1024 else { throw VoiceError.limit }
            let data = try Data(contentsOf: url)
            Task { await model.importAudio(data) }
        } catch { fileError = "未能读取音频。原录音与文字保留，请重试。" }
    }

    private var recordingControls: some View {
        Group {
            if model.phase == .permission {
                Button("取消录音请求") { model.cancelPermission() }
            } else if model.recording?.ended == false && !model.hardwareStopAvailable && !model.captureStopPending {
                Button(model.phase == .idle ? "开始录音" : "继续录音") { Task { await model.record() } }
                    .disabled(!model.canRecord).accessibilityIdentifier("recordVoice")
                if model.recording?.segments.isEmpty == false {
                    Button("结束录音") { Task { await model.pause(ending: true) } }.disabled(model.busy)
                }
            }
            if model.recording?.segments.contains(where: { !$0.finalized }) == true {
                Button("核对末段／重试保留") { Task { await model.retryRecovery() } }
                    .disabled(model.busy || model.phase == .recording)
            }
            if model.needsSettings { Link("系统权限设置", destination: URL(string: UIApplication.openSettingsURLString)!) }
        }
    }

    private func audioPieces(_ value: VoiceRecording) -> some View {
        Section("原音频（\(value.segments.count) 段）") {
            DisclosureGroup("查看录音片段", isExpanded: $showPieces) {
            ForEach(Array(value.segments.enumerated()), id: \.element.id) { index, segment in
                VStack(alignment: .leading) {
                    if model.currentInventory.first(where: { $0.segmentID == segment.id })?.unavailable == true {
                        Text("第 \(index + 1) 段当前不可用；原身份与历史时长保留。")
                    }
                    Text(segment.finalized ? String(format: "第 %d 段 · %.1f 秒", index + 1, segment.duration ?? 0) : "第 \(index + 1) 段 · 尚未核验音频是否可用")
                    HStack {
                        Button("回放第 \(index + 1) 段") { Task { await model.play(segment) } }
                            .disabled(!segment.finalized || model.busy || model.transcribing)
                            .accessibilityIdentifier("playVoice-\(index)")
                        Button("导出原音频") {
                            Task {
                                if let data = await model.export(segment) {
                                    document = VoiceAudioDocument(data: data, filename: segment.relativePath)
                                    exporting = true
                                }
                            }
                        }.accessibilityIdentifier("exportVoice-\(index)")
                    }.buttonStyle(.borderless).disabled(model.busy || value.deletionRequested || model.phase == .recording || model.phase == .permission)
                    if !segment.finalized { Text("若录制启动失败，可能尚未生成音频。已有的未完成原件可能无法播放，可尝试导出恢复。").font(.caption) }
                }
            }
            }
        }
    }

    private var insertionControls: some View {
        Section("插入当前日记正文") {
            if diary.isInsertionUnknown {
                Text("上次插入结果尚待核对。编辑与日期切换暂时停用，原文字保留。")
                Button("核对上次插入") { Task { await diary.retryVoiceInsertion() } }
                if let candidate = diary.abandonableInsertion {
                    Text("尚未写入：\(candidate.target.day.value) · \(candidate.target.section)")
                    Button("放弃这次未写入的插入确认") { confirmingAbandon = true }.disabled(diary.isBusy)
                }
            } else if diary.hasVoiceCommitRecovery {
                Text("录音文字与插入凭据已保留，追加到日记的保存尚未完成。")
                Button("重试原日记保存") {
                    Task { if await diary.retryVoiceDocumentCommit(), await model.dismiss() { dismiss() } }
                }.disabled(diary.isBusy).accessibilityIdentifier("retryVoiceDocumentCommit")
            } else if model.canEditRecording {
                DisclosureGroup("当前日记正文") { Text(diary.markdown).textSelection(.enabled) }
                if !diary.text.isEmpty { Text("同时保留并追加原草稿：").font(.caption); Text(diary.text).textSelection(.enabled) }
                Button("核对当前正文并刷新插入来源") { Task { await diary.refreshVoiceInsertionSource() } }
                    .disabled(model.busy || model.phase == .recording)
                Button("预览插入") { correcting = false; Task { await diary.prepareVoiceInsertion() } }
                    .disabled(model.busy || model.phase == .recording || model.recording?.deletionRequested != false)
                    .accessibilityIdentifier("previewVoiceInsertion")
                if let preview = model.preview {
                    Text(preview.preview).fixedSize(horizontal: false, vertical: true).textSelection(.enabled).accessibilityIdentifier("voiceInsertionPreview")
                    Text("确认后追加以上原文与音频相对链接到当前正文；原录音和插入凭据保留。")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("确认插入正文") {
                        Task { if await diary.insertVoiceIntoDocument(preview), await model.dismiss() { dismiss() } }
                    }.disabled(diary.isBusy || model.busy || model.phase == .recording || model.phase == .permission).accessibilityIdentifier("confirmVoiceInsertion")
                }
            }
            if let error = diary.errorMessage { Text(error).foregroundStyle(.red).accessibilityIdentifier("voiceDiaryCommitError") }
        }
    }

    #if DEBUG
    private static var isTestNamespace: Bool {
        ProcessInfo.processInfo.environment["RIJI_TEST_CONTAINER"].flatMap(UUID.init(uuidString:)) != nil
    }
    #endif

    private func exportFinished(_ result: Result<URL, Error>) {
        switch result {
        case .success(let url):
            #if DEBUG
            if ProcessInfo.processInfo.environment["RIJI_TEST_CONTAINER"].flatMap(UUID.init(uuidString:)) != nil {
                let access = url.startAccessingSecurityScopedResource()
                defer { if access { url.stopAccessingSecurityScopedResource() } }
                guard let data = try? Data(contentsOf: url), data == document?.data else {
                    fileError = "测试导出字节核验失败。"; return
                }
                exportedDigest = SourceHash.digest(data)
            }
            #endif
        case .failure(let error):
            if (error as NSError).code != NSUserCancelledError { fileError = "导出未完成，原音频保留。" }
        }
    }
}

struct VoiceAudioDocument: FileDocument {
    static var readableContentTypes: [UTType] { [.wav, .mpeg4Audio] }
    var data: Data
    let filename: String
    var contentType: UTType { filename.hasSuffix(".wav") ? .wav : .mpeg4Audio }
    init(data: Data, filename: String) { self.data = data; self.filename = filename }
    init(configuration: ReadConfiguration) throws {
        data = configuration.file.regularFileContents ?? Data()
        filename = configuration.file.preferredFilename ?? "Recording.m4a"
    }
    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper { FileWrapper(regularFileWithContents: data) }

    #if DEBUG
    static var syntheticWAV: Data {
        var data = Data()
        func word<T: FixedWidthInteger>(_ value: T) { var value = value.littleEndian; withUnsafeBytes(of: &value) { data.append(contentsOf: $0) } }
        data.append(Data("RIFF".utf8)); word(UInt32(32036)); data.append(Data("WAVEfmt ".utf8))
        word(UInt32(16)); word(UInt16(1)); word(UInt16(1)); word(UInt32(16000)); word(UInt32(32000))
        word(UInt16(2)); word(UInt16(16)); data.append(Data("data".utf8)); word(UInt32(32000))
        for sample in 0..<16000 { word(Int16(sin(Double(sample) * 2 * .pi * 440 / 16000) * 3000)) }
        return data
    }
    #endif
}

/// Explicit styling isolates each action inside a Form row. Native tests mount this production view.
struct VoiceRecordingActions: View {
    let pause: () -> Void
    let end: () -> Void
    var body: some View {
        HStack {
            Button("暂停", action: pause).accessibilityIdentifier("pauseVoice")
            Button("结束录音", action: end).accessibilityIdentifier("endVoice")
        }.buttonStyle(.borderless)
    }
}

/// Hardware stop remains independent from diary/metadata busy and edit eligibility.
struct VoiceCaptureSafetyControls: View {
    let active: Bool
    let pending: Bool
    let pause: () -> Void
    let end: () -> Void
    var body: some View {
        if active { VoiceRecordingActions(pause: pause, end: end) }
        else if pending {
            Button("结束并保留原件", action: end).buttonStyle(.borderless).accessibilityIdentifier("endPendingVoice")
        }
    }
}
