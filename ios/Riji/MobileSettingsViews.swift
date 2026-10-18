import SwiftUI
import RijiCore

struct MobileConnectionView: View {
    @ObservedObject var model: MobileClientModel
    @State private var revoke = false
    var body: some View {
        RijiForm {
            Section("AI 服务") {
                LabeledContent("连接", value: model.connectionStatus).accessibilityIdentifier("settingsAirStatus")
                if model.isSyntheticEnvironment {
                    Text("演示环境 · 合成回复").font(.caption).accessibilityIdentifier("mobileSyntheticEnvironment")
                }
                MobileErrorNotice(model: model)
                Button("重试连接") { Task { await model.retryConnection() } }
                    .disabled(model.busy.contains("connection"))
                    .accessibilityIdentifier("mobileRetryConnection")
                if model.connected {
                    NavigationLink("资料与 AI 使用") { MobilePrivacyView(model: model) }
                    Button("断开此设备连接") { model.disconnect() }
                    Button("撤销此设备授权", role: .destructive) { revoke = true }.disabled(model.hasUnresolved("device"))
                    MobileOperationView(model: model, target: "device")
                } else {
                    Text(model.connectionState.guidance)
                        .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                }
            }
            Section("能力") {
                if let capabilities = model.capabilities {
                    LabeledContent("导师私聊", value: capabilities.capabilities["mentors"] == true ? "可用" : "不可用")
                    LabeledContent("导师群聊", value: capabilities.capabilities["group_chat"] == true ? "可用" : "此服务版本暂未提供")
                    LabeledContent("记忆", value: capabilities.capabilities["memory"] == true ? "可用" : "不可用")
                    LabeledContent("资料使用控制", value: capabilities.capabilities["privacy"] == true ? "可用" : "不可用")
                    if capabilities.capabilities["group_chat"] != true {
                        Text("此服务版本暂未支持建群，已有记录仍可查看。")
                            .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                    }
                    if !model.serviceConnected {
                        Text("以上为上次读取的能力，连接恢复后会重新确认。")
                            .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                    }
                } else {
                    Text("连接成功后会读取此服务支持的能力。当前无法确认的能力不代表尚未提供。")
                        .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                }
                Text("本地日记可离线使用。手机不会上传原日记；自动任务沿用 Air 的既有设置。")
                    .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
            }
            Section("本机保留内容") {
                NavigationLink("未发草稿与原操作") { MobileDraftArchiveView(model: model) }
                Text("已保存的草稿保留在此设备。保存失败时请先复制或导出，退出 App 后可能无法恢复；旧草稿不会发送给新连接。")
                    .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
            }
        }.navigationTitle("AI 服务").navigationBarTitleDisplayMode(.inline)
            .confirmationDialog("撤销后此设备不能再访问 AI 服务。已保存的草稿保留在本机；未保存文字请先复制或导出。", isPresented: $revoke, titleVisibility: .visible) {
                Button("确认撤销设备", role: .destructive) { Task { await model.revokeDevice() } }
            }
    }
}

