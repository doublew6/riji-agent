import SwiftUI
import UIKit
import UniformTypeIdentifiers
import RijiCore

struct TodayDiaryView: View {
    @ObservedObject var model: DiaryViewModel
    @State private var showDirectory = false
    @State private var showDate = false
    @State private var dateSelection = Date()
    @State private var document: MarkdownDocument?
    @State private var showExporter = false
    @FocusState private var composing: Bool
    @State private var headingLayout = DiaryHeadingLayout()
    @State private var preservedPosition: PreservedDiaryPosition?
    @State private var scrollReference = DiaryScrollReference()

    var body: some View {
        let source = Data(model.markdown.utf8)
        return NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    Text(model.templateLabel).font(.caption).foregroundStyle(.secondary)
                        .accessibilityIdentifier("templateLabel")
                    ForEach(blocks) { block in
                        VStack(alignment: .leading, spacing: 12) {
                            Text(DiaryHeadingPresentation.styled(block.title)).font(block.level == 1 ? .largeTitle.weight(.semibold) : .headline)
                                .background(GeometryReader { geometry in
                                    Color.clear.preference(key: DiaryHeadingPositions.self,
                                        value: DiaryHeadingLayout(source: source,
                                            positions: [block.id: geometry.frame(in: .global).minY]))
                                })
                            if !block.body.isEmpty {
                                Text(block.body).font(.body).lineSpacing(6).textSelection(.enabled)
                                    .accessibilityIdentifier("diaryBody-\(block.id)")
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .id(block.id)
                    }
                }
                .background(DiaryScrollAccessor { scrollReference.view = $0 })
                .padding(.horizontal, 24).padding(.vertical, 24)
            }
            .onPreferenceChange(DiaryHeadingPositions.self) { layout in
                headingLayout = layout
                schedulePositionRestore()
            }
            .onChange(of: model.selectedDay) { _, _ in
                preservedPosition = nil
                if let scroll = scrollReference.view {
                    scroll.setContentOffset(CGPoint(x: 0, y: -scroll.adjustedContentInset.top), animated: false)
                }
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Color(uiColor: .systemBackground))
            .safeAreaInset(edge: .bottom, spacing: 0) { composer }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { navigationItems }
            .sheet(isPresented: $showDirectory) { DiaryDirectoryView(model: model) }
            .sheet(isPresented: $showDate) { datePicker }
            .fileExporter(isPresented: $showExporter, document: document,
                          contentType: MarkdownDocument.readableContentTypes[0],
                          defaultFilename: document?.filename ?? "Diary.md") { result in
                if let document { model.exportCompleted(result, document: document) }
            }
        }
    }

    private func preserveReadingPosition() {
        guard let scroll = scrollReference.view else { return }
        let bounds = scroll.convert(scroll.bounds, to: nil)
        let top = bounds.minY + scroll.adjustedContentInset.top
        let bottom = bounds.maxY - scroll.adjustedContentInset.bottom
        let sorted = headingLayout.positions.sorted { $0.value < $1.value }
        let candidate = sorted.first { $0.value >= top && $0.value <= bottom } ?? sorted.last { $0.value < top }
        guard let candidate else { return }
        preservedPosition = PreservedDiaryPosition(day: model.selectedDay, headingID: candidate.key,
                                                   y: candidate.value, source: Data(model.markdown.utf8))
    }

    private func schedulePositionRestore() {
        guard let saved = preservedPosition, saved.day == model.selectedDay,
              saved.source != Data(model.markdown.utf8),
              headingLayout.source == Data(model.markdown.utf8) else { return }
        DispatchQueue.main.async {
            guard saved.day == model.selectedDay, preservedPosition?.headingID == saved.headingID,
                  headingLayout.source == Data(model.markdown.utf8),
                  let scroll = scrollReference.view, let current = headingLayout.positions[saved.headingID] else { return }
            scroll.layoutIfNeeded()
            let proposed = scroll.contentOffset.y + current - saved.y
            let minimum = -scroll.adjustedContentInset.top
            let maximum = max(minimum, scroll.contentSize.height - scroll.bounds.height + scroll.adjustedContentInset.bottom)
            scroll.setContentOffset(CGPoint(x: scroll.contentOffset.x, y: min(maximum, max(minimum, proposed))), animated: false)
            preservedPosition = nil
        }
    }

    private var exportTestValue: String {
        #if DEBUG
        if let value = ProcessInfo.processInfo.environment["RIJI_TEST_CONTAINER"], UUID(uuidString: value) != nil {
            return model.exportVerificationDigest
        }
        #endif
        return ""
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 8) {
            if model.selectedDay != model.today {
                HStack {
                    Text("正在记录 \(model.selectedDay.value)").font(.caption)
                    Spacer()
                    Button("回到今天") { Task { _ = await model.navigate(to: model.today) } }
                        .font(.caption).accessibilityIdentifier("returnToday")
                }
            }
            HStack {
                Text("写入区块").font(.caption).foregroundStyle(.secondary)
                Picker("写入区块", selection: Binding(get: { model.section }, set: model.updateSection)) {
                    if model.requiresSectionSelection { Text("请选择写入区块").tag(DiaryViewModel.unselectedSection) }
                    ForEach(Array(Set(model.sections + [model.section])).filter { $0 != DiaryViewModel.unselectedSection }.sorted(), id: \.self) { Text($0.isEmpty ? "空标题" : $0).tag($0) }
                }
                .pickerStyle(.menu).labelsHidden().accessibilityIdentifier("sectionPicker")
                .disabled(model.isBusy || model.isPending)
                Spacer()
            }
            if model.requiresSectionSelection { Text("有多个 Notes 标题，请明确选择写入区块。").font(.caption).foregroundStyle(.secondary) }
            TextField("写一点…", text: Binding(get: { model.text }, set: model.updateText), axis: .vertical)
                .lineLimit(2...5).focused($composing).autocorrectionDisabled().padding(12)
                .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
                .accessibilityIdentifier("diaryInput").disabled(model.isBusy || model.isPending)
            if let error = model.errorMessage {
                Text(error).font(.caption).foregroundStyle(.red).accessibilityIdentifier("storageError")
                if !model.isReady {
                    Button("重试存储") { Task { await model.start() } }.font(.caption)
                } else if !model.isPending && !model.text.isEmpty {
                    Button("核对当前正文") { Task { await model.reviewCurrentSource() } }.font(.caption)
                }
            }
            HStack {
                Text(model.status).font(.caption).foregroundStyle(.secondary).accessibilityIdentifier("saveStatus")
                    .accessibilityValue(exportTestValue)
                Spacer(minLength: 8)
                Button(model.isPending ? "重试保存" : "保存这段") {
                    preserveReadingPosition()
                    composing = false
                    Task {
                        await model.save()
                        if model.errorMessage != nil { preservedPosition = nil }
                        else { schedulePositionRestore() }
                    }
                }
                .buttonStyle(.borderedProminent).disabled(!model.canSave).accessibilityIdentifier("saveEntry")
            }
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
        .background(.regularMaterial)
    }

    @ToolbarContentBuilder private var navigationItems: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            Button { showDirectory = true } label: { Image(systemName: "books.vertical") }
                .accessibilityLabel("日记目录").accessibilityIdentifier("diaryDirectory")
        }
        ToolbarItem(placement: .principal) {
            Button {
                dateSelection = model.selectedDay.date(in: model.timeZone)
                showDate = true
            } label: {
                HStack(spacing: 5) { Text(model.selectedDay.value); Image(systemName: "chevron.down").font(.caption2) }
            }
            .foregroundStyle(.primary).accessibilityIdentifier("selectedDate")
        }
        ToolbarItem(placement: .topBarTrailing) {
            Menu {
                Button("导出 Markdown", systemImage: "square.and.arrow.up") {
                    let day = model.selectedDay
                    Task {
                        if let captured = await model.export(day: day) { document = captured; showExporter = true }
                    }
                }.disabled(!model.isSaved).accessibilityIdentifier("exportMarkdown")
            } label: { Image(systemName: "ellipsis") }
            .accessibilityLabel("更多").accessibilityIdentifier("moreMenu")
        }
        ToolbarItemGroup(placement: .keyboard) {
            Spacer()
            Button("收起键盘") { composing = false }
        }
    }

    private var datePicker: some View {
        NavigationStack {
            DatePicker("选择日记日期", selection: $dateSelection, displayedComponents: .date)
                .datePickerStyle(.graphical).environment(\.timeZone, model.timeZone)
                .padding().navigationTitle("选择日期").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("取消") { showDate = false } }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("打开") {
                            Task {
                                let day = JournalDay(date: dateSelection, timeZone: model.timeZone)
                                if await model.navigate(to: day) { showDate = false }
                            }
                        }.disabled(model.isBusy)
                    }
                }
        }.presentationDetents([.medium, .large])
    }

    private var blocks: [DiaryBlock] {
        let data = Data(model.markdown.utf8)
        guard let headings = try? MarkdownSectionAppender.headings(in: data), !headings.isEmpty else {
            return [DiaryBlock(id: "body", title: model.selectedDay.value, level: 1, body: WorkflowSamples.visible(model.markdown))]
        }
        var occurrences: [String: Int] = [:]
        return headings.enumerated().map { index, heading in
            let occurrence = occurrences[heading.title, default: 0]
            occurrences[heading.title] = occurrence + 1
            let end = index + 1 < headings.count ? headings[index + 1].start : data.count
            let body = String(decoding: data[heading.end..<end], as: UTF8.self)
                .trimmingCharacters(in: .newlines)
            return DiaryBlock(id: "\(heading.level)-\(heading.title)-\(occurrence)", title: heading.title, level: heading.level, body: WorkflowSamples.visible(body))
        }
    }
}

