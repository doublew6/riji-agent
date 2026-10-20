import SwiftUI
import UIKit
import RijiCore

private let roundtableNames = ["host": "主持人", "gentle_reviewer": "温柔回顾者", "blunt_coach": "直率教练", "future_self": "未来的我", "wang_yangming": "王阳明", "user": "我"]

private func roundtableCopyText(_ item: MobileRoundtableMessage) -> String {
    guard item.unavailable == nil else { return item.visibleText }
    return ([item.visibleText] + item.uncertainties.map { "条件 / 分歧：" + $0 }
        + item.nextSteps.map { "建议下一步：" + $0 }
        + (item.superseded ? ["旧建议需重新评估"] : [])).joined(separator: "\n")
}

struct MobileCreateGroupView: View {
    @ObservedObject var model: MobileClientModel
    let onCreated: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var selected: Set<String> = []
    @State private var working = false
    @State private var recoveryOperationID: String?
    var body: some View {
        List {
            Section {
                ForEach(model.capabilities?.mentors ?? []) { mentor in
                    Button {
                        if selected.contains(mentor.id) { selected.remove(mentor.id) } else { selected.insert(mentor.id) }
                        model.saveGroupCreationSelection(Array(selected).sorted())
                    } label: {
                        HStack(spacing: 14) {
                            MentorPortrait(actor: mentor.id, topCrop: 0.34).frame(width: 50, height: 58)
                            Text(mentor.name).foregroundStyle(MentorPalette.text)
                            Spacer()
                            Image(systemName: selected.contains(mentor.id) ? "checkmark.circle.fill" : "circle")
                                .foregroundStyle(MentorPalette.action)
                        }.frame(minHeight: 64).contentShape(Rectangle())
                    }.buttonStyle(.plain).disabled(working || model.hasUnresolved(model.groupCreationTarget))
                        .accessibilityLabel(mentor.name).accessibilityValue(selected.contains(mentor.id) ? "已选择" : "未选择")
                        .accessibilityIdentifier("groupSelect-" + mentor.id).listRowBackground(MentorPalette.forest)
                }
            } header: { Text("选择一起聊的导师 · 2–4 位") }
            Section {
                Text("建好后直接聊天，群名可以稍后修改。")
                    .font(.callout).foregroundStyle(MentorPalette.secondary)
                MobileOperationView(model: model, target: model.groupCreationTarget)
                MobileErrorNotice(model: model)
            }.listRowBackground(MentorPalette.forest)
        }.scrollContentBackground(.hidden).mentorDestinationStyle().navigationTitle("建群")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() }.disabled(working) }
                ToolbarItem(placement: .confirmationAction) {
                    Button("创建 (\(selected.count))") { Task {
                        working = true
                        let previous = model.operation(model.groupCreationTarget)?.id
                        if let id = await model.createGroup(personas: orderedSelection) { onCreated(id) }
                        else if let operation = model.operation(model.groupCreationTarget), operation.id != previous {
                            recoveryOperationID = operation.id
                        }
                        working = false
                    } }.disabled(working || !model.canCreateGroup || selected.count < 2 || selected.count > 4 || model.hasUnresolved(model.groupCreationTarget))
                        .accessibilityIdentifier("groupConfirmCreate")
                }
            }.interactiveDismissDisabled(working)
            .task {
                selected = Set(model.local?.groupCreationPersonas ?? [])
                if let operation = model.operation(model.groupCreationTarget), operation.unresolved { recoveryOperationID = operation.id }
            }
            .onChange(of: model.operation(model.groupCreationTarget)?.state) {
                if let operation = model.operation(model.groupCreationTarget), operation.id == recoveryOperationID, operation.state == "completed",
                   let id = operation.result?["conversation_id"]?.string, !working { onCreated(id) }
            }
    }
    private var orderedSelection: [String] {
        (model.capabilities?.mentors ?? []).map(\.id).filter { selected.contains($0) }
    }
}

private struct MobileRoundtablePresentation: ViewModifier {
    @ObservedObject var diary: DiaryViewModel
    @ObservedObject var model: MobileClientModel
    let title: String
    let embedded: Bool
    @ViewBuilder func body(content: Content) -> some View {
        if embedded { content.mentorDestinationStyle() }
        else {
            content.mentorDestinationStyle().navigationTitle(title).navigationBarTitleDisplayMode(.inline)
                .northStarContext(diary: diary, mobile: model)
        }
    }
}

