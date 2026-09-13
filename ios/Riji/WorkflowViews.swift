import SwiftUI
import UniformTypeIdentifiers
import RijiCore

struct WorkflowNotice: View {
    @ObservedObject var model: WorkflowViewModel
    var body: some View {
        if let error = model.error { Text(error).foregroundStyle(.red).accessibilityIdentifier("workflowError") }
        if !model.status.isEmpty { Text(model.status).font(.caption).foregroundStyle(.secondary).accessibilityIdentifier("workflowStatus") }
    }
}

struct TemplatesView: View {
    @ObservedObject var model: WorkflowViewModel
    @State private var kind = TemplateKind.daily
    @State private var pasted = ""
    @State private var showImporter = false
    @State private var showSampleExporter = false
    @State private var selection: TemplateVersion?
    @State private var sampleDocument = WorkflowMarkdownDocument(data: Data(WorkflowSamples.template.utf8))
    var body: some View {
        Form {
            Section {
                Picker("模板类型", selection: $kind) { ForEach(TemplateKind.allCases) { Text($0.label).tag($0) } }.pickerStyle(.segmented)
                ForEach(model.templateVersions) { version in
                    Button { selection = version } label: {
                        HStack { Text(version.label); Spacer(); if model.selectedTemplate.id == version.id { Image(systemName: "checkmark") } }
                    }.accessibilityIdentifier("template-\(version.isInitial ? "initial" : "imported")")
                }
            } header: { Text("已选择：\(model.selectedTemplate.label)") }
            Section("日期偏好") {
                Picker("星期语言", selection: Binding(get: { model.weekdayLanguage }, set: { language in
                    Task { await model.chooseWeekdayLanguage(language) }
                })) {
                    ForEach(WeekdayLanguage.allCases) { Text($0.label).tag($0) }
                }.accessibilityIdentifier("weekdayLanguage")
                Text("默认中文。只用于新日期和新准备的复盘；已保存内容与待确认草稿保持原文。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("导入 Markdown") {
                Button("从文件选择模板") { showImporter = true }.accessibilityIdentifier("importTemplateFile")
                Text("先查看原文，再确认复制。支持 {{date}}、{{title}}、{{date:YYYY-MM-DD}} 和 {{date:dddd}}；其他宏保留为原文。")
                    .font(.caption).foregroundStyle(.secondary)
                TextEditor(text: $pasted).frame(minHeight: 130).accessibilityIdentifier("templateSourceInput")
                Button("预览粘贴的模板") { model.previewImport(Data(pasted.utf8), kind: kind) }
                    .disabled(pasted.isEmpty).accessibilityIdentifier("previewTemplate")
                if WorkflowSamples.enabled {
                    Button("导出合成示例到文件") { showSampleExporter = true }.accessibilityIdentifier("exportSampleTemplate")
                    Button("预览合成示例") { model.previewImport(Data(WorkflowSamples.template.utf8), kind: kind) }
                        .accessibilityIdentifier("previewSampleTemplate")
                    Text("合成验收示例，不是个人模板。先导出到系统“文件”，再用上方入口导入。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            WorkflowNotice(model: model)
        }
        .navigationTitle("日 / 周 / 月模板")
        .disabled(model.busy)
        .task { await model.loadTemplates(kind) }
        .onChange(of: kind) { _, value in Task { await model.loadTemplates(value) } }
        .fileImporter(isPresented: $showImporter, allowedContentTypes: [.plainText, UTType(filenameExtension: "md") ?? .plainText]) { result in
            do {
                let url = try result.get(); let access = url.startAccessingSecurityScopedResource()
                defer { if access { url.stopAccessingSecurityScopedResource() } }
                let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
                guard size <= 262_144 else { throw JournalError.invalidTemplate }
                model.previewImport(try Data(contentsOf: url, options: .mappedIfSafe), kind: kind)
            } catch { model.error = (error as? JournalError ?? .storage).localizedDescription }
        }
        .fileExporter(isPresented: $showSampleExporter, document: sampleDocument, contentType: .plainText,
                      defaultFilename: "Riji-Synthetic-Template.md") { result in
            if case .failure = result { model.error = "示例导出未完成，可重新选择文件位置。" }
        }
        .sheet(item: $model.importPreview) { version in
            NavigationStack {
                ScrollView { VStack(alignment: .leading, spacing: 16) {
                    Text("原始模板 · \(version.raw.count) 字节").font(.headline)
                    ForEach(version.warnings, id: \.self) { Text($0).foregroundStyle(.orange) }
                    Text(version.source).font(.system(.body, design: .monospaced)).textSelection(.enabled)
                    WorkflowNotice(model: model)
                }.padding() }
                .navigationTitle("核对模板原文")
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("取消") { model.importPreview = nil } }
                    ToolbarItem(placement: .confirmationAction) { Button("确认导入") { Task { await model.confirmImport() } }.accessibilityIdentifier("confirmTemplateImport") }
                }.disabled(model.busy)
            }
        }
        .sheet(item: $selection) { version in
            NavigationStack {
                ScrollView { VStack(alignment: .leading, spacing: 16) {
                    Text(version.source).font(.system(.body, design: .monospaced))
                    ForEach(version.warnings, id: \.self) { Text($0).foregroundStyle(.orange) }
                    WorkflowNotice(model: model)
                }.padding() }
                .navigationTitle("模板版本")
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("关闭") { selection = nil } }
                    ToolbarItem(placement: .confirmationAction) { Button("使用此模板") { Task { await model.chooseTemplate(version); if model.error == nil { selection = nil } } }.accessibilityIdentifier("selectTemplate") }
                }.disabled(model.busy)
            }
        }
    }
}

