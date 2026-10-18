import XCTest
import Foundation
import RijiCore
@testable import RijiState

private final class FileProbe: @unchecked Sendable {
    private let lock = NSLock()
    private var faults: [(FilePurpose, FileStage)] = []
    private var paused = false
    private var writes = 0
    let entered = DispatchSemaphore(value: 0)
    let release = DispatchSemaphore(value: 0)

    var draftWrites: Int { lock.lock(); defer { lock.unlock() }; return writes }
    func fail(_ purpose: FilePurpose, _ stage: FileStage) { failSequence([(purpose, stage)]) }
    func failSequence(_ values: [(FilePurpose, FileStage)]) { lock.lock(); faults = values; lock.unlock() }
    func pauseNextDraft() { lock.lock(); paused = true; lock.unlock() }

    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        lock.lock()
        if purpose == .draft && stage == .beforeWrite { writes += 1 }
        let shouldPause = paused && purpose == .draft && stage == .beforeWrite
        if shouldPause { paused = false }
        let shouldFail = faults.first.map { $0.0 == purpose && $0.1 == stage } ?? false
        if shouldFail { faults.removeFirst() }
        lock.unlock()
        if shouldPause { entered.signal(); _ = release.wait(timeout: .now() + 10) }
        if shouldFail { throw JournalError.storage }
    }
}

@MainActor
private final class StateFixture {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("RijiStateTests-\(UUID())")
    let day = try! JournalDay("2026-09-12")
    let probe = FileProbe()
    let store: LocalJournalStore
    var instant = ISO8601DateFormatter().date(from: "2026-09-12T08:00:00Z")!
    lazy var model = DiaryViewModel(now: { [unowned self] in self.instant }, storageFactory: { [store] in store })

    init(timeZone: TimeZone = TimeZone(identifier: "Asia/Shanghai")!) throws {
        let probe = self.probe
        store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"),
                                      stateDirectory: root.appendingPathComponent("State"),
                                      timeZone: timeZone,
                                      failure: { try probe.check($0, $1) })
    }
    func cleanup() { probe.release.signal(); try? FileManager.default.removeItem(at: root) }
}

@MainActor
final class DiaryViewModelTests: XCTestCase {
    func testDayChangeNotificationRefreshesTodayWithoutMovingDraft() async throws {
        let fixture = try StateFixture(); defer { fixture.cleanup() }
        let model = fixture.model
        let notifications = NotificationCenter()
        model.observeDayChanges(notifications)
        await model.start()
        model.updateText("Retain the original date")
        await model.flushDraft()
        fixture.instant = ISO8601DateFormatter().date(from: "2026-09-13T08:00:00Z")!
        notifications.post(name: .NSCalendarDayChanged, object: nil)
        await Task.yield()
        XCTAssertEqual(model.today.value, "2026-09-13")
        XCTAssertEqual(model.selectedDay, fixture.day)
        XCTAssertEqual(model.text, "Retain the original date")
    }

    func testForegroundTickUsesFixedJournalZoneWithoutMovingDraft() async throws {
        let zone = TimeZone(identifier: "Pacific/Kiritimati")!
        let fixture = try StateFixture(timeZone: zone); defer { fixture.cleanup() }
        fixture.instant = ISO8601DateFormatter().date(from: "2026-09-12T09:59:00Z")!
        let model = fixture.model
        await model.start()
        model.updateText("Travel draft stays on its original day")
        await model.flushDraft()
        let original = model.selectedDay
        let systemDay = JournalDay(date: fixture.instant, timeZone: .current)
        XCTAssertNotEqual(zone.secondsFromGMT(for: fixture.instant), TimeZone.current.secondsFromGMT(for: fixture.instant))
        fixture.instant = ISO8601DateFormatter().date(from: "2026-09-12T10:01:00Z")!
        XCTAssertEqual(JournalDay(date: fixture.instant, timeZone: .current), systemDay)
        model.foregroundTick(isActive: false)
        XCTAssertEqual(model.today, original)
        model.foregroundTick(isActive: true)
        XCTAssertEqual(model.today.value, "2026-09-13")
        XCTAssertEqual(model.selectedDay, original)
        XCTAssertEqual(model.text, "Travel draft stays on its original day")
    }

