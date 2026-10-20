import SwiftUI
import RijiCore

struct MobileServiceState: View {
    @ObservedObject var model: MobileClientModel
    let title: String
    var body: some View {
        ContentUnavailableView {
            Label(model.serviceConnected ? "暂时无法读取" : model.connectionStatus, systemImage: "network")
        } description: {
            Text(model.credentialError ?? model.installationError ?? model.error ??
                 model.connectionState.guidance)
        } actions: {
            Button("重试连接") { Task { await model.retryConnection() } }
            MobileRecoveryNotice(model: model)
        }.navigationTitle(title).accessibilityIdentifier("unavailable-" + title)
    }
}

struct MobileErrorNotice: View {
    @ObservedObject var model: MobileClientModel
    var body: some View {
        if let error = model.installationError { Text(error).font(.callout).foregroundStyle(.red).accessibilityIdentifier("mobileInstallationError") }
        if let error = model.credentialError { Text(error).font(.callout).foregroundStyle(.red).accessibilityIdentifier("mobileCredentialError") }
        if let error = model.error { Text(error).font(.callout).foregroundStyle(.red).accessibilityIdentifier("mobileError") }
        MobileRecoveryNotice(model: model)
    }
}

struct MobileRecoveryNotice: View {
    @ObservedObject var model: MobileClientModel
    var body: some View {
        if let error = model.recoveryError {
            Text(error).font(.callout).foregroundStyle(.red).accessibilityIdentifier("mobileRecoveryError")
            NavigationLink("复制或导出未保存文字") { MobileDraftArchiveView(model: model) }
        }
        if model.hasOtherRecovery {
            Text("旧连接有尚未保存的恢复内容，不影响当前草稿的保存状态。").font(.callout)
                .accessibilityIdentifier("mobileOtherRecoveryNotice")
            NavigationLink("处理旧连接的恢复内容") { MobileDraftArchiveView(model: model) }
        }
    }
}

struct MobileOperationView: View {
    @ObservedObject var model: MobileClientModel
    let target: String
    var body: some View {
        if let operation = model.operation(target), operation.state != "completed" || ![
            "mentor_message", "mentor_control", "roundtable_create", "roundtable_message", "roundtable_command", "roundtable_result"
        ].contains(operation.kind) {
            VStack(alignment: .leading, spacing: 8) {
                Text(operation.state == "completed" ? operation.actionLabel + "已完成" : operation.stateLabel)
                    .font(.caption).foregroundStyle(.secondary)
                if operation.state == "failed" {
                    DisclosureGroup("查看详情") {
                        Text(MobileError.server(400, operation.error ?? "unavailable").localizedDescription).font(.caption)
                        if let state = model.local { MobileFailedInputView(model: model, scope: state.scope, scopeLabel: state.archiveLabel, operation: operation) }
                    }
                }
                if operation.state == "not_recorded" {
                    if let text = operation.body["text"]?.string ?? operation.body["content"]?.string { Text(text).font(.caption).textSelection(.enabled) }
                    Button(operation.kind.hasSuffix("message") ? "重试发送" : "重试这次操作") { Task { await model.retryRecordedRequest(operation) } }
                        .disabled(model.busy.contains(target)).accessibilityIdentifier("mobileRetryOriginal")
                }
                if operation.unresolved {
                    Button("重新确认") { Task { await model.reconcile(operation) } }
                        .disabled(model.busy.contains(target)).accessibilityIdentifier("mobileReconcile")
                }
            }
        }
        if model.local?.operations.contains(where: { $0.target == target && $0.hasFailedInput }) == true {
            NavigationLink("查看已失败请求的保留文字") { MobileDraftArchiveView(model: model) }
        }
    }
}

struct MobileFailedInputView: View {
    @ObservedObject var model: MobileClientModel
    let scope: String
    let scopeLabel: String
    let operation: MobilePendingOperation
    var onDiscard: () -> Void = {}
    @State private var confirmDiscard = false
    @State private var discardError: String?
    var body: some View {
        if operation.hasFailedInput, let text = operation.inputText {
            VStack(alignment: .leading, spacing: 6) {
                Text(operation.recoveryLabel).font(.caption)
                Text("未完成请求的原文").font(.caption)
                Text(text).textSelection(.enabled)
                ShareLink("复制或导出失败请求文字", item: text).accessibilityIdentifier("mobileExportFailedInput")
                if let discardError { Text(discardError).font(.caption).foregroundStyle(.red) }
                Button("清除这份失败请求文字", role: .destructive) { confirmDiscard = true }
                    .accessibilityIdentifier("mobileDiscardFailedInput")
            }.confirmationDialog(scopeLabel + "\n" + operation.recoveryLabel + "\n已复制或导出需要的文字？仅清除此失败请求的本机原文，当前草稿和未决操作保留。", isPresented: $confirmDiscard, titleVisibility: .visible) {
                Button("确认清除失败请求文字", role: .destructive) {
                    if model.discardFailedInputs(scope: scope, operationID: operation.id) { discardError = nil; onDiscard() }
                    else { discardError = MobileError.storage.localizedDescription }
                }.accessibilityIdentifier("mobileConfirmDiscardFailedInput")
            }
        }
    }
}