struct MobileGroupChatView: View {
    @ObservedObject var model: MobileClientModel
    @ObservedObject var diary: DiaryViewModel
    let identifier: String
    var embedded = false
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var details = false
    @State private var result = false
    @State private var selection: Set<String> = []
    @State private var selecting = false
    @State private var selectionNotice: String?
    @State private var visibleMessageID: String?
    @State private var readingInitialized = false
    @State private var acceptingReadingChanges = false
    @State private var pendingReadingAnchor: String?
    @FocusState private var composerFocused: Bool
    private var value: MobileRoundtable? { model.roundtableID == identifier ? model.roundtable : nil }
    private var draftKey: String { "roundtable:" + identifier }
    private var title: String { value?.displayTitle ?? model.roundtableList?.items.first(where: { $0.id == identifier })?.title ?? "群聊" }
    private var members: [String] { value?.members ?? model.roundtableList?.items.first(where: { $0.id == identifier })?.personas ?? [] }
    private var hasRetainedPreview: Bool {
        diary.aiResultPreview?.account == model.credential?.scope &&
            diary.aiResultPreview?.result.provenance.problemId == identifier
    }
    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    Text(members.map { roundtableNames[$0] ?? "导师" }.joined(separator: " · "))
                        .font(.caption).foregroundStyle(MentorPalette.secondary)
                        .accessibilityIdentifier("groupChatMembers")
                    if !embedded { NorthStarAnchorBanner(diary: diary, mobile: model, compact: true) }
                    MobileErrorNotice(model: model)
                    if embedded { MobileOperationView(model: model, target: model.groupCreationTarget) }
                    if let notice = model.roundtableCacheNotice { Text(notice).font(.caption) }
                    if model.roundtableOffline {
                        Label("离线副本 · 可继续写草稿，联网后再发送", systemImage: "wifi.slash")
                            .font(.caption).foregroundStyle(MentorPalette.secondary)
                    }
                    if let value {
                        if let notice = model.roundtableReadingNotice {
                            Text(notice).font(.caption)
                            if value.nextCursor != nil { Button("继续找回阅读位置") { Task {
                                await model.restoreRoundtableReadingPosition(identifier)
                                guard model.roundtableID == identifier, !Task.isCancelled else { return }
                                visibleMessageID = model.local?.roundtableReading?[identifier]?.anchor
                            } } }
                        }
                        if value.status == "interrupted" {
                            Text("上次回复中断，请先核对原操作，再继续。")
                                .font(.callout).accessibilityIdentifier("groupInterruptedNotice")
                        }
                        if value.turnEnded == true { Text("上轮已结束，已收到的内容保留，可继续新消息。").font(.caption) }
                        if let reason = value.completionNotice { Text(reason).font(.callout) }
                        if value.nextCursor != nil { Button("查看更早消息") { Task { await refresh(older: true) } } }
                        if value.messages.isEmpty {
                            Text("群已建好，直接说说你想聊的事。")
                                .foregroundStyle(MentorPalette.secondary).accessibilityIdentifier("groupChatEmpty")
                        }
                        ForEach(value.messages) { item in
                            MentorSpeakerCard(actor: item.actor, name: roundtableNames[item.actor] ?? "导师",
                                kind: item.actor == "user" ? "我" : "AI 建议", compact: composerFocused,
                                accessory: {
                                    if selecting, item.actor != "user", item.unavailable == nil, !item.superseded {
                                        Button {
                                            toggleSelection(item)
                                        } label: { Image(systemName: selection.contains(item.id) ? "checkmark.circle.fill" : "circle").frame(minWidth: 44, minHeight: 44) }
                                            .accessibilityLabel(selection.contains(item.id) ? "取消选择这段结果" : "选择这段结果")
                                            .accessibilityValue(selection.contains(item.id) ? "已选择" : "未选择")
                                            .accessibilityIdentifier("roundtableSelect-" + item.id)
                                    } else if !selecting {
                                        Menu { messageActions(item) } label: {
                                            Image(systemName: "ellipsis").frame(minWidth: 44, minHeight: 44)
                                        }.accessibilityLabel("消息操作")
                                            .accessibilityIdentifier((item.actor == "user" ? "roundtableUserActions-" : "roundtableMessageActions-") + item.id)
                                    }
                                }) {
                                Text(item.visibleText).textSelection(.enabled)
                                ForEach(item.uncertainties, id: \.self) { Text("条件 / 分歧：" + $0).font(.callout) }
                                ForEach(item.nextSteps, id: \.self) { Text("建议下一步：" + $0).font(.callout) }
                                if item.superseded { Text("旧建议需重新评估").font(.caption) }
                            }.id(item.id).accessibilityElement(children: .contain).accessibilityIdentifier("roundtableMessage-" + item.id)
                                .contextMenu { messageActions(item) }
                        }
                        if value.active { ProgressView((roundtableNames[value.progress?.actor ?? ""] ?? "导师") + "正在回复").font(.caption) }
                        if value.status == "archived" {
                            Text("已归档，恢复后可以继续聊天。")
                            Button("恢复群聊") { Task { await model.roundtableCommand("restore", conversationID: identifier) } }
                                .disabled(!model.canMutateRoundtable).accessibilityIdentifier("groupRestore")
                        }
                    } else {
                        ProgressView("读取聊天")
                        Button("重新读取") { Task { await refresh() } }
                    }
                    MobileOperationView(model: model, target: draftKey)
                    MobileOperationView(model: model, target: "roundtable-result:" + identifier)
                    MobileOperationView(model: model, target: "roundtable-stop:" + identifier)
                    MobileOperationView(model: model, target: "roundtable-end:" + identifier)
                }.padding().scrollTargetLayout()
            }.accessibilityIdentifier("roundtableMessages")
                .scrollPosition(id: readingPosition, anchor: .top).scrollDismissesKeyboard(.interactively)
                .refreshable { await refresh() }
                .task(id: identifier) { initializeReading(); restoreReading(using: proxy) }
                .onChange(of: value?.messages.map(\.id)) { restoreReading(using: proxy) }
            }
            composer
        }.modifier(MobileRoundtablePresentation(diary: diary, model: model, title: title, embedded: embedded))
            .preference(key: MobileGroupComposerFocusKey.self, value: composerFocused)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { details = true } label: { Image(systemName: "ellipsis.circle").frame(minWidth: 44, minHeight: 44) }
                        .accessibilityLabel("群详情").accessibilityIdentifier("groupDetails")
                }
            }
            .sheet(isPresented: $details) { NavigationStack { MobileGroupDetailsView(model: model, identifier: identifier) }.mentorDestinationStyle() }
            .sheet(isPresented: $result) { NavigationStack { RoundtableResultView(model: model, diary: diary, identifier: identifier) } }
            .task(id: "\(identifier)-\(model.epoch)-\(model.foreground)") {
                guard model.foreground else { return }
                if let account = model.credential?.scope { await diary.restorePendingAIResults(account: account, conversationID: identifier) }
                while !Task.isCancelled {
                    do { try await Task.sleep(nanoseconds: 3_000_000_000) } catch { return }
                    guard model.roundtableID == identifier else { return }
                    await model.maintainRoundtable()
                }
            }
            .onChange(of: selection) { saveReading() }
    }
    @ViewBuilder private var newReplyButton: some View {
        if model.hasUnreadReply(draftKey), let latest = model.local?.lastKnownReplies?[draftKey] {
            Button { Task {
                model.saveRoundtableReadingState(selected: selection, anchor: latest, conversationID: identifier)
                await model.restoreRoundtableReadingPosition(identifier)
                if value?.messages.contains(where: { $0.id == latest }) == true {
                    pendingReadingAnchor = nil; acceptingReadingChanges = true
                    visibleMessageID = latest; model.markReplyRead(draftKey, visibleMessageID: latest)
                }
            } } label: {
                Text("新回复").font(.caption)
                    .padding(.horizontal, 12).frame(minHeight: 44)
                    .background(MentorPalette.surface, in: Capsule())
                    .overlay(Capsule().stroke(MentorPalette.border, lineWidth: 0.7))
            }.buttonStyle(.plain).disabled(value == nil)
                .accessibilityLabel("查看新回复").accessibilityIdentifier("groupNewReply")
        }
    }
    private var canResume: Bool {
        guard let value else { return false }
        return ["interrupted", "stopped"].contains(value.status) && value.turnEnded != true &&
            !value.runId.isEmpty && !model.hasUnresolved(draftKey)
    }
    private var hasComposerActions: Bool {
        selecting || !selection.isEmpty || hasRetainedPreview || canResume ||
            value?.active == true || model.hasUnresolved(draftKey)
    }
    private var composerActions: some View {
        HStack {
                if !selection.isEmpty {
                    Button(typeSize.isAccessibilitySize ? "保存 (\(selection.count))" : "保存选定结果 (\(selection.count))") { composerFocused = false; Task {
                        let validation = await model.previewRoundtableResult(Array(selection).sorted(), conversationID: identifier)
                        result = model.roundtableID == identifier && validation != nil
                    } }.disabled(value == nil || !model.canMutateRoundtable || !model.canSaveGroupResults(Array(selection))).accessibilityIdentifier("roundtableSaveSelection")
                        .accessibilityLabel("保存选定结果 (\(selection.count))")
                        .accessibilityValue("已选 \(selection.count) 段")
                } else if selecting {
                    Text("已选 0 段").font(.callout)
                } else if hasRetainedPreview {
                    Button("继续保存预览") { result = true }
                }
                Spacer()
                if selecting {
                    Button("取消") { selection = []; selecting = false; selectionNotice = nil }
                        .frame(minHeight: 44).accessibilityIdentifier("roundtableCancelSelection")
                }
                if canResume {
                    Button("核对后继续") { Task { await model.roundtableCommand("resume", conversationID: identifier) } }
                        .frame(minHeight: 44).disabled(!model.canMutateRoundtable)
                        .accessibilityIdentifier("groupResume")
                }
                if value?.active == true || model.hasUnresolved(draftKey) {
                    Button("停止") { Task { await model.roundtableCommand("stop", conversationID: identifier) } }
                        .disabled(value == nil || model.roundtableOffline || model.hasUnresolved("roundtable-stop:" + identifier))
                        .accessibilityIdentifier("roundtableStop")
                }
        }.font(.callout)
    }
    private var composer: some View {
        VStack(alignment: .leading, spacing: 8) {
            if hasComposerActions { composerActions }
            if selecting, let selectionNotice { Text(selectionNotice).font(.caption).foregroundStyle(MentorPalette.secondary) }
            if let value, value.status == "interrupted", value.turnEnded != true {
                Text("上轮回复是否完成暂无法确认，结束后不会重试，可继续新消息；晚到的原回执仍可能出现。").font(.caption)
                Button("结束本轮") { Task { await model.roundtableCommand("end_turn", conversationID: identifier) } }
                    .frame(minHeight: 44).disabled(model.roundtableOffline || model.hasUnresolved("roundtable-end:" + identifier))
                    .accessibilityIdentifier("groupEndTurn")
            }
            HStack(alignment: .bottom) {
                Menu { ForEach(members, id: \.self) { actor in
                    Button(roundtableNames[actor] ?? "导师") {
                        let draft = model.draft(draftKey)
                        model.setDraft(draftKey, text: draft + (draft.isEmpty || draft.hasSuffix(" ") ? "" : " ") + "@" + (roundtableNames[actor] ?? "") + " ")
                        composerFocused = true
                    }
                } } label: { Text("@").font(.title3).frame(minWidth: 44, minHeight: 44) }
                    .accessibilityLabel("选择回应的导师").accessibilityIdentifier("groupMention")
                TextField("发消息", text: Binding(get: { model.draft(draftKey) }, set: { model.setDraft(draftKey, text: $0) }), axis: .vertical)
                    .lineLimit(1...(typeSize.isAccessibilitySize ? 2 : 4)).mentorInputStyle().focused($composerFocused)
                    .accessibilityIdentifier("roundtableInput")
                Button { Task { await model.sendRoundtable(conversationID: identifier) } } label: {
                    Image(systemName: "arrow.up").font(.body.weight(.semibold))
                }.buttonStyle(MentorControlStyle(prominent: true)).accessibilityLabel("发送")
                    .accessibilityIdentifier("roundtableSend")
                    .disabled(value == nil || !model.canMutateRoundtable || !["completed", "partial", "stopped"].contains(value?.status ?? "") || model.draft(draftKey).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            let responders = model.groupResponders(model.draft(draftKey), members: members)
            HStack(spacing: 8) {
                Text(responders.isEmpty ? "大家回应" : "仅" + responders.map { roundtableNames[$0] ?? "导师" }.joined(separator: "、") + "回应 · 群内可见")
                    .font(.caption).foregroundStyle(MentorPalette.secondary)
                    .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityIdentifier("groupReplyScope")
                newReplyButton.fixedSize(horizontal: true, vertical: false)
            }
        }.padding(.horizontal).padding(.vertical, 10).background(MentorPalette.paper)
    }
    private func refresh(older: Bool = false) async {
        guard model.roundtableID == identifier, !Task.isCancelled else { return }
        await model.refreshRoundtable(older: older)
    }
    private var readingPosition: Binding<String?> {
        Binding(get: { visibleMessageID }, set: { anchor in
            guard acceptingReadingChanges, value != nil, anchor != visibleMessageID else { return }
            visibleMessageID = anchor
            saveReading()
        })
    }
    private func initializeReading() {
        guard !readingInitialized else { return }
        let retained = model.local?.roundtableReading?[identifier]
        selection = Set(retained?.selectedIDs ?? [])
        selecting = !selection.isEmpty
        pendingReadingAnchor = retained?.anchor
        readingInitialized = true
    }
    private func restoreReading(using proxy: ScrollViewProxy) {
        guard readingInitialized else { return }
        guard let value else {
            acceptingReadingChanges = false
            pendingReadingAnchor = model.local?.roundtableReading?[identifier]?.anchor
            return
        }
        guard !acceptingReadingChanges, !value.messages.isEmpty else { return }
        var anchor = pendingReadingAnchor ?? value.messages.last?.id
        if let retained = anchor, !value.messages.contains(where: { $0.id == retained }) {
            guard value.nextCursor == nil, !model.roundtableOffline else { return }
            anchor = value.messages.first?.id
        }
        // An ID assigned before messages mount does not reliably trigger scrolling again.
        visibleMessageID = anchor
        if let anchor { proxy.scrollTo(anchor, anchor: .top) }
        pendingReadingAnchor = nil
        acceptingReadingChanges = true
    }
    private func saveReading() {
        guard readingInitialized, model.roundtableID == identifier, value != nil else { return }
        let anchor = acceptingReadingChanges ? visibleMessageID : pendingReadingAnchor
        model.saveRoundtableReadingState(selected: selection, anchor: anchor, conversationID: identifier)
        if acceptingReadingChanges { model.markReplyRead(draftKey, visibleMessageID: visibleMessageID) }
    }
    @ViewBuilder private func messageActions(_ item: MobileRoundtableMessage) -> some View {
        Button("复制文字", systemImage: "doc.on.doc") { UIPasteboard.general.string = roundtableCopyText(item) }
        if item.actor != "user", item.unavailable == nil, !item.superseded {
            Button("保存到日记", systemImage: "book") { openResult([item.id]) }
                .disabled(!model.canMutateRoundtable).accessibilityIdentifier("roundtableSaveMessage-" + item.id)
            Button("多选消息…", systemImage: "checkmark.circle") {
                selecting = true; toggleSelection(item)
            }.accessibilityIdentifier("roundtableMultiSelect-" + item.id)
        }
    }
    private func openResult(_ identifiers: [String]) {
#if DEBUG && targetEnvironment(simulator)
        GroupSaveProbe.record(.actionEnter, model: model, conversationID: identifier)
#endif
        composerFocused = false
        Task {
            let validation = await model.previewRoundtableResult(identifiers, conversationID: identifier)
            result = model.roundtableID == identifier && validation != nil
#if DEBUG && targetEnvironment(simulator)
            GroupSaveProbe.record(.resultAssigned, model: model, conversationID: identifier,
                state: .init(valid: validation != nil, result: result))
#endif
        }
    }
    private func toggleSelection(_ item: MobileRoundtableMessage) {
        guard value != nil else { return }
        selectionNotice = nil
        if !selection.contains(item.id) {
            let previous = value?.messages.filter { selection.contains($0.id) } ?? []
            if !selection.isEmpty && (previous.count != selection.count || previous.contains(where: { $0.runId != item.runId })) {
                selectionNotice = "每次保存同一轮的回复。请先保存或取消当前选择。"; return
            }
            if selection.count >= 5 { selectionNotice = "每次最多选五段回复，当前选择已保留。"; return }
        }
        selection = model.groupResultSelection(toggling: item.id, selected: selection)
    }
}

private struct MobileGroupDetailsView: View {
    @ObservedObject var model: MobileClientModel
    let identifier: String
    @Environment(\.dismiss) private var dismiss
    @State private var editingName = false
    @State private var working = false
    private var value: MobileRoundtable? { model.roundtableID == identifier ? model.roundtable : nil }
    var body: some View {
        Form {
            Section("群成员") {
                ForEach(value?.members ?? [], id: \.self) { actor in
                    HStack { MentorPortrait(actor: actor, topCrop: 0.32).frame(width: 44, height: 52); Text(roundtableNames[actor] ?? "导师") }
                }
                Text("群消息对这些成员可见；私聊和其他群不会自动带入。")
                    .font(.caption).foregroundStyle(MentorPalette.secondary)
            }.listRowBackground(MentorPalette.forest)
            Section("群名称") {
                Button { editingName = true } label: {
                    HStack { Text(value?.displayTitle ?? "群名称"); Spacer(); Image(systemName: "chevron.right").font(.caption) }
                        .frame(minHeight: 44)
                }.disabled(value == nil || value?.active == true || value?.status == "archived")
                    .accessibilityIdentifier("groupNameEntry")
                if value?.active == true { Text("导师回复结束后可以改名。").font(.caption) }
            }.listRowBackground(MentorPalette.forest)
            Section {
                Button(value?.status == "archived" ? "恢复群聊" : "归档群聊") { Task {
                    working = true; await model.roundtableCommand(value?.status == "archived" ? "restore" : "archive", conversationID: identifier); working = false
                } }.disabled(working || value == nil || !model.canMutateRoundtable || value?.active == true)
                    .accessibilityIdentifier("groupArchiveToggle")
                Text(value?.active == true ? "导师回复结束或停止后，可以归档。" : "归档会收起这个群，消息和草稿保留，可从首页恢复。")
                    .font(.caption).foregroundStyle(MentorPalette.secondary)
            }.listRowBackground(MentorPalette.forest)
            MobileOperationView(model: model, target: "roundtable:" + identifier)
            MobileErrorNotice(model: model)
        }.scrollContentBackground(.hidden).mentorDestinationStyle().navigationTitle("群详情")
            .toolbar { Button("完成") { dismiss() } }
            .sheet(isPresented: $editingName) {
                NavigationStack { MobileGroupNameView(model: model, identifier: identifier) }.mentorDestinationStyle()
            }
    }
}

private struct MobileGroupNameView: View {
    @ObservedObject var model: MobileClientModel
    let identifier: String
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var originalName = ""
    @State private var working = false
    @State private var operationID: String?
    private var target: String { "roundtable:" + identifier }
    private var trimmedName: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var pending: Bool { operationID != nil && model.operation(target)?.id == operationID && model.hasUnresolved(target) }
    var body: some View {
        Form {
            Section {
                TextField("群名称", text: $name).accessibilityIdentifier("groupNameInput")
                    .disabled(working || pending)
                Text(name.count > 120 ? "群名称最多 120 个字符。" : "修改名称不会改变群成员和聊天记录。")
                    .font(.caption).foregroundStyle(MentorPalette.secondary)
            }.listRowBackground(MentorPalette.forest)
            MobileOperationView(model: model, target: target)
            MobileErrorNotice(model: model)
        }.scrollContentBackground(.hidden).mentorDestinationStyle().navigationTitle("修改群名称")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(pending ? "稍后处理" : "取消") { dismiss() }.disabled(working).accessibilityIdentifier("groupNameCancel")
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") { Task { await save() } }
                        .disabled(working || trimmedName.isEmpty || name.count > 120 || trimmedName == originalName || !model.canMutateRoundtable)
                        .accessibilityIdentifier("groupRename")
                }
            }
            .interactiveDismissDisabled(working || (!pending && name != originalName))
            .task {
                guard model.roundtableID == identifier else { return }
                originalName = model.roundtable?.displayTitle ?? ""; name = originalName
                if let operation = model.operation(target), operation.unresolved,
                   operation.body["action"]?.string == "rename", let retained = operation.body["text"]?.string {
                    name = retained; operationID = operation.id
                }
            }
            .onChange(of: model.operation(target)?.state) { closeIfSaved() }
            .onChange(of: model.roundtable?.displayTitle) { closeIfSaved() }
    }
    private func save() async {
        let previous = model.operation(target)?.id
        working = true
        await model.roundtableCommand("rename", text: trimmedName, conversationID: identifier)
        if let operation = model.operation(target), operation.id != previous { operationID = operation.id }
        working = false
        closeIfSaved()
    }
    private func closeIfSaved() {
        guard let operation = model.operation(target), operation.id == operationID, operation.state == "completed",
              model.roundtableID == identifier, model.roundtable?.displayTitle == trimmedName else { return }
        dismiss()
    }
}