    func testQueuedTypingSkipsSupersededWritesAndFlushesLatest() async throws {
        let fixture = try StateFixture(); defer { fixture.cleanup() }
        let model = fixture.model
        await model.start()
        for index in 0..<30 { model.updateText("Visible revision \(index)") }
        await model.flushDraft()
        let snapshot = try await fixture.store.open(fixture.day)
        XCTAssertEqual(snapshot.draft?.text, "Visible revision 29")
        XCTAssertEqual(fixture.probe.draftWrites, 1)
        XCTAssertEqual(model.text, "Visible revision 29")
    }

    func testSourceReviewBlocksNavigationAndSaveUntilCurrentDraftIsFlushed() async throws {
        let fixture = try StateFixture(); defer { fixture.cleanup() }
        let model = fixture.model
        await model.start()
        fixture.probe.pauseNextDraft()
        model.updateText("Pending text")
        for _ in 0..<100 where fixture.probe.draftWrites == 0 { try await Task.sleep(nanoseconds: 1_000_000) }
        let review = Task { await model.reviewCurrentSource() }
        await Task.yield()
        XCTAssertTrue(model.isBusy)
        let navigated = await model.navigate(to: try JournalDay("2026-09-13"))
        XCTAssertFalse(navigated)
        await model.save()
        fixture.probe.release.signal()
        await review.value
        XCTAssertEqual(model.selectedDay, fixture.day)
        XCTAssertEqual(model.text, "Pending text")
        XCTAssertFalse(model.isBusy)
        let snapshot = try await fixture.store.open(fixture.day)
        XCTAssertNil(snapshot.sourceHash)
        XCTAssertEqual(snapshot.draft?.text, "Pending text")
    }

    func testBeforeDiaryReplacementFailureRetainsDraftAndRetriesOriginalOperation() async throws {
        try await checkSaveFailure(.diary, .beforeReplace)
    }

    func testAfterReceiptPersistenceFailureRetainsDraftAndRetriesOriginalOperation() async throws {
        try await checkSaveFailure(.receipt, .afterReplace)
    }

    private func checkSaveFailure(_ purpose: FilePurpose, _ stage: FileStage) async throws {
        let fixture = try StateFixture(); defer { fixture.cleanup() }
        let model = fixture.model
        await model.start()
        model.updateSection("🌆 Evening")
        model.updateText("Earlier text")
        model.updateText("Latest visible e\u{0301}\nExact whitespace  ")
        await model.flushDraft()
        let original = try await fixture.store.open(fixture.day).draft!
        fixture.probe.fail(purpose, stage)
        await model.save()
        XCTAssertEqual(Data(model.text.utf8), Data(original.text.utf8))
        XCTAssertEqual(model.selectedDay, fixture.day)
        XCTAssertEqual(model.section, "🌆 Evening")
        XCTAssertNotNil(model.errorMessage)
        XCTAssertFalse(model.status.contains("已保存在手机"))
        XCTAssertTrue(model.isPending)
        model.updateText("Must be blocked")
        model.updateSection("🧠 Notes")
        XCTAssertEqual(Data(model.text.utf8), Data(original.text.utf8))
        XCTAssertEqual(model.section, original.section)
        let retained = try await fixture.store.open(fixture.day)
        XCTAssertEqual(retained.draft?.operationID, original.operationID)
        await model.save()
        XCTAssertEqual(model.status, "已保存在手机")
        XCTAssertNil(model.errorMessage)
        XCTAssertFalse(model.isPending)
        let data = try await fixture.store.exportMarkdown(fixture.day)
        XCTAssertEqual(String(decoding: data, as: UTF8.self).components(separatedBy: original.text).count, 2)
        let receipt = try await fixture.store.commit(original)
        XCTAssertEqual(receipt.operationID, original.operationID)
    }

    func testUnicodeEquivalentVisibleEditPersistsLatestBytes() async throws {
        let fixture = try StateFixture(); defer { fixture.cleanup() }
        let model = fixture.model
        await model.start()
        model.updateText("\u{00E9}")
        await model.flushDraft()
        model.updateText("e\u{0301}")
        await model.save()
        let actual = try await fixture.store.exportMarkdown(fixture.day)
        XCTAssertNotNil(actual.range(of: Data([0x65, 0xcc, 0x81])))
        XCTAssertNil(actual.range(of: Data([0xc3, 0xa9])))
    }

