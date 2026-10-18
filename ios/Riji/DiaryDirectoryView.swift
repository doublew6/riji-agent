import SwiftUI
import UniformTypeIdentifiers
import RijiCore

struct DiaryDirectoryView: View {
    @ObservedObject var model: DiaryViewModel
    @Environment(\.dismiss) private var dismiss
    @StateObject private var browser: VaultBrowserModel
    @State private var showFolderPicker = false

    init(model: DiaryViewModel) {
        self.model = model
        _browser = StateObject(wrappedValue: VaultBrowserModel(store: model.vaultStore))
    }

    var body: some View {
        NavigationStack {
            RijiList {
                if let workflows = model.workflows {
                    NavigationLink { JournalSearchView(model: workflows, diary: model) } label: {
                        RijiRow(title: model.isOriginalSource ? "搜索原目录已下载文件" : "搜索 App 本地日记",
                                detail: "范围：" + model.sourceLabel + "中的日记、周记与月记；不含其他已浏览文件夹", symbol: "magnifyingglass")
                    }.accessibilityIdentifier("searchFlow")
                }
                Button("打开今天") { open(model.today) }.accessibilityIdentifier("directoryToday")
                if let root = browser.rootName { fileTree(root) }
                else {
                    Button("打开本地文件夹", systemImage: "folder.badge.plus") { showFolderPicker = true }
                        .accessibilityIdentifier("chooseInitialVaultFolder")
                    Text("选择已有日记文件夹，按原目录展开和阅读。").font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                }
                if browser.rootName != nil, !model.isOriginalSource {
                    Button("在首页打开原目录日记") { Task { await model.useOriginalDiaries(); if model.isOriginalSource { dismiss() } } }
                }
                if model.isOriginalSource {
                    Button("打开原有 App 本地日记") { Task { await model.useLocalDiaries(); if !model.isOriginalSource { dismiss() } } }
                }
                if browser.isBusy { ProgressView("正在打开文件夹…").accessibilityIdentifier("vaultLoading") }
                if let error = browser.error ?? model.errorMessage { Text(error).font(.caption).foregroundStyle(Color(uiColor: RijiPalette.error)).accessibilityIdentifier("vaultError") }
                if model.isOriginalSource { applicationRows }
                else if browser.rootName == nil { applicationRows }
                else {
                    NavigationLink("App 日记与工具") {
                        RijiList { applicationRows }.navigationTitle("日记工具")
                            .task { await model.loadDirectory() }
                    }.accessibilityIdentifier("appDiaryTools")
                }
            }
            .listStyle(.plain)
            .task { await model.loadDirectory() }
            .navigationTitle("日记目录").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItemGroup(placement: .topBarLeading) {
                    Button { showFolderPicker = true } label: { Image(systemName: "folder.badge.plus") }
                        .accessibilityLabel("打开文件夹").accessibilityIdentifier("openVaultFolder").disabled(browser.isBusy || model.isBusy)
                    Button { Task { await browser.reload() } } label: { Image(systemName: "arrow.clockwise") }
                        .accessibilityLabel("刷新文件树").accessibilityIdentifier("refreshVaultFolder").disabled(browser.isBusy || model.isBusy)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { Task { if await model.prepareDirectory() { dismiss() } } }.disabled(browser.isBusy || model.isBusy)
                }
            }
            .interactiveDismissDisabled(browser.isBusy)
            .fileImporter(isPresented: $showFolderPicker, allowedContentTypes: [.folder], allowsMultipleSelection: false) { result in
                switch result {
                case .success(let urls): if let url = urls.first {
                    Task {
                        if model.isOriginalSource || model.needsFolder {
                            await model.selectOriginalFolder(url); await browser.reload(); await model.loadDirectory()
                        } else { await browser.selectFolder(url) }
                    }
                }
                case .failure(let error): if (error as NSError).code != NSUserCancelledError { browser.pickerFailed() }
                }
            }
            .sheet(isPresented: Binding(get: { browser.preview != nil }, set: { if !$0 { browser.closePreview() } })) {
                if let contents = browser.preview { VaultFileView(contents: contents, close: browser.closePreview) }
            }
        }
        .task { await browser.start() }
        .onDisappear { browser.directoryClosed() }
    }

    @ViewBuilder private func fileTree(_ root: String) -> some View {
        Button { browser.toggleRoot() } label: {
            Label(root, systemImage: browser.rootExpanded ? "chevron.down" : "chevron.right").font(.headline).foregroundStyle(Color(uiColor: RijiPalette.text))
        }.listRowBackground(Color(uiColor: RijiPalette.selectedSurface)).listRowSeparator(.hidden).accessibilityIdentifier("vaultRoot")
        ForEach(browser.rows) { row in
            VStack(alignment: .leading, spacing: 6) {
                Button {
                    Task {
                        if row.entry.kind == .folder { await browser.toggle(row.entry) }
                        else if let day = await model.originalDay(for: row.entry) { open(day) }
                        else { await browser.open(row.entry) }
                    }
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: icon(row.entry)).foregroundStyle(Color(uiColor: RijiPalette.secondary)).frame(width: 18)
                        Text(row.entry.name).foregroundStyle(Color(uiColor: RijiPalette.text)).lineLimit(1).truncationMode(.middle)
                        Spacer(minLength: 0)
                        if browser.loading.contains(row.id) || browser.openingFile == row.id { ProgressView().controlSize(.small) }
                    }.contentShape(Rectangle())
                }.buttonStyle(.plain).disabled(browser.isBusy || row.entry.kind == .unavailable)
                    .accessibilityIdentifier("vault-\(row.entry.kind.rawValue)-\(row.id)")
                    .accessibilityValue(row.entry.kind == .folder ? (browser.expanded.contains(row.id) ? "已展开" : "已折叠") : "")
                if let error = browser.folderErrors[row.id], browser.expanded.contains(row.id) {
                    Text(error).font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                    Button("重试") { Task { await browser.load(row.entry) } }.font(.caption)
                } else if browser.expanded.contains(row.id), browser.children[row.id]?.isEmpty == true, !browser.loading.contains(row.id) {
                    Text("空文件夹").font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary)).padding(.leading, 26)
                }
            }.padding(.leading, CGFloat(row.depth + 1) * 16).listRowSeparator(.hidden)
        }
        if browser.rootExpanded && browser.children[""]?.isEmpty == true { Text("空文件夹").foregroundStyle(Color(uiColor: RijiPalette.secondary)) }
    }

    private func icon(_ entry: VaultEntry) -> String {
        if entry.kind == .folder { return browser.expanded.contains(entry.id) ? "chevron.down" : "chevron.right" }
        return entry.kind == .unavailable ? "link" : "doc.text"
    }

    @ViewBuilder private var applicationRows: some View {
        if let workflows = model.workflows {
            Section("日常工作流") {
                NavigationLink("日 / 周 / 月模板") { TemplatesView(model: workflows) }.accessibilityIdentifier("templatesFlow")
                NavigationLink("保留空白日记") { BlankDayView(model: workflows, diary: model) }.accessibilityIdentifier("blankDayFlow")
                NavigationLink("北极星与今天的一件事") { DirectionAndTodayView(model: workflows, diary: model) }.accessibilityIdentifier("goalsFlow")
                NavigationLink("最重要的一件事") { MITView(model: workflows, diary: model) }.accessibilityIdentifier("mitFlow")
                NavigationLink("周记与月记") { ReviewsView(model: workflows, diary: model) }.accessibilityIdentifier("reviewsFlow")
            }
        }
        if model.directory.isEmpty {
            Text("保存后的日记和未完成草稿会出现在这里。").foregroundStyle(Color(uiColor: RijiPalette.secondary))
        }
        ForEach(model.directory) { entry in
            Button { open(entry.day) } label: {
                HStack {
                    Text(entry.day.value).monospacedDigit()
                        .fontWeight(entry.day == model.selectedDay ? .semibold : .regular)
                        .foregroundStyle(Color(uiColor: RijiPalette.text))
                    Spacer()
                    Text(entry.hasDraft ? "有草稿" : "已保存").font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                }
            }.listRowBackground(Color(uiColor: entry.day == model.selectedDay ? RijiPalette.selectedSurface : RijiPalette.paper))
                .accessibilityAddTraits(entry.day == model.selectedDay ? .isSelected : [])
                .accessibilityIdentifier("directory-\(entry.day.value)")
        }
    }

    private func open(_ day: JournalDay) {
        Task { if await model.navigate(to: day) { dismiss() } }
    }
}