struct MobileRoundtableView: View {
    @ObservedObject var model: MobileClientModel
    @ObservedObject var diary: DiaryViewModel
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var invite = false
    let identifier: String
    var embedded = false
    @State private var background = false
    @State private var selection: Set<String> = []
    @State private var selecting = false
    @State private var selectionNotice: String?
    @State private var visibleMessageID: String?
    @State private var readingInitialized = false
    @State private var acceptingReadingChanges = false
    @State private var pendingReadingAnchor: String?
    @FocusState private var composerFocused: Bool
    @State private var result = false
    @State private var statement = "user_statement"
    private var value: MobileRoundtable? { model.roundtableID == identifier ? model.roundtable : nil }
    private var draftKey: String { "roundtable:" + identifier }
    private var hasRetainedPreview: Bool {
        diary.aiResultPreview?.account == model.credential?.scope &&
            diary.aiResultPreview?.result.provenance.problemId == identifier
    }
    private var title: String {
        model.roundtableList?.items.first(where: { $0.id == identifier })?.title ??
            value?.displayTitle ?? "圆桌"
    }
    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 20) {
                    Text(participation).font(.caption).foregroundStyle(MentorPalette.secondary)
                        .accessibilityIdentifier("roundtableParticipants")
                    if !embedded { NorthStarAnchorBanner(diary: diary, mobile: model, compact: true) }
                    MobileErrorNotice(model: model)
                    if embedded { MobileOperationView(model: model, target: model.groupCreationTarget) }
                    if let notice = model.roundtableCacheNotice { Text(notice).font(.caption) }
                    if model.roundtableOffline, let cached = value {
                        VStack(alignment: .leading) {
                            Label("离线副本 · 草稿仍在本机", systemImage: "wifi.slash")
                            Text("读取时间：" + Date(timeIntervalSince1970: cached.cacheExpiresAt - 900).formatted(date: .abbreviated, time: .shortened))
                            Text("最多保留 15 分钟，联网核验前不能发送或保存。")
                        }.font(.caption)
                    }
                    if let value {
                        if value.status != "completed" { HStack {
                            Text(status(value)).font(.caption).foregroundStyle(.secondary)
                            Spacer()
                        } }
                        if let reason = value.completionNotice { Text(reason).font(.callout).accessibilityIdentifier("roundtableCompletionNotice") }
                        if value.nextCursor != nil { Button("查看更早消息") { Task { await refresh(older: true) } } }
                        ForEach(value.messages) { item in
                            MentorSpeakerCard(actor: item.actor, name: roundtableNames[item.actor] ?? item.actor,
                                kind: kind(item), featured: item.id == latestMentorMessageID,
                                compact: composerFocused, accessory: {
                                    if selecting, item.actor != "user", item.unavailable == nil && !item.superseded {
                                        Button { toggleSelection(item) } label: {
                                            Image(systemName: selection.contains(item.id) ? "checkmark.circle.fill" : "circle").frame(minWidth: 44, minHeight: 44).contentShape(Rectangle())
                                        }.accessibilityLabel(selection.contains(item.id) ? "取消选择这段结果" : "选择这段结果")
                                            .accessibilityValue(selection.contains(item.id) ? "已选择" : "未选择")
                                            .accessibilityIdentifier("roundtableSelect-" + item.id)
                                    } else if !selecting {
                                        Menu { messageActions(item) } label: {
                                            Image(systemName: "ellipsis").frame(minWidth: 44, minHeight: 44)
                                        }.accessibilityLabel("消息操作")
                                            .accessibilityIdentifier((item.actor == "user" ? "roundtableUserActions-" : "roundtableMessageActions-") + item.id)
                                    }
                                }) {
                                Text(item.visibleText).textSelection(.enabled)
                                if item.superseded { Text("条件已纠正 · 旧结论需要重新评估").font(.caption).foregroundStyle(.orange) }
                                ForEach(item.uncertainties, id: \.self) { Text("分歧 / 条件：" + $0).font(.callout) }
                                ForEach(item.nextSteps, id: \.self) { Text("建议下一步：" + $0).font(.callout) }
                            }.id(item.id).accessibilityElement(children: .contain).accessibilityIdentifier("roundtableMessage-" + item.id)
                                .contextMenu { messageActions(item) }
                        }
                        if hasRetainedPreview { Button("继续本地保存预览") { result = true } }
                        if value.active { ProgressView(progress(value)).font(.caption) }
                    } else { ProgressView("读取圆桌记录"); Button("重新读取") { Task { await refresh() } } }
                    MobileOperationView(model: model, target: draftKey)
                    MobileOperationView(model: model, target: "roundtable-result:" + identifier)
                    MobileOperationView(model: model, target: "roundtable-stop:" + identifier)
                }.padding().scrollTargetLayout()
            }.accessibilityIdentifier("roundtableMessages")
                .scrollPosition(id: readingPosition, anchor: .top)
            .scrollDismissesKeyboard(.interactively).refreshable { await refresh() }
            .task(id: identifier) { initializeReading(); restoreReading(using: proxy) }
            .onChange(of: value?.messages.map(\.id)) { restoreReading(using: proxy) }
            }
            Divider()
            actions
            HStack(alignment: .bottom) {
                TextField("和主持人聊聊", text: Binding(get: { model.draft(draftKey) }, set: { model.setDraft(draftKey, text: $0) }), axis: .vertical)
                    .lineLimit(1...(typeSize.isAccessibilitySize ? 2 : 4)).mentorInputStyle().focused($composerFocused).accessibilityIdentifier("roundtableInput")
                Button { Task { await model.sendRoundtable(statement: statement, conversationID: identifier) } } label: {
                    Image(systemName: "arrow.up").font(.body.weight(.semibold))
                }.accessibilityLabel("发送")
                    .disabled(value == nil || !model.canMutateRoundtable || value?.status == "archived" || model.draft(draftKey).isEmpty || model.hasUnresolved(draftKey))
                    .accessibilityIdentifier("roundtableSend").buttonStyle(MentorControlStyle(prominent: true))
            }.padding(.horizontal).padding(.bottom, 8)
        }
        .modifier(MobileRoundtablePresentation(diary: diary, model: model, title: title, embedded: embedded))
        .preference(key: MobileGroupComposerFocusKey.self, value: composerFocused)
        .toolbar {
            Menu {
                if hasRetainedPreview { Button("继续本地保存预览") { result = true } }
                Button("当前条件与来源") { background = true }
                if let value {
                    Button(value.status == "archived" ? "恢复这段讨论" : "归档这段讨论") { Task { await model.roundtableCommand(value.status == "archived" ? "restore" : "archive", conversationID: identifier) } }
                }
            } label: { Image(systemName: "ellipsis.circle") }.accessibilityIdentifier("roundtableMenu")
        }
        .task(id: "\(identifier)-\(model.epoch)-\(model.foreground)") {
            guard model.foreground else { return }
            if let account = model.credential?.scope { await diary.restorePendingAIResults(account: account, conversationID: identifier) }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                guard !Task.isCancelled, model.roundtableID == identifier else { return }
                await model.maintainRoundtable()
            }
        }
        .onChange(of: selection) { saveReading() }
        .sheet(isPresented: $invite) { NavigationStack { RoundtableInvitation(model: model, identifier: identifier) } }
        .sheet(isPresented: $background) { NavigationStack { RoundtableBackground(model: model, identifier: identifier) } }
        .sheet(isPresented: $result) { NavigationStack { RoundtableResultView(model: model, diary: diary, identifier: identifier) } }
    }

    private func refresh(older: Bool = false) async {
        guard model.roundtableID == identifier, !Task.isCancelled else { return }
        await model.refreshRoundtable(older: older)
    }
    private var readingPosition: Binding<String?> {
        Binding(get: { visibleMessageID }, set: { anchor in
            guard acceptingReadingChanges, value != nil, anchor != visibleMessageID else { return }
            visibleMessageID = anchor
            saveReading()
        })
    }
    private func initializeReading() {
        guard !readingInitialized else { return }
        let retained = model.local?.roundtableReading?[identifier]
        selection = Set(retained?.selectedIDs ?? [])
        selecting = !selection.isEmpty
        pendingReadingAnchor = retained?.anchor
        readingInitialized = true
    }
    private func restoreReading(using proxy: ScrollViewProxy) {
        guard readingInitialized else { return }
        guard let value else {
            acceptingReadingChanges = false
            pendingReadingAnchor = model.local?.roundtableReading?[identifier]?.anchor
            return
        }
        guard !acceptingReadingChanges, !value.messages.isEmpty else { return }
        var anchor = pendingReadingAnchor ?? value.messages.last?.id
        if let retained = anchor, !value.messages.contains(where: { $0.id == retained }) {
            guard value.nextCursor == nil, !model.roundtableOffline else { return }
            anchor = value.messages.first?.id
        }
        // An ID assigned before messages mount does not reliably trigger scrolling again.
        visibleMessageID = anchor
        if let anchor { proxy.scrollTo(anchor, anchor: .top) }
        pendingReadingAnchor = nil
        acceptingReadingChanges = true
    }
    private func saveReading() {
        guard readingInitialized, model.roundtableID == identifier, value != nil else { return }
        let anchor = acceptingReadingChanges ? visibleMessageID : pendingReadingAnchor
        model.saveRoundtableReadingState(selected: selection, anchor: anchor, conversationID: identifier)
        if acceptingReadingChanges { model.markReplyRead(draftKey, visibleMessageID: visibleMessageID) }
    }
    private var participation: String {
        let participants = (value?.runPersonas ?? []).filter { $0 != "host" }
        if participants.isEmpty { return "主持人接话 · 四位导师可按需邀请" }
        return "本轮发言：主持人、" + participants.map { roundtableNames[$0] ?? $0 }.joined(separator: "、")
    }
    private var latestMentorMessageID: String? {
        value?.messages.last(where: { $0.actor != "user" && $0.actor != "host" && $0.unavailable == nil && !$0.superseded })?.id
    }
    private var actions: some View {
        VStack(alignment: .leading, spacing: 4) {
            if selecting {
                HStack {
                    Text("选取日记内容 · 已选 \(selection.count) 段").font(.callout)
                    Spacer()
                    Button("取消") { selecting = false; selection = []; selectionNotice = nil }
                        .frame(minHeight: 44).accessibilityIdentifier("roundtableCancelSelection")
                }.padding(.horizontal)
                if let selectionNotice { Text(selectionNotice).font(.caption).padding(.horizontal) }
            }
        ScrollView(.horizontal) {
            HStack {
                if !selection.isEmpty {
                    Button("保存选定结果 (\(selection.count))") { composerFocused = false; Task {
                        let validation = await model.previewRoundtableResult(Array(selection), conversationID: identifier)
                        result = model.roundtableID == identifier && validation != nil
                    } }
                        .disabled(selection.count > 5 || !model.canMutateRoundtable).accessibilityIdentifier("roundtableSaveSelection")
                }
                Menu("内容性质") { Picker("内容性质", selection: $statement) { Text("我的陈述").tag("user_statement"); Text("我的计划").tag("user_plan"); Text("实际反馈").tag("user_feedback") } }
                if let value, !model.roundtableOffline {
                    if value.ready {
                        Button("邀请导师") { composerFocused = false; invite = true }.accessibilityIdentifier("roundtableInvite")
                        Button("继续追问") { Task { await model.roundtableCommand("continue", conversationID: identifier) } }
                    }
                    if model.canSummarizeRoundtable {
                        Button("先总结") { Task { await model.roundtableCommand("summarize", conversationID: identifier) } }.accessibilityIdentifier("roundtableSummarize")
                    }
                    if value.active || value.status == "waiting_user" {
                        Button("停止", role: .destructive) { Task { await model.roundtableCommand("stop", conversationID: identifier) } }.accessibilityIdentifier("roundtableStop")
                    }
                    if ["interrupted", "stopped", "waiting_user"].contains(value.status) || !value.suspendedRunId.isEmpty {
                        Button("核对后继续") { Task { await model.roundtableCommand("resume", conversationID: identifier) } }
                    }
                }
            }.font(.callout).buttonStyle(MentorControlStyle()).padding(.horizontal).padding(.vertical, 8)
        }
        }
    }
    @ViewBuilder private func messageActions(_ item: MobileRoundtableMessage) -> some View {
        Button("复制文字", systemImage: "doc.on.doc") { UIPasteboard.general.string = roundtableCopyText(item) }
        if item.actor != "user", item.unavailable == nil, !item.superseded {
            Button("保存到日记", systemImage: "book") {
                composerFocused = false
                Task { let validation = await model.previewRoundtableResult([item.id], conversationID: identifier)
                    result = model.roundtableID == identifier && validation != nil
                }
            }.disabled(!model.canMutateRoundtable).accessibilityIdentifier("roundtableSaveMessage-" + item.id)
            Button("多选消息…", systemImage: "checkmark.circle") { selecting = true; toggleSelection(item) }
                .accessibilityIdentifier("roundtableMultiSelect-" + item.id)
        }
    }
    private func toggleSelection(_ item: MobileRoundtableMessage) {
        guard value != nil else { return }
        selectionNotice = nil
        if !selection.contains(item.id) {
            let previous = value?.messages.filter { selection.contains($0.id) } ?? []
            if !selection.isEmpty && (previous.count != selection.count || previous.contains(where: { $0.runId != item.runId })) {
                selectionNotice = "每次保存同一轮的回复。请先保存或取消当前选择。"; return
            }
            if selection.count >= 5 { selectionNotice = "每次最多选五段回复，当前选择已保留。"; return }
            selection.insert(item.id)
        } else { selection.remove(item.id) }
    }
    private func progress(_ value: MobileRoundtable) -> String {
        guard let step = value.progress else { return "等待本次发言，已完成内容保留在上方" }
        let name = roundtableNames[step.actor] ?? "导师"
        let stage = ["delivery": "回复送达中", "opinion": "整理观点", "comparison": "比较不同观点", "debate": "讨论分歧", "synthesis": "整理总结", "followup": "回复中"][step.stage] ?? "处理中"
        return name + " · " + stage + (step.round > 0 ? " · 第 \(step.round) 轮" : "")
    }
    private func kind(_ item: MobileRoundtableMessage) -> String {
        item.actor != "user" ? "AI 建议" : ["user_plan": "我的计划", "user_feedback": "实际反馈"][item.originKind] ?? "我的陈述"
    }
    private func status(_ value: MobileRoundtable) -> String {
        if value.active { return value.runKind == "followup" ? "主持人回复中" : "导师讨论中 · 第 \(value.rounds) 轮预算内" }
        return ["completed": "本轮已完成", "partial": "部分完成", "stopped": "已确认停止", "waiting_user": "条件已更新，等待继续", "interrupted": "运行中断，请先核对原操作", "archived": "已归档"][value.status] ?? "等待核对状态"
    }
}

