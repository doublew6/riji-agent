import Foundation
import SwiftUI
import Combine
import RijiCore

enum DiaryDateDirection: Int, CaseIterable, Sendable {
    case previous = -1
    case next = 1
}

enum DiarySourceIssue { case contentConflict, cloudConflict, unavailable, identity }

/// Captured by each native editor callback, before SwiftUI receives a replacement.
struct DiaryBodyInputOrigin {
    let day: JournalDay
    let identity: String
    let sourceID: String?
    let replacementRevision: Int
    let base: Data?
    let visible: Data
    let snapshot: JournalSnapshot?
    let wasEditable: Bool
}

@MainActor
final class DiaryViewModel: ObservableObject {
    @Published private(set) var aiResultPending = false
    @Published private(set) var aiResultPreview: AIResultPreview?
    @Published private(set) var aiResultStatus: String?
    @Published private(set) var localAIResultPreviews: [AIResultPreview] = []
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
    @Published private(set) var abandonableInsertion: VoiceInsertionCandidate?
    @Published private(set) var voice: VoiceViewModel?
    @Published private(set) var isInsertionUnknown = false
    @Published private(set) var templateLabel = DailyTemplate.label
    @Published private(set) var bodyDirty = false
    @Published private(set) var bodyRecoveryPending = false
    @Published private(set) var bodyRecoveryUnavailable = false
    @Published private(set) var bodyMergeError: String?
    @Published private(set) var bodyMergeDirty = false
    @Published private(set) var bodyStatus = ""
    @Published private(set) var bodyError: String?
    @Published private(set) var bodyConflict: BodyConflict?
    @Published private(set) var bodyMergeText = ""
    @Published private(set) var bodyReplacementRevision = 0
    @Published private(set) var bodySourceIssue: DiarySourceIssue?
    @Published private(set) var isCheckingResumedSource = false
    @Published private(set) var cloudConflict: CloudReview?
    @Published private(set) var cloudDraft: CloudRecoveryDraft?
    @Published private(set) var cloudMergeText = ""
    @Published private(set) var cloudError: String?
    @Published private(set) var cloudBackupWarning: String?
    @Published private(set) var bodyHeldForCloud = false
    @Published private(set) var cloudRecoveryPending = false
    @Published private(set) var cloudBackups: [CloudRecoveryDraft] = []
    @Published private(set) var cloudLocalVersions: [Data] = []
    @Published private(set) var cloudReviewPresented = false
    private var cloudPersistence: Task<Void, Never>?
    private var cloudMergeDirty = false
    private var cloudSequence = 0
    var cloudDebounceNanoseconds: UInt64 = 250_000_000
    private var cloudRecoveryUnreadable = false
    var hasCloudRecovery: Bool { cloudConflict != nil || cloudDraft != nil || cloudRecoveryPending || cloudRecoveryUnreadable }
    var cloudBlocksEditing: Bool { cloudConflict != nil || cloudRecoveryPending || cloudRecoveryUnreadable }
    private var mergeSequence = 0
    private var bodyMerge: BodyMergeDraft?
    private var mergePersistence: Task<Void, Never>?
    private var bodyQuietTask: Task<Void, Never>?
    private var bodyCheckpointTask: Task<Void, Never>?
    var bodyQuietNanoseconds: UInt64 = 1_000_000_000
    var bodyCheckpointNanoseconds: UInt64 = 15_000_000_000
    private var bodyBase: Data?
    private var bodyEdit: BodyEdit?
    private var bodyPersistence: Task<Void, Never>?
    private var bodySequence = 0
    private var store: LocalJournalStore?
    private var aiActionID = UUID()
    private var aiRecoveryID = UUID()
    private var aiActionInFlight: UUID?
    private var aiResultPreparedForConfirmation: AIResultPreview?
    let vaultStore: VaultBrowserStore
    let northStar: NorthStarAnchorModel
    private let usesOriginalCollection: Bool
    @Published private(set) var isOriginalSource = false
    @Published private(set) var sourceLabel = "App 本地日记"
    @Published private(set) var sourceIdentity = "local"
    @Published private(set) var needsFolder = false
    @Published private(set) var sourceUnavailable = false
    private var sourceID: String?
    private var refreshTask: Task<Void, Never>?
    private var resumeTask: Task<Void, Never>?
    private var refreshVersion = 0
    private var completedRefreshVersion = 0
    private var handledRefreshVersion = 0
    var resumeRefreshTimeout: TimeInterval = 5
    private var foregroundActive = true
    private var lifecycleRevision = 0
    private var resumeCheckRevision = 0
    private var bodyWriteInProgress = false
    private var previousRefreshOrigin: DiaryBodyInputOrigin?
    private var acceptedLateRefreshInput = false
    private var sourceAccessMessage: String?
    private var sourceBodyMessage: String?
    private var refreshErrorMessage: String?
    // The native editor admits replacement only outside active input/composition.
    var bodyRefreshAdmission: () -> Bool = { true }
    var bodyResumeAdmission: (Bool) -> Void = { _ in }
    @Published private(set) var sourceTransitionInProgress = false
    var isOriginalReadOnly: Bool { isOriginalSource && store?.permitsBodyWriting != true }

    private var snapshot: JournalSnapshot?
    private var draft: JournalDraft?
    private var persistence: Task<Void, Never>?
    private var revision = 0
    private var dayObserver: AnyCancellable?
    @Published private(set) var exportVerificationDigest = ""
    private let now: () -> Date
    private let storageFactory: () throws -> LocalJournalStore
    private let voiceServices: VoiceServices?

    var timeZone: TimeZone { store?.timeZone ?? .current }
    var isReady: Bool { snapshot != nil && store != nil }
    // Parsed heading titles never contain a newline; the empty string remains a valid raw anchor.
    static let unselectedSection = "\n"
    var requiresSectionSelection: Bool {
        section == Self.unselectedSection || rawHeadings.filter { $0.title.utf8.elementsEqual(section.utf8) }.count != 1
    }
    var canSave: Bool { !isOriginalSource && isReady && !bodyRecoveryUnavailable && !isBusy && !isInsertionUnknown && section != Self.unselectedSection && !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    private var rawHeadings: [MarkdownHeading] {
        (try? MarkdownSectionAppender.headings(in: Data(markdown.utf8))) ?? []
    }
    var sections: [String] {
        let headings = rawHeadings
        let counts = Dictionary(grouping: headings, by: { Data($0.title.utf8) }).mapValues(\.count)
        return headings.filter { ($0.level > 1 || DiaryHeadingPresentation.isNotes($0.title)) && counts[Data($0.title.utf8)] == 1 }.map(\.title).sorted()
    }
    var sectionChoices: [DiarySectionChoice] { sections.map(DiarySectionChoice.init) }
    var selectedSectionID: Data { Data(section.utf8) }
    func selectSectionID(_ id: Data) {
        guard let choice = sectionChoices.first(where: { $0.id == id }) else { return }
        updateSection(choice.title)
    }

    init(now: @escaping () -> Date = Date.init, storageFactory: @escaping () throws -> LocalJournalStore = DiaryViewModel.makeStore,
         voiceServices: VoiceServices? = nil, useOriginalCollection: Bool = false, vaultStore: VaultBrowserStore? = nil) {
        self.usesOriginalCollection = useOriginalCollection
        self.vaultStore = vaultStore ?? VaultBrowserStore(stateDirectory: Self.supportDirectory())
        self.northStar = NorthStarAnchorModel(store: NorthStarAnchorStore(directory: Self.supportDirectory()))
        needsFolder = useOriginalCollection
        self.now = now
        self.storageFactory = storageFactory
        self.voiceServices = voiceServices
        let day = JournalDay(date: now(), timeZone: .current)
        self.selectedDay = day
        self.today = day
        self.markdown = useOriginalCollection ? "" : (try? DailyTemplate.instantiate(day: day)) ?? ""
        if useOriginalCollection { templateLabel = "请选择原日记文件夹"; sourceLabel = "尚未授权原目录" }
    }

    func start() async {
        guard !sourceTransitionInProgress else { return }
        if isReady {
            guard !isBusy else { return }
            isBusy = true
            if !bodyRecoveryUnavailable, !(await flushDocumentInput()) { isBusy = false; return }
            if bodyRecoveryUnavailable && bodyDirty { isBusy = false; await retryBodyRecoveryRead(); return }
        }
        let retainedText = text
        let canRestoreRetained = !isInsertionUnknown
        let retainedSection = section
        isBusy = true
        do {
            if store == nil, usesOriginalCollection {
                if let original = try await restoreOriginalAccess() {
                    store = try await vaultStore.homeStore(original)
                    isOriginalSource = true; sourceIdentity = original.identity
                    sourceLabel = "原目录 · " + original.name; needsFolder = false
                } else { isBusy = false; return }
            }
            if store == nil { store = try storageFactory() }
            guard let store else { return }
            if !isOriginalSource, workflows == nil { workflows = WorkflowViewModel(store: store) }
            if !isOriginalSource, voice == nil {
                voice = VoiceViewModel(store: store, services: voiceServices)
                voice?.diaryEditingBlocked = { [weak self] in self?.isBusy != false || self?.bodyRecoveryUnavailable != false || self?.isPending != false || self?.isInsertionUnknown != false }
            }
            today = JournalDay(date: now(), timeZone: store.timeZone)
            let day = try await retainedText.isEmpty ? store.initialDay(now: now()) : selectedDay
            try await load(day)
        } catch { sourceUnavailable = isOriginalSource || usesOriginalCollection; show(error) }
        isBusy = false
        #if DEBUG
        if isExternalRefreshFixture, ProcessInfo.processInfo.environment["RIJI_TEST_LIFECYCLE_REFRESH"] == "1" {
            bodyQuietNanoseconds = 3_600_000_000_000; bodyCheckpointNanoseconds = 3_600_000_000_000
        }
        if let harness = RecoveryControlTestHarness.shared, harness.scenario == "legacy" {
            bodyQuietNanoseconds = 3_600_000_000_000
            bodyCheckpointNanoseconds = 3_600_000_000_000
            if harness.seeded { updateBody(markdown + "Pending body before legacy save\n") }
        }
        #endif
        if isReady && canRestoreRetained && !isInsertionUnknown && !retainedText.isEmpty {
            text = retainedText
            section = retainedSection
            queuePersistence()
        }
    }

    private func restoreOriginalAccess() async throws -> OriginalVaultAccess? {
        do {
            _ = try await vaultStore.restore()
            return try await vaultStore.originalAccess()
        } catch {
            if let recovery = try await vaultStore.recoveryAccess() { return recovery }
            throw error
        }
    }

    func observeDayChanges(_ center: NotificationCenter = .default) {
        guard dayObserver == nil else { return }
        dayObserver = center.publisher(for: .NSCalendarDayChanged).sink { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                self.today = JournalDay(date: self.now(), timeZone: self.timeZone)
                self.foregroundTick(isActive: self.foregroundActive)
            }
        }
    }

    func flushDraft() async { await persistence?.value; _ = await flushDocumentInput() }

    func foregroundTick(isActive: Bool) {
        guard isActive && foregroundActive, refreshTask == nil, !isCheckingResumedSource else { return }
        today = JournalDay(date: now(), timeZone: timeZone)
        Task { await refreshOriginalBody() }
    }