struct BlankDayView: View {
    @ObservedObject var model: WorkflowViewModel
    @ObservedObject var diary: DiaryViewModel
    @State private var date: Date
    init(model: WorkflowViewModel, diary: DiaryViewModel) {
        self.model = model; self.diary = diary; _date = State(initialValue: diary.selectedDay.date(in: diary.timeZone))
    }
    var body: some View {
        Form {
            DatePicker("日记日期", selection: $date, displayedComponents: .date).environment(\.timeZone, diary.timeZone)
            Text("按选定模板保留空白日记；已有日记会保持原文。")
            Button("预览空白日记") { Task { await model.prepareBlank(JournalDay(date: date, timeZone: diary.timeZone)) } }.accessibilityIdentifier("previewBlankDay")
            if let blank = model.blank {
                Section("\(blank.day.value) · 已固定的日记预览") {
                    Text("这是已固定的预览；仍显示的变量会按原文保存，不再替换。")
                        .font(.caption).foregroundStyle(.secondary).accessibilityIdentifier("fixedPreviewNotice")
                    Text(blank.preview).accessibilityIdentifier("blankDayPreview").font(.system(.body, design: .monospaced))
                    ForEach(TemplateVersion.warnings(in: blank.preview), id: \.self) { Text($0).foregroundStyle(.orange) }
                    Button("确认保留空白日记") { Task { if await model.confirmBlank() { _ = await diary.navigate(to: blank.day) } } }.accessibilityIdentifier("confirmBlankDay")
                }
            }
            WorkflowNotice(model: model)
        }.navigationTitle("保留空白日记").disabled(model.busy)
    }
}

struct JournalSearchView: View {
    @ObservedObject var model: WorkflowViewModel
    @ObservedObject var diary: DiaryViewModel
    @State private var query = ""
    var body: some View {
        List {
            TextField("文字或日期", text: $query).accessibilityIdentifier("searchInput")
                .onSubmit { Task { await model.search(query) } }
            Button("搜索本地文件") { Task { await model.search(query) } }.accessibilityIdentifier("searchSaved")
            Text(model.searchSummary).font(.caption).foregroundStyle(.secondary)
            ForEach(model.matches) { match in
                NavigationLink {
                    SavedWorkflowFileView(model: model, diary: diary, file: match.file)
                } label: { VStack(alignment: .leading) { Text("\(match.file.label) · \(match.file.title)"); Text(match.snippet).font(.caption).lineLimit(3).foregroundStyle(.secondary) } }
                .accessibilityIdentifier("search-\(match.id)")
            }
            WorkflowNotice(model: model)
        }.navigationTitle("搜索").disabled(model.busy)
    }
}

struct SavedWorkflowFileView: View {
    @ObservedObject var model: WorkflowViewModel
    @ObservedObject var diary: DiaryViewModel
    let file: SavedFile
    @State private var text = ""
    @State private var exportDocument: WorkflowMarkdownDocument?
    @State private var showExport = false
    var body: some View {
        ScrollView { VStack(alignment: .leading, spacing: 20) {
            Text(WorkflowSamples.visible(text)).textSelection(.enabled)
            if case .daily(let day) = file {
                Button("切换日记到此日期") { Task { _ = await diary.navigate(to: day); model.status = "关闭目录即可继续记录这一天。" } }
            }
            Button("导出 Markdown") { exportDocument = WorkflowMarkdownDocument(data: Data(text.utf8)); showExport = true }
            WorkflowNotice(model: model)
        }.padding() }.navigationTitle(file.label)
            .task { text = await model.savedText(file) }
            .fileExporter(isPresented: $showExport, document: exportDocument, contentType: .plainText, defaultFilename: file.id + ".md") { result in
                if case .failure = result { model.error = "导出未完成，原文件仍保留。" }
            }
    }
}