    func testCapturedExportKeepsDayFilenameBytesAndExcludesUnconfirmedDraft() async throws {
        let fixture = try StateFixture(); defer { fixture.cleanup() }
        let model = fixture.model
        await model.start()
        model.updateText("Confirmed original")
        await model.save()
        let expected = try await fixture.store.exportMarkdown(fixture.day)
        model.updateText("Unconfirmed private draft")
        await model.flushDraft()
        let export = Task { await model.export(day: fixture.day) }
        _ = await model.navigate(to: try JournalDay("2026-09-13"))
        let document = await export.value!
        XCTAssertEqual(document.filename, "2026-09-12.md")
        XCTAssertEqual(document.data, expected)
        XCTAssertEqual(document.makeFileWrapper().regularFileContents, expected)
        let destination = fixture.root.appendingPathComponent(document.filename)
        try document.makeFileWrapper().write(to: destination, options: .atomic, originalContentsURL: nil)
        try model.verifyExportForTesting(destination, document: document)
        model.exportCompleted(.success(destination), document: document)
        XCTAssertEqual(model.status, "Markdown 已导出")
        XCTAssertEqual(model.exportVerificationDigest, SourceHash.digest(try Data(contentsOf: destination)))
        XCTAssertFalse(String(decoding: try Data(contentsOf: destination), as: UTF8.self).contains("Unconfirmed private draft"))
        try Data("Mismatched bytes".utf8).write(to: destination)
        XCTAssertThrowsError(try model.verifyExportForTesting(destination, document: document))
    }
    func testStyledNotesSelectsRawAnchorDisplaysInlineStyleAndExportsExactBytes() async throws {
        let fixture = try StateFixture(); defer { fixture.cleanup() }
        let source = "---\r\ndate: {{date:YYYY-MM-DD}}\r\n---\r\n# {{title}} {{date:dddd}}\r\n## Alpha\r\n## 🧠  **Notes**\r\n"
        let version = try TemplateVersion(kind: .daily, raw: Data(source.utf8))
        try await fixture.store.importTemplate(version, operationID: UUID()); try await fixture.store.selectTemplate(version)
        await fixture.model.start()
        XCTAssertEqual(fixture.model.section, "🧠  **Notes**")
        let styled = DiaryHeadingPresentation.styled("🧠  **Notes**")
        XCTAssertEqual(String(styled.characters), "🧠  Notes")
        XCTAssertTrue(styled.runs.contains { ($0.inlinePresentationIntent ?? []).contains(.stronglyEmphasized) })
        let italic = DiaryHeadingPresentation.styled("🧠 _Notes_")
        XCTAssertEqual(String(italic.characters), "🧠 Notes")
        XCTAssertTrue(italic.runs.contains { ($0.inlinePresentationIntent ?? []).contains(.emphasized) })
        XCTAssertEqual(fixture.model.templateLabel, version.label)
        fixture.model.updateText("Synthetic literal {{date:dddd}}")
        await fixture.model.flushDraft()
        try await fixture.store.setWeekdayLanguage(.english)
        _ = await fixture.model.navigate(to: fixture.day)
        XCTAssertEqual(fixture.model.templateLabel, "草稿绑定的原模板")
        await fixture.model.save()
        let actual = try await fixture.store.exportMarkdown(fixture.day)
        let expected = "---\r\ndate: 2026-09-12\r\n---\r\n# 2026-09-12 星期六\r\n## Alpha\r\n## 🧠  **Notes**\r\nSynthetic literal {{date:dddd}}\r\n"
        XCTAssertEqual(actual, Data(expected.utf8))
    }

    func testEquivalentNotesOccurrencesRequireExplicitSelectionBeforeSave() async throws {
        for headings in ["## **🧠 Notes**\n## 🧠 Notes\n", "## **🧠 Notes**\n## **🧠 Notes**\n", "# 🧠 Notes\n## **🧠 Notes**\n"] {
            let fixture = try StateFixture(); defer { fixture.cleanup() }
            let version = try TemplateVersion(kind: .daily, raw: Data(("## Alpha\n" + headings).utf8))
            try await fixture.store.importTemplate(version, operationID: UUID()); try await fixture.store.selectTemplate(version)
            await fixture.model.start(); fixture.model.updateText("Retained synthetic text"); await fixture.model.flushDraft()
            XCTAssertEqual(fixture.model.section, DiaryViewModel.unselectedSection); XCTAssertFalse(fixture.model.canSave)
            await fixture.model.save(); let snapshot = try await fixture.store.open(fixture.day); XCTAssertFalse(snapshot.isSaved)
            fixture.model.updateSection("Alpha"); await fixture.model.save(); XCTAssertTrue(fixture.model.isSaved)
        }
    }