struct MobileDraftArchiveView: View {
    @ObservedObject var model: MobileClientModel
    @State private var archives: [MobileLocalState] = []
    @State private var failure: String?
    @State private var archiveWarning: String?
    @State private var discardScope: String?
    @State private var discardAllFailedScope: String?
    @State private var discardContext = ""
    @State private var discardAllFailedContext = ""
    @State private var confirmRecoveryDiscard = false
    @State private var confirmFailedDiscard = false
    var body: some View {
        RijiList {
            if let failure { Text(failure).foregroundStyle(Color(uiColor: RijiPalette.error)) }
            if let archiveWarning { Text(archiveWarning).foregroundStyle(Color(uiColor: RijiPalette.error)).accessibilityIdentifier("mobileArchiveReadWarning") }
            ForEach(visibleArchives, id: \.scope) { state in
                Section(state.archiveLabel) {
                    if model.unsavedRecovery[state.scope] != nil {
                        Text("这份恢复记录尚未保存到磁盘，退出 App 后可能无法恢复。").foregroundStyle(Color(uiColor: RijiPalette.error))
                        Button("重新保存到此设备") {
                            if model.retryRecoverySave(scope: state.scope) { reload() }
                            else { failure = MobileError.storage.localizedDescription }
                        }.accessibilityIdentifier("mobileRetryRecoverySave")
                        Button("放弃这份未保存修改", role: .destructive) {
                            discardScope = state.scope; discardContext = state.archiveLabel + "\n" + state.recoveryTargetsLabel; confirmRecoveryDiscard = true
                        }.accessibilityIdentifier("mobileDiscardRecovery")
                    }
                    ForEach(state.drafts.keys.sorted(), id: \.self) { key in
                        if let text = state.drafts[key], !text.isEmpty {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(MobileLocalState.targetLabel(key) + "草稿").font(.caption)
                                Text(text).textSelection(.enabled)
                                ShareLink("复制或导出文字", item: text)
                            }
                        } else if model.pendingDraftClears(scope: state.scope).contains(key) {
                            Text(MobileLocalState.targetLabel(key) + "草稿已清空，清除尚未保存。放弃这份未保存修改会恢复已保存版本。")
                                .font(.callout).accessibilityIdentifier("mobilePendingDraftClear")
                        }
                    }
                    ForEach(state.operations.filter { $0.unresolved || $0.hasFailedInput }) { operation in
                        VStack(alignment: .leading, spacing: 6) {
                            if operation.hasFailedInput {
                                MobileFailedInputView(model: model, scope: state.scope, scopeLabel: state.archiveLabel, operation: operation, onDiscard: reload)
                            } else {
                                Text("原操作尚未确认").font(.caption)
                                Text(operation.recoveryLabel).font(.caption)
                                if let text = operation.inputText {
                                    Text(text).textSelection(.enabled)
                                    ShareLink("复制或导出原请求文字", item: text)
                                }
                                if state.endpoint == model.endpointLabel, state.deviceId == model.credential?.deviceId {
                                    Button("核对原操作") { Task { await model.reconcile(operation); reload() } }
                                }
                            }
                        }
                    }
                    if state.operations.contains(where: \.hasFailedInput) {
                        Button("清除所有失败请求的保留文字", role: .destructive) {
                            discardAllFailedScope = state.scope; discardAllFailedContext = state.archiveLabel + "\n" + state.failedTargetsLabel; confirmFailedDiscard = true
                        }.accessibilityIdentifier("mobileDiscardAllFailedInputs")
                    }
                }
            }
            if visibleArchives.isEmpty, failure == nil, archiveWarning == nil { Text("没有保留的草稿或操作记录。") }
        }.navigationTitle("本机保留内容").task { reload() }
            .confirmationDialog(discardContext + "\n已复制或导出需要的文字？放弃未保存修改后恢复已保存版本；已发送但结果未明的操作仍保留。", isPresented: $confirmRecoveryDiscard, titleVisibility: .visible) {
                Button("确认放弃未保存修改", role: .destructive) {
                    if let scope = discardScope { model.discardRecovery(scope: scope); reload() }
                }.accessibilityIdentifier("mobileConfirmDiscardRecovery")
            }
            .confirmationDialog(discardAllFailedContext + "\n已复制或导出需要的文字？清除此连接所有失败请求的本机原文，当前草稿和未决操作保留。", isPresented: $confirmFailedDiscard, titleVisibility: .visible) {
                Button("确认清除所有失败请求文字", role: .destructive) {
                    if let scope = discardAllFailedScope {
                        if model.discardFailedInputs(scope: scope) { reload() }
                        else { failure = MobileError.storage.localizedDescription }
                    }
                }.accessibilityIdentifier("mobileConfirmDiscardAllFailedInputs")
            }
    }
    private var visibleArchives: [MobileLocalState] {
        model.visibleArchives(archives)
    }
    private func reload() {
        do {
            let result = try model.archives(); archives = result.states; archiveWarning = result.warning; failure = nil
        } catch { failure = "本机记录暂时无法读取，恢复内容可能不完整。请稍后重新打开；已有记录未被修改。" }
    }
}