struct NorthStarsView: View {
    @ObservedObject var model: WorkflowViewModel
    let day: JournalDay
    var body: some View {
        Form {
            Section("已确认指标（当前测量缺失时不计为 0）") {
                if model.goals.isEmpty { Text("尚未录入北极星，可手动填写；不会自动定义目标。") }
                ForEach(model.goals) { goal in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(goal.name).font(.headline)
                        Text("\(goal.definition) · \(goal.unit)\n目标：\(goal.target) · \(goal.period)\n阶段：\(goal.stage)\n当前测量：\(goal.measurement ?? "尚未记录")")
                        Button("修改为新版本") { model.editGoal(goal) }
                    }
                }
                Button("新增另一项指标") { model.editGoal(nil) }.accessibilityIdentifier("newNorthStar")
            }
            Section("本人定义") {
                goalField("指标名称", "name"); goalField("指标口径", "definition"); goalField("单位", "unit")
                goalField("确认目标", "target"); goalField("目标周期", "period"); goalField("当前阶段", "stage")
                goalField("当前测量（可留空）", "measurement"); goalField("测量来源与截止时间", "measurementSource")
                Button("预览北极星版本") { Task { await model.previewGoal(day: day) } }.accessibilityIdentifier("previewNorthStar")
            }
            DisclosureGroup("历史版本（\(model.goalHistory.count)）") {
                ForEach(model.goalHistory) { goal in Text("\(goal.day.value) · \(goal.name)\n目标 \(goal.target) \(goal.unit) · \(goal.id.uuidString)") }
            }
            WorkflowNotice(model: model)
        }.navigationTitle("北极星指标").disabled(model.busy)
            .task { await model.loadGoals() }
            .sheet(item: $model.goalPreview) { goal in
                NavigationStack {
                    Form {
                        Text("\(goal.name)\n\(goal.definition)\n单位：\(goal.unit)\n目标：\(goal.target)\n周期：\(goal.period)\n阶段：\(goal.stage)\n测量：\(goal.measurement ?? "尚未记录")\n来源及截止：\(goal.measurementSource ?? "尚未记录")")
                        Text("确认后保存不可变的新版本，历史 MIT 的引用保持原版本。")
                        Button("确认北极星版本") { Task { await model.confirmGoal() } }.accessibilityIdentifier("confirmNorthStar")
                        WorkflowNotice(model: model)
                    }.navigationTitle("核对北极星").toolbar { Button("关闭") { model.goalPreview = nil } }.disabled(model.busy)
                }
            }
    }
    private func goalField(_ title: String, _ key: String) -> some View {
        TextField(title, text: model.field(key), axis: .vertical).accessibilityIdentifier("goal-\(key)")
    }
}