    func testFencedCommentNotesIgnoredAndNoNotesKeepsExistingFirstSection() async throws {
        for source in ["## Zebra\n## Alpha\n```md\n## **🧠 Notes**\n```\n<!--\n## 🧠 Notes\n-->\n", "## Alpha\n~~~\n## 🧠 Notes\n~~~\n## _🧠 Notes_\n"] {
            let fixture = try StateFixture(); defer { fixture.cleanup() }
            let version = try TemplateVersion(kind: .daily, raw: Data(source.utf8))
            try await fixture.store.importTemplate(version, operationID: UUID()); try await fixture.store.selectTemplate(version)
            await fixture.model.start()
            XCTAssertEqual(fixture.model.section, source.contains("_🧠 Notes_") ? "_🧠 Notes_" : "Alpha")
        }
    }

    func testWeekdayUIOnlyPublishesPersistedChoiceAndKeepsFrozenBlankPreview() async throws {
        let fixture = try StateFixture(); defer { fixture.cleanup() }
        let version = try TemplateVersion(kind: .daily, raw: Data("# {{date:dddd}}\n## 🧠 Notes\n".utf8))
        try await fixture.store.importTemplate(version, operationID: UUID()); try await fixture.store.selectTemplate(version)
        let workflow = WorkflowViewModel(store: fixture.store); await workflow.loadTemplates(.daily)
        XCTAssertEqual(workflow.weekdayLanguage, .chinese)
        await workflow.prepareBlank(fixture.day); let preview = workflow.blank!
        fixture.probe.fail(.metadata, .beforeReplace); await workflow.chooseWeekdayLanguage(.english)
        XCTAssertEqual(workflow.weekdayLanguage, .chinese); XCTAssertNotNil(workflow.error)
        await workflow.chooseWeekdayLanguage(.english)
        XCTAssertEqual(workflow.weekdayLanguage, .english); XCTAssertNil(workflow.error)
        XCTAssertEqual(workflow.blank, preview)
        let relaunched = WorkflowViewModel(store: fixture.store); await relaunched.loadTemplates(.daily)
        XCTAssertEqual(relaunched.weekdayLanguage, .english)
        let confirmed = await workflow.confirmBlank(); XCTAssertTrue(confirmed)
        let actual = try await fixture.store.exportMarkdown(fixture.day); XCTAssertEqual(actual, Data("# 星期六\n## 🧠 Notes\n".utf8))
    }

    func testReviewFixCorruptPreferenceRelaunchRestoresOldContentAndDraft() async throws {
        let fixture = try StateFixture(); defer { fixture.cleanup() }
        await fixture.model.start(); fixture.model.updateText("Saved original"); await fixture.model.save()
        let before = try await fixture.store.exportMarkdown(fixture.day)
        fixture.model.updateText("Retained old literal {{date:dddd}}"); await fixture.model.flushDraft()
        let original = try await fixture.store.open(fixture.day).draft!
        try Data("invalid preference".utf8).write(to: fixture.root.appendingPathComponent("State/weekday-language.json"))
        let reopened = DiaryViewModel(now: { fixture.instant }, storageFactory: { fixture.store })
        await reopened.start()
        XCTAssertTrue(reopened.isReady); XCTAssertNil(reopened.errorMessage)
        XCTAssertEqual(Data(reopened.markdown.utf8), before); XCTAssertEqual(reopened.text, original.text)
        XCTAssertEqual(reopened.section, original.section); XCTAssertEqual(reopened.selectedDay, fixture.day)
        await reopened.save()
        let receipt = try await fixture.store.commit(original); XCTAssertEqual(receipt.operationID, original.operationID)
    }

    func testReviewFixFailedNavigationKeepsCompleteCurrentPageAndSourceBinding() async throws {
        let fixture = try StateFixture(); defer { fixture.cleanup() }
        let model = fixture.model; await model.start()
        model.updateText("Saved source remains current"); await model.save()
        let before = model.markdown; let section = model.section; let label = model.templateLabel
        let tomorrow = try JournalDay("2026-09-13")
        fixture.probe.fail(.metadata, .beforeWrite)
        let moved = await model.navigate(to: tomorrow)
        XCTAssertFalse(moved); XCTAssertEqual(model.selectedDay, fixture.day)
        XCTAssertEqual(Data(model.markdown.utf8), Data(before.utf8)); XCTAssertEqual(model.section, section)
        XCTAssertEqual(model.templateLabel, label); XCTAssertTrue(model.isSaved); XCTAssertFalse(model.isPending)
        XCTAssertEqual(model.text, ""); XCTAssertNotNil(model.errorMessage); XCTAssertFalse(model.isBusy)
        try Data("invalid".utf8).write(to: fixture.root.appendingPathComponent("State/weekday-language.json"))
        let second = await model.navigate(to: tomorrow); XCTAssertFalse(second)
        XCTAssertEqual(model.selectedDay, fixture.day); XCTAssertEqual(model.markdown, before)
        model.updateText("Still appended to the original day"); await model.save()
        XCTAssertNil(model.errorMessage)
        let actual = try await fixture.store.exportMarkdown(fixture.day)
        XCTAssertTrue(String(decoding: actual, as: UTF8.self).contains("Still appended to the original day"))
        XCTAssertFalse(FileManager.default.fileExists(atPath: fixture.root.appendingPathComponent("Journals/2026-09-13.md").path))
    }

