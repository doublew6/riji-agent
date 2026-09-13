import SwiftUI
import RijiCore

struct DiaryDirectoryView: View {
    @ObservedObject var model: DiaryViewModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Button("打开今天") { open(model.today) }.accessibilityIdentifier("directoryToday")
                if let workflows = model.workflows {
                    Section("日常工作流") {
                        NavigationLink("日 / 周 / 月模板") { TemplatesView(model: workflows) }.accessibilityIdentifier("templatesFlow")
                        NavigationLink("保留空白日记") { BlankDayView(model: workflows, diary: model) }.accessibilityIdentifier("blankDayFlow")
                        NavigationLink("搜索本地文件") { JournalSearchView(model: workflows, diary: model) }.accessibilityIdentifier("searchFlow")
                        NavigationLink("北极星指标") { NorthStarsView(model: workflows, day: model.selectedDay) }.accessibilityIdentifier("goalsFlow")
                        NavigationLink("最重要的一件事") { MITView(model: workflows, diary: model) }.accessibilityIdentifier("mitFlow")
                        NavigationLink("周报 / 月报") { ReviewsView(model: workflows, diary: model) }.accessibilityIdentifier("reviewsFlow")
                    }
                }
                if model.directory.isEmpty {
                    Text("保存后的日记和未完成草稿会出现在这里。")
                        .foregroundStyle(.secondary)
                }
                ForEach(model.directory) { entry in
                    Button { open(entry.day) } label: {
                        HStack {
                            Text(entry.day.value).foregroundStyle(.primary)
                            Spacer()
                            Text(entry.hasDraft ? "有草稿" : "已保存").font(.caption).foregroundStyle(.secondary)
                        }
                    }.accessibilityIdentifier("directory-\(entry.day.value)")
                }
            }
            .navigationTitle("日记目录")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { Task { if await model.navigate(to: model.selectedDay) { dismiss() } } } } }
            .task { await model.loadDirectory() }
        }
    }

    private func open(_ day: JournalDay) {
        Task { if await model.navigate(to: day) { dismiss() } }
    }
}