private struct RoundtableInvitation: View {
    @ObservedObject var model: MobileClientModel
    let identifier: String
    private var value: MobileRoundtable? { model.roundtableID == identifier ? model.roundtable : nil }
    private var preview: MobileMaterialPreview? { model.roundtableID == identifier ? model.materialPreview : nil }
    @Environment(\.dismiss) private var dismiss
    @State private var personas: Set<String> = ["gentle_reviewer", "blunt_coach"]
    @State private var artifacts: Set<String> = []
    @State private var memories: Set<String> = []
    @State private var selectedMemories: [String: MobileMemory] = [:]
    @State private var mode = "reference"
    @State private var rounds = 1
    @State private var working = false
    @State private var submitting = false
    var body: some View {
        Form {
            if let preview { review(preview) }
            else { selectionForm.disabled(working) }
        }
        .id(preview == nil ? "selection" : "review")
        .navigationTitle(preview == nil ? "邀请导师" : "核对邀请")
        .safeAreaInset(edge: .bottom, spacing: 0) { invitationAction }
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("取消") { invalidatePreview(); dismiss() }.disabled(submitting) }
            if preview != nil {
                ToolbarItem(placement: .primaryAction) {
                    Button("返回修改") { invalidatePreview() }.disabled(working)
                        .accessibilityIdentifier("roundtableEditInvite")
                }
            }
        }
        .interactiveDismissDisabled(submitting)
        .onDisappear { invalidatePreview() }
        .task { invalidatePreview(); artifacts = Set((value?.messages ?? []).filter { $0.kind == "user" && !$0.superseded && $0.unavailable == nil }.suffix(48).map(\.id)); await model.refreshMemories() }
        .onChange(of: personas) { invalidatePreview() }.onChange(of: artifacts) { invalidatePreview() }
        .onChange(of: memories) { invalidatePreview() }.onChange(of: mode) { invalidatePreview() }.onChange(of: rounds) { invalidatePreview() }
    }
    @ViewBuilder private var selectionForm: some View {
        Section("本次发言者 · 选择 2–4 位") {
            ForEach(model.capabilities?.mentors ?? []) { mentor in Toggle(mentor.name, isOn: toggle(mentor.id, in: $personas)) }
            Picker("讨论方式", selection: Binding(get: { mode }, set: { invalidatePreview(); mode = $0 })) {
                Text("先听参考意见").tag("reference"); Text("讨论分歧").tag("debate")
            }.accessibilityIdentifier("roundtableMode")
            if mode == "debate" {
                Stepper("最多 \(rounds) 轮", value: Binding(get: { rounds }, set: { invalidatePreview(); rounds = $0 }), in: 1...10)
                    .accessibilityIdentifier("roundtableRounds")
                Text("最多讨论 \(rounds) 轮，可随时停止或先总结；初次比较无实质分歧时会跳过后续讨论，达到资料使用限制时也会提前结束。").font(.caption)
            } else {
                Text("各导师分别给出意见，再由主持人汇总。需要多轮交锋时，选择“讨论分歧”。").font(.caption)
            }
        }
        Section("选定聊天材料 · 已选 \(artifacts.count)/48") {
            ForEach((value?.messages ?? []).filter { $0.unavailable == nil && !$0.superseded }) { item in
                Toggle(isOn: toggle(item.id, in: $artifacts)) { Text(item.visibleText).lineLimit(4) }
                    .disabled(!artifacts.contains(item.id) && artifacts.count >= 48)
            }
            Text("默认选择最近最多 48 段已读取的用户聊天，可取消或更换。选择旧 AI 观点时，也需选择它所依据的聊天与记忆；后台会核验。私聊不会自动带入。").font(.caption)
        }
        memorySection
    }
    @ViewBuilder private func review(_ preview: MobileMaterialPreview) -> some View {
        Section("本次讨论") {
            Text(preview.personas.map { roundtableNames[$0] ?? $0 }.joined(separator: "、"))
                .accessibilityIdentifier("roundtableInviteSpeakers")
            Text(preview.mode == "debate" ? "讨论分歧 · 最多 \(preview.rounds) 轮" : "先听参考意见 · 一次汇总")
                .accessibilityIdentifier("roundtableInviteSummary")
        }
        Section("将分享的聊天材料") {
            ForEach((value?.messages ?? []).filter { preview.artifactIds.contains($0.id) }) { item in
                Text(item.visibleText)
            }
        }
        Section("将分享的共享记忆") {
            if preview.memories.isEmpty { Text("未选择共享记忆").foregroundStyle(.secondary) }
            ForEach(preview.memories) { item in Text(item.content).accessibilityIdentifier("roundtableReviewMemory-" + item.id) }
        }
    }
    private var invitationAction: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("选定材料对主持人和全部四位固定导师可见。私聊不会自动带入。").font(.caption).foregroundStyle(.secondary)
                .accessibilityIdentifier("roundtableShareNotice")
            MobileErrorNotice(model: model)
            if let error = model.materialPreviewError { Text(error).font(.caption).foregroundStyle(.red) }
            Button { Task { await advanceInvitation() } } label: {
                HStack {
                    if working { ProgressView().tint(.white) }
                    Text(preview == nil ? "下一步：核对材料" : "确认邀请并开始")
                }.frame(maxWidth: .infinity, minHeight: 32)
            }
            .buttonStyle(.borderedProminent).controlSize(.large)
            .disabled(working || value == nil || !model.canMutateRoundtable || personas.count < 2 || artifacts.isEmpty)
            .accessibilityIdentifier(preview == nil ? "roundtablePreviewInvite" : "roundtableConfirmInvite")
        }.padding(.horizontal).padding(.vertical, 12).background(.bar)
    }
    private func invalidatePreview() {
        guard model.roundtableID == identifier else { return }
        model.invalidateMaterialPreview()
    }
    private func advanceInvitation() async {
        guard !working, model.roundtableID == identifier else { return }
        working = true
        defer { working = false }
        if preview == nil {
            await model.previewMaterials(artifacts: Array(artifacts).sorted(), memories: selectedMemoryItems,
                personas: Array(personas).sorted(), mode: mode, rounds: mode == "debate" ? rounds : 1)
        } else {
            submitting = true
            defer { submitting = false }
            let target = "roundtable:" + identifier
            let previousOperation = model.operation(target)?.id
            await model.roundtableCommand("start_run", conversationID: identifier)
            if let receipt = model.operation(target), receipt.id != previousOperation,
               receipt.kind == "roundtable_command", receipt.state == "completed" { dismiss() }
        }
    }
    private var eligibleMemories: [MobileMemory] {
        (model.memories?.items ?? []).filter { $0.scope == "shared" && $0.status == "active" && $0.privacy == "cloud" }
    }
    private var selectedMemoryCharacters: Int { selectedMemoryItems.reduce(0) { $0 + $1.content.unicodeScalars.count } }
    private func canSelectMemory(_ item: MobileMemory) -> Bool {
        memories.contains(item.id) || (memories.count < 10 && selectedMemoryCharacters + item.content.unicodeScalars.count <= 4000)
    }
    private var selectedMemoryItems: [MobileMemory] { selectedMemories.values.sorted { $0.id < $1.id } }
    private func memoryToggle(_ item: MobileMemory) -> Binding<Bool> {
        Binding(get: { memories.contains(item.id) }, set: { selected in
            invalidatePreview()
            if selected { guard canSelectMemory(item) else { return }; memories.insert(item.id); selectedMemories[item.id] = item }
            else { memories.remove(item.id); selectedMemories[item.id] = nil }
        })
    }
    private var memorySection: some View {
        Section("共享记忆 · 已选 \(memories.count)/10 · \(selectedMemoryCharacters)/4000 字符") {
            ForEach(eligibleMemories) { item in
                Toggle(item.content, isOn: memoryToggle(item)).disabled(!canSelectMemory(item))
                    .accessibilityIdentifier("roundtableMemory-" + item.id)
            }
            ForEach(selectedMemoryItems) { item in
                HStack {
                    Text("已选：" + item.content).font(.caption)
                    Spacer()
                    Button("移除") { invalidatePreview(); memories.remove(item.id); selectedMemories[item.id] = nil }
                }
            }
            Text("这里只显示已读取的共享记忆页，不上传手机日记文件。").font(.caption)
            if let cursor = model.memories?.nextCursor { Button("读取下一页记忆") { Task { await model.refreshMemories(cursor: cursor) } } }
        }
    }
    private func toggle(_ value: String, in selected: Binding<Set<String>>) -> Binding<Bool> {
        Binding(get: { selected.wrappedValue.contains(value) }, set: { enabled in invalidatePreview(); if enabled { selected.wrappedValue.insert(value) } else { selected.wrappedValue.remove(value) } })
    }
}