struct MobileGroupComposerFocusKey: PreferenceKey {
    static let defaultValue = false
    static func reduce(value: inout Bool, nextValue: () -> Bool) { value = value || nextValue() }
}

struct MobileMentorsView: View {
    @ObservedObject var model: MobileClientModel
    @ObservedObject var diary: DiaryViewModel
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.verticalSizeClass) private var verticalSizeClass
    @State private var availableHeight: CGFloat = 0
    private struct Selection {
        let account: UUID
        let identifier: String
    }
    @State private var selection: Selection?
    private var selectedID: String? {
        selection?.account == model.accountIdentity ? selection?.identifier : nil
    }
    @State private var creating = false
    @State private var showingNorthStar = false
    @State private var composerFocused = false
    private var compactHeader: Bool {
        composerFocused || typeSize.isAccessibilitySize || verticalSizeClass == .compact ||
            (availableHeight > 0 && availableHeight < 580)
    }
    private var toolbarHeader: Bool {
        composerFocused || (availableHeight > 0 && availableHeight < 400)
    }
    private var items: [MobileRoundtableList.Item] {
        (model.roundtableList?.items ?? []).filter { !model.unavailableRoundtableIDs.contains($0.id) }
    }
    private var otherGroupsUnread: Bool {
        items.contains { $0.id != selectedID && model.hasUnreadReply("roundtable:" + $0.id) }
    }
    private var selectedTitle: String {
        if model.roundtableID == selectedID, let title = model.roundtable?.displayTitle { return title }
        return items.first(where: { $0.id == selectedID })?.title ?? (selectedID == nil ? "选择群聊" : "群聊")
    }
    var body: some View {
        NavigationStack {
            Group {
                if model.canOpenRoundtable || model.capabilities?.capabilities["mentors"] == true {
                    VStack(spacing: 0) {
                        header
                        groupSwitcher
                        Divider().overlay(MentorPalette.border)
                        if let selectedID {
                            MobileRoundtableDestination(model: model, diary: diary, identifier: selectedID)
                                .id(selectedID)
                        } else { emptyGroups }
                    }.onPreferenceChange(MobileGroupComposerFocusKey.self) { composerFocused = $0 }
                } else { MobileServiceState(model: model, title: "导师") }
            }.background {
                GeometryReader { proxy in
                    Color.clear.onAppear { availableHeight = proxy.size.height }
                        .onChange(of: proxy.size.height) { availableHeight = proxy.size.height }
                }
            }.mentorDestinationStyle().navigationTitle("导师").navigationBarTitleDisplayMode(.inline)
                .northStarContext(diary: diary, mobile: model)
                .toolbar {
                    ToolbarItem(placement: .primaryAction) {
                        Button { creating = true } label: { Label("建群", systemImage: "plus") }
                            .disabled(!model.canCreateGroup).accessibilityIdentifier("mobileCreateGroup")
                    }
                }
                .sheet(isPresented: $creating) {
                    NavigationStack {
                        MobileCreateGroupView(model: model) { identifier in
                            creating = false; selectGroup(identifier)
                        }
                    }.mentorDestinationStyle()
                }
                .task(id: "\(model.epoch)-\(model.foreground)") {
                    guard model.foreground, model.canOpenRoundtable else { return }
                    await model.openRoundtables(refreshSelection: false)
                    guard !Task.isCancelled else { return }
                    selectInitialGroup()
                }
                .onChange(of: model.roundtableList?.items.map(\.id)) { selectInitialGroup() }
                .onChange(of: model.unavailableRoundtableIDs) { selectInitialGroup() }
                .onChange(of: model.accountIdentity) {
                    selection = nil; composerFocused = false; showingNorthStar = false
                }
        }.tint(MentorPalette.action)
            // Keep presentation alive when keyboard focus changes the header branch.
            .northStarAnchorSheet(isPresented: $showingNorthStar, diary: diary)
            .id(model.accountIdentity)
    }
    @ViewBuilder private var header: some View {
        if toolbarHeader {
            HStack(spacing: 8) {
                NorthStarAnchorBanner(diary: diary, mobile: model, compact: true, iconOnly: true,
                                      onOpen: { showingNorthStar = true })
                if model.capabilities?.capabilities["mentors"] == true {
                    ForEach(model.capabilities?.mentors ?? []) { mentor in
                        NavigationLink { MobileChatView(model: model, diary: diary, mentor: mentor) } label: {
                            MentorPortrait(actor: mentor.id, topCrop: 0.32).frame(width: 40, height: 44)
                                .frame(maxWidth: .infinity, minHeight: 44).contentShape(Rectangle())
                                .overlay(alignment: .topTrailing) {
                                    if model.hasUnreadReply("mentor:" + mentor.id) {
                                        Circle().fill(MentorPalette.action).frame(width: 6, height: 6)
                                            .accessibilityHidden(true)
                                    }
                                }
                        }.accessibilityLabel(mentor.name + "，私聊" + (model.hasUnreadReply("mentor:" + mentor.id) ? "，新回复" : ""))
                            .accessibilityIdentifier("mobileMentor-" + mentor.id)
                    }
                }
            }.buttonStyle(.plain).padding(.horizontal, 20)
        } else {
            expandedHeader
        }
    }
    private var expandedHeader: some View {
        VStack(spacing: 0) {
            if model.isSyntheticEnvironment && !compactHeader {
                Text("演示环境 · 合成回复").font(.caption).foregroundStyle(MentorPalette.secondary)
                    .accessibilityIdentifier("mobileSyntheticNotice")
            }
            NorthStarAnchorBanner(diary: diary, mobile: model, compact: compactHeader,
                                  onOpen: { showingNorthStar = true })
            if model.capabilities?.capabilities["mentors"] == true { directoryStage }
        }.padding(.horizontal, 20).padding(.top, compactHeader ? 0 : 4)
    }
    private var groupSwitcher: some View {
        HStack(spacing: 8) {
            Text(selectedTitle).font(.headline).lineLimit(1)
                .accessibilityIdentifier("mobileCurrentGroup")
            if items.first(where: { $0.id == selectedID })?.status == "archived" {
                Text("已归档").font(.caption)
            }
            Spacer(minLength: 0)
            switchMenu
        }.foregroundStyle(MentorPalette.text).padding(.horizontal, 20).padding(.vertical, 4)
    }
    private var switchMenu: some View {
        Menu {
            ForEach(items.filter { $0.status != "archived" }) { item in groupChoice(item) }
            Menu {
                let archived = items.filter { $0.status == "archived" }
                if archived.isEmpty { Text("暂时没有归档的群。") }
                ForEach(archived) { item in groupChoice(item) }
            } label: { Label("已归档", systemImage: "archivebox") }
                .accessibilityIdentifier("mobileArchivedGroups")
            if model.roundtableList?.nextCursor != nil {
                Button("更多群聊") { Task { await model.openRoundtables(more: true, refreshSelection: false) } }
            }
            Button("重新读取群聊") { Task { await model.openRoundtables(refreshSelection: false) } }
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "bubble.left.and.bubble.right")
                if otherGroupsUnread {
                    if typeSize.isAccessibilitySize {
                        Circle().fill(MentorPalette.action).frame(width: 8, height: 8)
                            .accessibilityHidden(true)
                    } else {
                        Text("新回复").font(.caption2).foregroundStyle(MentorPalette.action).lineLimit(1)
                    }
                }
                Image(systemName: "chevron.down").font(.caption.weight(.semibold))
            }.frame(minWidth: 52, minHeight: 44).contentShape(Rectangle())
        }.accessibilityLabel("切换群聊")
            .accessibilityValue(selectedTitle + (otherGroupsUnread ? "，其他群有新回复" : ""))
            .accessibilityIdentifier("mobileGroupSwitcher")
    }

    private func groupChoice(_ item: MobileRoundtableList.Item) -> some View {
        Button { selectGroup(item.id) } label: {
            let draft = !model.draft("roundtable:" + item.id).isEmpty
            let unread = model.hasUnreadReply("roundtable:" + item.id)
            Label(item.title + (draft ? " · 草稿" : "") + (unread ? " · 新回复" : ""),
                  systemImage: selectedID == item.id ? "checkmark" : "bubble.left.and.bubble.right")
        }.accessibilityIdentifier("mobileGroup-" + item.id)
    }
    private var emptyGroups: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if !model.roundtableLoaded { ProgressView("读取群聊") }
                else if model.roundtableList == nil {
                    Text("群聊列表暂时未能读取，已保存的草稿仍在本机。")
                    Button("重新读取群聊") { Task { await model.openRoundtables(refreshSelection: false) } }
                } else {
                    Text(model.unavailableRoundtableIDs.isEmpty ? "想让几位导师一起聊，可以建一个群。" : "此前选择的群暂不可访问，草稿仍保留。可切换其他群或重新读取列表。")
                        .accessibilityIdentifier("mobileGroupsEmpty")
                }
                if model.roundtableOffline {
                    Text("离线时仅显示仍有效的本机缓存，列表可能不完整。").font(.caption)
                }
                if !model.canCreateGroup { Text("当前连接暂不支持新建群聊，已有记录仍保留。").font(.caption) }
                MobileOperationView(model: model, target: model.groupCreationTarget)
                MobileErrorNotice(model: model)
            }.foregroundStyle(MentorPalette.secondary).frame(maxWidth: .infinity, alignment: .leading).padding(20)
        }
    }
    private func selectInitialGroup() {
        guard selectedID == nil || selectedID.map(model.unavailableRoundtableIDs.contains) == true else { return }
        selection = model.preferredRoundtableSelection.map {
            Selection(account: model.accountIdentity, identifier: $0)
        }
    }
    private func selectGroup(_ identifier: String) {
        guard selectedID != identifier else { return }
        composerFocused = false
        selection = Selection(account: model.accountIdentity, identifier: identifier)
    }
    private func compactName(_ mentor: MobileMentor) -> String {
        ["gentle_reviewer": "回顾", "blunt_coach": "教练", "future_self": "未来", "wang_yangming": "阳明"][mentor.id] ?? mentor.name
    }
    private var directoryStage: some View {
        GeometryReader { proxy in
            ZStack(alignment: .bottom) {
                MentorStageDepth()
                if !compactHeader {
                    Ellipse().fill(MentorPalette.surface.opacity(0.55)).frame(height: 36)
                    Ellipse().stroke(MentorPalette.secondary.opacity(0.45), lineWidth: 0.7).frame(height: 36)
                }
                HStack(alignment: .bottom, spacing: 0) {
                    ForEach(model.capabilities?.mentors ?? []) { mentor in
                        NavigationLink { MobileChatView(model: model, diary: diary, mentor: mentor) } label: {
                            if compactHeader {
                                VStack(spacing: 0) {
                                    MentorPortrait(actor: mentor.id, topCrop: 0.32).frame(width: 32, height: 28)
                                    HStack(spacing: 3) {
                                        Text(compactName(mentor)).font(.caption2).lineLimit(1)
                                        if model.hasUnreadReply("mentor:" + mentor.id) {
                                            Circle().fill(MentorPalette.action).frame(width: 5, height: 5)
                                        }
                                    }
                                }.frame(width: proxy.size.width / 4, height: 56).contentShape(Rectangle())
                            } else {
                                MentorDirectoryPerson(mentor: mentor, width: proxy.size.width / 4,
                                    unread: model.hasUnreadReply("mentor:" + mentor.id))
                            }
                        }.accessibilityLabel(mentor.name + "，私聊" + (model.hasUnreadReply("mentor:" + mentor.id) ? "，新回复" : ""))
                            .accessibilityIdentifier("mobileMentor-" + mentor.id)
                    }
                }.padding(.bottom, compactHeader ? 0 : 14)
            }
        }.frame(height: compactHeader ? 56 : 190).buttonStyle(.plain)
    }
}

