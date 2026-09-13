import Foundation
import SwiftUI
import Combine
import RijiCore

@MainActor
final class WorkflowViewModel: ObservableObject {
    let store: LocalJournalStore
    @Published var error: String?
    @Published var status = ""
    @Published private(set) var busy = false
    @Published private(set) var pendingCandidate = false
    @Published private(set) var templateVersions: [TemplateVersion] = []
    @Published private(set) var selectedTemplate: TemplateVersion = .initial(.daily)
    @Published private(set) var weekdayLanguage = WeekdayLanguage.chinese
    @Published var importPreview: TemplateVersion?
    private var importID = UUID()
    @Published var blank: BlankDayDraft?
    @Published private(set) var matches: [SearchMatch] = []
    @Published private(set) var searchSummary = "仅搜索已保存的日记、周报和月报。"
    @Published private(set) var goals: [NorthStarDefinition] = []
    @Published private(set) var goalHistory: [NorthStarDefinition] = []
    @Published private(set) var mit: MITEvent?
    @Published private(set) var mitHistory: [MITEvent] = []
    @Published private(set) var mitSections: [String] = []
    @Published private(set) var fields: [String: String] = [:]
    @Published private(set) var review: ReviewDraft?
    @Published private(set) var pendingReview = false
    @Published private(set) var savedReview = ""
    @Published var goalPreview: NorthStarDefinition?
    @Published var mitPreview: MITDraft?
    @Published var showReviewConfirmation = false
    private var formName = ""
    private var persistence: Task<Void, Never>?
    private enum PendingWrite {
        case form(String, [String: String])
        case review(ReviewDraft)
    }
    private var unpersisted: PendingWrite?
    private var persistenceRevision = 0
    private var idleWaiters: [UUID: CheckedContinuation<Void, Never>] = [:]
    private var candidateGoal: NorthStarDefinition?
    private var candidateMIT: MITDraft?

    init(store: LocalJournalStore) { self.store = store }
    var timeZone: TimeZone { store.timeZone }
    var requiresGoalReselection: Bool {
        guard let selected = fields["goal"], !selected.isEmpty else { return false }
        return !goals.contains { $0.id.uuidString == selected }
    }

    func run(waitForPrevious: Bool = false, _ action: () async throws -> Void) async {
        if waitForPrevious {
            while busy {
                guard !Task.isCancelled else { return }
                await waitUntilIdle()
            }
        }
        guard !busy, !Task.isCancelled else { return }
        busy = true; error = nil; status = ""
        defer {
            busy = false
            let waiting = idleWaiters.values; idleWaiters.removeAll()
            for continuation in waiting { continuation.resume() }
        }
        do { try await action() }
        catch is CancellationError { }
        catch { self.error = (error as? JournalError ?? .storage).localizedDescription }
    }