    func resume() {
        foregroundActive = true
        today = JournalDay(date: now(), timeZone: timeZone)
        guard isOriginalSource, isReady, bodyRefreshAdmission() else {
            Task { await refreshOriginalBody() }; return
        }
        resumeCheckRevision += 1
        let check = resumeCheckRevision
        resumeTask?.cancel()
        isCheckingResumedSource = true; bodyResumeAdmission(false)
        resumeTask = Task { await checkResumedSource(check) }
    }

    private func checkResumedSource(_ check: Int) async {
        let deadline = ProcessInfo.processInfo.systemUptime + resumeRefreshTimeout
        var waitingFor: Int?
        while check == resumeCheckRevision && foregroundActive && !Task.isCancelled {
            if let waitingFor, completedRefreshVersion >= waitingFor, handledRefreshVersion >= waitingFor {
                finishResumeCheck(check); return
            }
            if ProcessInfo.processInfo.systemUptime >= deadline {
                // A blocked coordinator/transaction retains ownership. Invalidate
                // its eventual UI result without cancelling or force-writing it.
                lifecycleRevision += 1
                if bodyError == nil {
                    bodyError = "读取文件暂时没有完成。当前文字仍保留，可稍后重新读取；保存时仍会核对原文件。"
                    refreshErrorMessage = bodyError
                }
                finishResumeCheck(check); return
            }
            if let attempt = waitingFor, completedRefreshVersion >= attempt { waitingFor = nil }
            if waitingFor == nil, !isBusy, !bodyWriteInProgress, refreshTask == nil {
                waitingFor = refreshVersion + 1
                Task { await refreshOriginalBody() }
            }
            do { try await Task.sleep(nanoseconds: 10_000_000) } catch { return }
        }
    }

    private func finishResumeCheck(_ check: Int) {
        guard check == resumeCheckRevision else { return }
        isCheckingResumedSource = false; bodyResumeAdmission(canEditBody)
    }

    @discardableResult func scenePhaseChanged(_ phase: ScenePhase, commitEditing: () -> Void) -> Task<Void, Never>? {
        if phase == .active { resume(); return nil }
        commitEditing()
        foregroundActive = false; lifecycleRevision += 1
        resumeCheckRevision += 1; isCheckingResumedSource = false
        resumeTask?.cancel(); resumeTask = nil
        return Task { await flushDraft() }
    }

    /// All callers share one read. No write, navigation or editor reset on an unchanged tick.
    func refreshOriginalBody() async {
        if let refreshTask { await refreshTask.value; return }
        refreshVersion += 1
        let version = refreshVersion
        let task = Task {
            if await performOriginalRefresh() { handledRefreshVersion = version }
            completedRefreshVersion = version; refreshTask = nil
        }
        refreshTask = task
        await task.value
    }

    private var admitsOriginalRefresh: Bool {
        foregroundActive && isOriginalSource && isReady && !isBusy && !sourceTransitionInProgress
            && !cloudReviewPresented && !bodyWriteInProgress && !isPending && !isInsertionUnknown
            && bodyRefreshAdmission()
    }

    private func performOriginalRefresh() async -> Bool {
        guard admitsOriginalRefresh, let store else { return false }
        await bodyPersistence?.value
        guard admitsOriginalRefresh else { return false }
        let day = selectedDay, identity = sourceIdentity, sequence = bodySequence, lifecycle = lifecycleRevision
        let base = bodyBase, visible = Data(markdown.utf8), replacement = bodyReplacementRevision, dirty = bodyDirty
        let expectedSource = sourceID, inputOrigin = bodyInputOrigin
        let recoveryOnly = bodyRecoveryPending || bodyRecoveryUnavailable || hasRetainedBodyMerge || bodyConflict != nil
        func stillCurrent() -> Bool {
            admitsOriginalRefresh && self.store === store && selectedDay == day && sourceIdentity == identity && sourceID == expectedSource
                && bodySequence == sequence && bodyBase == base && bodyReplacementRevision == replacement
                && lifecycleRevision == lifecycle && bodyDirty == dirty
                && recoveryOnly == (bodyRecoveryPending || bodyRecoveryUnavailable || hasRetainedBodyMerge || bodyConflict != nil)
        }
        do {
            let latest = try await store.refreshBodySnapshot(day, sourceID: expectedSource, previouslySaved: base != nil || isSaved)
            guard stillCurrent() else { return false }
            let held = (try? await store.bodyHeldByCloud(day)) ?? true
            let cloudPending = (try? await store.cloudWorkPending(day)) ?? true
            let pendingAI = (try? await store.hasAIResultPending(day)) ?? true
            guard stillCurrent() else { return false }
            clearReadableSourceState()
            aiResultPending = pendingAI
            bodyHeldForCloud = held || latest.cloudHoldFailed; cloudRecoveryPending = cloudPending
            if latest.cloudConflict != nil && cloudConflict == nil {
                previousRefreshOrigin = inputOrigin; acceptedLateRefreshInput = false
                bodyReplacementRevision += 1
            }
            cloudConflict = latest.cloudConflict
            if latest.cloudConflict != nil {
                recordBodyError(OriginalVaultError.conflict)
                if !bodyDirty && !recoveryOnly {
                    snapshot = latest; markdown = latest.markdown; bodyBase = Data(latest.markdown.utf8)
                    isSaved = latest.isSaved
                }
                return true
            }
            if bodyHeldForCloud { bodyError = CloudRecoveryError.heldBody.localizedDescription; return true }
            if recoveryOnly {
                if bodyRecoveryPending && bodyError == nil { bodyError = "原保存尚待核对，请重试保存或核对双方版本。" }
                return true
            }
            let latestBytes = latest.isSaved ? Data(latest.markdown.utf8) : nil
            let unchanged = latestBytes == base && (latestBytes != nil || Data(latest.markdown.utf8) == visible)
            if dirty && visible != unchangedBodyContent && visible != latestBytes {
                if latestBytes != base { recordBodyError(JournalError.changedSource) }
                else if bodySourceIssue == .contentConflict { bodySourceIssue = nil; bodyError = nil }
                return true
            }
            if dirty, let edit = bodyEdit {
                try await store.retireBodyBuffer(edit)
                guard stillCurrent() else { return false }
                bodyDirty = false; bodyEdit = nil
            }
            if bodySourceIssue == .contentConflict { bodySourceIssue = nil; bodyError = nil }
            if !unchanged {
                previousRefreshOrigin = bodyInputOrigin
                acceptedLateRefreshInput = false
                snapshot = latest; markdown = latest.markdown; bodyBase = latestBytes
                bodyReplacementRevision += 1
                isSaved = latest.isSaved
                templateLabel = latest.isSaved ? sourceLabel + " · 原文件" : sourceLabel + " · 原日模板预览（尚未创建文件）"
            }
            bodyStatus = latest.isSaved ? savedBodyStatus : ""
            return true
        } catch {
            guard stillCurrent() else { return false }
            recordBodyError(error)
            refreshErrorMessage = bodyError
            if bodySourceIssue == .unavailable || bodySourceIssue == .identity { sourceUnavailable = true }
            return true
        }
    }

    func retryOriginalBodySource() async {
        guard isOriginalSource, !isBusy else { return }
        if !isReady { await start(); return }
        resume()
        await resumeTask?.value
    }

    func updateText(_ value: String) {
        guard canEditLegacy else { return }
        text = value
        queuePersistence()
    }

    func updateSection(_ value: String) {
        guard canEditLegacy else { return }
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
        guard canSave, let store else { return }
        isBusy = true
        defer { isBusy = false }
        guard await flushDocumentInput() else { return }
        await persistence?.value
        guard let draft else { return }
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
        guard !isBusy, !isInsertionUnknown, let store else { return false }
        isBusy = true
        guard await flushDocumentInput(allowReadOnlyNavigation: true) else { isBusy = false; return false }
        await persistence?.value
        do {
            if let draft, !isPending { try await store.saveDraft(draft) }
            try await load(day, selecting: true)
            isBusy = false
            return true
        } catch {
            if isOriginalSource { sourceUnavailable = true }
            show(error)
            isBusy = false
            return false
        }
    }

    var canNavigateAdjacentDay: Bool {
        isReady && !isBusy && !isPending && !isInsertionUnknown && !bodyRecoveryUnavailable
            && bodyConflict == nil && !hasRetainedBodyMerge
    }

    func navigateToAdjacentDay(_ direction: DiaryDateDirection, from sourceDay: JournalDay) async -> Bool {
        guard canNavigateAdjacentDay, selectedDay == sourceDay else { return false }
        guard !(direction == .previous && sourceDay.value == "0001-01-01"),
              !(direction == .next && sourceDay.value == "9999-12-31") else { return false }
        // selectedDay is already a date label in the store's timezone. Arithmetic on
        // labels uses UTC so DST gaps, including missing local midnights/days, cannot
        // normalize the requested label or turn a calendar day into 86,400 seconds.
        let zone = TimeZone(secondsFromGMT: 0)!
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = zone
        guard let adjacent = calendar.date(byAdding: .day, value: direction.rawValue, to: sourceDay.date(in: zone)),
              calendar.component(.era, from: adjacent) == 1,
              (1...9999).contains(calendar.component(.year, from: adjacent)),
              let day = try? JournalDay(JournalDay(date: adjacent, timeZone: zone).value) else { return false }
        return await navigate(to: day)
    }

    /// A failed or unresolved save stays on Diary, where its recovery actions live.
    func prepareMainDestination() async -> Bool {
        if !isReady { return !isBusy && !bodyDirty && !hasLegacyDraft }
        guard canNavigateAdjacentDay, let store else { return false }
        isBusy = true
        defer { isBusy = false }
        guard await flushDocumentInput() else { return false }
        await persistence?.value
        do {
            if let draft {
                try await store.saveDraft(draft)
                errorMessage = nil
                status = "草稿已保留在手机 · 尚未写入日记"
            }
            return true
        } catch { show(error); return false }
    }

    func loadDirectory() async {
        do { directory = try await store?.directory() ?? [] }
        catch { show(error) }
    }