private struct MentorDirectoryPerson: View {
    let mentor: MobileMentor
    let width: CGFloat
    let unread: Bool
    @State private var hovering = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var typeSize
    var body: some View {
        VStack(spacing: 2) {
            MentorPortrait(actor: mentor.id, topCrop: 0.4)
                .frame(height: width * (typeSize.isAccessibilitySize ? 1.35 : 1.6)).mentorPortraitFade()
                .brightness(hovering ? 0.08 : 0)
            Text(mentor.name).font(.caption).foregroundStyle(MentorPalette.text)
                .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
            if unread { Text("新回复").font(.caption2).foregroundStyle(MentorPalette.action) }
        }.frame(width: width, alignment: .bottom).frame(minHeight: 44)
            .background(hovering ? MentorPalette.surface.opacity(0.4) : .clear, in: RoundedRectangle(cornerRadius: 14))
            .contentShape(Rectangle()).onHover { hovering = $0 }
            .animation(reduceMotion ? nil : .easeOut(duration: 0.15), value: hovering)
    }
}

struct MobileGroupAvatar: View {
    let members: [String]
    var body: some View {
        LazyVGrid(columns: [GridItem(.flexible(), spacing: 1), GridItem(.flexible(), spacing: 1)], spacing: 1) {
            ForEach(members.prefix(4), id: \.self) { actor in MentorPortrait(actor: actor, topCrop: 0.3).frame(height: 28) }
            if members.isEmpty { Image(systemName: "person.2").frame(width: 56, height: 56).foregroundStyle(MentorPalette.secondary) }
        }.frame(width: 58, height: 58).background(MentorPalette.surface)
            .clipShape(RoundedRectangle(cornerRadius: 12)).accessibilityHidden(true)
    }
}

