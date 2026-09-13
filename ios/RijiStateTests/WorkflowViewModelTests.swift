import XCTest
import Foundation
@testable import RijiState
import RijiCore

private final class StateWorkflowFailure: @unchecked Sendable {
    private let lock = NSLock()
    var armed = false
    private var rejectDrafts = false
    func failDrafts(_ value: Bool) { lock.lock(); defer { lock.unlock() }; rejectDrafts = value }
    func arm() { lock.lock(); defer { lock.unlock() }; armed = true }
    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        lock.lock(); defer { lock.unlock() }
        if rejectDrafts, purpose == .draft, stage == .beforeWrite { throw JournalError.storage }
        if armed, purpose == .diary, stage == .afterReplace { armed = false; throw JournalError.storage }
    }
}

@MainActor
final class WorkflowViewModelTests: XCTestCase {
    private var root: URL!
    private let zone = TimeZone(identifier: "Asia/Shanghai")!
    private let day = try! JournalDay("2026-09-12")
    override func setUpWithError() throws { root = FileManager.default.temporaryDirectory.appendingPathComponent("RijiSyntheticWorkflowState-\(UUID().uuidString)") }
    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }
    private func store(_ failure: StateWorkflowFailure? = nil) throws -> LocalJournalStore {
        try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), timeZone: zone,
                              failure: { try failure?.check($0, $1) })
    }
    func testMITFormAndConfirmationRetrySurviveRelaunch() async throws {
        let failure = StateWorkflowFailure(); let store = try store(failure); let model = WorkflowViewModel(store: store)
        await model.loadMIT(day)
        model.updateField("action", "Synthetic persistent action"); model.updateField("criterion", "One exact artifact")
        model.updateField("section", "🌅 Morning"); await model.flush()
        let reopened = WorkflowViewModel(store: try self.store(failure)); await reopened.loadMIT(day)
        XCTAssertEqual(reopened.fields["action"], "Synthetic persistent action")
        await reopened.previewMIT(day: day, section: "🌅 Morning", feedback: false)
        let operation = try XCTUnwrap(reopened.mitPreview?.operationID)
        failure.arm(); await reopened.confirmMIT()
        XCTAssertNotNil(reopened.error); XCTAssertTrue(reopened.pendingCandidate)
        reopened.updateField("action", "This must not replace an uncertain operation")
        XCTAssertEqual(reopened.fields["action"], "Synthetic persistent action")
        let retry = WorkflowViewModel(store: try self.store()); await retry.loadMIT(day)
        XCTAssertTrue(retry.pendingCandidate)
        await retry.previewMIT(day: day, section: "🌅 Morning", feedback: false)
        XCTAssertEqual(retry.mitPreview?.operationID, operation)
        await retry.confirmMIT()
        XCTAssertNil(retry.error); XCTAssertEqual(retry.mitHistory.count, 1); XCTAssertTrue(retry.fields.isEmpty)
    }
    func testNorthStarInvalidConfirmationRetainsVisibleInputs() async throws {
        let model = WorkflowViewModel(store: try store()); await model.loadGoals()
        model.updateField("name", "Synthetic goal without definition"); await model.flush()
        await model.previewGoal(day: day); await model.confirmGoal()
        XCTAssertNotNil(model.error); XCTAssertEqual(model.fields["name"], "Synthetic goal without definition")
        let reopened = WorkflowViewModel(store: try store()); await reopened.loadGoals()
        XCTAssertEqual(reopened.fields["name"], "Synthetic goal without definition"); XCTAssertTrue(reopened.goals.isEmpty)
    }
    func testReviewUncertainWriteFreezesEditsAndRelaunchRecoversOriginalOnce() async throws {
        let failure = StateWorkflowFailure(); let store = try store(failure); let model = WorkflowViewModel(store: store)
        let period = try ReviewPeriod(kind: .weekly, containing: day, timeZone: zone)
        await model.openReview(period, now: day.date(in: zone))
        model.updateReview("Synthetic original confirmed reflection"); await model.flush()
        let operation = try XCTUnwrap(model.review?.operationID)
        failure.arm(); await model.confirmReview()
        XCTAssertNotNil(model.error); XCTAssertTrue(model.pendingReview)
        model.updateReview("Attempted edit while result is uncertain")
        XCTAssertEqual(model.review?.operationID, operation)
        XCTAssertEqual(model.review?.text, "Synthetic original confirmed reflection")
        await model.confirmReview(); XCTAssertNil(model.error); XCTAssertNil(model.review)
        let reopened = WorkflowViewModel(store: try self.store()); await reopened.openReview(period, now: day.date(in: zone))
        XCTAssertNil(reopened.review)
        XCTAssertEqual(reopened.savedReview.components(separatedBy: "Synthetic original confirmed reflection").count, 2)
        XCTAssertFalse(reopened.savedReview.contains("Attempted edit"))
    }
    func testReviewDraftTextAndSourcesSurviveRelaunchThenStaleRefreshRetainsEdits() async throws {
        let store = try store(); let model = WorkflowViewModel(store: store)
        let period = try ReviewPeriod(kind: .monthly, containing: day, timeZone: zone)
        await model.openReview(period, now: day.date(in: zone)); model.updateReview("Synthetic retained monthly review"); await model.flush()
        let reopened = WorkflowViewModel(store: try self.store()); await reopened.openReview(period, now: day.date(in: zone))
        XCTAssertEqual(reopened.review?.text, "Synthetic retained monthly review")
        let blank = try await store.prepareBlankDay(day); try await store.createBlankDay(blank)
        await reopened.confirmReview(); XCTAssertNotNil(reopened.error)
        XCTAssertEqual(reopened.review?.text, "Synthetic retained monthly review")
        await reopened.reviseReview(period, now: day.date(in: zone))
        XCTAssertEqual(reopened.review?.text, "Synthetic retained monthly review"); XCTAssertEqual(reopened.review?.sources.count, 1)
        await reopened.confirmReview(); XCTAssertNil(reopened.error)
    }
    func testDiaryViewModelBindsNewDraftToSelectedImportedTemplate() async throws {
        let store = try store()
        let template = try TemplateVersion(kind: .daily, raw: Data("# {{title}}\n\n## Synthetic Anchor\n\n".utf8))
        try await store.importTemplate(template, operationID: UUID()); try await store.selectTemplate(template)
        let model = DiaryViewModel(now: { self.day.date(in: self.zone) }, storageFactory: { store })
        await model.start(); XCTAssertEqual(model.section, "Synthetic Anchor")
        model.updateText("Synthetic bound text"); await model.flushDraft()
        try await store.selectTemplate(.initial(.daily))
        await model.save(); XCTAssertNil(model.errorMessage)
        let data = try await store.exportMarkdown(day)
        XCTAssertEqual(String(decoding: data, as: UTF8.self), "# 2026-09-12\n\n## Synthetic Anchor\n\nSynthetic bound text\n")
    }
    func testFeedbackRequiresExplicitStatusThroughPreviewAndConfirmation() async throws {
        let store = try store()
        let event = MITEvent(day: day, action: "Synthetic adopted plan", criterion: "One artifact")
        try await store.commitMIT(store.prepareMIT(event, section: "🌅 Morning"))
        let model = WorkflowViewModel(store: store); await model.loadMIT(day)
        model.updateField("mode", "feedback"); model.updateField("result", "Synthetic actual result")
        await model.previewMIT(day: day, section: "🌆 Evening", feedback: true)
        XCTAssertNotNil(model.mitPreview); XCTAssertNil(model.mitPreview?.event.status)
        await model.confirmMIT()
        XCTAssertNotNil(model.error); XCTAssertEqual(model.fields["result"], "Synthetic actual result")
        var history = try await store.mitHistory(day); XCTAssertEqual(history.count, 1)
        for status in MITStatus.allCases {
            model.updateField("mode", "feedback"); model.updateField("result", "Synthetic result: \(status.rawValue)")
            model.updateField("feedbackStatus", status.rawValue)
            await model.previewMIT(day: day, section: "🌆 Evening", feedback: true)
            XCTAssertEqual(model.mitPreview?.event.status, status)
            await model.confirmMIT(); XCTAssertNil(model.error)
        }
        history = try await store.mitHistory(day)
        XCTAssertEqual(history.compactMap(\.status), MITStatus.allCases)
        XCTAssertEqual(history.count, 4)
    }

    func testMITEntryReadsChangedTemplateWithoutReturningToDiaryPage() async throws {
        let store = try store()
        let diary = DiaryViewModel(now: { self.day.date(in: self.zone) }, storageFactory: { store })
        await diary.start(); XCTAssertTrue(diary.sections.contains("🧠 Notes"))
        let model = try XCTUnwrap(diary.workflows)
        let template = try TemplateVersion(kind: .daily, raw: Data("# {{title}}\n## Synthetic MIT destination\n".utf8))
        try await store.importTemplate(template, operationID: UUID()); await model.chooseTemplate(template)
        // Entering the directory's MIT route does not first reload the diary page.
        await model.loadMIT(day)
        XCTAssertEqual(model.mitSections, ["Synthetic MIT destination"])
        XCTAssertFalse(diary.sections.contains("Synthetic MIT destination"))
        model.updateField("action", "Synthetic direct-entry task"); model.updateField("criterion", "One")
        await model.previewMIT(day: day, section: "Synthetic MIT destination", feedback: false)
        await model.confirmMIT(); XCTAssertNil(model.error)
        let saved = String(decoding: try await store.exportMarkdown(day), as: UTF8.self)
        XCTAssertTrue(saved.contains("## Synthetic MIT destination\n")); XCTAssertTrue(saved.contains("Synthetic direct-entry task"))
    }

    func testMITEntryKeepsExistingDraftTemplateWhenSelectionChanges() async throws {
        let store = try store()
        let diary = DiaryViewModel(now: { self.day.date(in: self.zone) }, storageFactory: { store })
        await diary.start(); diary.updateText("Synthetic retained diary draft"); await diary.flushDraft()
        let before = try await store.open(day)
        let template = try TemplateVersion(kind: .daily, raw: Data("## Replacement synthetic section\n".utf8))
        try await store.importTemplate(template, operationID: UUID()); try await store.selectTemplate(template)
        let model = try XCTUnwrap(diary.workflows); await model.loadMIT(day)
        XCTAssertTrue(model.mitSections.contains("🧠 Notes")); XCTAssertFalse(model.mitSections.contains("Replacement synthetic section"))
        let after = try await store.open(day); XCTAssertEqual(after.draft, before.draft)
        XCTAssertEqual(diary.text, "Synthetic retained diary draft")
    }

    func testSavedDiaryLabelDoesNotClaimNewGlobalTemplateAndBytesRemainUnchanged() async throws {
        let store = try store()
        let diary = DiaryViewModel(now: { self.day.date(in: self.zone) }, storageFactory: { store })
        await diary.start(); XCTAssertEqual(diary.templateLabel, DailyTemplate.label)
        diary.updateText("Synthetic saved diary"); await diary.flushDraft(); await diary.save()
        XCTAssertEqual(diary.templateLabel, "已保存日记 · 原模板保留")
        let before = try await store.exportMarkdown(day)
        let template = try TemplateVersion(kind: .daily, raw: Data("# {{title}}\n## Synthetic imported template\n".utf8))
        try await store.importTemplate(template, operationID: UUID()); try await store.selectTemplate(template)
        _ = await diary.navigate(to: day)
        XCTAssertEqual(diary.templateLabel, "已保存日记 · 原模板保留")
        let after = try await store.exportMarkdown(day); XCTAssertEqual(after, before)
        let tomorrow = try JournalDay("2026-09-13"); _ = await diary.navigate(to: tomorrow)
        XCTAssertEqual(diary.templateLabel, template.label)
        diary.updateText("Synthetic bound imported draft"); await diary.flushDraft()
        try await store.selectTemplate(.initial(.daily)); _ = await diary.navigate(to: tomorrow)
        XCTAssertEqual(diary.templateLabel, "草稿绑定的原模板")
        XCTAssertEqual(diary.text, "Synthetic bound imported draft")
    }

    func testMITRefreshReloadsStaleGoalAndCompetingPlanWithoutChangingTypedFields() async throws {
        let store = try store()
        let originalGoal = NorthStarDefinition(name: "Synthetic goal", definition: "Practice", unit: "times", target: "3", period: "Month", stage: "Learn", day: day)
        try await store.saveNorthStar(originalGoal)
        let model = WorkflowViewModel(store: store); await model.loadMIT(day)
        model.updateField("goal", originalGoal.id.uuidString)
        model.updateField("action", "Synthetic retained action"); model.updateField("criterion", "One artifact")
        await model.previewMIT(day: day, section: "🌅 Morning", feedback: false)
        let updatedGoal = NorthStarDefinition(previousID: originalGoal.id, name: originalGoal.name, definition: "Practice", unit: "times", target: "4", period: "Month", stage: "Learn", day: day)
        try await store.saveNorthStar(updatedGoal)
        await model.confirmMIT(); XCTAssertNotNil(model.error)
        await model.refreshMITPreview(day: day, section: "🌅 Morning", feedback: false)
        XCTAssertNotNil(model.error); XCTAssertNil(model.mitPreview); XCTAssertTrue(model.requiresGoalReselection)
        XCTAssertEqual(model.fields["goal"], originalGoal.id.uuidString)
        XCTAssertEqual(model.fields["action"], "Synthetic retained action")
        XCTAssertEqual(model.goals.map(\.id), [updatedGoal.id])
        model.updateField("goal", updatedGoal.id.uuidString)
        await model.previewMIT(day: day, section: "🌅 Morning", feedback: false); await model.confirmMIT()
        XCTAssertNil(model.error); let first = try XCTUnwrap(model.mit)
        model.updateField("goal", updatedGoal.id.uuidString)
        model.updateField("action", "Synthetic deliberate replacement"); model.updateField("criterion", "Two artifacts")
        await model.previewMIT(day: day, section: "🌅 Morning", feedback: false)
        let competitor = MITEvent(day: day, replaces: first.id, goalID: updatedGoal.id, action: "Synthetic competing action", criterion: "One")
        try await store.commitMIT(store.prepareMIT(competitor, section: "🌅 Morning"))
        await model.confirmMIT(); XCTAssertNotNil(model.error)
        await model.refreshMITPreview(day: day, section: "🌅 Morning", feedback: false)
        XCTAssertNil(model.error); XCTAssertEqual(model.mit?.id, competitor.id)
        XCTAssertEqual(model.mitPreview?.event.replaces, competitor.id)
        XCTAssertEqual(model.mitPreview?.event.action, "Synthetic deliberate replacement")
        XCTAssertEqual(model.mitPreview?.event.goalID, updatedGoal.id)
        await model.confirmMIT(); XCTAssertNil(model.error)
        XCTAssertEqual(model.mit?.action, "Synthetic deliberate replacement"); XCTAssertEqual(model.mitHistory.count, 3)
    }

    func testFailedReviewAutosaveCannotBeReplacedByReloadUntilRetrySucceeds() async throws {
        let failure = StateWorkflowFailure(); let store = try store(failure); let model = WorkflowViewModel(store: store)
        let period = try ReviewPeriod(kind: .weekly, containing: day, timeZone: zone)
        await model.openReview(period, now: day.date(in: zone))
        model.updateReview("Synthetic older disk draft"); await model.flush()
        failure.failDrafts(true); model.updateReview("Synthetic latest visible review")
        let operation = model.review?.operationID
        let flushed = await model.flush(); XCTAssertFalse(flushed); XCTAssertNotNil(model.error)
        await model.openReview(period, now: day.date(in: zone))
        XCTAssertNotNil(model.error); XCTAssertEqual(model.review?.text, "Synthetic latest visible review")
        XCTAssertEqual(model.review?.operationID, operation)
        failure.failDrafts(false); await model.openReview(period, now: day.date(in: zone))
        XCTAssertNil(model.error); XCTAssertEqual(model.review?.text, "Synthetic latest visible review")
        XCTAssertEqual(model.review?.operationID, operation)
        let reopened = WorkflowViewModel(store: try self.store()); await reopened.openReview(period, now: day.date(in: zone))
        XCTAssertEqual(reopened.review?.text, "Synthetic latest visible review")
    }

    func testFailedGoalAndMITFormAutosavesCannotBeReplacedByReload() async throws {
        let failure = StateWorkflowFailure(); let store = try store(failure)
        for kind in ["goal", "mit"] {
            let model = WorkflowViewModel(store: store)
            if kind == "goal" { await model.loadGoals() } else { await model.loadMIT(day) }
            let key = kind == "goal" ? "name" : "action"
            model.updateField(key, "Synthetic older \(kind) fields"); await model.flush()
            failure.failDrafts(true); model.updateField(key, "Synthetic latest visible \(kind)")
            let flushed = await model.flush(); XCTAssertFalse(flushed)
            if kind == "goal" { await model.loadGoals() } else { await model.loadMIT(day) }
            XCTAssertNotNil(model.error); XCTAssertEqual(model.fields[key], "Synthetic latest visible \(kind)")
            // Switching form type also cannot discard an outstanding write.
            if kind == "goal" { await model.loadMIT(day) } else { await model.loadGoals() }
            XCTAssertNotNil(model.error); XCTAssertEqual(model.fields[key], "Synthetic latest visible \(kind)")
            failure.failDrafts(false)
            if kind == "goal" { await model.loadGoals() } else { await model.loadMIT(day) }
            XCTAssertNil(model.error); XCTAssertEqual(model.fields[key], "Synthetic latest visible \(kind)")
            let reopened = WorkflowViewModel(store: try self.store())
            if kind == "goal" { await reopened.loadGoals() } else { await reopened.loadMIT(day) }
            XCTAssertEqual(reopened.fields[key], "Synthetic latest visible \(kind)")
        }
    }

    func testStateCreatesTwoGoalsAndEditsOneAsImmutableVersion() async throws {
        let store = try store(); let model = WorkflowViewModel(store: store); await model.loadGoals()
        for name in ["Synthetic practice", "Synthetic movement"] {
            model.editGoal(nil)
            for (key, value) in ["name": name, "definition": "Confirmed sessions", "unit": "sessions", "target": "3", "period": "Month", "stage": "Start"] {
                model.updateField(key, value)
            }
            await model.previewGoal(day: day); await model.confirmGoal(); XCTAssertNil(model.error)
        }
        let first = try XCTUnwrap(model.goals.first { $0.name == "Synthetic practice" })
        let other = try XCTUnwrap(model.goals.first { $0.name == "Synthetic movement" })
        let originalURL = root.appendingPathComponent("Goals/\(first.id.uuidString).md")
        let originalBytes = try Data(contentsOf: originalURL)
        model.editGoal(first); model.updateField("target", "5")
        await model.previewGoal(day: day)
        let edited = try XCTUnwrap(model.goalPreview)
        XCTAssertEqual(edited.previousID, first.id); XCTAssertEqual(edited.target, "5")
        await model.confirmGoal(); XCTAssertNil(model.error)
        let reopened = WorkflowViewModel(store: try self.store()); await reopened.loadGoals()
        XCTAssertEqual(Set(reopened.goals.map(\.id)), Set([edited.id, other.id]))
        XCTAssertEqual(reopened.goals.first { $0.id == other.id }, other)
        XCTAssertEqual(reopened.goalHistory.count, 3)
        XCTAssertEqual(reopened.goalHistory.first { $0.id == edited.id }?.previousID, first.id)
        XCTAssertEqual(try Data(contentsOf: originalURL), originalBytes)
    }

    func testDestinationLoadsWaitForInFlightWorkAndRouteWritesToCorrectForm() async throws {
        let store = try store()
        for source in ["goal", "mit"] {
            let sourceName = source == "goal" ? "goal" : "mit-\(day.value)"
            let destinationName = source == "goal" ? "mit-\(day.value)" : "goal"
            let sourceKey = source == "goal" ? "name" : "action"
            let destinationKey = source == "goal" ? "action" : "name"
            try await store.retainForm([sourceKey: "Synthetic old source"], name: sourceName)
            try await store.retainForm([destinationKey: "Synthetic destination"], name: destinationName)
            let model = WorkflowViewModel(store: store)
            if source == "goal" { await model.loadGoals() } else { await model.loadMIT(day) }
            let started = expectation(description: "Old operation started")
            var release: CheckedContinuation<Void, Never>?
            let action = Task {
                await model.run {
                    await withCheckedContinuation { continuation in release = continuation; started.fulfill() }
                    try await store.retainForm([sourceKey: "Synthetic completed source"], name: sourceName)
                }
            }
            await fulfillment(of: [started], timeout: 2)
            let destination = Task {
                if source == "goal" { await model.loadMIT(day) } else { await model.loadGoals() }
            }
            await Task.yield(); await Task.yield()
            XCTAssertTrue(model.busy); XCTAssertEqual(model.fields[sourceKey], "Synthetic old source")
            var reentered = false
            await model.run { reentered = true }
            XCTAssertFalse(reentered, "Commands must remain non-reentrant")
            release?.resume(); release = nil
            await action.value; await destination.value
            XCTAssertEqual(model.fields[destinationKey], "Synthetic destination")
            model.updateField(destinationKey, "Synthetic edited destination"); await model.flush()
            let oldForm = try await store.loadForm(sourceName); let newForm = try await store.loadForm(destinationName)
            XCTAssertEqual(oldForm, [sourceKey: "Synthetic completed source"])
            XCTAssertEqual(newForm[destinationKey], "Synthetic edited destination")
            XCTAssertNil(newForm[sourceKey])
        }
    }

    func testCancelledDestinationLoadDoesNotReplaceCurrentFormOrWaitForCommand() async throws {
        let store = try store(); let model = WorkflowViewModel(store: store); await model.loadGoals()
        model.updateField("name", "Synthetic current goal"); await model.flush()
        let started = expectation(description: "Command started")
        var release: CheckedContinuation<Void, Never>?
        let action = Task {
            await model.run { await withCheckedContinuation { continuation in release = continuation; started.fulfill() } }
        }
        await fulfillment(of: [started], timeout: 2)
        let cancelled = expectation(description: "Cancelled load returned")
        let destination = Task { await model.loadMIT(day); cancelled.fulfill() }
        await Task.yield(); await Task.yield(); destination.cancel()
        await fulfillment(of: [cancelled], timeout: 2)
        XCTAssertTrue(model.busy); XCTAssertEqual(model.fields["name"], "Synthetic current goal")
        release?.resume(); release = nil; await action.value; await destination.value
        model.updateField("name", "Synthetic still current goal"); await model.flush()
        let goal = try await store.loadForm("goal"); let mit = try await store.loadForm("mit-\(day.value)")
        XCTAssertEqual(goal["name"], "Synthetic still current goal"); XCTAssertTrue(mit.isEmpty)
    }

}