private struct DiaryBlock: Identifiable {
    let id: String
    let title: String
    let level: Int
    let body: String
}

private struct DiaryHeadingLayout: Equatable {
    var source: Data?
    var positions: [String: CGFloat] = [:]
}

private struct DiaryHeadingPositions: PreferenceKey {
    static var defaultValue = DiaryHeadingLayout()
    static func reduce(value: inout DiaryHeadingLayout, nextValue: () -> DiaryHeadingLayout) {
        let next = nextValue()
        if value.source == next.source {
            value.positions.merge(next.positions, uniquingKeysWith: { _, new in new })
        } else if next.source != nil {
            value = next
        }
    }
}

private struct PreservedDiaryPosition {
    let day: JournalDay
    let headingID: String
    let y: CGFloat
    let source: Data
}

private final class DiaryScrollReference {
    weak var view: UIScrollView?
}

private struct DiaryScrollAccessor: UIViewRepresentable {
    let resolve: (UIScrollView) -> Void
    func makeUIView(context: Context) -> Probe { Probe(resolve: resolve) }
    func updateUIView(_ uiView: Probe, context: Context) { uiView.findScrollView() }

    final class Probe: UIView {
        let resolve: (UIScrollView) -> Void
        init(resolve: @escaping (UIScrollView) -> Void) {
            self.resolve = resolve
            super.init(frame: .zero)
            isUserInteractionEnabled = false
        }
        required init?(coder: NSCoder) { return nil }
        override func didMoveToWindow() { super.didMoveToWindow(); findScrollView() }
        func findScrollView() {
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                var ancestor = self.superview
                while let view = ancestor {
                    if let scroll = view as? UIScrollView { self.resolve(scroll); return }
                    ancestor = view.superview
                }
            }
        }
    }
}
