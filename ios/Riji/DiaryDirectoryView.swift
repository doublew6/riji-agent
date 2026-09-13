import SwiftUI
import RijiCore

struct DiaryDirectoryView: View {
    @ObservedObject var model: DiaryViewModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Button("打开今天") { open(model.today) }.accessibilityIdentifier("directoryToday")
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
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
            .task { await model.loadDirectory() }
        }
    }

    private func open(_ day: JournalDay) {
        Task { if await model.navigate(to: day) { dismiss() } }
    }
}