private struct MobileRoundtableDestination: View {
    @ObservedObject var model: MobileClientModel
    @ObservedObject var diary: DiaryViewModel
    let identifier: String
    private var isGroup: Bool {
        model.roundtableList?.items.first(where: { $0.id == identifier })?.isGroup == true ||
            (model.roundtableID == identifier && model.roundtable?.isGroup == true)
    }
    private var isLegacy: Bool {
        model.roundtableList?.items.first(where: { $0.id == identifier }).map { !$0.isGroup } == true ||
            (model.roundtableID == identifier && model.roundtable?.isGroup == false)
    }
    var body: some View {
        Group {
            if isGroup { MobileGroupChatView(model: model, diary: diary, identifier: identifier, embedded: true) }
            else if isLegacy {
                MobileRoundtableView(model: model, diary: diary, identifier: identifier, embedded: true)
            } else {
                ScrollView {
                    VStack(spacing: 20) {
                    if !model.roundtableLoaded { ProgressView("读取聊天") }
                    else { Text("聊天暂时未能读取，草稿仍保留。") }
                    Button("重新读取") { Task {
                        guard model.roundtableID == identifier else { return }
                        await model.refreshRoundtable()
                    } }
                    MobileOperationView(model: model, target: model.groupCreationTarget)
                    MobileErrorNotice(model: model)
                    }.padding()
                }.mentorDestinationStyle()
            }
        }.task(id: "\(identifier)-\(model.epoch)-\(model.foreground)") {
            guard !Task.isCancelled, model.foreground else { return }
            await model.selectRoundtable(identifier)
        }
    }
}