struct MITView: View {
    @ObservedObject var model: WorkflowViewModel
    @ObservedObject var diary: DiaryViewModel
    @State private var feedback = false
    var body: some View {
        Form {
            Section("\(diary.selectedDay.value) · 当前 MIT") {
                if let mit = model.mit {
                    Text(mit.action).font(.headline).accessibilityIdentifier("currentMIT")
                    Text("完成标准：\(mit.criterion)\n确认计划不代表已完成。")
                } else { Text("尚未确认 MIT，可先填写一个手动计划。") }
            }
            Section(feedback ? "实际反馈" : "手动计划") {
                if model.mit != nil {
                    Toggle("记录实际反馈", isOn: $feedback).accessibilityIdentifier("mitFeedbackMode")
                        .onChange(of: feedback) { _, value in model.updateField("mode", value ? "feedback" : "plan") }
                }
                if feedback {
                    Picker("完成情况", selection: model.field("feedbackStatus")) {
                        Text("请选择").tag("")
                        ForEach(MITStatus.allCases, id: \.self) { Text($0.rawValue).tag($0.rawValue) }
                    }.accessibilityIdentifier("mitFeedbackStatus")
                    TextField("实际结果与原因", text: model.field("result"), axis: .vertical).accessibilityIdentifier("mit-result")
                } else {
                    Picker("关联北极星", selection: model.field("goal")) {
                        Text("未关联 · 手动计划").tag("")
                        if model.requiresGoalReselection { Text("原版本已失效，请重新选择").tag(model.fields["goal"] ?? "") }
                        ForEach(model.goals) { Text($0.name).tag($0.id.uuidString) }
                    }
                    TextField("具体行动", text: model.field("action"), axis: .vertical).accessibilityIdentifier("mit-action")
                    TextField("可验证完成标准", text: model.field("criterion"), axis: .vertical).accessibilityIdentifier("mit-criterion")
                    TextField("为何推动目标", text: model.field("rationale"), axis: .vertical)
                    TextField("建议投入（可留空）", text: model.field("effort"))
                }
                Picker("写入区块", selection: model.field("section")) {
                    Text("请选择现有区块").tag("")
                    ForEach(model.mitSections, id: \.self) { Text($0).tag($0) }
                }.pickerStyle(.menu).accessibilityIdentifier("mitSection")
                Text("有 MIT 或反馈专属标题时请选择该区块；自定义模板请核对实际位置。")
                    .font(.caption).foregroundStyle(.secondary)
                Button("预览 MIT 记录") {
                    Task { await model.previewMIT(day: diary.selectedDay, section: model.fields["section"] ?? "", feedback: feedback) }
                }.accessibilityIdentifier("previewMIT")
            }
            DisclosureGroup("计划与反馈历史（\(model.mitHistory.count)）") {
                ForEach(model.mitHistory) { item in Text(item.feedbackFor == nil ? "计划：\(item.action)" : "\(item.status?.rawValue ?? "")：\(item.result)") }
            }
            WorkflowNotice(model: model)
        }.navigationTitle("最重要的一件事").disabled(model.busy)
            .task { await diary.flushDraft(); await model.loadMIT(diary.selectedDay); feedback = model.fields["mode"] == "feedback" }
            .sheet(isPresented: Binding(get: { model.mitPreview != nil }, set: { if !$0 { model.mitPreview = nil } })) {
                if let draft = model.mitPreview {
                    NavigationStack {
                        Form {
                            Text("\(draft.event.day.value) → \(draft.section)")
                            Text(draft.event.feedbackFor == nil ? "行动：\(draft.event.action)\n完成标准：\(draft.event.criterion)\n原因：\(draft.event.rationale)\n投入：\(draft.event.effort)" : "\(draft.event.status?.rawValue ?? "请选择完成情况")\n\(draft.event.result)")
                            if draft.event.replaces != nil { Text("更换当前 MIT，保留之前的计划及反馈。") }
                            Button("确认写入当天日记") {
                                Task { await model.confirmMIT(); if model.error == nil { _ = await diary.navigate(to: draft.event.day) } }
                            }.accessibilityIdentifier("confirmMIT")
                            WorkflowNotice(model: model)
                            if model.error != nil && !model.pendingCandidate {
                                Button("核对最新来源并保留输入") { Task { await model.refreshMITPreview(day: draft.event.day, section: draft.section, feedback: feedback) } }
                            }
                        }.navigationTitle("核对 MIT").toolbar { Button("关闭") { model.mitPreview = nil } }.disabled(model.busy)
                    }
                }
            }
    }
}

