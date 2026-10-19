import SwiftUI
import RijiCore

struct NorthStarAnchorBanner: View {
    @ObservedObject var diary: DiaryViewModel
    @ObservedObject var mobile: MobileClientModel
    @ObservedObject private var anchor: NorthStarAnchorModel
    @State private var showing = false
    let compact: Bool
    let iconOnly: Bool
    init(diary: DiaryViewModel, mobile: MobileClientModel, compact: Bool = false, iconOnly: Bool = false) {
        self.diary = diary; self.mobile = mobile; self.anchor = diary.northStar
        self.compact = compact; self.iconOnly = iconOnly
    }
    private var verified: NorthStarDisplay? {
        guard !diary.sourceTransitionInProgress, !diary.needsFolder,
              anchor.scope == NorthStarAnchor.scope(source: diary.sourceIdentity, account: mobile.credential?.scope ?? "unconnected") else { return nil }
        return anchor.current
    }
    var body: some View {
        Button { showing = true } label: {
            if iconOnly {
                Image(systemName: "sparkle").resizable().scaledToFit().frame(width: 24, height: 24)
                    .foregroundStyle(MentorPalette.secondary).frame(width: 44, height: 44)
                    .contentShape(Rectangle())
                    .accessibilityLabel("北极星 · " + (verified?.title ?? "选择本阶段方向"))
            } else if compact {
                HStack(spacing: 8) {
                    Image(systemName: "sparkle")
                    if let current = verified {
                        Text(current.title).lineLimit(1).accessibilityIdentifier("northStarCurrentGoal")
                    } else {
                        Text("北极星 · 选择本阶段方向").lineLimit(1)
                    }
                }.font(.caption).foregroundStyle(MentorPalette.secondary).frame(maxWidth: .infinity, minHeight: 44)
            } else {
            VStack(spacing: 8) {
                MentorNorthStar()
                Text("北极星 · 本阶段").font(.caption).foregroundStyle(MentorPalette.secondary)
                if let current = verified {
                    Text(current.title).font(.headline).multilineTextAlignment(.center)
                        .lineLimit(3).accessibilityIdentifier("northStarCurrentGoal")
                    Label("查看完整目标", systemImage: "chevron.right").font(.caption)
                } else {
                    Text(anchor.error == nil && anchor.sourceError == nil ? "选择本阶段方向 · 可选" : "来源待核验 · 查看恢复方式")
                        .font(.callout).multilineTextAlignment(.center)
                }
            }.frame(maxWidth: .infinity, minHeight: 44).padding(.vertical, 8)
            }
        }.buttonStyle(.plain).accessibilityIdentifier("northStarAnchor")
            .sheet(isPresented: $showing) {
                NavigationStack { NorthStarAnchorPicker(model: anchor, diary: diary) }
                    .environment(\.colorScheme, .dark).tint(MentorPalette.action)
            }
    }
}

// Keep source verification attached to the destination, not a lazily recycled banner.
private struct NorthStarContext: ViewModifier {
    @ObservedObject var diary: DiaryViewModel
    @ObservedObject var mobile: MobileClientModel
    @Environment(\.scenePhase) private var scenePhase
    private var context: String {
        diary.sourceIdentity + mobile.accountIdentity.uuidString + String(diary.sourceTransitionInProgress)
            + String(diary.needsFolder) + String(diary.workflows != nil) + String(describing: scenePhase)
    }
    func body(content: Content) -> some View {
        content.task(id: context) {
            let anchor = diary.northStar
            guard scenePhase == .active else { anchor.invalidate(); return }
            anchor.activate(diary.northStarReaders(account: mobile.credential?.scope ?? "unconnected"))
            while !Task.isCancelled {
                await anchor.refresh()
                do { try await Task.sleep(nanoseconds: 15_000_000_000) } catch { return }
            }
        }
    }
}

extension View {
    func northStarContext(diary: DiaryViewModel, mobile: MobileClientModel) -> some View {
        modifier(NorthStarContext(diary: diary, mobile: mobile))
    }
}

