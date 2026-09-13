import Foundation
import SwiftUI
import Combine
import RijiCore

@MainActor
final class DiaryViewModel: ObservableObject {
    @Published private(set) var selectedDay: JournalDay
    @Published private(set) var today: JournalDay
    @Published private(set) var markdown: String
    @Published private(set) var text = ""
    @Published private(set) var section = "🧠 Notes"
    @Published private(set) var status = ""
    @Published private(set) var errorMessage: String?
    @Published private(set) var isBusy = true
    @Published private(set) var isPending = false
    @Published private(set) var isSaved = false
    @Published private(set) var directory: [JournalDirectoryEntry] = []
    @Published private(set) var workflows: WorkflowViewModel?
    @Published private(set) var templateLabel = DailyTemplate.label
    private var store: LocalJournalStore?
    private var snapshot: JournalSnapshot?
    private var draft: JournalDraft?
    private var persistence: Task<Void, Never>?
    private var revision = 0
    private var dayObserver: AnyCancellable?
    @Published private(set) var exportVerificationDigest = ""
    private let now: () -> Date
    private let storageFactory: () throws -> LocalJournalStore

    var timeZone: TimeZone { store?.timeZone ?? .current }
    var isReady: Bool { snapshot != nil && store != nil }
    // Parsed heading titles never contain a newline; the empty string remains a valid raw anchor.
    static let unselectedSection = "\n"
    var requiresSectionSelection: Bool { section == Self.unselectedSection }
    var canSave: Bool { isReady && !isBusy && !requiresSectionSelection && !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    private var rawHeadings: [MarkdownHeading] {
        (try? MarkdownSectionAppender.headings(in: Data(markdown.utf8))) ?? []
    }
    var sections: [String] {
        return Array(Set(rawHeadings.filter { $0.level > 1 || DiaryHeadingPresentation.isNotes($0.title) }.map(\.title))).sorted()
    }

    init(now: @escaping () -> Date = Date.init, storageFactory: @escaping () throws -> LocalJournalStore = DiaryViewModel.makeStore) {
        self.now = now
        self.storageFactory = storageFactory
        let day = JournalDay(date: now(), timeZone: .current)
        self.selectedDay = day
        self.today = day
        self.markdown = (try? DailyTemplate.instantiate(day: day)) ?? ""
    }

    func start() async {
        let retainedText = text
        let retainedSection = section
        isBusy = true
        do {
            if store == nil { store = try storageFactory() }
            guard let store else { return }
            if workflows == nil { workflows = WorkflowViewModel(store: store) }
            today = JournalDay(date: now(), timeZone: store.timeZone)
            let day = try await retainedText.isEmpty ? store.initialDay(now: now()) : selectedDay
            try await load(day)
        } catch { show(error) }
        isBusy = false
        if isReady && !retainedText.isEmpty {
            text = retainedText
            section = retainedSection
            queuePersistence()
        }
    }

    func observeDayChanges(_ center: NotificationCenter = .default) {
        guard dayObserver == nil else { return }
        dayObserver = center.publisher(for: .NSCalendarDayChanged).sink { [weak self] _ in
            Task { @MainActor in self?.resume() }
        }
    }

    func flushDraft() async { await persistence?.value }

    func foregroundTick(isActive: Bool) {
        if isActive { resume() }
    }

    func resume() {
        today = JournalDay(date: now(), timeZone: timeZone)
        // An open day and draft never move when the clock or device timezone changes.
    }

    func updateText(_ value: String) {
        guard !isBusy, !isPending else { return }
        text = value
        queuePersistence()
    }

    func updateSection(_ value: String) {
        guard !isBusy, !isPending else { return }
        section = value
        queuePersistence()
    }

    private func queuePersistence() {
        guard let store, let snapshot else { errorMessage = JournalError.storage.localizedDescription; return }
        if draft == nil {
            draft = JournalDraft(day: selectedDay, section: section, sourceHash: snapshot.sourceHash,
                                 effectiveTemplate: snapshot.draft?.effectiveTemplate ?? snapshot.markdown)
        }
        draft = draft?.updating(text: text, section: section)
        guard let value = draft else { return }
        revision += 1
        let currentRevision = revision
        let previous = persistence
        status = "正在保留草稿…"
        persistence = Task {
            await previous?.value
            guard currentRevision == revision else { return }
            do {
                try await store.saveDraft(value)
                guard currentRevision == revision else { return }
                status = "草稿已保留在手机 · 尚未写入日记"
                errorMessage = nil
            } catch {
                guard currentRevision == revision else { return }
                show(error)
                status = "草稿尚未保存，请重试"
            }
        }
    }

    func save() async {
        guard canSave, let store, let draft else { return }
        isBusy = true
        await persistence?.value
        status = "正在保存…"
        do {
            _ = try await store.commit(draft)
            try await refreshAfterSave()
            status = "已保存在手机"
            errorMessage = nil
        } catch {
            isPending = (try? await store.hasOperation(draft.operationID)) ?? true
            show(error)
            status = "保存尚未确认，请重试"
        }
        isBusy = false
    }

    func navigate(to day: JournalDay) async -> Bool {
        guard !isBusy, let store else { return false }
        isBusy = true
        await persistence?.value
        do {
            if let draft, !isPending { try await store.saveDraft(draft) }
            try await load(day, selecting: true)
            isBusy = false
            return true
        } catch {
            show(error)
            isBusy = false
            return false
        }
    }

    func loadDirectory() async {
        do { directory = try await store?.directory() ?? [] }
        catch { show(error) }
    }

    func export(day: JournalDay) async -> MarkdownDocument? {
        do {
            guard let data = try await store?.exportMarkdown(day) else { return nil }
            return MarkdownDocument(day: day, data: data)
        }
        catch { show(error); return nil }
    }

    func exportCompleted(_ result: Result<URL, Error>, document: MarkdownDocument) {
        switch result {
        case .success(let destination):
            #if DEBUG
            if let name = ProcessInfo.processInfo.environment["RIJI_TEST_CONTAINER"], UUID(uuidString: name) != nil {
                do { try verifyExportForTesting(destination, document: document) }
                catch { errorMessage = "测试导出文件未通过字节核验。"; return }
            }
            #endif
            status = "Markdown 已导出"
        case .failure(let error):
            if (error as NSError).code != NSUserCancelledError {
                errorMessage = "导出未完成。日记和草稿仍在手机，请重新选择可写入的位置。"
            }
        }
    }

    #if DEBUG
    func verifyExportForTesting(_ destination: URL, document: MarkdownDocument) throws {
        let access = destination.startAccessingSecurityScopedResource()
        defer { if access { destination.stopAccessingSecurityScopedResource() } }
        let actual = try Data(contentsOf: destination)
        guard actual == document.data else { throw JournalError.storage }
        exportVerificationDigest = SourceHash.digest(actual)
    }
    #endif

    /// A source conflict needs a new, visible confirmation; never reuse its old operation.
    func reviewCurrentSource() async {
        guard !isBusy, !isPending, let store else { return }
        isBusy = true
        defer { isBusy = false }
        let day = selectedDay
        await persistence?.value
        do {
            let latest = try await store.open(day)
            snapshot = latest
            markdown = latest.markdown
            draft = JournalDraft(day: selectedDay, section: section, text: text, sourceHash: latest.sourceHash,
                                 effectiveTemplate: draft?.effectiveTemplate ?? latest.markdown)
            queuePersistence()
            await persistence?.value
            if errorMessage == nil { status = "已显示当前正文，请核对后点保存这段" }
        } catch { show(error) }
    }

    private func load(_ day: JournalDay, selecting: Bool = false) async throws {
        guard let store else { throw JournalError.storage }
        var recoveryError: Error?
        do { _ = try await store.recover(day) } catch { recoveryError = error }
        let value = try await store.open(day)
        let label = try await label(for: value, store: store)
        let headings = try MarkdownSectionAppender.headings(in: Data(value.markdown.utf8))
        let nextSection = value.draft?.section ?? defaultSection(headings)
        if selecting { try await store.select(day) }
        // All throwing reads complete before the current page is replaced.
        snapshot = value
        selectedDay = day
        markdown = value.markdown
        templateLabel = label
        draft = value.draft
        text = value.draft?.text ?? ""
        section = nextSection
        isSaved = value.isSaved
        isPending = value.isPending
        status = value.draft == nil ? (value.isSaved ? "已保存在手机" : "") : "草稿已保留在手机 · 尚未写入日记"
        errorMessage = nil
        if let recoveryError { show(recoveryError); status = "上次保存尚待核对，请重试" }
    }

    private func label(for value: JournalSnapshot, store: LocalJournalStore) async throws -> String {
        if value.isSaved { return "已保存日记 · 原模板保留" }
        if value.draft != nil { return "草稿绑定的原模板" }
        return try await store.selectedTemplate(.daily).label
    }

    private func defaultSection(_ headings: [MarkdownHeading]) -> String {
        let notes = headings.filter { DiaryHeadingPresentation.isNotes($0.title) }
        if notes.count == 1 { return notes[0].title }
        if notes.count > 1 { return Self.unselectedSection }
        return Array(Set(headings.filter { $0.level > 1 }.map(\.title))).sorted().first ?? "🧠 Notes"
    }

    private func refreshAfterSave() async throws {
        guard let store else { throw JournalError.storage }
        let value = try await store.open(selectedDay)
        snapshot = value
        markdown = value.markdown
        isSaved = value.isSaved
        templateLabel = "已保存日记 · 原模板保留"
        draft = nil
        text = ""
        isPending = false
    }

    private func show(_ error: Error) {
        errorMessage = (error as? JournalError ?? .storage).localizedDescription
    }

    nonisolated private static func makeStore() throws -> LocalJournalStore {
        let manager = FileManager.default
        var documents = try manager.url(for: .documentDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        var support = try manager.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        #if DEBUG
        if let name = ProcessInfo.processInfo.environment["RIJI_TEST_CONTAINER"], let id = UUID(uuidString: name) {
            documents.appendPathComponent("UITests-\(id.uuidString)")
            support.appendPathComponent("UITests-\(id.uuidString)")
        }
        #endif
        return try LocalJournalStore(journalDirectory: documents.appendingPathComponent("Journals"),
                                     stateDirectory: support.appendingPathComponent("Riji"))
    }
}

/// Inline presentation is separate from the raw heading used by storage and view identity.
enum DiaryHeadingPresentation {
    static func styled(_ raw: String) -> AttributedString {
        (try? AttributedString(markdown: raw, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(raw)
    }
    static func isNotes(_ raw: String) -> Bool {
        let title = normalized(String(styled(raw).characters))
        let withoutStyleDelimiters = normalized(String(raw.filter { !"*_~`".contains($0) }))
        // Links, images, HTML and entities are not mere changes to inline heading style.
        guard withoutStyleDelimiters == title else { return false }
        return title == "🧠 Notes" || title == "Notes"
    }
    private static func normalized(_ text: String) -> String {
        text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    }
}