private struct MobileChatView: View {
    @ObservedObject var model: MobileClientModel
    @ObservedObject var diary: DiaryViewModel
    @Environment(\.dynamicTypeSize) private var typeSize
    @FocusState private var composerFocused: Bool
    @State private var visibleMessageID: String?
    let mentor: MobileMentor
    private var key: String { "mentor:" + mentor.id }
    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 20) {
                    NorthStarAnchorBanner(diary: diary, mobile: model, compact: composerFocused)
                    MentorStage(mentor: mentor.id, activeActor: mentor.id,
                                compact: composerFocused || typeSize.isAccessibilitySize)
                    if let conversation = model.conversations[mentor.id] {
                        if let notice = model.mentorReadingNotices[mentor.id] {
                            Text(notice).font(.caption)
                            if model.historyCursor(mentor.id) != nil { Button("继续找回阅读位置") { Task {
                                await model.restoreMentorReadingPosition(mentor.id)
                                visibleMessageID = model.mentorReadingAnchor(mentor.id)
                            } } }
                        }
                        if !["completed", "empty"].contains(conversation.status) {
                            Text(status(conversation.status)).font(.caption).foregroundStyle(MentorPalette.secondary)
                        }
                        if let notice = conversation.noticeText {
                            Text(notice).font(.callout).accessibilityIdentifier("mobileConversationNotice")
                            Button("重新读取对话与原操作") { Task { await model.refreshConversation(mentor.id)
                                if let operation = model.operation(key), operation.unresolved { await model.reconcile(operation) }
                            } }
                        }
                        if model.historyCursor(mentor.id) != nil {
                            Button("查看更早消息") { Task { await model.loadOlderMessages(mentor.id) } }
                                .disabled(model.busy.contains("history:" + mentor.id)).accessibilityIdentifier("mobileOlderMessages")
                        }
                        ForEach(model.messages(mentor.id)) { message in
                            MentorSpeakerCard(actor: message.actor == "user" ? "user" : mentor.id,
                                name: message.actor == "user" ? "我" : mentor.name,
                                kind: message.actor == "user" ? "我的陈述" : "AI 建议", accessory: { EmptyView() }) {
                                Text(message.visibleText).textSelection(.enabled)
                                if message.unavailable == nil {
                                    ForEach(message.sourceIds, id: \.self) { id in
                                        NavigationLink("查看记忆依据") { MobileMemoryDetailView(model: model, id: id) }
                                    }
                                }
                            }.frame(maxWidth: .infinity, alignment: .leading).id(message.id)
                        }
                        if conversation.messages.isEmpty { Text("从这里开始与\(mentor.name)交流。") }
                    } else { Text("正在读取对话；离线时可继续保留草稿。").foregroundStyle(.secondary) }
                    MobileErrorNotice(model: model)
                    MobileOperationView(model: model, target: key)
                    MobileOperationView(model: model, target: "control:" + mentor.id)
                    MobileOperationView(model: model, target: "stop:" + mentor.id)
                }.padding().scrollTargetLayout()
            }.scrollPosition(id: $visibleMessageID, anchor: .top).scrollDismissesKeyboard(.interactively)
            composer
        }.mentorDestinationStyle().navigationTitle(mentor.name).navigationBarTitleDisplayMode(.inline)
            .northStarContext(diary: diary, mobile: model)
            .toolbar { controls }
            .onChange(of: visibleMessageID) {
                model.saveMentorReadingAnchor(mentor.id, anchor: visibleMessageID)
                model.markReplyRead(key, visibleMessageID: visibleMessageID)
            }
            .task(id: "\(model.epoch)-\(model.foreground)") {
                guard model.foreground else { return }
                visibleMessageID = model.mentorReadingAnchor(mentor.id)
                while !Task.isCancelled, model.connected, model.foreground {
                    await model.refreshConversation(mentor.id)
                    if let anchor = model.mentorReadingAnchor(mentor.id) {
                        if model.messages(mentor.id).contains(where: { $0.id == anchor }) { visibleMessageID = anchor }
                    } else if visibleMessageID == nil { visibleMessageID = model.messages(mentor.id).last?.id }
                    for key in [key, "control:" + mentor.id, "stop:" + mentor.id] {
                        if let operation = model.operation(key), operation.unresolved { await model.reconcile(operation) }
                    }
                    do { try await Task.sleep(nanoseconds: 5_000_000_000) } catch { return }
                }
            }
    }
    private var composer: some View {
        VStack(alignment: .leading, spacing: 8) {
            if model.hasUnreadReply(key), let latest = model.local?.lastKnownReplies?[key] {
                Button("查看新回复") { Task {
                    model.saveMentorReadingAnchor(mentor.id, anchor: latest)
                    await model.restoreMentorReadingPosition(mentor.id)
                    if model.messages(mentor.id).contains(where: { $0.id == latest }) {
                        visibleMessageID = latest; model.markReplyRead(key, visibleMessageID: latest)
                    }
                } }
                    .frame(minHeight: 44).accessibilityIdentifier("mentorNewReply")
            }
            HStack(alignment: .bottom) {
                TextField("写给\(mentor.name)", text: Binding(get: { model.draft(key) }, set: { model.setDraft(key, text: $0) }), axis: .vertical)
                    .lineLimit(1...(typeSize.isAccessibilitySize ? 2 : 4)).mentorInputStyle().focused($composerFocused)
                    .accessibilityIdentifier("mobileChatDraft").accessibilityHint(draftNotice)
                Button { Task { await model.send(mentor.id) } } label: {
                    Image(systemName: "arrow.up").font(.body.weight(.semibold))
                }.buttonStyle(MentorControlStyle(prominent: true)).accessibilityLabel("发送")
                    .disabled(model.draft(key).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !model.canSend(mentor.id))
                    .accessibilityIdentifier("mobileSend")
            }
            if !typeSize.isAccessibilitySize {
                Text(draftNotice).font(.caption).foregroundStyle(MentorPalette.secondary)
                    .fixedSize(horizontal: false, vertical: true).accessibilityIdentifier("mobileChatSaveStatus")
            }
        }.padding(.horizontal).padding(.vertical, 10).background(MentorPalette.paper)
    }
    private var draftNotice: String {
        model.recoveryError == nil ? "文字保留在此设备，点发送后才传给服务。" : "文字尚未保存，请先复制或导出。"
    }
    @ToolbarContentBuilder private var controls: some ToolbarContent {
        #if DEBUG
        if diary.isExternalRefreshFixture {
            ToolbarItem {
                Button("Synthetic external edit") { diary.simulateExternalRefreshFixture(divergent: false) }
                    .accessibilityIdentifier("simulateExternalEdit")
            }
        }
        #endif
        if let conversation = model.conversations[mentor.id] {
            if conversation.canStop || model.hasUnresolved("control:" + mentor.id) {
                ToolbarItem { Button("停止") { Task { await model.control(mentor.id, action: "stop", revision: conversation.revision) } }.disabled(model.hasUnresolved("stop:" + mentor.id) || model.busy.contains("stop:" + mentor.id)).accessibilityIdentifier("mobileStopConversation") }
            } else if conversation.resumable {
                ToolbarItem { Button("继续") { Task { await model.control(mentor.id, action: "resume", revision: conversation.revision) } }.disabled(model.hasUnresolved("control:" + mentor.id)) }
            }
        }
    }
    private func status(_ value: String) -> String {
        switch value {
        case "empty": return "尚无消息"
        case "queued": return "等待导师处理"
        case "running": return "导师正在回复"
        case "delivering": return "正在交付回复"
        case "completed": return "本轮已完成"
        case "stopped": return "已停止"
        case "interrupted": return "已中断"
        case "failed": return "回复失败"
        default: return "当前状态需要重新核对"
        }
    }
}