private struct RoundtableBackground: View {
    @ObservedObject var model: MobileClientModel
    let identifier: String
    private var value: MobileRoundtable? { model.roundtableID == identifier ? model.roundtable : nil }
    private var draftKey: String { "roundtable-correction:" + identifier }
    @Environment(\.dismiss) private var dismiss
    @State private var target: String?
    var body: some View {
        Form {
            Text("背景版本 \(value?.revision ?? 0) · 摘要版本 \(value?.summaryVersion ?? 0)")
            ForEach((value?.messages ?? []).filter { $0.actor == "user" }) { item in
                Button { target = item.id } label: {
                    VStack(alignment: .leading) {
                        Text(item.visibleText).foregroundStyle(item.superseded ? .secondary : .primary)
                        Text(item.superseded ? "已纠正，旧依据需重评" : (value?.selectedArtifactIds.contains(item.id) == true ? "当前选定聊天依据" : "历史陈述，未选入当前背景")).font(.caption)
                        if target == item.id { Label("已选定纠正", systemImage: "checkmark.circle").font(.caption) }
                    }
                }
            }
            Section("当前选定共享记忆") {
                if (value?.materialSources ?? []).isEmpty { Text("未选入共享记忆；主持人不会自动读取。").foregroundStyle(.secondary) }
                ForEach(value?.materialSources ?? []) { source in
                    Text(source.unavailable == nil ? source.text : "依据已失效，此内容暂不可用。")
                }
            }
            TextField("新的条件或更正", text: Binding(get: { model.draft(draftKey) }, set: { model.setDraft(draftKey, text: $0) }), axis: .vertical)
            Button("确认纠正选定条件") { Task {
                guard let target else { return }
                await model.roundtableCommand("correct", text: model.draft(draftKey), supersedes: [target], conversationID: identifier)
                dismiss()
            } }.disabled(target == nil || value == nil || !model.canMutateRoundtable)
            Text("旧陈述和旧 AI 建议会保留，并标记需要重新评估。").font(.caption)
        }.navigationTitle("条件与来源").toolbar { Button("完成") { dismiss() } }
    }
}

