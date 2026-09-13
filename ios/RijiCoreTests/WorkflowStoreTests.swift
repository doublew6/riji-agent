import Foundation
import XCTest
@testable import RijiCore

private final class WorkflowFailure: @unchecked Sendable {
    private let lock = NSLock()
    private var boundary: (FilePurpose, FileStage)?
    func arm(_ purpose: FilePurpose, _ stage: FileStage) { lock.lock(); defer { lock.unlock() }; boundary = (purpose, stage) }
    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        lock.lock(); defer { lock.unlock() }
        if let boundary, boundary.0 == purpose, boundary.1 == stage { self.boundary = nil; throw JournalError.storage }
    }
}

final class WorkflowStoreTests: XCTestCase {
    private var root: URL!
    private let zone = TimeZone(identifier: "Asia/Shanghai")!
    private let day = try! JournalDay("2026-09-12")
    override func setUpWithError() throws { root = FileManager.default.temporaryDirectory.appendingPathComponent("RijiWorkflowSynthetic-\(UUID().uuidString)") }
    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }
    private func store(_ failure: WorkflowFailure? = nil, suffix: String = "") throws -> LocalJournalStore {
        let directory = root.appendingPathComponent(suffix)
        return try LocalJournalStore(journalDirectory: directory.appendingPathComponent("Journals"), stateDirectory: directory.appendingPathComponent("State"), timeZone: zone, failure: { try failure?.check($0, $1) })
    }
    private func template(_ kind: TemplateKind = .daily, source: String = "---\r\ncustom: keep\r\n---\r\n# {{title}}\r\n## Custom\r\n{{weather}}\r\n<% synthetic_macro %>\r\n") throws -> TemplateVersion {
        try TemplateVersion(kind: kind, raw: Data(source.utf8))
    }
    private func addDiary(_ store: LocalJournalStore, day: JournalDay? = nil, text: String = "Synthetic evidence") async throws {
        let snapshot = try await store.open(day ?? self.day)
        var draft = try await store.makeDraft(for: snapshot, section: "🧠 Notes"); draft.text = text
        _ = try await store.commit(draft)
    }
    private func period(_ kind: TemplateKind = .weekly, day: JournalDay? = nil) throws -> ReviewPeriod {
        try ReviewPeriod(kind: kind, containing: day ?? self.day, timeZone: zone)
    }
    private func goal(previous: UUID? = nil, name: String = "Synthetic learning", target: String = "3") -> NorthStarDefinition {
        NorthStarDefinition(previousID: previous, name: name, definition: "Confirmed practice sessions", unit: "sessions", target: target,
                            period: "Synthetic September", stage: "Practice", day: day)
    }

    func testTemplatePreviewImportSelectionAndLiteralUnknownMacros() async throws {
        let store = try store(); let value = try template()
        XCTAssertEqual(value.warnings.filter { $0 == "{{weather}}" }.count, 1)
        XCTAssertTrue(value.instantiate(day: day).contains("<% synthetic_macro %>"))
        XCTAssertTrue(value.instantiate(day: day).contains("# 2026-09-12\r\n"))
        let before = try await store.templates(.daily); XCTAssertEqual(before.count, 1)
        let id = UUID(); try await store.importTemplate(value, operationID: id); try await store.importTemplate(value, operationID: id)
        let copied = try Data(contentsOf: root.appendingPathComponent("Templates/\(value.id).md")); XCTAssertEqual(copied, value.raw)
        let initial = try await store.selectedTemplate(.daily); XCTAssertTrue(initial.isInitial)
        try await store.selectTemplate(value)
        let reopened = try self.store(); let selected = try await reopened.selectedTemplate(.daily); XCTAssertEqual(selected, value)
        let snapshot = try await reopened.open(day); XCTAssertEqual(snapshot.markdown, value.instantiate(day: day))
        XCTAssertFalse(snapshot.isSaved)
    }
    func testInvalidAndOversizeTemplatesDoNotReplaceSelection() async throws {
        let store = try store()
        for data in [Data(), Data([0xff]), Data(repeating: 65, count: 262145)] {
            XCTAssertThrowsError(try TemplateVersion(kind: .daily, raw: data))
        }
        let selected = try await store.selectedTemplate(.daily); XCTAssertTrue(selected.isInitial)
    }
    func testSelectedTemplateDoesNotRebuildOldDraft() async throws {
        let store = try store(); let snapshot = try await store.open(day)
        var draft = try await store.makeDraft(for: snapshot, section: "🧠 Notes"); draft.text = "Bound synthetic draft"
        try await store.saveDraft(draft)
        let value = try template(); try await store.importTemplate(value, operationID: UUID()); try await store.selectTemplate(value)
        let reopened = try self.store(); let restored = try await reopened.open(day)
        XCTAssertEqual(restored.draft?.effectiveTemplate, draft.effectiveTemplate)
        _ = try await reopened.commit(draft)
        let saved = try await reopened.exportMarkdown(day)
        XCTAssertTrue(String(decoding: saved, as: UTF8.self).contains("## 🧠 Notes\nBound synthetic draft"))
        XCTAssertFalse(String(decoding: saved, as: UTF8.self).contains("custom: keep"))
    }
    func testImportedMissingOrAmbiguousAnchorBlocksAppend() async throws {
        let store = try store()
        for source in ["# {{date}}\n## Other\n", "## Custom\n## Custom\n"] {
            let value = try template(source: source); try await store.importTemplate(value, operationID: UUID()); try await store.selectTemplate(value)
            let draftDay = try JournalDay(source.contains("Other") ? "2026-09-10" : "2026-09-11")
            let snapshot = try await store.open(draftDay)
            var draft = try await store.makeDraft(for: snapshot, section: "Custom"); draft.text = "Retained synthetic input"
            do { _ = try await store.commit(draft); XCTFail() } catch { XCTAssertTrue([JournalError.missingSection, .ambiguousSection].contains(error as? JournalError ?? .storage)) }
            let after = try await store.open(draftDay); XCTAssertEqual(after.draft, draft); XCTAssertFalse(after.isSaved)
        }
    }
    func testBlankDayIsExactTemplateAndNeverOverwrites() async throws {
        let store = try store(); let value = try template()
        try await store.importTemplate(value, operationID: UUID()); try await store.selectTemplate(value)
        let draft = try await store.prepareBlankDay(day)
        try await store.createBlankDay(draft); try await store.createBlankDay(draft)
        let bytes = try await store.exportMarkdown(day); XCTAssertEqual(bytes, Data(value.instantiate(day: day).utf8))
        let another = try await store.prepareBlankDay(day); try await store.createBlankDay(another)
        let unchanged = try await store.exportMarkdown(day); XCTAssertEqual(unchanged, bytes)
    }
    func testSearchOnlySavedScopedFilesWithCorrectKindsAndSnippets() async throws {
        let store = try store(); try await addDiary(store, text: "Synthetic searchable orchid")
        let tomorrow = try JournalDay("2026-09-13"); let snap = try await store.open(tomorrow)
        var unsaved = try await store.makeDraft(for: snap, section: "🧠 Notes"); unsaved.text = "Unconfirmed orchid"; try await store.saveDraft(unsaved)
        for kind in [TemplateKind.weekly, .monthly] {
            let draft = try await store.prepareReview(period(kind)).editing("Synthetic orchid review")
            try await store.commitReview(draft)
        }
        try Data("orchid".utf8).write(to: root.appendingPathComponent("Journals/not-a-day.md"))
        let results = try await store.search("orchid")
        XCTAssertEqual(results.matches.count, 3)
        XCTAssertEqual(Set(results.matches.map(\.file.label)), ["日记", "周报", "月报"])
        XCTAssertFalse(results.matches.contains { $0.id.contains(tomorrow.value) })
        for match in results.matches { XCTAssertTrue(match.snippet.contains("orchid")); _ = try await store.readSavedFile(match.file) }
    }
    func testNorthStarMultipleMetricsVersionsAndMissingMeasurement() async throws {
        let store = try store(); let first = goal(); let second = goal(name: "Synthetic movement")
        try await store.saveNorthStar(first); try await store.saveNorthStar(first); try await store.saveNorthStar(second)
        let update = goal(previous: first.id, target: "5"); try await store.saveNorthStar(update)
        let current = try await store.currentNorthStars(); let history = try await store.northStars()
        XCTAssertEqual(current.count, 2); XCTAssertEqual(history.count, 3)
        XCTAssertFalse(current.contains { $0.id == first.id }); XCTAssertTrue(history.allSatisfy { $0.measurement == nil })
        let firstBytes = try String(contentsOf: root.appendingPathComponent("Goals/\(first.id.uuidString).md"), encoding: .utf8)
        XCTAssertTrue(firstBytes.contains("当前测量：尚未记录")); XCTAssertTrue(firstBytes.contains("目标：3"))
        var invalid = goal(previous: update.id); invalid.unit = ""
        do { try await store.saveNorthStar(invalid); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .invalidDefinition) }
        let remaining = try await store.currentNorthStars(); XCTAssertEqual(remaining.count, 2)
        var noSource = goal(); noSource.measurement = "0"
        do { try await store.saveNorthStar(noSource); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .invalidDefinition) }
    }
    func testMITAdoptionReplacementFeedbackAndRelaunchAreAppendOnly() async throws {
        let store = try store(); let goal = goal(); try await store.saveNorthStar(goal)
        let first = MITEvent(day: day, goalID: goal.id, action: "Synthetic practice", criterion: "One written example")
        let draft = try await store.prepareMIT(first, section: "🌅 Morning")
        try await store.commitMIT(draft); try await store.commitMIT(draft)
        let oldBytes = try await store.exportMarkdown(day)
        let second = MITEvent(day: day, replaces: first.id, goalID: goal.id, action: "Synthetic revised practice", criterion: "Two examples")
        try await store.commitMIT(store.prepareMIT(second, section: "🌅 Morning"))
        for status in MITStatus.allCases {
            let feedback = MITEvent(day: day, feedbackFor: second.id, status: status, result: "Synthetic actual result: \(status.rawValue)")
            let feedbackDraft = try await store.prepareMIT(feedback, section: "🌆 Evening")
            try await store.commitMIT(feedbackDraft); try await store.commitMIT(feedbackDraft)
        }
        let reopened = try self.store(); let current = try await reopened.currentMIT(day); let history = try await reopened.mitHistory(day)
        XCTAssertEqual(current?.id, second.id); XCTAssertEqual(history.count, 5)
        let bytes = try await reopened.exportMarkdown(day)
        XCTAssertTrue(String(decoding: bytes, as: UTF8.self).contains(first.action))
        XCTAssertTrue(String(decoding: oldBytes, as: UTF8.self).contains(first.action))
        XCTAssertEqual(Set(history.compactMap(\.status)), Set(MITStatus.allCases))
    }
    func testMITStaleGoalAndCompetingAdoptionRejectWithoutReplacing() async throws {
        let store = try store(); let firstGoal = goal(); try await store.saveNorthStar(firstGoal)
        let event = MITEvent(day: day, goalID: firstGoal.id, action: "Synthetic", criterion: "One")
        let draft = try await store.prepareMIT(event, section: "🌅 Morning")
        try await store.saveNorthStar(goal(previous: firstGoal.id, target: "4"))
        do { try await store.commitMIT(draft); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        let manual = MITEvent(day: day, action: "Synthetic manual", criterion: "One")
        let one = try await store.prepareMIT(manual, section: "🌅 Morning")
        let two = try await store.prepareMIT(MITEvent(day: day, action: "Competitor", criterion: "One"), section: "🌅 Morning")
        try await store.commitMIT(one)
        do { try await store.commitMIT(two); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        let current = try await store.currentMIT(day); XCTAssertEqual(current?.id, manual.id)
    }
    func testISOYearBoundaryLeapMonthAndCurrentPeriod() async throws {
        let store = try store()
        let week = try period(.weekly, day: JournalDay("2021-01-01"))
        XCTAssertEqual(week.start.value, "2020-12-28"); XCTAssertEqual(week.end.value, "2021-01-03")
        let leap = try period(.monthly, day: JournalDay("2024-02-12"))
        XCTAssertEqual(leap.days(timeZone: zone).count, 29); XCTAssertEqual(leap.end.value, "2024-02-29")
        let draft = try await store.prepareReview(period(), now: day.date(in: zone))
        XCTAssertTrue(draft.isCurrentPeriod); XCTAssertEqual(draft.sources.count, 0); XCTAssertEqual(draft.missingDays.count, 7)
    }
    func testReviewSourceHashesDraftRelaunchFrontmatterAndImmutableRevisions() async throws {
        let store = try store(); try await addDiary(store)
        let template = try template(.weekly, source: "---\ncustom: keep\n---\n# {{title}}\n\n## Manual review\n")
        try await store.importTemplate(template, operationID: UUID()); try await store.selectTemplate(template)
        var draft = try await store.prepareReview(period(), now: day.date(in: zone)); draft = draft.editing(draft.text + "Synthetic manual reflection")
        try await store.retainReviewDraft(draft)
        let reopened = try self.store(); let restored = try await reopened.savedReviewDraft(period()); XCTAssertEqual(restored, draft)
        try await reopened.commitReview(draft); try await reopened.commitReview(draft)
        let first = try await reopened.readSavedFile(.review(period())); XCTAssertTrue(first.starts(with: Data("---\ncustom: keep\n---\n".utf8)))
        XCTAssertEqual(draft.sources.count, 1); XCTAssertEqual(draft.missingDays.count, 6)
        XCTAssertTrue(String(decoding: first, as: UTF8.self).contains(draft.sources[0].hash))
        let revision = try await reopened.prepareReview(period()).editing("Synthetic second revision")
        try await reopened.commitReview(revision)
        let second = try await reopened.readSavedFile(.review(period())); XCTAssertTrue(second.starts(with: first))
        XCTAssertTrue(String(decoding: second, as: UTF8.self).contains("Synthetic second revision"))
        let third = try await reopened.prepareReview(period()).editing("Synthetic unconfirmed third revision")
        try await reopened.retainReviewDraft(third); try await reopened.commitReview(draft)
        let stillRetained = try await reopened.savedReviewDraft(period()); XCTAssertEqual(stillRetained, third)
    }
    func testStaleReviewSourceTargetAndTemplateKeepDraft() async throws {
        let store = try store(); var draft = try await store.prepareReview(period()).editing("Retained synthetic edits")
        try await store.retainReviewDraft(draft); try await addDiary(store)
        do { try await store.commitReview(draft); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        let retained = try await store.savedReviewDraft(period()); XCTAssertEqual(retained?.text, draft.text)
        draft = try await store.prepareReview(period(), retaining: draft.text)
        let imported = try template(.weekly); try await store.importTemplate(imported, operationID: UUID()); try await store.selectTemplate(imported)
        do { try await store.commitReview(draft); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        draft = try await store.prepareReview(period(), retaining: draft.text)
        try Data("Externally changed synthetic review".utf8).write(to: root.appendingPathComponent("Weekly/2026-09-07.md"))
        do { try await store.commitReview(draft); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        let latest = try await store.savedReviewDraft(period()); XCTAssertEqual(latest?.text, "Retained synthetic edits")
    }
    func testMonthlySourcesIncludeEachDailyOnceWithoutWeeklyDoubleCounting() async throws {
        let store = try store(); try await addDiary(store)
        let weekly = try await store.prepareReview(period()).editing("Synthetic weekly summary")
        try await store.commitReview(weekly)
        let month = try await store.prepareReview(period(.monthly))
        XCTAssertEqual(month.sources.map(\.day), [day]); XCTAssertEqual(month.missingDays.count, 29)
    }
    func testWorkflowRecoveryBoundaryMatrixForMITBlankAndReview() async throws {
        let boundaries: [(FilePurpose, FileStage)] = [(.transaction, .beforeWrite), (.transaction, .beforeReplace), (.transaction, .afterReplace),
            (.diary, .beforeWrite), (.diary, .beforeReplace), (.diary, .afterReplace), (.receipt, .beforeWrite), (.receipt, .beforeReplace),
            (.receipt, .afterReplace), (.transaction, .beforeRemove), (.transaction, .afterRemove)]
        for (index, boundary) in boundaries.enumerated() {
            for workflow in ["mit", "blank", "review"] {
                let suffix = "\(index)-\(workflow)"; let failure = WorkflowFailure(); let store = try store(failure, suffix: suffix)
                let blank = try await store.prepareBlankDay(day)
                let mit = try await store.prepareMIT(MITEvent(day: day, action: "Synthetic atomic action", criterion: "Once"), section: "🌅 Morning")
                let review = try await store.prepareReview(period()).editing("Synthetic atomic reflection")
                failure.arm(boundary.0, boundary.1)
                do {
                    if workflow == "mit" { try await store.commitMIT(mit) }
                    else if workflow == "blank" { try await store.createBlankDay(blank) }
                    else { try await store.commitReview(review) }
                    XCTFail("Expected injected failure")
                } catch { XCTAssertEqual(error as? JournalError, .storage) }
                let reopened = try self.store(suffix: suffix); try await reopened.recoverWorkflows()
                if workflow == "mit" {
                    try await reopened.commitMIT(mit); let history = try await reopened.mitHistory(day); XCTAssertEqual(history.count, 1)
                } else if workflow == "blank" {
                    try await reopened.createBlankDay(blank); let bytes = try await reopened.exportMarkdown(day)
                    XCTAssertEqual(bytes, Data(blank.template.instantiate(day: day).utf8))
                } else {
                    try await reopened.commitReview(review); let bytes = try await reopened.readSavedFile(.review(period()))
                    let records: [ReviewDraft] = try await reopened.records(bytes, type: "review"); XCTAssertEqual(records.count, 1)
                }
            }
        }
    }
    func testUnknownWorkflowRecoverySourceIsRejectedAndRetryPayloadBound() async throws {
        let failure = WorkflowFailure(); let store = try store(failure)
        let draft = try await store.prepareMIT(MITEvent(day: day, action: "Synthetic action", criterion: "Once"), section: "🌅 Morning")
        failure.arm(.diary, .beforeReplace)
        do { try await store.commitMIT(draft); XCTFail() } catch {}
        try Data("## 🌅 Morning\nExternal synthetic change\n".utf8).write(to: root.appendingPathComponent("Journals/\(day.value).md"))
        do { try await store.recoverWorkflows(); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        let changed = MITDraft(event: draft.event, section: "🌆 Evening", sourceHash: draft.sourceHash, effectiveTemplate: draft.effectiveTemplate)
        do { try await store.commitMIT(changed); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
    }
    func testTemplateImportRecoversOnListingOrReimportWithoutOldOperationID() async throws {
        for entry in ["listing", "import"] {
            let failure = WorkflowFailure(); let store = try store(failure, suffix: entry)
            let value = try template()
            failure.arm(.diary, .beforeReplace)
            do { try await store.importTemplate(value, operationID: UUID()); XCTFail() }
            catch { XCTAssertEqual(error as? JournalError, .storage) }
            let reopened = try self.store(suffix: entry)
            if entry == "import" { try await reopened.importTemplate(value, operationID: UUID()) }
            let versions = try await reopened.templates(.daily)
            XCTAssertEqual(versions.filter { $0.id == value.id }.count, 1)
            try await reopened.importTemplate(value, operationID: UUID())
            let bytes = try Data(contentsOf: root.appendingPathComponent("\(entry)/Templates/\(value.id).md"))
            XCTAssertEqual(bytes, value.raw)
        }
    }

    func testTemplateImportRecoveryRejectsUnexpectedSourceBytes() async throws {
        let failure = WorkflowFailure(); let store = try store(failure); let value = try template()
        failure.arm(.diary, .beforeReplace)
        do { try await store.importTemplate(value, operationID: UUID()); XCTFail() } catch {}
        let url = root.appendingPathComponent("Templates/\(value.id).md")
        let external = Data("Synthetic unknown external version".utf8); try external.write(to: url)
        let reopened = try self.store()
        do { _ = try await reopened.templates(.daily); XCTFail() }
        catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        do { try await reopened.importTemplate(value, operationID: UUID()); XCTFail() }
        catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        XCTAssertEqual(try Data(contentsOf: url), external)
    }

    func testImportingBundledTemplateDeduplicatesIdentityButKeepsRawFile() async throws {
        let store = try store()
        for kind in TemplateKind.allCases {
            let initial = TemplateVersion.initial(kind)
            let imported = try TemplateVersion(kind: kind, raw: initial.raw)
            try await store.importTemplate(imported, operationID: UUID())
            try await store.selectTemplate(imported)
            let reopened = try self.store()
            let versions = try await reopened.templates(kind); let selected = try await reopened.selectedTemplate(kind)
            XCTAssertEqual(versions.count, 1); XCTAssertEqual(versions.map(\.id), [initial.id])
            XCTAssertEqual(selected.id, imported.id)
            XCTAssertEqual(try Data(contentsOf: root.appendingPathComponent("Templates/\(imported.id).md")), initial.raw)
        }
    }

    func testOptionalGoalFieldsRejectOversizeAndProvenanceWithoutDamagingHistory() async throws {
        let store = try store(); let original = goal(); try await store.saveNorthStar(original)
        let url = root.appendingPathComponent("Goals/\(original.id.uuidString).md"); let bytes = try Data(contentsOf: url)
        for field in ["measurement", "measurementSource"] {
            for invalid in ["", String(repeating: "x", count: 8001), "\n<!-- riji-record:goal:invalid -->"] {
                var candidate = goal(previous: original.id)
                candidate.measurement = field == "measurement" ? invalid : "0"
                candidate.measurementSource = field == "measurementSource" ? invalid : "Synthetic source as of 2026-09-12"
                do { try await store.saveNorthStar(candidate); XCTFail() }
                catch { XCTAssertEqual(error as? JournalError, .invalidDefinition) }
                let values = try await store.northStars(); XCTAssertEqual(values, [original])
                XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("Goals/\(candidate.id.uuidString).md").path))
            }
        }
        XCTAssertEqual(try Data(contentsOf: url), bytes)
    }

    func testSearchSkipsOversizedFileAndKeepsExplicitPartialMatches() async throws {
        let store = try store()
        try await addDiary(store, text: "Synthetic orchid match")
        let oversized = root.appendingPathComponent("Journals/2026-09-13.md")
        try Data(repeating: 65, count: 2_097_153).write(to: oversized)
        let results = try await store.search("orchid")
        XCTAssertEqual(results.matches.map(\.file), [.daily(day)])
        XCTAssertEqual(results.skippedOversized, 1); XCTAssertEqual(results.scanned, 1); XCTAssertTrue(results.truncated)
    }

    func testSearchLimitUsesDateAcrossKindsWithDeterministicTies() async throws {
        let store = try store(); var expected = [SavedFile]()
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = zone
        for offset in 0..<102 {
            let date = calendar.date(byAdding: .day, value: -offset, to: day.date(in: zone))!
            let dateDay = JournalDay(date: date, timeZone: zone); expected.append(.daily(dateDay))
            try Data("Synthetic chronology match".utf8).write(to: root.appendingPathComponent("Journals/\(dateDay.value).md"))
        }
        for kind in [TemplateKind.weekly, .monthly] {
            let value = try period(kind); expected.append(.review(value))
            let folder = kind == .weekly ? "Weekly" : "Monthly"
            try Data("Synthetic chronology match".utf8).write(to: root.appendingPathComponent("\(folder)/\(value.start.value).md"))
        }
        expected.sort { $0.date == $1.date ? $0.id < $1.id : $0.date > $1.date }
        let results = try await store.search("chronology")
        XCTAssertTrue(results.truncated); XCTAssertEqual(results.matches.count, 100)
        XCTAssertEqual(results.matches.map(\.file), Array(expected.prefix(100)))
        XCTAssertEqual(results.matches.first?.file, .daily(day))
        let sameDay = results.matches.map(\.file).filter { $0.date == (try! period()).start }
        XCTAssertEqual(sameDay, [.daily(try period().start), .review(try period())])
    }

    func testFutureReviewPhaseIsVisibleInSavedMarkdown() async throws {
        let store = try store(); let future = try period(.monthly, day: JournalDay("2026-10-10"))
        let draft = try await store.prepareReview(future, now: day.date(in: zone)).editing("Synthetic future plan")
        XCTAssertEqual(draft.phase, .future); XCTAssertFalse(draft.isCurrentPeriod)
        try await store.commitReview(draft)
        let saved = String(decoding: try await store.readSavedFile(.review(future)), as: UTF8.self)
        XCTAssertTrue(saved.contains("截止：2026-09-12 · 尚未开始")); XCTAssertFalse(saved.contains("历史周期"))
    }

    func testPendingMITBlocksOrdinaryAppendWithoutChangingBytesThenRecoversOnce() async throws {
        let failure = WorkflowFailure(); let store = try store(failure); try await addDiary(store)
        let before = try await store.exportMarkdown(day)
        let mit = try await store.prepareMIT(MITEvent(day: day, action: "Synthetic pending MIT", criterion: "Once"), section: "🌅 Morning")
        let snapshot = try await store.open(day)
        var ordinary = try await store.makeDraft(for: snapshot, section: "🧠 Notes"); ordinary.text = "Synthetic blocked append"
        failure.arm(.diary, .beforeReplace)
        do { try await store.commitMIT(mit); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .storage) }
        do { _ = try await store.commit(ordinary); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .pendingOperation) }
        let unchanged = try await store.exportMarkdown(day); XCTAssertEqual(unchanged, before)
        _ = try await store.recover(day); try await store.commitMIT(mit)
        let history = try await store.mitHistory(day); XCTAssertEqual(history.count, 1)
        let after = String(decoding: try await store.exportMarkdown(day), as: UTF8.self)
        XCTAssertEqual(after.components(separatedBy: "Synthetic pending MIT").count, 2)
        XCTAssertFalse(after.contains("Synthetic blocked append"))
    }

    func testPendingOrdinaryAppendBlocksMITWithoutChangingBytesThenRecoversOnce() async throws {
        let failure = WorkflowFailure(); let store = try store(failure); try await addDiary(store)
        let before = try await store.exportMarkdown(day); let snapshot = try await store.open(day)
        var ordinary = try await store.makeDraft(for: snapshot, section: "🧠 Notes"); ordinary.text = "Synthetic pending append"
        let mit = try await store.prepareMIT(MITEvent(day: day, action: "Synthetic blocked MIT", criterion: "Once"), section: "🌅 Morning")
        failure.arm(.diary, .beforeReplace)
        do { _ = try await store.commit(ordinary); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .storage) }
        do { try await store.commitMIT(mit); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .pendingOperation) }
        let unchanged = try await store.exportMarkdown(day); XCTAssertEqual(unchanged, before)
        _ = try await store.recover(day); _ = try await store.commit(ordinary)
        let after = String(decoding: try await store.exportMarkdown(day), as: UTF8.self)
        XCTAssertEqual(after.components(separatedBy: "Synthetic pending append").count, 2)
        let history = try await store.mitHistory(day); XCTAssertTrue(history.isEmpty)
        XCTAssertFalse(after.contains("Synthetic blocked MIT"))
    }

}