    func testReviewFixEveryNotesCandidateCanBeExplicitlySelectedAfterRelaunch() async throws {
        for raw in ["🧠 Notes", "**🧠 Notes**"] {
            let fixture = try StateFixture(); defer { fixture.cleanup() }
            let source = "# 🧠 Notes\n## Child\n# Other\n## **🧠 Notes**\n"
            let version = try TemplateVersion(kind: .daily, raw: Data(source.utf8))
            try await fixture.store.importTemplate(version, operationID: UUID()); try await fixture.store.selectTemplate(version)
            await fixture.model.start()
            XCTAssertTrue(fixture.model.sections.contains("🧠 Notes")); XCTAssertTrue(fixture.model.sections.contains("**🧠 Notes**"))
            XCTAssertTrue(fixture.model.requiresSectionSelection)
            fixture.model.updateText("Explicit synthetic target"); await fixture.model.flushDraft()
            let reopened = DiaryViewModel(now: { fixture.instant }, storageFactory: { fixture.store })
            await reopened.start(); XCTAssertTrue(reopened.requiresSectionSelection); XCTAssertFalse(reopened.canSave)
            XCTAssertEqual(reopened.text, "Explicit synthetic target")
            reopened.updateSection(raw); await reopened.flushDraft(); XCTAssertTrue(reopened.canSave)
            await reopened.save(); XCTAssertNil(reopened.errorMessage)
            let actual = try await fixture.store.exportMarkdown(fixture.day)
            let expected = raw == "🧠 Notes" ? "# 🧠 Notes\n## Child\nExplicit synthetic target\n# Other\n## **🧠 Notes**\n"
                : source + "Explicit synthetic target\n"
            XCTAssertEqual(actual, Data(expected.utf8))
        }
    }

    func testReviewFixEmptyRawHeadingDraftKeepsLegacyIdentityAcrossRelaunchAndSave() async throws {
        let fixture = try StateFixture(); defer { fixture.cleanup() }
        let source = "# Synthetic\n##\n## After\n"
        let original = JournalDraft(day: fixture.day, section: "", text: "Empty raw heading record", sourceHash: nil, effectiveTemplate: source)
        try await fixture.store.saveDraft(original)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let before = try encoder.encode(original)
        await fixture.model.start()
        XCTAssertEqual(fixture.model.section, ""); XCTAssertFalse(fixture.model.requiresSectionSelection)
        XCTAssertTrue(fixture.model.sections.contains("")); XCTAssertTrue(fixture.model.canSave)
        let restored = try await fixture.store.open(fixture.day).draft!
        XCTAssertEqual(try encoder.encode(restored), before)
        await fixture.model.save(); XCTAssertNil(fixture.model.errorMessage)
        let receipt = try await fixture.store.commit(original); XCTAssertEqual(receipt.operationID, original.operationID)
        let actual = try await fixture.store.exportMarkdown(fixture.day)
        XCTAssertEqual(actual, Data("# Synthetic\n##\nEmpty raw heading record\n## After\n".utf8))
    }