struct MobilePrivacyView: View {
    @ObservedObject var model: MobileClientModel
    @State private var permissions = MobilePermissions()
    @State private var acknowledged = false
    @State private var confirmGrant = false
    @State private var grantCandidate: (MobilePrivacy, MobilePermissions)?
    var body: some View {
        RijiForm {
            MobileErrorNotice(model: model)
            if let value = model.privacy, value.available {
                status(value)
                destinations(value)
                sourceScope(value)
                Section("四项用途") {
                    Toggle("历史日记提取", isOn: $permissions.history)
                    Toggle("新增 / 修改日记提取", isOn: $permissions.incremental)
                    Toggle("记忆整理", isOn: $permissions.organization)
                    Toggle("导师召回", isOn: $permissions.recall)
                    Toggle("我已核对上述用途及实际处理去向", isOn: $acknowledged)
                    Text("当前授权 → 本次选择\n" + permissionComparison(value.permissions))
                        .font(.callout).accessibilityIdentifier("privacyPurposeComparison")
                    Button("确认这些用途") { grantCandidate = (value, permissions); confirmGrant = true }
                        .disabled(!acknowledged || !value.canGrant || unavailable)
                    Text("目录访问与 iCloud 同步不代表模型授权。确认后仍暂停提取，需另点继续。")
                        .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                }
                Section("提取控制") {
                    Button("撤销四项用途", role: .destructive) { Task { await model.privacyAction("revoke", value: value) } }.disabled(model.hasUnresolved("privacy-revoke") || model.busy.contains("privacy-revoke"))
                    Button(value.paused == true ? "继续提取" : "暂停后续提取") {
                        Task { await model.privacyAction(value.paused == true ? "resume" : "pause", value: value) }
                    }.disabled((value.paused == true ? unavailable : model.hasUnresolved("privacy-pause") || model.busy.contains("privacy-pause")) || value.valid != true)
                    Button("检查新增与修改") { Task { await model.privacyAction("scan", value: value) } }
                        .disabled(unavailable || value.valid != true)
                    Text("已发送给模型的请求无法撤回。手机不会上传原日记；服务只处理已获准的来源。")
                        .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                }
            } else { Text("资料使用状态暂不可用。") }
            MobileOperationView(model: model, target: "privacy")
            MobileOperationView(model: model, target: "privacy-revoke")
            MobileOperationView(model: model, target: "privacy-pause")
            Button("重新读取状态") { Task { await model.refreshPrivacy(); loadPermissions() } }
        }.navigationTitle("资料与 AI 使用").navigationBarTitleDisplayMode(.inline)
            .task(id: model.epoch) {
                await model.refreshPrivacy(); loadPermissions()
                while !Task.isCancelled, model.connected {
                    do { try await Task.sleep(nanoseconds: 15_000_000_000) } catch { return }
                    await model.refreshPrivacy()
                }
            }
            .onChange(of: model.privacy?.binding) { _, _ in acknowledged = false; loadPermissions() }
            .confirmationDialog("按当前显示的处理去向和四项选择更新授权？", isPresented: $confirmGrant, titleVisibility: .visible) {
                if let candidate = grantCandidate {
                    Button("确认更新用途") { Task { await model.privacyAction("grant", value: candidate.0, permissions: candidate.1); acknowledged = false } }
                }
            }
    }
    private func permissionComparison(_ value: MobilePermissions?) -> String {
        guard let current = value else { return "服务未提供当前四项用途；下方选择尚未提交。" }
        return [("历史提取", current.history, permissions.history), ("新增修改", current.incremental, permissions.incremental),
         ("记忆整理", current.organization, permissions.organization), ("导师召回", current.recall, permissions.recall)]
            .map { label, old, new in label + "：" + (old ? "允许" : "未允许") + " → " + (new ? "允许" : "未允许") }
            .joined(separator: "\n")
    }
    private var unavailable: Bool { model.busy.contains("privacy") || model.hasUnresolved("privacy") }
    private func loadPermissions() { permissions = model.privacy?.permissions ?? MobilePermissions() }
    private func status(_ value: MobilePrivacy) -> some View {
        Section("当前状态") {
            LabeledContent("授权", value: value.valid == true ? "当前有效" : "尚未授权或配置已变化")
            LabeledContent("提取", value: value.paused == true ? "已暂停" : value.paused == false ? "运行中" : "未提供")
            if let progress = value.progress {
                LabeledContent("已发现", value: progress["discovered"].map(String.init) ?? "未提供")
                LabeledContent("已完成", value: progress["completed"].map(String.init) ?? "未提供")
                LabeledContent("待处理", value: progress["pending"].map(String.init) ?? "未提供")
                LabeledContent("失败", value: progress["failed"].map(String.init) ?? "未提供")
            }
            if let limits = value.limits {
                Text("每 UTC 日 \(limits["daily_chars"].map(String.init) ?? "未提供") 字符；单片段 \(limits["fragment_chars"].map(String.init) ?? "未提供")；来源版本累计 \(limits["source_chars"].map(String.init) ?? "未提供")。")
                    .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
            }
        }
    }
    private func sourceScope(_ value: MobilePrivacy) -> some View {
        Section("实际来源范围") {
            if let scope = value.sourceScope {
                LabeledContent("日记区块", value: scope.sections.isEmpty ? "未选区块" : scope.sections.joined(separator: "、"))
                LabeledContent("开始日期", value: scope.dateFrom ?? "未限定")
                LabeledContent("结束日期", value: scope.dateTo ?? "未限定")
                LabeledContent("接收导师", value: scope.mentors.map { id in model.capabilities?.mentors.first { $0.id == id }?.name ?? id }.joined(separator: "、"))
            } else { Text("服务未提供来源范围，暂不能确认新的授权。").foregroundStyle(Color(uiColor: RijiPalette.secondary)) }
        }
    }
    private func destinations(_ value: MobilePrivacy) -> some View {
        Section("实际处理去向") {
            ForEach(["extraction", "recall"], id: \.self) { key in
                if let target = value.targets?[key] {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(key == "extraction" ? "提取与整理" : "导师召回").font(.headline)
                        Text(target.provider + " · " + target.model)
                        Text(target.host).font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                    }
                }
            }
        }
    }
}