private struct RoundtableResultView: View {
    private struct Input: Equatable {
        let account: String
        let resultID: String
        let day: JournalDay
        let text: String
        let source: String
    }
    @ObservedObject var model: MobileClientModel
    @ObservedObject var diary: DiaryViewModel
    let identifier: String
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var date = Date()
    @State private var sourceResult: MobileResultValidation?
    @State private var sourceAccount: String?
    @State private var initialized = false
    @State private var editing = false
    @State private var preparing = false
    @State private var saving = false
    @State private var saved = false
    @State private var checkingOutcome = false
    @State private var confirmingDiscard = false
    @State private var cancellationStatus: String?
    @State private var revision = 0
    @State private var preparedInput: Input?
    @State private var preparationTask: Task<Void, Never>?
    private var input: Input? {
        guard initialized, !saved, let result = sourceResult, let account = sourceAccount,
              account == model.credential?.scope else { return nil }
        return Input(account: account, resultID: result.resultId,
                     day: JournalDay(date: date, timeZone: diary.timeZone), text: text,
                     source: diary.sourceIdentity)
    }
    private var retainedInput: Bool {
        guard let input, let preview = diary.aiResultPreview else { return false }
        return preview.account == input.account && preview.result.resultId == input.resultID
            && preview.day == input.day && preview.text == input.text
    }
    private var canLeaveRetainingInput: Bool { saved || !initialized || retainedInput }
    private var readyPreview: AIResultPreview? {
        guard !editing, !preparing, !checkingOutcome, let input, preparedInput == input,
              retainedInput else { return nil }
        return diary.aiResultPreview
    }
    var body: some View {
        Form {
            if !initialized {
                ProgressView("正在读取保存内容")
            } else if !saved {
                Section {
                    DatePicker("保存日期", selection: Binding(get: { date }, set: {
                        date = $0; invalidatePreparation()
                    }), displayedComponents: .date).disabled(saving || checkingOutcome)
                    Text("仅追加到所选日记的 Notes，保留 AI 来源；保存不代表采纳或执行。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                contentSection
                actionSection
            }
            if let cancellationStatus {
                Text(cancellationStatus).font(.callout).accessibilityIdentifier("roundtableCancelSaveStatus")
            }
            if let status = diary.aiResultStatus {
                Text(status).font(.callout).accessibilityIdentifier("roundtableSaveStatus")
            }
            MobileErrorNotice(model: model)
        }.rijiPage().navigationTitle("保存到日记").toolbar {
            Button("完成") { close() }.foregroundStyle(.primary).disabled(saving)
                .accessibilityIdentifier("roundtableCloseResult")
        }.interactiveDismissDisabled(saving || !canLeaveRetainingInput)
            .alert("最新修改尚未保留", isPresented: $confirmingDiscard) {
                Button("继续编辑", role: .cancel) { }
                    .accessibilityIdentifier("roundtableContinueEditingResult")
                Button("放弃未保存修改并离开", role: .destructive) {
                    invalidatePreparation(); dismiss()
                }.accessibilityIdentifier("roundtableDiscardResultEdits")
            } message: {
                Text("继续编辑并完成预览可保留最新文字和日期。放弃后，之前已保留的预览仍在，最新修改不会保留。")
            }
            .tint(Color(uiColor: RijiPalette.action))
            .task { await initialize() }
            .onChange(of: input) { _, _ in
                if !editing { schedulePreparation() }
            }
            .onDisappear { preparationTask?.cancel() }
    }
    @ViewBuilder private var contentSection: some View {
        Section(editing ? "修改保存文字" : "本次追加预览") {
            if editing {
                TextEditor(text: Binding(get: { text }, set: {
                    text = $0; invalidatePreparation()
                })).frame(minHeight: 180).accessibilityIdentifier("roundtableResultText")
                Button("完成编辑") { editing = false; schedulePreparation() }
                    .accessibilityIdentifier("roundtableFinishEditingResult")
            } else if let preview = readyPreview {
                readablePreview(preview)
                DisclosureGroup("查看确切 Markdown（含来源记录）") {
                    Text(preview.block).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                        .accessibilityIdentifier("roundtableExactMarkdown")
                }
            } else if preparing {
                ProgressView("正在准备保存预览")
            } else if checkingOutcome {
                Text("正在核对保存结果，文字与本机记录已保留。")
            } else {
                Text("预览尚未准备好，文字和日期仍保留。")
            }
            if !editing {
                Button("修改文字") { editing = true; invalidatePreparation() }.disabled(saving || checkingOutcome)
                    .accessibilityIdentifier("roundtableEditResult")
            }
        }.disabled(saving)
    }
    @ViewBuilder private var actionSection: some View {
        Section {
            if checkingOutcome {
                Button("核对保存结果") { Task { await checkOutcome() } }.disabled(saving)
                    .accessibilityIdentifier("roundtableReconcileSave")
            } else {
                Button("确认保存") { Task { await confirm() } }
                    .disabled(readyPreview == nil || editing || saving)
                    .accessibilityIdentifier("roundtableConfirmSave")
                if !editing && !preparing && readyPreview == nil {
                    Button("重新准备预览") { schedulePreparation() }
                        .accessibilityIdentifier("roundtableRetryPreview")
                }
            }
            Button("取消保存", role: .destructive) { Task { await cancelSave() } }
                .disabled(saving)
                .accessibilityIdentifier("roundtableCancelSave")
        }
    }
    private func readablePreview(_ preview: AIResultPreview) -> some View {
        let provenance = preview.result.provenance
        let mentors = provenance.mentors.map { roundtableNames[$0] ?? "其他导师" }.joined(separator: "、")
        let edited = provenance.editedByUser || preview.text != preview.result.text
        return VStack(alignment: .leading, spacing: 12) {
            Text("将追加到 \(preview.day.value) 日记 · Notes").font(.headline)
            Text("AI 导师讨论结果").font(.subheadline.weight(.semibold))
            Text("这是 AI 讨论资料；保存不代表采纳建议，也不代表计划已经执行。")
                .font(.caption).foregroundStyle(.secondary)
            DiaryBodyEditor(text: preview.text, day: preview.day, editable: false, onChange: { _, _ in })
                .frame(height: 240).accessibilityIdentifier("roundtableRenderedPreview")
            Text("来源：\(provenance.topic)")
            Text("参与导师：\(mentors)")
            Text("讨论时间：\(readableDate(provenance.recordedAt))")
            Text("用户编辑：\(edited ? "是" : "否")")
        }.font(.callout)
    }
    private func readableDate(_ value: String) -> String {
        guard let date = ISO8601DateFormatter().date(from: value) else { return value }
        return date.formatted(date: .abbreviated, time: .shortened)
    }
    private func close() {
        guard !saving else { return }
        if canLeaveRetainingInput { preparationTask?.cancel(); dismiss() }
        else { invalidatePreparation(); confirmingDiscard = true }
    }
    private func cancelSave() async {
        guard !saving, let input else { return }
        saving = true; cancellationStatus = nil
        let task = preparationTask
        invalidatePreparation()
        await task?.value
        defer { saving = false }
        guard self.input == input else { return }
        let stop = await diary.restoreAIResult(account: input.account, resultID: input.resultID)
        guard self.input == input else { return }
        saved = diary.aiResultPreview == nil && diary.aiResultStatus?.hasPrefix("已保存到") == true
        if stop {
            checkingOutcome = !saved
            cancellationStatus = saved ? "已核对为保存成功，不能通过取消撤回日记内容。" : "保存结果尚未确定，核对完成前不能取消。"
            return
        }
        if let preview = diary.aiResultPreview {
            guard preview.account == input.account, preview.result.resultId == input.resultID else { return }
            await diary.cancelLocalAIResult(preview)
        }
        guard self.input == input else { return }
        if diary.aiResultPreview == nil && diary.aiResultStatus == nil {
            dismiss()
        } else {
            saved = diary.aiResultPreview == nil && diary.aiResultStatus?.hasPrefix("已保存到") == true
            checkingOutcome = !saved
            cancellationStatus = saved ? "已核对为保存成功，不能通过取消撤回日记内容。" : "暂时无法安全取消，文字与预览仍保留；请先核对保存结果。"
        }
    }
    private func initialize() async {
        guard !initialized, let account = model.credential?.scope else { return }
        let retained = diary.aiResultPreview.flatMap {
            $0.account == account && $0.result.provenance.problemId == identifier ? $0.result : nil
        }
        let current = model.resultValidation.flatMap { $0.provenance.problemId == identifier ? $0 : nil }
        guard let result = current ?? retained else { return }
        sourceResult = result; sourceAccount = account
        let stop = await diary.restoreAIResult(account: account, resultID: result.resultId)
        guard !Task.isCancelled, model.credential?.scope == account else { return }
        date = diary.selectedDay.date(in: diary.timeZone); text = result.text
        if let preview = diary.aiResultPreview, preview.account == account,
           preview.result.resultId == result.resultId {
            date = preview.day.date(in: diary.timeZone); text = preview.text
        }
        saved = diary.aiResultPreview == nil && diary.aiResultStatus?.hasPrefix("已保存到") == true
        checkingOutcome = stop && !saved
        initialized = true
    }
    private func invalidatePreparation() {
        revision += 1; preparationTask?.cancel(); preparedInput = nil; preparing = false
        cancellationStatus = nil
        diary.invalidateAIResultPreparation()
    }
    private func schedulePreparation() {
        invalidatePreparation()
        guard let input, !editing, !checkingOutcome, !saving else { return }
        preparing = true
        let version = revision
        preparationTask = Task {
            do { try await Task.sleep(nanoseconds: 350_000_000) } catch { return }
            guard revision == version, self.input == input, let result = sourceResult else { return }
            let validation = await model.validateRoundtableResult(result.resultId,
                                                                  conversationID: result.provenance.problemId)
            guard !Task.isCancelled, revision == version, self.input == input else { return }
            if let validation {
                let preview = await diary.rebuildAIResultPreview(validation, account: input.account,
                                                               day: input.day, text: input.text)
                guard !Task.isCancelled, revision == version, self.input == input else { return }
                if let preview, diary.aiResultPreview == preview { preparedInput = input }
                saved = diary.aiResultPreview == nil && diary.aiResultStatus?.hasPrefix("已保存到") == true
            }
            preparing = false
        }
    }
    private func confirm() async {
        guard !saving, !editing, let input, let preview = readyPreview else { return }
        saving = true
        defer { saving = false }
        if await diary.restoreAIResult(account: input.account, resultID: input.resultID) {
            saved = diary.aiResultPreview == nil && diary.aiResultStatus?.hasPrefix("已保存到") == true
            checkingOutcome = !saved; preparedInput = nil; return
        }
        guard self.input == input, diary.aiResultPreview == preview, let result = sourceResult,
              let validation = await model.validateRoundtableResult(result.resultId,
                   conversationID: result.provenance.problemId) else { preparedInput = nil; return }
        guard self.input == input, diary.aiResultPreview == preview else { preparedInput = nil; return }
        checkingOutcome = true
        await diary.confirmAIResult(account: input.account, validation: validation, expectedPreview: preview)
        saved = diary.aiResultPreview == nil && diary.aiResultStatus?.hasPrefix("已保存到") == true
        checkingOutcome = !saved; preparedInput = nil
    }
    private func checkOutcome() async {
        guard !saving, let input else { return }
        saving = true
        let stop = await diary.restoreAIResult(account: input.account, resultID: input.resultID)
        saved = diary.aiResultPreview == nil && diary.aiResultStatus?.hasPrefix("已保存到") == true
        checkingOutcome = stop && !saved
        saving = false
        if !stop { schedulePreparation() }
    }
}