    private func waitUntilIdle() async {
        let id = UUID()
        await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                if Task.isCancelled || !busy { continuation.resume() }
                else { idleWaiters[id] = continuation }
            }
        } onCancel: {
            Task { @MainActor [weak self] in self?.idleWaiters.removeValue(forKey: id)?.resume() }
        }
    }

    func loadTemplates(_ kind: TemplateKind) async {
        await run(waitForPrevious: true) {
            templateVersions = try await store.templates(kind); selectedTemplate = try await store.selectedTemplate(kind)
            weekdayLanguage = try await store.weekdayLanguage()
        }
    }
    func chooseWeekdayLanguage(_ language: WeekdayLanguage) async {
        await run {
            try await store.setWeekdayLanguage(language)
            weekdayLanguage = try await store.weekdayLanguage()
            status = "星期语言已保存；用于新日期和新准备的复盘。"
        }
    }
    func previewImport(_ data: Data, kind: TemplateKind) {
        do { importPreview = try TemplateVersion(kind: kind, raw: data); importID = UUID(); error = nil }
        catch { self.error = (error as? JournalError ?? .invalidTemplate).localizedDescription }
    }
    func confirmImport() async {
        guard let value = importPreview else { return }
        await run {
            try await store.importTemplate(value, operationID: importID)
            templateVersions = try await store.templates(value.kind)
            importPreview = nil; status = "模板已复制到手机。请选择后用于新的日记或复盘。"
        }
    }
    func chooseTemplate(_ value: TemplateVersion) async {
        await run { try await store.selectTemplate(value); selectedTemplate = value; status = "已选择；已有日记和待保存草稿保留原模板。" }
    }
    func prepareBlank(_ day: JournalDay) async {
        await run { blank = try await store.prepareBlankDay(day) }
    }
    func confirmBlank() async -> Bool {
        guard let draft = blank else { return false }
        var success = false
        await run {
            try await store.createBlankDay(draft); blank = nil
            status = "已保存在手机；已有日记保留原文。"; success = true
        }
        return success
    }
    func search(_ query: String) async {
        await run {
            let result = try await store.search(query); matches = result.matches
            searchSummary = "本地已保存文件 · 已扫描 \(result.scanned) 篇" + (result.truncated ? " · 部分结果，未扫描全部文件" : "")
            if result.skippedOversized > 0 { searchSummary += " · 跳过 \(result.skippedOversized) 篇超过 2 MiB 的文件" }
            if matches.isEmpty && !query.isEmpty { searchSummary += " · 在本次范围内未找到" }
        }
    }
    func savedText(_ file: SavedFile) async -> String {
        do { return String(decoding: try await store.readSavedFile(file), as: UTF8.self) }
        catch { self.error = (error as? JournalError ?? .storage).localizedDescription; return "" }
    }

    func loadGoals() async {
        await run(waitForPrevious: true) {
            try await flushPersistence()
            goals = try await store.currentNorthStars(); goalHistory = try await store.northStars()
            let retained = try await store.loadForm("goal")
            formName = "goal"; fields = retained
            candidateGoal = decodeCandidate(fields["candidate"])
            if let candidateGoal { pendingCandidate = try await store.workflowHasOperation(candidateGoal.id) }
            else { pendingCandidate = false }
        }
    }
    func editGoal(_ goal: NorthStarDefinition?) {
        guard !busy, !pendingCandidate else { return }
        fields = goal.map { ["previous": $0.id.uuidString, "name": $0.name, "definition": $0.definition, "unit": $0.unit,
                            "target": $0.target, "period": $0.period, "stage": $0.stage,
                            "measurement": $0.measurement ?? "", "measurementSource": $0.measurementSource ?? ""] } ?? [:]
        candidateGoal = nil; queueForm()
    }
    func field(_ key: String) -> Binding<String> { Binding(get: { self.fields[key] ?? "" }, set: { self.updateField(key, $0) }) }
    func updateField(_ key: String, _ value: String) {
        guard !busy, !pendingCandidate else { return }
        fields[key] = value; fields.removeValue(forKey: "candidate")
        candidateGoal = nil; candidateMIT = nil
        queueForm()
    }
    private func queueForm() { queue(.form(formName, fields)) }

    private func queue(_ write: PendingWrite) {
        persistenceRevision += 1
        let revision = persistenceRevision; let previous = persistence
        unpersisted = write; status = "正在保留输入…"
        persistence = Task {
            await previous?.value
            do {
                try await persist(write)
                guard revision == persistenceRevision else { return }
                unpersisted = nil; error = nil; status = retainedStatus(write)
            } catch {
                guard revision == persistenceRevision else { return }
                self.error = (error as? JournalError ?? .storage).localizedDescription
                status = "输入尚未保存，请重试"
            }
        }
    }

    private func persist(_ write: PendingWrite) async throws {
        switch write {
        case .form(let name, let values): try await store.retainForm(values, name: name)
        case .review(let draft): try await store.retainReviewDraft(draft)
        }
    }

    private func retainedStatus(_ write: PendingWrite) -> String {
        if case .review = write { return "复盘草稿已保留在手机" }
        return "输入已保留在手机"
    }

    /// A failed queued write remains in memory until a retry succeeds; loaders must not replace it.
    private func flushPersistence() async throws {
        await persistence?.value
        guard let write = unpersisted else { return }
        try await persist(write)
        unpersisted = nil; error = nil; status = retainedStatus(write)
    }

    @discardableResult func flush() async -> Bool {
        var succeeded = false
        await run { try await flushPersistence(); succeeded = true }
        return succeeded
    }

    func previewGoal(day: JournalDay) async {
        await run {
            try await flushPersistence()
            let candidate = candidateGoal ?? NorthStarDefinition(previousID: fields["previous"].flatMap(UUID.init(uuidString:)),
                name: fields["name"] ?? "", definition: fields["definition"] ?? "", unit: fields["unit"] ?? "",
                target: fields["target"] ?? "", period: fields["period"] ?? "", stage: fields["stage"] ?? "",
                measurement: nonempty(fields["measurement"]), measurementSource: nonempty(fields["measurementSource"]), day: day)
            candidateGoal = candidate; fields["candidate"] = encodeCandidate(candidate)
            try await store.retainForm(fields, name: formName); goalPreview = candidate
        }
    }
    func confirmGoal() async {
        guard !busy, let candidate = goalPreview else { return }
        await run {
            try await store.saveNorthStar(candidate)
            try await store.retainForm([:], name: formName)
            fields = [:]; candidateGoal = nil; goalPreview = nil
            pendingCandidate = false
            goals = try await store.currentNorthStars(); goalHistory = try await store.northStars()
            status = "北极星已保存为新版本"
        }
        if error != nil { pendingCandidate = (try? await store.workflowHasOperation(candidate.id)) ?? true }
    }
    func loadMIT(_ day: JournalDay) async {
        await run(waitForPrevious: true) {
            try await flushPersistence()
            try await reloadMITDependencies(day)
            let name = "mit-\(day.value)"; let retained = try await store.loadForm(name)
            formName = name; fields = retained
            candidateMIT = decodeCandidate(fields["candidate"])
            if let candidateMIT { pendingCandidate = try await store.workflowHasOperation(candidateMIT.operationID) }
            else { pendingCandidate = false }
        }
    }
    private func reloadMITDependencies(_ day: JournalDay) async throws {
        let snapshot = try await store.open(day)
        let headings = try MarkdownSectionAppender.headings(in: Data(snapshot.markdown.utf8))
        mitSections = Array(Set(headings.filter { $0.level > 1 }.map(\.title))).sorted()
        mit = try await store.currentMIT(day); mitHistory = try await store.mitHistory(day)
        goals = try await store.currentNorthStars()
    }

    func previewMIT(day: JournalDay, section: String, feedback: Bool) async {
        await run {
            try await flushPersistence()
            if candidateMIT == nil, !feedback, requiresGoalReselection { throw JournalError.changedSource }
            let event = MITEvent(day: day, replaces: feedback ? nil : mit?.id,
                goalID: fields["goal"].flatMap(UUID.init(uuidString:)), action: fields["action"] ?? "",
                criterion: fields["criterion"] ?? "", rationale: fields["rationale"] ?? "", effort: fields["effort"] ?? "",
                feedbackFor: feedback ? mit?.id : nil, status: feedback ? fields["feedbackStatus"].flatMap(MITStatus.init(rawValue:)) : nil,
                result: fields["result"] ?? "")
            let candidate: MITDraft
            if let existing = candidateMIT { candidate = existing }
            else { candidate = try await store.prepareMIT(event, section: section) }
            candidateMIT = candidate; fields["candidate"] = encodeCandidate(candidate)
            try await store.retainForm(fields, name: formName); mitPreview = candidate
        }
    }
    func refreshMITPreview(day: JournalDay, section: String, feedback: Bool) async {
        guard !pendingCandidate else { return }
        var refreshed = false
        await run {
            try await flushPersistence()
            try await reloadMITDependencies(day)
            candidateMIT = nil; mitPreview = nil; fields.removeValue(forKey: "candidate")
            try await store.retainForm(fields, name: formName)
            if !feedback, requiresGoalReselection { throw JournalError.changedSource }
            refreshed = true
        }
        if !feedback, requiresGoalReselection {
            error = "北极星版本已变化，请重新选择一个有效版本后预览。原输入与原关联仍保留。"
        }
        if refreshed { await previewMIT(day: day, section: section, feedback: feedback) }
    }
    func confirmMIT() async {
        guard !busy, let candidate = mitPreview else { return }
        await run {
            try await store.commitMIT(candidate)
            try await store.retainForm([:], name: formName)
            fields = [:]; candidateMIT = nil; mitPreview = nil
            pendingCandidate = false
            mit = try await store.currentMIT(candidate.event.day); mitHistory = try await store.mitHistory(candidate.event.day)
            status = "MIT 已保存在当天日记，历史记录已保留"
        }
        if error != nil { pendingCandidate = (try? await store.workflowHasOperation(candidate.operationID)) ?? true }
    }

    func openReview(_ period: ReviewPeriod, now: Date) async {
        await run(waitForPrevious: true) {
            try await flushPersistence()
            try await store.recoverReview(period)
            do { savedReview = String(decoding: try await store.readSavedFile(.review(period)), as: UTF8.self) }
            catch JournalError.noDiary { savedReview = "" }
            review = try await store.savedReviewDraft(period)
            if let review { pendingReview = try await store.workflowHasOperation(review.operationID) }
            else { pendingReview = false }
            // Existing reports open for reading first. A new revision is an explicit action.
            if review == nil && savedReview.isEmpty {
                let value = try await store.prepareReview(period, now: now)
                try await store.retainReviewDraft(value); review = value
            }
        }
    }
    func reviseReview(_ period: ReviewPeriod, now: Date) async {
        guard !pendingReview else { return }
        await run {
            try await flushPersistence()
            let next = try await store.prepareReview(period, now: now, retaining: review?.text)
            try await store.retainReviewDraft(next); review = next
        }
    }
    func updateReview(_ text: String) {
        guard !busy, !pendingReview, let current = review else { return }
        let next = current.editing(text); review = next
        queue(.review(next))
    }

    func confirmReview() async {
        guard !busy, let value = review else { return }
        await run {
            try await flushPersistence()
            try await store.commitReview(value)
            savedReview = String(decoding: try await store.readSavedFile(.review(value.period)), as: UTF8.self)
            review = nil; showReviewConfirmation = false; status = "复盘已保存在手机"
            pendingReview = false
        }
        if error != nil { pendingReview = (try? await store.workflowHasOperation(value.operationID)) ?? true }
    }
    private func nonempty(_ text: String?) -> String? { text?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false ? text : nil }
    private func encodeCandidate<T: Encodable>(_ value: T) -> String? { (try? JSONEncoder().encode(value))?.base64EncodedString() }
    private func decodeCandidate<T: Decodable>(_ value: String?) -> T? {
        guard let value, let data = Data(base64Encoded: value) else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }
}