    func testReviewFixExactDuplicateHeadingRejectsExplicitSaveAndRetainsText() async throws {
        let fixture = try StateFixture(); defer { fixture.cleanup() }
        let source = "## **🧠 Notes**\n## **🧠 Notes**\n"
        let version = try TemplateVersion(kind: .daily, raw: Data(source.utf8))
        try await fixture.store.importTemplate(version, operationID: UUID()); try await fixture.store.selectTemplate(version)
        await fixture.model.start(); fixture.model.updateText("Retain duplicate target text")
        fixture.model.updateSection("**🧠 Notes**"); await fixture.model.flushDraft()
        let original = try await fixture.store.open(fixture.day).draft!
        await fixture.model.save()
        XCTAssertEqual(fixture.model.errorMessage, JournalError.ambiguousSection.localizedDescription)
        XCTAssertEqual(fixture.model.text, original.text); XCTAssertFalse(fixture.model.isSaved)
        let restored = try await fixture.store.open(fixture.day).draft; XCTAssertEqual(restored, original)
        let reopened = DiaryViewModel(now: { fixture.instant }, storageFactory: { fixture.store })
        await reopened.start(); XCTAssertEqual(reopened.text, original.text)
        XCTAssertEqual(reopened.section, original.section); await reopened.save()
        XCTAssertEqual(reopened.errorMessage, JournalError.ambiguousSection.localizedDescription)
    }

    func testReviewFixLinkAndImageLabelsDoNotCreateAutomaticNotesMatches() async throws {
        let fixture = try StateFixture(); defer { fixture.cleanup() }
        let link = "[🧠 Notes](https://example.invalid)"; let image = "![🧠 Notes](synthetic.png)"
        XCTAssertFalse(DiaryHeadingPresentation.isNotes(link)); XCTAssertFalse(DiaryHeadingPresentation.isNotes(image))
        XCTAssertFalse(DiaryHeadingPresentation.isNotes("&#78;otes"))
        let source = "## \(link)\n## \(image)\n## 🧠  **Notes**\n"
        let version = try TemplateVersion(kind: .daily, raw: Data(source.utf8))
        try await fixture.store.importTemplate(version, operationID: UUID()); try await fixture.store.selectTemplate(version)
        await fixture.model.start(); XCTAssertEqual(fixture.model.section, "🧠  **Notes**")
        XCTAssertFalse(fixture.model.requiresSectionSelection)
        XCTAssertTrue(fixture.model.sections.contains(link)); XCTAssertTrue(fixture.model.sections.contains(image))
        fixture.model.updateSection(link); fixture.model.updateText("Explicit link heading record"); await fixture.model.save()
        let bytes = try await fixture.store.exportMarkdown(fixture.day)
        XCTAssertEqual(bytes, Data("## \(link)\nExplicit link heading record\n## \(image)\n## 🧠  **Notes**\n".utf8))
    }

    func testReviewFixConsecutivePreferenceFaultsNeverPublishTheUncommittedChoice() async throws {
        let fixture = try StateFixture(); defer { fixture.cleanup() }
        try await fixture.store.setWeekdayLanguage(.english)
        let model = WorkflowViewModel(store: fixture.store); await model.loadTemplates(.daily)
        fixture.probe.failSequence([(.metadata, .afterReplace), (.metadata, .beforeWrite)])
        await model.chooseWeekdayLanguage(.chinese)
        XCTAssertEqual(model.weekdayLanguage, .english); XCTAssertNotNil(model.error)
        let reopened = WorkflowViewModel(store: fixture.store); await reopened.loadTemplates(.daily)
        XCTAssertEqual(reopened.weekdayLanguage, .english)
        await reopened.chooseWeekdayLanguage(.chinese)
        XCTAssertEqual(reopened.weekdayLanguage, .chinese); XCTAssertNil(reopened.error)
    }

    func testReviewFixBlankCollisionRetainsCandidateUntilExplicitFreshPreview() async throws {
        let fixture = try StateFixture(); defer { fixture.cleanup() }
        let model = WorkflowViewModel(store: fixture.store); await model.prepareBlank(fixture.day)
        let original = model.blank!
        let mit = try await fixture.store.prepareMIT(MITEvent(day: fixture.day, action: "MIT after preview", criterion: "Once"), section: "🌅 Morning")
        try await fixture.store.commitMIT(mit); let bytes = try await fixture.store.exportMarkdown(fixture.day)
        for _ in 0..<2 {
            let confirmed = await model.confirmBlank(); XCTAssertFalse(confirmed)
            XCTAssertEqual(model.blank, original); XCTAssertEqual(model.error, JournalError.changedSource.localizedDescription)
        }
        await model.prepareBlank(fixture.day); let reviewed = model.blank!
        XCTAssertNotEqual(reviewed.operationID, original.operationID); XCTAssertEqual(Data(reviewed.preview.utf8), bytes)
        let confirmed = await model.confirmBlank(); XCTAssertTrue(confirmed); XCTAssertNil(model.blank)
        let after = try await fixture.store.exportMarkdown(fixture.day); XCTAssertEqual(after, bytes)
    }

}