    func export(day: JournalDay) async -> MarkdownDocument? {
        // A captured export keeps its day while an already-owned navigation finishes.
        while isBusy {
            do { try await Task.sleep(nanoseconds: 5_000_000) } catch { return nil }
        }
        isBusy = true
        defer { isBusy = false }
        if day == selectedDay, !(await flushDocumentInput()) { return nil }
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
            status = document.isRecoveryDraft ? "本机恢复草稿已导出（尚未写入原文件）" : document.includesOnlySavedVersion ? "已保存版本 Markdown 已导出（不含待恢复修改）" : "Markdown 已导出"
        case .failure(let error):
            if (error as NSError).code != NSUserCancelledError {
                errorMessage = "导出未完成。日记和草稿仍在手机，请重新选择可写入的位置。"
            }
        }
    }

    #if DEBUG
    var isExternalRefreshFixture: Bool {
        #if targetEnvironment(simulator)
        let environment = ProcessInfo.processInfo.environment
        return environment["RIJI_TEST_CONTAINER"].flatMap(UUID.init(uuidString:)) != nil
            && environment["RIJI_TEST_EXTERNAL_REFRESH"] == "1" && environment["RIJI_TEST_ORIGINAL_VAULT"] == "1"
        #else
        return false
        #endif
    }

    func simulateExternalRefreshFixture(divergent: Bool) {
        guard isExternalRefreshFixture, isOriginalSource, !isBusy else { return }
        let target = Self.supportDirectory().appendingPathComponent("VaultFixture/riji/daily/\(selectedDay.value).md")
        do {
            let before = try Data(contentsOf: target)
            if divergent {
                bodyQuietNanoseconds = 3_600_000_000_000; bodyCheckpointNanoseconds = 3_600_000_000_000
                updateBody(markdown + "Local simulated edit\r\n")
            }
            try (before + Data("External simulated edit\r\n".utf8)).write(to: target, options: .atomic)
        } catch { recordBodyError(error) }
    }

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
        guard !bodyRecoveryUnavailable, !isBusy, !isPending, !isInsertionUnknown, let store else { return }
        isBusy = true
        defer { isBusy = false }
        let day = selectedDay
        guard await flushDocumentInput() else { return }
        await persistence?.value
        do {
            let latest = try await store.open(day)
            snapshot = latest
            markdown = latest.markdown
            bodyBase = latest.sourceHash == nil ? nil : Data(latest.markdown.utf8)
            let stamps = draft?.voiceInsertions
            draft = JournalDraft(day: selectedDay, section: section, text: text, sourceHash: latest.sourceHash,
                                 effectiveTemplate: draft?.effectiveTemplate ?? latest.markdown)
            draft?.voiceInsertions = stamps
            queuePersistence()
            await persistence?.value
            if errorMessage == nil { status = "已显示当前正文，请核对后点保存这段" }
        } catch { show(error) }
    }

    /// The Today microphone is one explicit user action; preparation completes before requesting permission.
    func beginVoiceCapture(present: @escaping () -> Void) async {
        var presented = false
        guard await captureVoice(deferredPresentation: { if !presented { presented = true; present() } }), let voice else { return }
        if !presented { present() }
        await voice.record()
    }

    func captureVoice(deferredPresentation: (() -> Void)? = nil) async -> Bool {
        guard !bodyRecoveryUnavailable, !isBusy, !isPending, !isInsertionUnknown, !requiresSectionSelection,
              let store, let voice else { return false }
        isBusy = true
        guard await flushDocumentInput(allowReadOnlyNavigation: true) else { isBusy = false; return false }
        voice.cancelPermission()
        var ownsBusy = true
        defer { if ownsBusy { isBusy = false } }
        await persistence?.value
        do {
            let value = try await retainedDraftForVoice(store)
            let target = try await store.voiceTarget(value)
            return await voice.attach(target: target, deferredVerification: { [weak self] in
                guard ownsBusy else { return }
                ownsBusy = false
                self?.isBusy = false
                deferredPresentation?()
            })
        } catch { show(error); return false }
    }

    private func retainedDraftForVoice(_ store: LocalJournalStore) async throws -> JournalDraft {
        guard let snapshot else { throw JournalError.storage }
        let retained = draft.flatMap { $0.text.isEmpty && $0.voiceInsertions?.isEmpty != false ? nil : $0 }
        let value = retained ?? JournalDraft(day: selectedDay, section: section, text: text,
            sourceHash: snapshot.sourceHash, effectiveTemplate: snapshot.markdown)
        try await store.saveDraft(value)
        draft = value
        return value
    }

    func prepareVoiceInsertion() async {
        guard !bodyRecoveryUnavailable, !isBusy, !isPending, !isInsertionUnknown, let store, let voice else { return }
        isBusy = true
        defer { isBusy = false }
        guard await flushDocumentInput() else { return }
        await persistence?.value
        do {
            let value = try await retainedDraftForVoice(store)
            _ = await voice.prepareInsertion(draft: value)
        } catch { show(error) }
    }

    func refreshVoiceInsertionSource() async {
        guard !isBusy, !isPending, !isInsertionUnknown, let store, let voice,
              let target = voice.recording?.target, target.day == selectedDay,
              target.section.utf8.elementsEqual(section.utf8) else { return }
        guard voice.lockSourceRefresh() else { return }
        defer { voice.unlockInsertion() }
        await reviewCurrentSource()
        guard !isBusy, errorMessage == nil else { return }
        isBusy = true
        defer { isBusy = false }
        do {
            let value = try await retainedDraftForVoice(store)
            await voice.refreshSource(draft: value, locked: true)
        } catch { show(error) }
    }

    func insertVoice(_ candidate: VoiceInsertionCandidate) async -> Bool {
        guard candidate.target.day == selectedDay, candidate.target.section.utf8.elementsEqual(section.utf8) else {
            show(VoiceError.invalidTarget); return false
        }
        guard !isBusy, !isPending, !isInsertionUnknown, let store, let voice,
              voice.lockInsertion(candidate) else { return false }
        isBusy = true
        defer { isBusy = false; voice.unlockInsertion() }
        guard await flushDocumentInput() else { return false }
        await persistence?.value
        guard await voice.flushCorrection() else { return false }
        isInsertionUnknown = true
        revision += 1
        do {
            _ = try await store.insertVoice(candidate)
            try await load(selectedDay)
            await voice.refreshInsertionState()
            status = "已插入输入区 · 核对后点保存这段"
            return true
        } catch {
            // Query disk before allowing any old autosave or visible editor to resume.
            do {
                let pending = try await store.pendingVoiceInsertion(selectedDay)
                let actual = try await store.open(selectedDay)
                draft = actual.draft; text = actual.draft?.text ?? ""; snapshot = actual
                isInsertionUnknown = pending != nil
                abandonableInsertion = try? await store.abandonableVoiceInsertion(selectedDay)
                await voice.refreshInsertionState()
            } catch { isInsertionUnknown = true }
            show(error)
            status = "插入结果尚待核对，请重试原操作"
            return false
        }
    }

    func retryVoiceInsertion() async {
        guard !isBusy, isInsertionUnknown else { return }
        isBusy = true
        defer { isBusy = false }
        guard await flushDocumentInput() else { return }
        do { try await load(selectedDay); await voice?.refreshInsertionState() } catch { show(error) }
    }

    func abandonVoiceInsertion() async {
        guard !isBusy, isInsertionUnknown, let candidate = abandonableInsertion, let store else { return }
        isBusy = true
        defer { isBusy = false }
        guard await flushDocumentInput() else { return }
        do {
            try await store.abandonVoiceInsertion(candidate)
            try await load(candidate.target.day)
            voice?.invalidatePreview()
            await voice?.refreshInsertionState()
            status = "已放弃未写入的插入确认。原输入、校正文字和音频保留，请重新预览。"
        } catch { show(error) }
    }

    private func load(_ day: JournalDay, selecting: Bool = false, beforeApply: (() async throws -> Void)? = nil, sourceLabelOverride: String? = nil) async throws {
        guard let store else { throw JournalError.storage }
        guard await flushCloudMerge(), await flushBodyMerge() else { throw JournalError.storage }
        var recoveryError: Error?
        var unknown = false
        if !isOriginalSource {
            do { try await store.recoverVoiceInsertion(day) } catch { recoveryError = error; unknown = true }
        }
        if !unknown { do { _ = try await store.recover(day) } catch { recoveryError = error } }
        var recoveredBody: BodyEdit?
        var bodyRecoveryError: Error?
        let opening = try? await store.open(day)
        let cloudPending = (try? await store.cloudWorkPending(day)) ?? true
        let nextAIPending = (try? await store.hasAIResultPending(day)) ?? true
        let nextAIResults = (try? await store.pendingAIResults(day: day)) ?? []
        let heldByCloud = (try? await store.bodyHeldByCloud(day)) ?? true
        let readable = await store.bodyRecoveryState(day).isReadable
        if readable && opening?.cloudConflict == nil && !cloudPending && !heldByCloud { do { recoveredBody = try await store.recoverBodyEdit(day) } catch { bodyRecoveryError = error } }
        else if !readable { bodyRecoveryError = JournalError.storage }
        let value: JournalSnapshot
        let nextSourceID: String?
        do {
            value = try await store.open(day)
            nextSourceID = try await store.bodySourceID(day)
        } catch {
            if beforeApply == nil, await restoreUnavailableOriginal(day, error: error) { return }
            throw error
        }
        let recovery = await store.bodyRecoveryState(day)
        var nextCloudDraft: CloudRecoveryDraft?
        var nextCloudBackups: [CloudRecoveryDraft] = []
        var cloudReadFailed = false, nextBackupWarning: String?
        do { nextCloudDraft = try await store.retainedCloudDraft(day) } catch { cloudReadFailed = true }
        do {
            let backups = try await store.cloudBackupState(day)
            nextCloudBackups = backups.drafts; nextBackupWarning = backups.warning
        } catch { nextBackupWarning = CloudRecoveryError.storage.localizedDescription }
        var retainedBody = recovery.edit
        if let recoveredBody, let pending = retainedBody, pending.id != recoveredBody.id,
           pending.before == recoveredBody.before, value.sourceHash == SourceHash.digest(recoveredBody.content) {
            retainedBody = BodyEdit(day: day, before: recoveredBody.content, content: pending.content, sourceID: pending.sourceID)
            try await store.retainBodyEdit(retainedBody!)
        }
        let retainedMerge = recovery.merge
        let bodyOperation = recovery.hasOperation
        let unavailable = !recovery.isReadable
        let retainedIdentityChanged = retainedBody != nil && retainedBody?.sourceID != nextSourceID
        let label = try await label(for: value, store: store, originalLabel: sourceLabelOverride)
        let headings = isOriginalSource ? (try? MarkdownSectionAppender.headings(in: Data(value.markdown.utf8))) ?? []
            : try MarkdownSectionAppender.headings(in: Data(value.markdown.utf8))
        let nextSection = value.draft?.section ?? defaultSection(headings)
        let nextAbandonableInsertion = unknown ? (try? await store.abandonableVoiceInsertion(day)) : nil
        if selecting { try await store.select(day) }
        if beforeApply != nil {
            guard !unavailable, bodyRecoveryError == nil, recoveryError == nil else { throw JournalError.storage }
            try await beforeApply?()
        }
        // All throwing reads and the optional selection commit precede visible state replacement.
        snapshot = value
        sourceID = retainedBody?.sourceID ?? nextSourceID; sourceUnavailable = false
        previousRefreshOrigin = nil
        bodySourceIssue = nil; bodyReplacementRevision += 1
        selectedDay = day
        aiResultPending = nextAIPending
        localAIResultPreviews = nextAIResults
        cloudConflict = value.cloudConflict; cloudRecoveryPending = cloudPending
        bodyHeldForCloud = heldByCloud || value.cloudHoldFailed; cloudBackupWarning = nextBackupWarning
        cloudDraft = nextCloudDraft; cloudBackups = nextCloudBackups
        cloudMergeText = nextCloudDraft.map { String(decoding: $0.content, as: UTF8.self) } ?? ""
        cloudLocalVersions = [recovery.edit?.content, recovery.operation?.content, recovery.merge?.content].compactMap { $0 }
        cloudReviewPresented = false; cloudRecoveryUnreadable = cloudReadFailed
        cloudError = cloudReadFailed ? CloudRecoveryError.storage.localizedDescription : nil; cloudMergeDirty = false
        markdown = unavailable || value.cloudConflict != nil || cloudPending ? value.markdown : retainedBody.map { String(decoding: $0.content, as: UTF8.self) } ?? value.markdown
        bodyBase = retainedBody.map { $0.before } ?? (value.sourceHash == nil ? nil : Data(value.markdown.utf8))
        bodyEdit = retainedBody
        bodyDirty = !unavailable && retainedBody != nil && value.cloudConflict == nil && !cloudPending
        bodyRecoveryUnavailable = unavailable
        bodyRecoveryPending = bodyOperation || bodyRecoveryError != nil
        bodyError = unavailable ? "正文恢复记录暂时无法读取；已保存版本可只读查看和导出。原恢复文件保留，请修复后重新读取。" : bodyRecoveryError.map(bodyErrorDescription)
            ?? (retainedIdentityChanged ? OriginalVaultError.changedIdentity.localizedDescription : retainedBody == nil ? nil : "上次正文修订尚未保存，请重试或核对双方版本。")
        if let bodyRecoveryError { classifyBodyError(bodyRecoveryError) }
        if value.cloudConflict != nil { recordBodyError(OriginalVaultError.conflict) }
        if cloudPending { bodyError = CloudRecoveryError.pending.localizedDescription }
        if bodyHeldForCloud && !cloudPending && value.cloudConflict == nil { bodyError = CloudRecoveryError.heldBody.localizedDescription }
        if retainedIdentityChanged { bodySourceIssue = .identity }
        bodyStatus = unavailable ? "只读 · 已保存版本" : retainedMerge != nil ? "合并尚未确认保存" : bodyError != nil ? "正文保存尚待核对" : (value.isSaved ? savedBodyStatus : "")
        bodyConflict = nil
        bodyMerge = retainedMerge
        bodyMergeDirty = false; bodyMergeError = nil
        bodyMergeText = retainedMerge.map { String(decoding: $0.content, as: UTF8.self) } ?? ""
        templateLabel = retainedIdentityChanged ? "原目录 · 本机恢复草稿（原位置已变化）" : label
        draft = value.draft
        text = value.draft?.text ?? ""
        section = nextSection
        isSaved = value.isSaved
        isPending = value.isPending
        isInsertionUnknown = unknown
        abandonableInsertion = nextAbandonableInsertion
        status = value.draft == nil ? (value.isSaved ? savedBodyStatus : "") : "草稿已保留在手机 · 尚未写入日记"
        errorMessage = nil
        if let recoveryError { show(recoveryError); status = "上次保存尚待核对，请重试" }
        if isOriginalReadOnly { bodyStatus = "只读 · 原文件"; status = bodyStatus }
    }

    /// Local recovery remains viewable without treating it as a current source.
    private func restoreUnavailableOriginal(_ day: JournalDay, error: Error) async -> Bool {
        guard isOriginalSource, let store else { return false }
        let recovery = await store.bodyRecoveryState(day)
        guard recovery.isReadable, let edit = recovery.edit ?? recovery.merge?.conflict.edit ?? recovery.operation else {
            return await restoreCloudEvidence(day, error: error)
        }
        selectedDay = day; sourceID = edit.sourceID; sourceUnavailable = true; needsFolder = false
        previousRefreshOrigin = nil
        bodyReplacementRevision += 1
        markdown = String(decoding: edit.content, as: UTF8.self); bodyBase = edit.before; bodyEdit = edit
        snapshot = recovery.localSnapshot
        bodyDirty = true; bodyRecoveryPending = recovery.hasOperation; bodyRecoveryUnavailable = false
        recordBodyError(error); bodyStatus = "本机恢复草稿 · 尚未写入原文件"
        bodyMerge = recovery.merge; bodyConflict = nil; bodyMergeDirty = false; bodyMergeError = nil
        bodyMergeText = recovery.merge.map { String(decoding: $0.content, as: UTF8.self) } ?? ""
        isSaved = false; isPending = false; isInsertionUnknown = false; text = ""; draft = nil
        templateLabel = sourceLabel + " · 本机恢复草稿"; status = ""; errorMessage = nil
        await restoreLocalCloudState(day)
        return true
    }

    private func restoreCloudEvidence(_ day: JournalDay, error: Error) async -> Bool {
        guard let store, let retained = try? await store.retainedCloudDraft(day) else { return false }
        selectedDay = day; sourceID = retained.review.sourceID; sourceUnavailable = true; needsFolder = false
        previousRefreshOrigin = nil; bodyReplacementRevision += 1
        snapshot = retained.localSnapshot; markdown = retained.localSnapshot.markdown
        bodyBase = nil; bodyEdit = nil; bodyDirty = false
        let recovery = await store.bodyRecoveryState(day)
        bodyRecoveryPending = recovery.hasOperation; bodyRecoveryUnavailable = !recovery.isReadable
        bodyMerge = recovery.merge; bodyConflict = nil; bodyMergeDirty = false; bodyMergeError = nil
        bodyMergeText = recovery.merge.map { String(decoding: $0.content, as: UTF8.self) } ?? ""
        await restoreLocalCloudState(day)
        recordBodyError(error); bodyStatus = "只读 · 本机版本备份（原文件暂不可读）"
        isSaved = false; isPending = false; isInsertionUnknown = false; text = ""; draft = nil
        templateLabel = bodyStatus; status = ""; errorMessage = nil
        return true
    }

    private func restoreLocalCloudState(_ day: JournalDay) async {
        cloudConflict = nil; cloudDraft = nil; cloudMergeText = ""; cloudBackups = []; cloudLocalVersions = []
        cloudReviewPresented = false; cloudMergeDirty = false; cloudError = nil; cloudBackupWarning = nil
        cloudRecoveryUnreadable = false; bodyHeldForCloud = false; cloudRecoveryPending = false
        guard let store else { return }
        do { cloudDraft = try await store.retainedCloudDraft(day) }
        catch { cloudRecoveryUnreadable = true; cloudError = CloudRecoveryError.storage.localizedDescription }
        cloudMergeText = cloudDraft.map { String(decoding: $0.content, as: UTF8.self) } ?? ""
        do { let backups = try await store.cloudBackupState(day); cloudBackups = backups.drafts; cloudBackupWarning = backups.warning }
        catch { cloudBackupWarning = CloudRecoveryError.storage.localizedDescription }
        cloudRecoveryPending = (try? await store.cloudWorkPending(day)) ?? true
        bodyHeldForCloud = (try? await store.bodyHeldByCloud(day)) ?? true
        let body = await store.bodyRecoveryState(day)
        cloudLocalVersions = [body.edit?.content, body.operation?.content, body.merge?.content].compactMap { $0 }
    }

    func exportRetainedBody() async -> MarkdownDocument? {
        guard isOriginalSource, bodyDirty || sourceUnavailable || bodySourceIssue == .cloudConflict,
              !isBusy, !bodyRecoveryUnavailable else { return nil }
        isBusy = true
        defer { isBusy = false }
        await bodyPersistence?.value
        do {
            if bodyDirty { try await retainVisibleBody() }
        } catch { recordBodyError(error) }
        return MarkdownDocument(day: selectedDay, data: Data(markdown.utf8), recoveryDraft: true)
    }

    private func label(for value: JournalSnapshot, store: LocalJournalStore, originalLabel: String? = nil) async throws -> String {
        if isOriginalSource {
            let source = originalLabel ?? sourceLabel
            return value.isSaved ? source + " · 原文件" : source + " · 原日模板预览（尚未创建文件）"
        }
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
        bodyBase = value.sourceHash == nil ? nil : Data(value.markdown.utf8)
        bodyStatus = savedBodyStatus
        isSaved = value.isSaved
        templateLabel = "已保存日记 · 原模板保留"
        draft = nil
        text = ""
        isPending = false
    }

    private var savedBodyStatus: String { isOriginalSource ? "已保存在此设备" : "已保存在手机" }

    private func bodyErrorDescription(_ error: Error) -> String {
        if error as? JournalError == .changedSource {
            return "文件已被其他编辑器更新，本机也有尚未保存的修改。请核对两个版本，再选择使用最新文件、保留本机编辑或手动合并。"
        }
        return (error as? CloudRecoveryError)?.localizedDescription
            ?? (error as? OriginalVaultError)?.localizedDescription
            ?? (error as? VaultReadError)?.localizedDescription
            ?? (error as? JournalError ?? .storage).localizedDescription
    }

    private func classifyBodyError(_ error: Error) {
        if error is VaultReadError { bodySourceIssue = .unavailable; return }
        switch error as? OriginalVaultError {
        case .conflict: bodySourceIssue = .cloudConflict
        case .unavailable, .layout, .ambiguousDate: bodySourceIssue = .unavailable
        case .changedIdentity: bodySourceIssue = .identity
        default:
            if error as? JournalError == .changedSource { bodySourceIssue = .contentConflict }
        }
    }

    private func recordBodyError(_ error: Error) {
        classifyBodyError(error)
        bodyError = bodyErrorDescription(error)
        sourceBodyMessage = error is OriginalVaultError || error is VaultReadError ? bodyError : nil
    }

    private func clearReadableSourceState() {
        sourceUnavailable = false
        if let sourceAccessMessage, errorMessage == sourceAccessMessage { errorMessage = nil }
        sourceAccessMessage = nil
        if bodySourceIssue == .unavailable || bodySourceIssue == .identity || bodySourceIssue == .cloudConflict {
            bodySourceIssue = nil
            if bodyError == sourceBodyMessage { bodyError = nil }
        }
        if let refreshErrorMessage, bodyError == refreshErrorMessage { bodyError = nil }
        refreshErrorMessage = nil; sourceBodyMessage = nil
    }

    private func show(_ error: Error) {
        if isOriginalSource || usesOriginalCollection { classifyBodyError(error) }
        errorMessage = (error as? VoiceError)?.localizedDescription ?? bodyErrorDescription(error)
        sourceAccessMessage = error is OriginalVaultError || error is VaultReadError ? errorMessage : nil
    }

    nonisolated private static func supportDirectory() -> URL {
        var support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        #if DEBUG
        if let value = ProcessInfo.processInfo.environment["RIJI_TEST_CONTAINER"], let id = UUID(uuidString: value) {
            support.appendPathComponent("UITests-\(id.uuidString)")
        }
        #endif
        return support.appendingPathComponent("Riji")
    }

    func sourcePickerFailed(_ error: Error) {
        guard (error as NSError).code != NSUserCancelledError else { return }
        errorMessage = "未能打开文件夹。原连接和日记已保留，请重新选择可访问的文件夹。"
    }

    private func beginSourceChange() -> Bool {
        guard !isBusy, !isPending, !isInsertionUnknown, !sourceTransitionInProgress else { return false }
        sourceTransitionInProgress = true; isBusy = true
        northStar.invalidate()
        return true
    }

    private func endSourceChange() { sourceTransitionInProgress = false; isBusy = false }

    func northStarReaders(account: String) -> NorthStarReaders? {
        guard !sourceTransitionInProgress, !needsFolder, let store else { return nil }
        let source = sourceIdentity, vault = vaultStore
        return NorthStarReaders(source: source, account: account, original: isOriginalSource,
            goals: { try await store.currentNorthStars() },
            entries: { try await vault.northStarEntries(path: $0, identity: source) },
            text: { try await vault.northStarText(path: $0, identity: source) })
    }

    func selectOriginalFolder(_ url: URL) async {
        guard beginSourceChange() else { return }
        defer { endSourceChange() }
        do {
            let candidate = try await vaultStore.connectOriginal(url)
            if isOriginalSource, candidate.original.identity == sourceIdentity {
                try await reauthorizeOriginal(candidate)
                return
            }
            guard await prepareSourceChange() else { return }
            let access = candidate.original, next = try await vaultStore.homeStore(candidate.original)
            try await changeStore(next, identity: access.identity, label: "原目录 · " + access.name) {
                try await self.vaultStore.commitOriginal(candidate)
            }
        } catch { show(error) }
    }

    private func reauthorizeOriginal(_ candidate: VaultOriginalCandidate) async throws {
        await bodyPersistence?.value
        guard await flushBodyMerge() else { throw JournalError.storage }
        if bodyDirty { try await retainVisibleBody() }
        if let sourceID { try candidate.original.validateRetainedSource(sourceID, day: selectedDay) }
        let next = try await vaultStore.homeStore(candidate.original)
        try await vaultStore.commitOriginal(candidate)
        store = next; sourceUnavailable = false
        sourceLabel = "原目录 · " + candidate.original.name
        // A changed or missing file still fails the ordinary live commit checks.
        guard await flushDocumentInput() else { return }
        try await load(selectedDay)
    }

    func useLocalDiaries() async {
        guard beginSourceChange() else { return }
        defer { endSourceChange() }
        guard await prepareSourceChange() else { return }
        do { try await changeStore(storageFactory(), identity: "local", label: "App 本地日记") }
        catch { show(error) }
    }

    func useOriginalDiaries() async {
        guard beginSourceChange() else { return }
        defer { endSourceChange() }
        do {
            guard let candidate = try await vaultStore.restoredOriginalCandidate() else { needsFolder = true; return }
            if isOriginalSource, candidate.original.identity == sourceIdentity {
                try await reauthorizeOriginal(candidate)
                return
            }
            guard await prepareSourceChange() else { return }
            let access = candidate.original
            try await changeStore(vaultStore.homeStore(access), identity: access.identity, label: "原目录 · " + access.name)
        } catch { show(error) }
    }

    private func prepareSourceChange() async -> Bool {
        guard sourceTransitionInProgress, !isPending, !isInsertionUnknown else { return false }
        if !isReady { return true }
        guard await flushDocumentInput() else { return false }
        await persistence?.value
        if let draft, let store {
            do { try await store.saveDraft(draft) } catch { show(error); return false }
        }
        return true
    }

    private func changeStore(_ next: LocalJournalStore, identity: String, label: String, commit: (() async throws -> Void)? = nil) async throws {
        let day = try await next.initialDay(now: now())
        _ = try await next.open(day) // Validate before replacing visible input.
        let previous = store, previousOriginal = isOriginalSource
        store = next; isOriginalSource = next.isOriginal
        do { try await load(day, beforeApply: commit, sourceLabelOverride: label) }
        catch { store = previous; isOriginalSource = previousOriginal; throw error }
        // Only a committed source change retires its visible AI actions. Disk recovery
        // stays with the previous store, including any operation still finishing there.
        aiActionID = UUID(); aiRecoveryID = UUID(); aiActionInFlight = nil
        aiResultPreview = nil; aiResultStatus = nil; aiResultPreparedForConfirmation = nil
        sourceIdentity = identity; sourceLabel = label; needsFolder = false
        today = JournalDay(date: now(), timeZone: next.timeZone)
        workflows = isOriginalSource ? nil : WorkflowViewModel(store: next)
        voice = isOriginalSource ? nil : VoiceViewModel(store: next, services: voiceServices)
        voice?.diaryEditingBlocked = { [weak self] in self?.isBusy != false || self?.bodyRecoveryUnavailable != false || self?.isPending != false || self?.isInsertionUnknown != false }
    }

    func originalDay(for entry: VaultEntry) async -> JournalDay? {
        guard isOriginalSource else { return nil }
        do { return try await store?.originalDay(path: entry.path) }
        catch { show(error); return nil }
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
        let journals = documents.appendingPathComponent("Journals"), state = support.appendingPathComponent("Riji")
        #if DEBUG
        if let harness = RecoveryControlTestHarness.shared {
            try harness.seed(journals: journals, state: state)
            return try LocalJournalStore(journalDirectory: journals, stateDirectory: state,
                timeZone: harness.navigationTimeZone ?? .current, failure: { try harness.check($0, $1) })
        }
        #endif
        return try LocalJournalStore(journalDirectory: journals, stateDirectory: state)
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

struct DiarySectionChoice: Identifiable {
    let title: String
    var id: Data { Data(title.utf8) }
    init(_ title: String) { self.title = title }
}

extension DiaryViewModel {
    var bodyInputOrigin: DiaryBodyInputOrigin {
        DiaryBodyInputOrigin(day: selectedDay, identity: sourceIdentity, sourceID: sourceID,
            replacementRevision: bodyReplacementRevision, base: bodyBase, visible: Data(markdown.utf8),
            snapshot: snapshot, wasEditable: canEditBody)
    }
    private var unchangedBodyContent: Data { bodyBase ?? Data((snapshot?.markdown ?? markdown).utf8) }
    var canReviewBodyVersions: Bool {
        !cloudBlocksEditing && bodySourceIssue != .cloudConflict && bodySourceIssue != .unavailable && bodySourceIssue != .identity
    }
    var needsSourceReauthorization: Bool { bodySourceIssue == .unavailable || bodySourceIssue == .identity }
    var canEditLegacy: Bool { !isOriginalSource && isReady && !bodyRecoveryUnavailable && !isBusy && !isPending && !isInsertionUnknown }
    var canEditBody: Bool { !aiResultPending && !bodyHeldForCloud && !cloudBlocksEditing && !cloudReviewPresented && !isCheckingResumedSource && !isOriginalReadOnly && !sourceUnavailable && isReady && !bodyRecoveryUnavailable && !isBusy && !isPending && !isInsertionUnknown && bodyConflict == nil }
    var canEditBodyMerge: Bool { !isOriginalReadOnly && !bodyRecoveryUnavailable && !isBusy && bodyConflict != nil && canReviewBodyVersions }
    var hasRetainedBodyMerge: Bool { bodyMerge != nil }
    var hasLegacyDraft: Bool { !text.isEmpty || isPending || isInsertionUnknown }
    var hasAISections: Bool { markdown.contains("AI 导师") || markdown.contains("riji-record:ai") }

    func updateBody(_ value: String) { updateBody(value, for: selectedDay) }

    func updateBody(_ value: String, for day: JournalDay, origin: DiaryBodyInputOrigin? = nil) {
        guard day == selectedDay, !markdown.utf8.elementsEqual(value.utf8), admitBodyInput(value, origin: origin) else { return }
        markdown = value
        status = ""; exportVerificationDigest = ""
        bodyDirty = true
        bodySequence += 1
        let sequence = bodySequence, previous = bodyPersistence
        bodyStatus = "正在保存…"
        bodyPersistence = Task {
            await previous?.value
            guard sequence == bodySequence, day == selectedDay else { return }
            do { try await retainVisibleBody() }
            catch { bodyError = bodyErrorDescription(error); bodyStatus = "正文尚未保存" }
        }
        scheduleBodyCheckpoint()
    }

    private func admitBodyInput(_ value: String, origin: DiaryBodyInputOrigin?) -> Bool {
        guard let origin else { return canEditBody }
        guard origin.wasEditable, origin.day == selectedDay, origin.identity == sourceIdentity, origin.sourceID == sourceID,
              isReady, !isOriginalReadOnly, !bodyRecoveryUnavailable, !isBusy, !isPending, !isInsertionUnknown, bodyConflict == nil else { return false }
        if origin.replacementRevision == bodyReplacementRevision { return !cloudBlocksEditing && !cloudReviewPresented && !bodyHeldForCloud }
        guard let previous = previousRefreshOrigin, previous.replacementRevision == origin.replacementRevision,
              (!cloudBlocksEditing && acceptedLateRefreshInput) || Data(value.utf8) != previous.visible else { return false }
        // Publication precedes UIKit's next update. Late genuine input still owns
        // its old base; an unchanged didEndEditing echo never restores old bytes.
        bodyBase = previous.base; snapshot = previous.snapshot; bodyEdit = nil
        acceptedLateRefreshInput = true
        bodyReplacementRevision += 1
        if cloudBlocksEditing { bodyHeldForCloud = true; recordBodyError(OriginalVaultError.conflict) }
        else { recordBodyError(JournalError.changedSource) }
        return true
    }

    private func scheduleBodyCheckpoint() {
        bodyQuietTask?.cancel()
        bodyQuietTask = Task {
            do { try await Task.sleep(nanoseconds: bodyQuietNanoseconds) } catch { return }
            _ = await flushBody()
        }
        if bodyCheckpointTask == nil {
            bodyCheckpointTask = Task {
                do { try await Task.sleep(nanoseconds: bodyCheckpointNanoseconds) } catch { return }
                _ = await flushBody()
            }
        }
    }

    /// Buffers are replaceable; immutable history is written only after a quiet period or a checkpoint.
    private func retainVisibleBody() async throws {
        guard let store else { throw JournalError.storage }
        let content = Data(markdown.utf8)
        if bodyEdit?.content != content || bodyEdit?.before != bodyBase {
            bodyEdit = BodyEdit(day: selectedDay, before: bodyBase, content: content, sourceID: sourceID)
        }
        guard let bodyEdit else { throw JournalError.storage }
        try await store.retainBodyEdit(bodyEdit)
        if cloudConflict != nil || bodyHeldForCloud { try await store.observeCloudConflict(selectedDay); bodyHeldForCloud = true }
    }

    @discardableResult func flushBody() async -> Bool {
        guard !bodyRecoveryUnavailable else { return false }
        if cloudBlocksEditing || bodyHeldForCloud {
            await bodyPersistence?.value
            if bodyDirty { do { try await retainVisibleBody() } catch { recordBodyError(error); return false } }
            return !bodyDirty && !bodyRecoveryPending
        }
        bodyQuietTask?.cancel(); bodyQuietTask = nil
        bodyCheckpointTask?.cancel(); bodyCheckpointTask = nil
        while true {
            await bodyPersistence?.value
            guard bodyDirty else {
                guard bodyRecoveryPending, let store else { return true }
                let day = selectedDay, sequence = bodySequence
                do {
                    _ = try await store.recoverBodyEdit(day)
                    let latest = try await store.open(day)
                    let pending = try await store.hasBodyOperation(day)
                    guard day == selectedDay else { return false }
                    bodyRecoveryPending = pending
                    if sequence == bodySequence && !bodyDirty {
                        snapshot = latest; markdown = latest.markdown
                        bodyBase = latest.sourceHash == nil ? nil : Data(latest.markdown.utf8)
                        isSaved = latest.isSaved; bodyError = nil
                        bodyStatus = bodyMerge != nil ? "合并尚未确认保存" : latest.isSaved ? savedBodyStatus : ""
                        return !pending
                    }
                    continue
                } catch { bodyError = bodyErrorDescription(error); return false }
            }
            let previous = bodyPersistence
            var success = false
            let task = Task { await previous?.value; success = await persistBody() }
            bodyPersistence = task
            await task.value
            guard success else { return false }
            // Typing during a successful write remains dirty and gets its own durable buffer.
        }
    }

    private func persistBody(confirmedMerge: Bool = false) async -> Bool {
        guard let store, bodyDirty, bodyConflict == nil || confirmedMerge else { return !bodyDirty }
        bodyWriteInProgress = true
        defer { bodyWriteInProgress = false }
        let content = Data(markdown.utf8), sequence = bodySequence
        do {
            try await retainVisibleBody()
            guard var candidate = bodyEdit else { return false }
            if !confirmedMerge, !hasRetainedBodyMerge, !bodyRecoveryPending, content == unchangedBodyContent {
                try await store.retireBodyBuffer(candidate)
                if sequence == bodySequence {
                    bodyDirty = false; bodyEdit = nil; bodyError = nil; bodySourceIssue = nil
                    bodyStatus = isSaved ? savedBodyStatus : ""
                }
                return true
            }
            if let recovered = try await store.recoverBodyEdit(candidate.day), recovered.id != candidate.id,
               candidate.before == recovered.before {
                let source = try await store.open(candidate.day)
                guard source.sourceHash == SourceHash.digest(recovered.content) else { throw JournalError.changedSource }
                candidate = BodyEdit(day: candidate.day, before: recovered.content, content: content, sourceID: candidate.sourceID)
                bodyEdit = candidate
                bodyBase = recovered.content
            }
            try await store.commitBodyEdit(candidate)
            let latest = try await store.open(candidate.day)
            guard latest.sourceHash == SourceHash.digest(candidate.content) else { throw JournalError.changedSource }
            bodyBase = candidate.content; snapshot = latest
            isSaved = true; templateLabel = isOriginalSource ? sourceLabel + " · 原文件" : "已保存日记 · 原模板保留"
            if sequence == bodySequence {
                bodyDirty = false; bodyEdit = nil; bodyError = nil; bodySourceIssue = nil; sourceUnavailable = false; bodyRecoveryPending = false; bodyStatus = bodyMerge != nil && !confirmedMerge ? "合并尚未确认保存" : savedBodyStatus
            }
            return true
        } catch {
            // Receipt cleanup can precede the final source check. Re-retain the
            // latest visible input, not a captured older candidate, before reporting failure.
            do { try await retainVisibleBody() } catch { }
            bodyRecoveryPending = (try? await store.hasBodyOperation(selectedDay)) ?? true
            bodyRecoveryUnavailable = !(await store.bodyRecoveryState(selectedDay).isReadable)
            recordBodyError(error)
            bodyStatus = "正文尚未保存 · 文字留在正文中"
            return false
        }
    }

    func reviewBodyConflict(refreshingSource: Bool = false) async {
        guard !bodyRecoveryUnavailable, !isBusy, canReviewBodyVersions, let store else { return }
        isBusy = true
        defer { isBusy = false }
        if let bodyMerge { bodyConflict = bodyMerge.conflict }
        guard await flushBodyMerge() else { return }
        await bodyPersistence?.value; await mergePersistence?.value
        do {
            let storedMerge = try await store.retainedBodyMerge(selectedDay)
            if !refreshingSource, let retained = bodyMerge ?? storedMerge {
                bodyMerge = retained; bodyConflict = retained.conflict
                bodyMergeText = String(decoding: retained.content, as: UTF8.self)
                bodyMergeDirty = true
                guard await flushBodyMerge() else { return }
                return
            }
            try await retainVisibleBody()
            guard let conflict = try await store.bodyConflict(selectedDay) else { return }
            let content = bodyMerge?.content ?? Data(markdown.utf8)
            let merge = BodyMergeDraft(conflict: conflict, content: content)
            bodyMerge = merge; bodyConflict = conflict; bodyMergeText = String(decoding: content, as: UTF8.self)
            bodyMergeDirty = true
            _ = await flushBodyMerge()
        } catch { recordBodyError(error) }
    }

    func updateBodyMerge(_ content: String) {
        guard canEditBodyMerge, let conflict = bodyConflict, let store else { return }
        bodyMergeText = content
        bodyMergeDirty = true; mergeSequence += 1
        let sequence = mergeSequence
        status = ""; exportVerificationDigest = ""
        bodyStatus = "合并尚未确认保存"
        let merge = BodyMergeDraft(conflict: conflict, content: Data(content.utf8))
        bodyMerge = merge
        let previous = mergePersistence
        mergePersistence = Task {
            await previous?.value
            do {
                try await store.retainBodyMerge(merge)
                if sequence == mergeSequence { bodyMergeDirty = false; bodyMergeError = nil }
            } catch {
                if sequence == mergeSequence { bodyMergeError = "合并文字尚未保留，请重试；当前文字和来源仍在此页。" }
            }
        }
    }

    func resolveBodyConflict(with content: String) async -> Bool {
        guard canEditBodyMerge, let store, let conflict = bodyConflict else { return false }
        updateBodyMerge(content)
        isBusy = true
        defer { isBusy = false }
        await mergePersistence?.value
        do {
            // Retry durable retention here too: a failed buffer write cannot authorize losing the merge.
            try await store.retainBodyMerge(BodyMergeDraft(conflict: conflict, content: Data(content.utf8)))
            let next = try await store.resolveBodyConflict(conflict, content: Data(content.utf8))
            previousRefreshOrigin = nil
            markdown = content; bodyEdit = next; bodyBase = next.before; bodyDirty = true
            bodySequence += 1; bodyReplacementRevision += 1
            bodyHeldForCloud = false
            let saved = await persistBody(confirmedMerge: true)
            if saved { bodyMerge = nil; bodyMergeText = ""; bodyConflict = nil; bodyMergeDirty = false; bodyMergeError = nil }
            return saved
        } catch {
            recordBodyError(error)
            return false
        }
    }

    func useLatestBodyVersion() async -> Bool {
        guard let source = bodyConflict?.source else { return false }
        return await resolveBodySelection(source)
    }

    var localBodyChoiceExplanation: String? {
        guard let conflict = bodyConflict else { return nil }
        do { try BodyMarkdown.validatePreservation(before: conflict.source, after: conflict.edit.content); return nil }
        catch { return "最新文件的属性或来源标识与本机版本不同，不能直接用本机版本覆盖。可使用最新文件，或在手动合并中保留这些标记后再确认。" }
    }

    func keepLocalBodyVersion() async -> Bool {
        guard let local = bodyConflict?.edit.content else { return false }
        if let explanation = localBodyChoiceExplanation { bodyError = explanation; return false }
        return await resolveBodySelection(local)
    }

    private func resolveBodySelection(_ content: Data) async -> Bool {
        guard canEditBodyMerge, let store, let conflict = bodyConflict else { return false }
        isBusy = true
        defer { isBusy = false }
        guard await flushBodyMerge() else { return false }
        do {
            let manual = try await store.retainedBodyMerge(selectedDay)
            let next = try await store.resolveBodyConflict(conflict, content: content)
            previousRefreshOrigin = nil
            markdown = String(decoding: content, as: UTF8.self); bodyEdit = next; bodyBase = next.before; bodyDirty = true
            bodySequence += 1; bodyReplacementRevision += 1
            bodyHeldForCloud = false
            guard await persistBody(confirmedMerge: true) else { return false }
            if let manual { try await store.retireBodyMerge(manual) }
            bodyMerge = nil; bodyMergeText = ""; bodyConflict = nil; bodyMergeDirty = false; bodyMergeError = nil
            return true
        } catch { recordBodyError(error); return false }
    }

    @discardableResult func flushBodyMerge() async -> Bool {
        await mergePersistence?.value
        guard bodyMergeDirty, let bodyMerge, let store else { return true }
        let sequence = mergeSequence
        do {
            try await store.retainBodyMerge(bodyMerge)
            if sequence == mergeSequence { bodyMergeDirty = false; bodyMergeError = nil }
            return sequence == mergeSequence
        } catch {
            bodyMergeError = "合并文字尚未保留，请重试；当前文字和来源仍在此页。"
            return false
        }
    }

    /// Every state replacement retains both independent kinds of visible input first.
    private func flushDocumentInput(allowReadOnlyNavigation: Bool = false) async -> Bool {
        guard await flushCloudMerge(), await flushBodyMerge() else { return false }
        if allowReadOnlyNavigation && cloudBlocksEditing && !bodyDirty { return true }
        if allowReadOnlyNavigation && bodyRecoveryUnavailable && !bodyDirty { return true }
        return await flushBody()
    }

    func retryBodyRecoveryRead() async {
        guard !isBusy, bodyRecoveryUnavailable else { return }
        isBusy = true
        defer { isBusy = false }
        guard let store, await store.bodyRecoveryState(selectedDay).isReadable else { return }
        if bodyDirty {
            bodyRecoveryUnavailable = false
            _ = await flushDocumentInput()
            return
        }
        do { try await load(selectedDay) }
        catch { bodyError = bodyErrorDescription(error) }
    }

    func exportSavedBodyVersion() async -> MarkdownDocument? {
        guard bodyRecoveryUnavailable, let store else { return nil }
        let day = selectedDay
        while isBusy {
            do { try await Task.sleep(nanoseconds: 5_000_000) } catch { return nil }
        }
        isBusy = true
        defer { isBusy = false }
        do { return try await MarkdownDocument(day: day, data: store.exportMarkdown(day), onlySavedVersion: true) }
        catch { bodyError = bodyErrorDescription(error); return nil }
    }

    func reviewCloudVersions(refresh: Bool = false) async {
        guard !isBusy, let store, isOriginalSource else { return }
        isBusy = true
        defer { isBusy = false }
        await bodyPersistence?.value
        if bodyDirty { do { try await retainVisibleBody() } catch { cloudError = bodyErrorDescription(error) } }
        let inMemory = cloudDraft?.review.day == selectedDay ? cloudDraft : nil
        let retained = (try? await store.retainedCloudDraft(selectedDay)) ?? inMemory
        let untouched = (inMemory ?? retained).map { $0.content == $0.review.current } ?? true
        let pending = (try? await store.cloudConfirmationPending(selectedDay)) ?? true
        let fresh = refresh || retained == nil || (untouched && !pending)
        do {
            let prepared = try await fresh ? store.prepareCloudReview(selectedDay) : CloudReviewState(draft: retained!, warning: nil)
            let preserveInput = !untouched || pending
            let next = CloudRecoveryDraft(review: prepared.draft.review, content: preserveInput ? inMemory?.content ?? prepared.draft.content : prepared.draft.content)
            cloudDraft = next; cloudMergeText = String(decoding: next.content, as: UTF8.self)
            cloudReviewPresented = true; cloudError = prepared.warning; cloudRecoveryUnreadable = false
            cloudMergeDirty = true
        } catch {
            cloudError = bodyErrorDescription(error)
            if let retained { cloudDraft = retained; cloudMergeText = String(decoding: retained.content, as: UTF8.self); cloudReviewPresented = true }
        }
        if cloudReviewPresented { _ = await flushCloudMerge() }
        do { let backups = try await store.cloudBackupState(selectedDay); cloudBackups = backups.drafts; cloudBackupWarning = backups.warning }
        catch { cloudBackupWarning = CloudRecoveryError.storage.localizedDescription }
        let body = await store.bodyRecoveryState(selectedDay)
        cloudLocalVersions = [body.edit?.content, body.operation?.content, body.merge?.content].compactMap { $0 }
        cloudRecoveryPending = (try? await store.cloudWorkPending(selectedDay)) ?? true
    }

    @discardableResult func applyCloudDifference(reviewID: UUID, expected: Data, result: Data) -> Bool {
        guard !isBusy, cloudDraft?.review.id == reviewID, Data(cloudMergeText.utf8) == expected,
              result.count <= CloudReview.maximumBytes, let text = String(data: result, encoding: .utf8) else { return false }
        if result != expected { updateCloudMerge(text) }
        return true
    }

    func updateCloudMerge(_ content: String) {
        guard !isBusy, let draft = cloudDraft, let store else { return }
        cloudMergeText = content; cloudMergeDirty = true; cloudSequence += 1
        let sequence = cloudSequence
        let next = CloudRecoveryDraft(review: draft.review, content: Data(content.utf8))
        cloudDraft = next
        let previous = cloudPersistence
        previous?.cancel()
        cloudPersistence = Task {
            await previous?.value
            do { try await Task.sleep(nanoseconds: cloudDebounceNanoseconds) } catch { return }
            guard sequence == cloudSequence, !Task.isCancelled else { return }
            do {
                try await store.retainCloudDraft(next)
                if sequence == cloudSequence { cloudMergeDirty = false; cloudError = nil }
            } catch { if sequence == cloudSequence { cloudError = bodyErrorDescription(error) } }
        }
    }

    @discardableResult func flushCloudMerge() async -> Bool {
        cloudPersistence?.cancel()
        await cloudPersistence?.value
        while cloudMergeDirty, let cloudDraft, let store {
            let sequence = cloudSequence
            do {
                try await store.retainCloudDraft(cloudDraft)
                if sequence == cloudSequence { cloudMergeDirty = false; cloudError = nil }
            } catch { cloudError = bodyErrorDescription(error); return false }
        }
        return true
    }

    func closeCloudReview() async -> Bool {
        guard !isBusy else { return false }
        isBusy = true
        defer { isBusy = false }
        guard await flushCloudMerge() else { return false }
        cloudReviewPresented = false
        return true
    }

    func confirmCloudVersion(_ content: Data? = nil, retry: Bool = false, keepCurrent: Bool = false) async -> Bool {
        guard !isBusy, let store, let draft = cloudDraft, !isOriginalReadOnly else { return false }
        guard await flushCloudMerge() else { return false }
        isBusy = true
        defer { isBusy = false }
        do {
            if keepCurrent { try await store.keepReviewedCloudCurrent(draft) }
            else if retry { try await store.retryCloudResolution(selectedDay) }
            else {
                let choice = CloudRecoveryDraft(review: draft.review, content: content ?? Data(cloudMergeText.utf8))
                try await store.confirmCloudResolution(choice)
            }
            try await load(selectedDay)
            cloudReviewPresented = false
            return true
        } catch {
            cloudRecoveryPending = (try? await store.cloudWorkPending(selectedDay)) ?? true
            cloudError = bodyErrorDescription(error)
            return false
        }
    }

    func needsOriginalVoiceTarget(_ target: VoiceTarget) -> Bool {
        selectedDay != target.day || !section.utf8.elementsEqual(target.section.utf8)
    }

    func dismissBodyConflict() async -> Bool {
        guard !isBusy else { return false }
        isBusy = true
        defer { isBusy = false }
        guard await flushBodyMerge() else { return false }
        bodyConflict = nil
        return true
    }

    func closeBodyConflict() { guard !isBusy else { return }; bodyConflict = nil }

    func retryLegacySave() async {
        guard !isBusy, !isInsertionUnknown, let store else { return }
        isBusy = true
        defer { isBusy = false }
        guard await flushDocumentInput() else { return }
        do { _ = try await store.recover(selectedDay); try await load(selectedDay) }
        catch { show(error) }
    }

    /// Workflows can write the daily file. Flush before presentation and reload after dismissal.
    func prepareDirectory() async -> Bool {
        guard !isBusy, !isPending, !isInsertionUnknown else { return false }
        isBusy = true
        defer { isBusy = false }
        return await flushDocumentInput(allowReadOnlyNavigation: true)
    }

    func directoryClosed() async {
        guard !isBusy else { return }
        _ = await navigate(to: selectedDay)
    }

    var hasVoiceCommitRecovery: Bool {
        guard let recording = voice?.recording else { return false }
        return draft?.voiceInsertions?.contains { $0.recordingID == recording.id } == true && hasLegacyDraft
    }

    func retryVoiceDocumentCommit() async -> Bool {
        guard hasVoiceCommitRecovery else { return false }
        await save()
        return !hasLegacyDraft && errorMessage == nil
    }

    func insertVoiceIntoDocument(_ candidate: VoiceInsertionCandidate) async -> Bool {
        guard await insertVoice(candidate) else { return false }
        await save()
        return !isPending && text.isEmpty && errorMessage == nil
    }
}

#if DEBUG
/// Explicit UI-test fixtures are confined to the existing UUID namespace.
final class RecoveryControlTestHarness: ObservableObject, @unchecked Sendable {
    static let shared: RecoveryControlTestHarness? = {
        let environment = ProcessInfo.processInfo.environment
        guard let name = environment["RIJI_TEST_CONTAINER"], UUID(uuidString: name) != nil,
              let scenario = environment["RIJI_TEST_BODY_RECOVERY"], ["merge", "legacy", "protected", "corrupt", "navigation", "navigation-failure"].contains(scenario) else { return nil }
        return RecoveryControlTestHarness(scenario: scenario)
    }()
    let scenario: String
    @Published private(set) var reached = false
    private let condition = NSCondition()
    private var released = false
    private var fired = false
    private(set) var seeded = false
    init(scenario: String) { self.scenario = scenario }

    var navigationTimeZone: TimeZone? {
        guard scenario == "navigation", let name = ProcessInfo.processInfo.environment["RIJI_TEST_NAVIGATION_ZONE"],
              ["America/Sao_Paulo", "Pacific/Apia"].contains(name) else { return nil }
        return TimeZone(identifier: name)
    }

    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        if scenario == "navigation-failure", purpose == .bodyDiary, stage == .beforeReplace { throw JournalError.storage }
        let matches = scenario == "merge" ? purpose == .bodyResolution && stage == .afterReplace : purpose == .bodyDiary && stage == .beforeReplace
        guard ["merge", "legacy"].contains(scenario), matches else { return }
        condition.lock(); defer { condition.unlock() }
        guard !fired else { return }
        fired = true
        DispatchQueue.main.async { self.reached = true }
        while !released { condition.wait() }
        throw JournalError.storage
    }

    func release() {
        condition.lock(); released = true; condition.broadcast(); condition.unlock()
    }

    func seed(journals: URL, state: URL) throws {
        let marker = state.appendingPathComponent("recovery-control-test.json")
        guard !FileManager.default.fileExists(atPath: marker.path) else { return }
        let navigationDay = navigationTimeZone?.identifier == "America/Sao_Paulo" ? "2018-11-05" : "2011-12-31"
        let day = try JournalDay(navigationTimeZone == nil ? "2026-09-12" : navigationDay)
        if scenario.hasPrefix("navigation") {
            try seedNavigation(journals: journals, state: state, day: day)
            try JSONEncoder().encode(scenario).write(to: marker)
            seeded = true
            return
        }
        let base = Data((scenario == "protected" ? "---\r\nunknown: keep\r\n---\r\n# Synthetic\r\n## Notes\r\nBody\r\n" : "# Synthetic recovery\n## Notes\nBase\n").utf8)
        for url in [journals, state.appendingPathComponent("drafts"), state.appendingPathComponent("body-drafts"), state.appendingPathComponent("body-merges")] {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        }
        let encoder = JSONEncoder()
        let source = scenario == "merge" ? base + Data("External source\n".utf8) : base
        try source.write(to: journals.appendingPathComponent("\(day.value).md"))
        try encoder.encode(day).write(to: state.appendingPathComponent("selection.json"))
        if scenario == "merge" {
            let edit = BodyEdit(day: day, before: base, content: base + Data("Local candidate\n".utf8))
            let merge = BodyMergeDraft(conflict: BodyConflict(edit: edit, source: source), content: source + Data("Reviewed merge\n".utf8))
            try encoder.encode(edit).write(to: state.appendingPathComponent("body-drafts/\(day.value).json"))
            try encoder.encode(merge).write(to: state.appendingPathComponent("body-merges/\(day.value).json"))
        } else if scenario == "legacy" {
            let draft = JournalDraft(day: day, section: "Notes", text: "Legacy original", sourceHash: SourceHash.digest(base), effectiveTemplate: String(decoding: base, as: UTF8.self))
            try encoder.encode(draft).write(to: state.appendingPathComponent("drafts/\(day.value).json"))
        }
        if scenario == "corrupt" {
            try Data("# Another healthy day\n".utf8).write(to: journals.appendingPathComponent("2026-09-11.md"))
            try Data("{ synthetic damaged draft".utf8).write(to: state.appendingPathComponent("body-drafts/\(day.value).json"))
            let candidate = BodyEdit(day: day, before: base, content: base + Data("Pending candidate\n".utf8))
            let merge = BodyMergeDraft(conflict: BodyConflict(edit: candidate, source: source), content: base + Data("Unconfirmed merge\n".utf8))
            try encoder.encode(merge).write(to: state.appendingPathComponent("body-merges/\(day.value).json"))
        }
        try encoder.encode(scenario).write(to: marker)
        seeded = true
    }

    private func seedNavigation(journals: URL, state: URL, day: JournalDay) throws {
        let tree = state.appendingPathComponent("VaultFixture/riji/daily")
        for directory in [journals, state, tree] {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        }
        let body = "Synthetic navigation\n| Column | Value |\n| --- | --- |\n"
            + (0..<140).map { "Reading row \($0): 中文 e\u{301} 🧠\r\n" }.joined()
        try Data(body.utf8).write(to: journals.appendingPathComponent("\(day.value).md"))
        try Data("Previous synthetic day\n".utf8).write(to: journals.appendingPathComponent("2026-09-11.md"))
        try JSONEncoder().encode(day).write(to: state.appendingPathComponent("selection.json"))
        for index in 0..<60 {
            let name = String(format: "Row-%03d.md", index)
            try Data("Synthetic tree row \(index)\n".utf8).write(to: tree.appendingPathComponent(name))
        }
    }
}
#endif