struct MobileRawMemoriesView: View {
    @ObservedObject var model: MobileClientModel
    @State private var search = ""
    var body: some View {
        RijiList {
            MobileErrorNotice(model: model)
            if let page = model.memories {
                if page.items.isEmpty { Text("当前页没有可见记忆。") }
                ForEach(page.items.filter { search.isEmpty || $0.content.localizedCaseInsensitiveContains(search) }) { item in
                    NavigationLink { MobileMemoryDetailView(model: model, id: item.id) } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(item.content).lineLimit(3)
                            Text(MobileMemoryDetailView.stateLabel(item)).font(.caption).foregroundStyle(.secondary)
                        }
                    }.accessibilityIdentifier("mobileMemory-" + item.id)
                }
                if let next = page.nextCursor {
                    Button("下一页原始记录") { Task { await model.refreshMemories(cursor: next) } }
                }
            }
            Button("从第一页重新读取") { Task { await model.refreshMemories() } }
        }.navigationTitle("原始记忆").searchable(text: $search, prompt: "搜索当前页原始记录")
            .task(id: model.epoch) { await model.refreshMemories() }
            .refreshable { await model.refreshMemories() }
    }
}

private struct MobileCorrectionComparison: Identifiable {
    let memory: MobileMemory
    let submitting: Bool
    var id: String { memory.id + ":" + memory.version + (submitting ? ":submit" : ":rebase") }
}