private struct NorthStarAnchorPicker: View {
    @ObservedObject var model: NorthStarAnchorModel
    @ObservedObject var diary: DiaryViewModel
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        List {
            Section {
                Text("选择你当前的中期或阶段方向。关联只留在本机，不会自动发送给导师，也不会修改原目标文件。")
                    .font(.callout)
            }
            if model.error != nil || model.sourceError != nil {
                Section {
                    if let error = model.error { Text(error).foregroundStyle(.orange).accessibilityIdentifier("northStarError") }
                    if let error = model.sourceError { Text(error).foregroundStyle(.orange).accessibilityIdentifier("northStarSourceError") }
                    Button("重新核验来源") { Task { await model.recover() } }.accessibilityIdentifier("northStarRetry")
                }
            }
            if let preview = model.preview { previewSection(preview) }
            else {
                if let current = model.current {
                    Section("当前已核验方向") {
                        Text(current.title).font(.headline)
                        if current.detail != current.title { Text(current.detail) }
                        Text(current.sourceLabel).font(.caption).foregroundStyle(.secondary)
                    }
                }
                sourceSection
            }
            if model.available && (model.hasAssociation || model.sourceError != nil) {
                Section {
                    Button("取消本机关联", role: .destructive) { Task { await model.unlink() } }
                        .accessibilityIdentifier("northStarUnlink")
                }
            }
        }.navigationTitle("本阶段北极星").navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("完成") { model.cancelPreview(); dismiss() }.disabled(model.busy) }
            .disabled(model.busy).overlay { if model.busy { ProgressView() } }
            .task(id: model.activationID) { await model.browseWhenReady() }
    }
    private func previewSection(_ preview: NorthStarPreview) -> some View {
        Section("核对关联") {
            Text(preview.display.title).font(.headline).textSelection(.enabled)
            if preview.display.detail != preview.display.title { Text(preview.display.detail) }
            Text(preview.display.sourceLabel).font(.caption).foregroundStyle(.secondary)
            Button("确认作为本阶段方向") { Task { if await model.confirm() { dismiss() } } }
                .accessibilityIdentifier("northStarConfirm")
            Button("返回选择") { model.cancelPreview() }.accessibilityIdentifier("northStarBackToSelection")
        }
    }
    @ViewBuilder private var sourceSection: some View {
        if !model.available {
            Section { Text("请先在日记或设置中连接可访问的日记目录。未设置方向也可以继续聊天。") }
        } else if model.original { originalSection }
        else {
            Section("选择已定义目标的当前版本") {
                if model.goals.isEmpty { Text("尚无本地目标定义。你可以在原有北极星编辑器中定义后再选择。") }
                ForEach(model.goals) { goal in
                    Button { Task { await model.prepare(goal) } } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(goal.name); Text(goal.period + " · " + goal.stage).font(.caption).foregroundStyle(.secondary)
                        }.padding(.vertical, 6)
                    }.accessibilityIdentifier("northStarGoal-" + goal.id.uuidString)
                }
                Button("重新读取目标版本") { Task { await model.browse() } }
                if let workflows = diary.workflows {
                    NavigationLink("打开北极星定义") { NorthStarsView(model: workflows, day: diary.selectedDay) }
                }
            }
        }
    }
    private var originalSection: some View {
        Section(model.selectedFile == nil ? "在已授权原目录中选文件" : "选择确切段落") {
            Text("可选择不超过 2 MiB 的文本文件。").font(.caption).foregroundStyle(.secondary)
            if let selectedFile = model.selectedFile {
                Text(selectedFile.joined(separator: "/")).font(.caption).foregroundStyle(.secondary)
                Text("按空行划分段落；只列出不超过 16 KB 的段落。确认前会再次核对整份文件版本。")
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(model.paragraphs) { paragraph in
                    Button { Task { await model.prepare(paragraph) } } label: { Text(paragraph.text).padding(.vertical, 8) }
                        .accessibilityIdentifier("northStarParagraph-" + String(paragraph.start))
                }
                Button("返回文件列表") { Task { await model.browse(model.path) } }
            } else {
                Button("重新读取此层目录") { Task { await model.browse(model.path) } }
                if !model.path.isEmpty {
                    Button("上一级") { Task { await model.browse(Array(model.path.dropLast())) } }
                }
                if model.entries.isEmpty { Text("此层没有可选文本；只读取你选择的文件，不会搜索其他目录。") }
                ForEach(model.entries) { entry in
                    Button { Task {
                        if entry.kind == .folder { await model.browse(entry.path) }
                        else { await model.open(entry) }
                    } } label: {
                        Label(entry.name, systemImage: entry.kind == .folder ? "folder" : "doc.text")
                            .frame(minHeight: 44)
                    }.accessibilityIdentifier("northStarFile-" + entry.id)
                }
            }
        }
    }
}