extension DiaryViewModel {
    private struct AIRequest {
        let store: LocalJournalStore
        let day: JournalDay
        let identity: String
        let replacement: Int
        let bodySequence: Int
        let lifecycle: Int
        let action: UUID
        let recovery: UUID?
    }
    private struct AIRecovery {
        let previews: [AIResultPreview]
        let held: Bool
        let failed: Bool
    }
    private func beginAIRequest(recovery: Bool = false) -> AIRequest? {
        guard let store, !sourceTransitionInProgress, !Task.isCancelled else { return nil }
        if recovery {
            guard aiActionInFlight == nil else { return nil }
            aiRecoveryID = UUID()
        } else {
            aiActionID = UUID(); aiActionInFlight = aiActionID
        }
        return AIRequest(store: store, day: selectedDay, identity: sourceIdentity,
                         replacement: bodyReplacementRevision, bodySequence: bodySequence, lifecycle: lifecycleRevision,
                         action: aiActionID, recovery: recovery ? aiRecoveryID : nil)
    }
    private func currentAIRequest(_ request: AIRequest, afterLoad: Bool = false) -> Bool {
        !Task.isCancelled && !sourceTransitionInProgress && store === request.store
            && selectedDay == request.day && sourceIdentity == request.identity && lifecycleRevision == request.lifecycle
            && bodySequence == request.bodySequence && (afterLoad || bodyReplacementRevision == request.replacement)
            && aiActionID == request.action && (request.recovery == nil || request.recovery == aiRecoveryID)
    }
    private func endAIRequest(_ request: AIRequest) {
        if aiActionInFlight == request.action { aiActionInFlight = nil }
    }
    /// Read/reconcile only. Unapplied writes still require a separate fresh confirmation.
    private func collectAIRecovery(_ request: AIRequest) async -> AIRecovery {
        var previews: [AIResultPreview] = []
        do {
            previews = try await request.store.pendingAIResults(day: request.day)
            var pending: [AIResultPreview] = []
            for preview in previews {
                guard currentAIRequest(request) else { return AIRecovery(previews: [], held: true, failed: true) }
                if try await request.store.reconcileAIResult(preview) == nil { pending.append(preview) }
            }
            let held = try await request.store.hasAIResultPending(request.day)
            return AIRecovery(previews: pending, held: held, failed: false)
        } catch { return AIRecovery(previews: previews, held: true, failed: true) }
    }
    private func publishAIRecovery(_ value: AIRecovery, preview: AIResultPreview?, status: String?) {
        // No suspension between the day lock, retained records and the actionable preview.
        aiResultPending = value.held; localAIResultPreviews = value.previews
        aiResultPreview = preview
        if aiResultPreparedForConfirmation != preview { aiResultPreparedForConfirmation = nil }
        aiResultStatus = value.failed ? (status.map { $0 + "\n" } ?? "") + "本地恢复记录需要重新核对，原记录仍保留。" : status
    }
    func refreshLocalAIRecovery() async {
        guard let request = beginAIRequest(recovery: true) else { return }
        let value = await collectAIRecovery(request)
        guard currentAIRequest(request) else { return }
        publishAIRecovery(value, preview: aiResultPreview, status: aiResultStatus)
    }
    func cancelLocalAIResult(_ preview: AIResultPreview) async {
        guard let request = beginAIRequest() else { return }
        defer { endAIRequest(request) }
        do {
            try await request.store.cancelAIResult(preview)
            guard currentAIRequest(request) else { return }
            if let receipt = try await request.store.aiResultReceipt(account: preview.account, resultID: preview.result.resultId) {
                await finishAISave(receipt, preview: preview, request: request); return
            }
            let value = await collectAIRecovery(request)
            guard currentAIRequest(request) else { return }
            publishAIRecovery(value, preview: aiResultPreview?.id == preview.id ? nil : aiResultPreview, status: nil)
        } catch {
            await publishAIFailure(request, preview: preview, status: "已有写入需要核对，预览仍保留。")
        }
    }
    func restorePendingAIResults(account: String, conversationID: String? = nil) async {
        guard let request = beginAIRequest() else { return }
        defer { endAIRequest(request) }
        var retained: AIResultPreview?
        do {
            for preview in try await request.store.pendingAIResults(account: account) {
                guard currentAIRequest(request) else { return }
                guard conversationID == nil || preview.result.provenance.problemId == conversationID else { continue }
                retained = preview
                if try await request.store.reconcileAIResult(preview) == nil { break }
                retained = nil
            }
            let value = await collectAIRecovery(request)
            guard currentAIRequest(request) else { return }
            publishAIRecovery(value, preview: retained, status: nil)
        } catch { await publishAIFailure(request, preview: retained, status: "本地保存恢复需要核对，预览与日记仍保留。") }
    }
    func restoreAIResult(account: String, resultID: String) async -> Bool {
        // An invalidated lookup must stop its caller from continuing an old confirmation.
        guard let request = beginAIRequest() else { return true }
        defer { endAIRequest(request) }
        var retained: AIResultPreview?
        do {
            retained = try await request.store.aiPreview(account: account, resultID: resultID)
            guard currentAIRequest(request) else { return true }
            let receipt: AIResultReceipt?
            if let retained { receipt = try await request.store.reconcileAIResult(retained) }
            else { receipt = try await request.store.aiResultReceipt(account: account, resultID: resultID) }
            let value = await collectAIRecovery(request)
            guard currentAIRequest(request) else { return true }
            publishAIRecovery(value, preview: receipt == nil ? retained : nil,
                              status: receipt.map { "已保存到 \($0.day.value)，没有重复追加。" })
            return receipt != nil
        } catch {
            await publishAIFailure(request, preview: retained, status: "本地写入需要核对，预览仍保留。")
            return true
        }
    }
    func prepareAIResult(_ result: MobileResultValidation, account: String, day: JournalDay, text: String) async {
        guard let request = beginAIRequest() else { return }
        defer { endAIRequest(request) }
        guard await flushBody(), currentAIRequest(request) else { return }
        guard !hasCloudRecovery, !bodyHeldForCloud else {
            aiResultStatus = "请先保存正文并处理尚未完成的恢复。"; return
        }
        var retained: AIResultPreview?
        do {
            retained = try await request.store.aiPreview(account: account, resultID: result.resultId)
            guard currentAIRequest(request) else { return }
            let existing = retained != nil
            if retained == nil { retained = try await request.store.prepareAIResult(result, account: account, day: day, text: text) }
            let value = await collectAIRecovery(request)
            guard currentAIRequest(request) else { return }
            publishAIRecovery(value, preview: retained, status: existing ? "保留了原预览；请核对日期、文字和来源后确认。" : nil)
        } catch { await publishAIFailure(request, preview: retained, status: error.localizedDescription) }
    }
    /// Editing retires confirmation eligibility before asynchronous work can resume.
    func invalidateAIResultPreparation() {
        aiActionID = UUID(); aiActionInFlight = nil; aiResultPreparedForConfirmation = nil
    }
    /// Rebuild only after proving an earlier attempt was not applied. The store
    /// retains the previous preview until a replacement has been persisted.
    func rebuildAIResultPreview(_ result: MobileResultValidation, account: String,
                                day: JournalDay, text: String) async -> AIResultPreview? {
        guard let request = beginAIRequest() else { return nil }
        aiResultPreparedForConfirmation = nil
        defer { endAIRequest(request) }
        guard await flushBody(), currentAIRequest(request) else { return nil }
        guard !hasCloudRecovery, !bodyHeldForCloud else {
            aiResultStatus = "请先保存正文并处理尚未完成的恢复。"; return nil
        }
        var retained: AIResultPreview?
        do {
            retained = try await request.store.aiPreview(account: account, resultID: result.resultId)
            guard currentAIRequest(request) else { return nil }
            if let old = retained {
                if let receipt = try await request.store.reconcileAIResult(old) {
                    await finishAISave(receipt, preview: old, request: request); return nil
                }
                guard currentAIRequest(request) else { return nil }
            } else if let receipt = try await request.store.aiResultReceipt(account: account, resultID: result.resultId) {
                let value = await collectAIRecovery(request)
                guard currentAIRequest(request) else { return nil }
                publishAIRecovery(value, preview: nil, status: "已保存到 \(receipt.day.value)，没有重复追加。")
                return nil
            }
            guard currentAIRequest(request) else { return nil }
            retained = try await request.store.rebuildAIResultPreview(result, account: account, day: day, text: text)
            let value = await collectAIRecovery(request)
            guard currentAIRequest(request) else { return nil }
            publishAIRecovery(value, preview: retained, status: nil)
            guard !value.failed else { return nil }
            aiResultPreparedForConfirmation = retained
            return retained
        } catch {
            let status = error.localizedDescription
            if let latest = try? await request.store.aiPreview(account: account, resultID: result.resultId) { retained = latest }
            await publishAIFailure(request, preview: retained, status: status)
            return nil
        }
    }
    func confirmAIResult(account: String, validation: MobileResultValidation,
                         expectedPreview: AIResultPreview? = nil) async {
        guard let preview = aiResultPreview else { return }
        guard expectedPreview == nil || (expectedPreview == preview && aiResultPreparedForConfirmation == preview) else {
            aiResultStatus = "保存预览已更新，请查看当前内容后重新确认。"; return
        }
        guard let request = beginAIRequest() else { return }
        aiResultPreparedForConfirmation = nil
        defer { endAIRequest(request) }
        do {
            let receipt = try await request.store.confirmAIResult(preview, account: account, validation: validation)
            await finishAISave(receipt, preview: preview, request: request)
        } catch {
            guard currentAIRequest(request) else { return }
            // A write/receipt cleanup may have succeeded before an error. Never replay it
            // or label it unsaved until read-only reconciliation establishes the outcome.
            if let receipt = try? await request.store.reconcileAIResult(preview) {
                await finishAISave(receipt, preview: preview, request: request)
            } else {
                await publishAIFailure(request, preview: preview, status: "保存结果需要核对，预览与本机记录仍保留。")
            }
        }
    }
    private func finishAISave(_ receipt: AIResultReceipt, preview: AIResultPreview, request: AIRequest) async {
        guard currentAIRequest(request) else { return }
        let value = await collectAIRecovery(request)
        guard currentAIRequest(request) else { return }
        let saved = "已保存到 \(receipt.day.value)，没有重复追加。"
        if request.day == preview.day {
            do {
                try await load(request.day, beforeApply: {
                    guard self.currentAIRequest(request) else { throw CancellationError() }
                })
            } catch {
                guard currentAIRequest(request) else { return }
                publishAIRecovery(value, preview: nil, status: saved + "\n当前页面暂未刷新，请重新读取。"); return
            }
        }
        guard currentAIRequest(request, afterLoad: request.day == preview.day) else { return }
        publishAIRecovery(value, preview: nil, status: saved)
    }
    private func publishAIFailure(_ request: AIRequest, preview: AIResultPreview?, status: String) async {
        guard currentAIRequest(request) else { return }
        let value = await collectAIRecovery(request)
        guard currentAIRequest(request) else { return }
        publishAIRecovery(value, preview: preview ?? aiResultPreview, status: status)
    }
    func cancelAIResultPreview() async {
        guard let preview = aiResultPreview else { return }
        await cancelLocalAIResult(preview)
    }
}