struct MobileMemoryDetailView: View {
    @ObservedObject var model: MobileClientModel
    let id: String
    @State private var deletion = false
    @State private var deleteCandidate: MobileMemory?
    @State private var comparison: MobileCorrectionComparison?
    @FocusState private var correctionFocused: Bool
    private var key: String { "memory:" + id }
    var body: some View {
        RijiForm {
            MobileErrorNotice(model: model)
            if let memory = model.memoryDetails[id] {
                Section("记忆") {
                    Text(memory.content).textSelection(.enabled)
                    Text(Self.stateLabel(memory)).font(.caption).foregroundStyle(.secondary)
                    if memory.state != "current" { Text("来源状态需要核对，此条不代表当前事实。").foregroundStyle(.secondary) }
                }
                Section("来源依据") {
                    if memory.sources.isEmpty { Text("当前没有可用来源元数据。") }
                    ForEach(memory.sources) { source in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(source.safeRelativePath ?? "来源位置不可用")
                            if let section = source.section { Text(section).font(.caption) }
                            if let line = source.line { Text("第 \(line) 行").font(.caption) }
                        }
                    }
                    Text("可在日记目录中核对原文件。服务不向手机传输原日记。").font(.caption).foregroundStyle(.secondary)
                }
                Section("纠正") {
                    TextField("填写更正后的记忆", text: Binding(get: { model.draft(key) }, set: { model.setDraft(key, text: $0) }), axis: .vertical)
                        .lineLimit(3...8).focused($correctionFocused).accessibilityIdentifier("mobileMemoryCorrection")
                    Button("采用当前文字作为草稿") { model.setDraft(key, text: memory.content) }.disabled(!model.draft(key).isEmpty)
                    if model.correctionNeedsRebase(memory) {
                        Text("这份草稿基于较早版本或未记录版本。请比较当前记忆后，再确认采用当前版本继续纠正。").font(.callout)
                        Button("比较草稿与当前记忆") { correctionFocused = false; comparison = MobileCorrectionComparison(memory: memory, submitting: false) }.accessibilityIdentifier("mobileCompareCorrection")
                    }
                    Button("确认纠正") { correctionFocused = false; comparison = MobileCorrectionComparison(memory: memory, submitting: true) }
                        .disabled(model.draft(key).isEmpty || model.draft(key).count > 2000 || model.correctionNeedsRebase(memory) || unavailable)
                }
                Section("复核与使用") {
                    Button("确认仍然有效") { Task { await model.memoryAction(memory, action: "reconfirm") } }.disabled(unavailable)
                    Button(memory.status == "archived" ? "恢复记忆" : "归档记忆") {
                        Task { await model.memoryAction(memory, action: memory.status == "archived" ? "restore" : "archive") }
                    }.disabled(unavailable)
                    Menu("使用范围：" + permissionLabel(memory.privacy)) {
                        ForEach(["cloud", "local", "none"], id: \.self) { permission in
                            Button(permissionLabel(permission)) { Task { await model.memoryAction(memory, action: "permission", permission: permission) } }
                        }
                    }.disabled(unavailable)
                    Button("删除记忆", role: .destructive) { deleteCandidate = memory; deletion = true }.disabled(unavailable)
                }
            } else if model.operation(key)?.result?["deleted"] == .bool(true) { Text("这条记忆已删除，原日记仍保留。")
            } else { Button("读取当前记忆") { Task { await model.refreshMemory(id) } } }
            MobileOperationView(model: model, target: key)
        }.accessibilityIdentifier("mobileMemoryDetailForm")
            .navigationTitle("记忆详情").navigationBarTitleDisplayMode(.inline)
            .task(id: model.epoch) {
                while !Task.isCancelled, model.connected {
                    if model.operation(key)?.result?["deleted"] != .bool(true) { await model.refreshMemory(id) }
                    do { try await Task.sleep(nanoseconds: 15_000_000_000) } catch { return }
                }
            }
            .refreshable { await model.refreshMemory(id) }
            .sheet(item: $comparison) { candidate in
                let memory = candidate.memory
                NavigationStack {
                    RijiForm {
                        Section("服务当前记忆") { Text(memory.content).textSelection(.enabled) }
                        Section("你的纠正草稿") { Text(model.draft(key)).textSelection(.enabled) }
                        if candidate.submitting {
                            Text("确认前后内容后提交；摘要将依据当前原始记忆重新核对。")
                            Button("提交纠正") { comparison = nil; Task { await model.memoryAction(memory, action: "update") } }
                                .disabled(model.memoryDetails[id]?.version != memory.version || unavailable)
                                .accessibilityIdentifier("mobileSubmitCorrection")
                        } else {
                            Text("保留草稿文字，把本次比较的版本设为新的纠正依据；这一步不会发送文字。")
                            Button("已比较，保留草稿并采用此版本") { model.rebaseCorrection(memory); comparison = nil }
                                .disabled(model.memoryDetails[id]?.version != memory.version)
                                .accessibilityIdentifier("mobileRebaseCorrection")
                        }
                        if model.memoryDetails[id]?.version != memory.version { Text("记忆再次变化，请返回重新比较。") }
                    }.navigationTitle("比较纠正草稿")
                        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("稍后处理") { comparison = nil } } }
                }
            }
            .confirmationDialog("删除这条记忆？原日记保留；导出副本和备份不会同时擦除。", isPresented: $deletion, titleVisibility: .visible) {
                if let memory = deleteCandidate { Button("确认删除记忆", role: .destructive) { Task { await model.memoryAction(memory, action: "delete") } } }
            }
    }
    private var unavailable: Bool { model.busy.contains(key) || model.hasUnresolved(key) }
    private func permissionLabel(_ value: String) -> String {
        switch value { case "cloud": return "允许获准模型使用"; case "local": return "仅本地，不供模型使用"; default: return "停止使用" }
    }
    static func stateLabel(_ memory: MobileMemory) -> String {
        let scope = memory.scope == "shared" ? "共享事实" : "导师观察"
        let status = memory.status == "archived" ? "已归档" : memory.state == "current" ? "来源当前有效" : "来源待核对"
        return scope + " · " + status
    }
}