struct ReviewsView: View {
    @ObservedObject var model: WorkflowViewModel
    @ObservedObject var diary: DiaryViewModel
    @State private var kind = TemplateKind.weekly
    @State private var date: Date
    @State private var period: ReviewPeriod?
    @State private var sourceFile: SavedFile?
    init(model: WorkflowViewModel, diary: DiaryViewModel) {
        self.model = model; self.diary = diary
        var calendar = Calendar(identifier: .iso8601); calendar.timeZone = diary.timeZone
        _date = State(initialValue: calendar.date(byAdding: .day, value: -7, to: diary.today.date(in: diary.timeZone))!)
    }
    var body: some View {
        Form {
            Section("选择周期") {
                Picker("复盘类型", selection: $kind) { Text("周报").tag(TemplateKind.weekly); Text("月报").tag(TemplateKind.monthly) }.pickerStyle(.segmented)
                    .onChange(of: kind) { _, value in
                        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = diary.timeZone
                        date = calendar.date(byAdding: value == .monthly ? .month : .day, value: value == .monthly ? -1 : -7, to: diary.today.date(in: diary.timeZone))!
                    }
                DatePicker("周期内任一天", selection: $date, displayedComponents: .date).environment(\.timeZone, diary.timeZone)
                Button("打开周期复盘") { Task { await openPeriod() } }.accessibilityIdentifier("openReviewPeriod")
            }
            if let period {
                Section(period.title) {
                    Text("日记时区：\(diary.timeZone.identifier)")
                    if let draft = model.review {
                        Text("\(draft.phase.rawValue) · 截止 \(draft.asOf.value)").foregroundStyle(.secondary)
                        Text("可用日记 \(draft.sources.count) 篇 · 缺少 \(draft.missingDays.count) 天")
                        Text("缺少日期：\(draft.missingDays.map(\.value).joined(separator: "、"))").font(.caption)
                        if draft.sources.isEmpty { Text("本周期没有已保存日记。可以手动复盘，不会生成经历或指标数值。") }
                        ForEach(draft.sources) { source in
                            NavigationLink(source.day.value) { SavedWorkflowFileView(model: model, diary: diary, file: .daily(source.day)) }
                        }
                        Text("\(draft.template.label) · 本人手动复盘").font(.caption)
                        ForEach(draft.template.warnings, id: \.self) { Text($0).foregroundStyle(.orange) }
                        TextEditor(text: Binding(get: { model.review?.text ?? "" }, set: model.updateReview))
                            .frame(minHeight: 220).accessibilityIdentifier("reviewEditor").disabled(model.pendingReview)
                        Button("预览确认复盘") { Task { if await model.flush() { model.showReviewConfirmation = true } } }.accessibilityIdentifier("previewReview")
                        Button("重新核对来源（保留编辑）") { Task { await model.reviseReview(period, now: diary.today.date(in: diary.timeZone)) } }.disabled(model.pendingReview)
                        if model.pendingReview { Text("上次保存尚待核对；请用原预览重试确认。输入暂时保持原确认内容。").foregroundStyle(.orange) }
                    } else {
                        Text(WorkflowSamples.visible(model.savedReview)).textSelection(.enabled).accessibilityIdentifier("savedReview")
                        Button("追加新的复盘版本") { Task { await model.reviseReview(period, now: diary.today.date(in: diary.timeZone)) } }.accessibilityIdentifier("reviseReview")
                        NavigationLink("打开并导出已保存复盘") { SavedWorkflowFileView(model: model, diary: diary, file: .review(period)) }
                    }
                }
            }
            WorkflowNotice(model: model)
        }.navigationTitle("周报 / 月报").disabled(model.busy)
            .sheet(isPresented: $model.showReviewConfirmation) {
                if let draft = model.review {
                    NavigationStack {
                        ScrollView { VStack(alignment: .leading, spacing: 16) {
                            Text(draft.period.title).font(.headline)
                            Text("保存到\(draft.period.kind == .weekly ? "周报" : "月报")；追加新版本，保留已保存内容。")
                            Text(draft.text).textSelection(.enabled)
                            Button("确认保存复盘") { Task { await model.confirmReview() } }.accessibilityIdentifier("confirmReview")
                            WorkflowNotice(model: model)
                        }.padding() }.navigationTitle("核对复盘").toolbar { Button("关闭") { model.showReviewConfirmation = false } }.disabled(model.busy)
                    }
                }
            }
    }
    private func openPeriod() async {
        do {
            let value = try ReviewPeriod(kind: kind, containing: JournalDay(date: date, timeZone: diary.timeZone), timeZone: diary.timeZone)
            await model.openReview(value, now: diary.today.date(in: diary.timeZone)); if model.error == nil { period = value }
        } catch { model.error = JournalError.invalidDay.localizedDescription }
    }
}

struct WorkflowMarkdownDocument: FileDocument {
    static var readableContentTypes: [UTType] { [.plainText] }
    var data: Data
    init(data: Data) { self.data = data }
    init(configuration: ReadConfiguration) throws { data = configuration.file.regularFileContents ?? Data() }
    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper { FileWrapper(regularFileWithContents: data) }
}

enum WorkflowSamples {
    static var enabled: Bool {
        #if DEBUG
        let environment = ProcessInfo.processInfo.environment
        return environment["RIJI_SAMPLE_MODE"] == "1" || environment["RIJI_TEST_CONTAINER"].flatMap(UUID.init(uuidString:)) != nil
        #else
        return false
        #endif
    }
    static let template = "---\nfixture: synthetic-manual-acceptance\ndate: '{{date:YYYY-MM-DD}}'\nweekday: '{{date:dddd}}'\nunknown: keep-this-field\n---\n# {{title}}\n\n## 🎯 Most Important Task (MIT)\n\n## 🌆 Evening\n\n## 🧠  **Notes**\n\nWeather: {{weather}}\n<% unsupported_synthetic_macro %>\n"
    static func visible(_ markdown: String) -> String {
        markdown.components(separatedBy: "\n").filter { !$0.hasPrefix("<!-- riji-record:") }.joined(separator: "\n")
    }
}
