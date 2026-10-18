import XCTest
import Foundation
import Dispatch
import RijiCore
@testable import RijiState

private final class BodyStateFailure: @unchecked Sendable {
    let lock = NSLock()
    private var failing = false
    private var purpose: FilePurpose = .bodyDiary
    func set(_ value: Bool, purpose: FilePurpose = .bodyDiary) { lock.lock(); failing = value; self.purpose = purpose; lock.unlock() }
    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        lock.lock(); defer { lock.unlock() }
        if failing && purpose == self.purpose && stage == .beforeReplace { throw JournalError.storage }
    }
}

@MainActor
final class BodyEditorStateTests: XCTestCase {
    private func assertTrue(_ value: Bool, file: StaticString = #filePath, line: UInt = #line) { XCTAssertTrue(value, file: file, line: line) }
    private func assertFalse(_ value: Bool, file: StaticString = #filePath, line: UInt = #line) { XCTAssertFalse(value, file: file, line: line) }
    private func assertNil<T>(_ value: T?, file: StaticString = #filePath, line: UInt = #line) { XCTAssertNil(value, file: file, line: line) }
    private let day = try! JournalDay("2026-09-14")
    private let instant = ISO8601DateFormatter().date(from: "2026-09-14T08:00:00Z")!

    private func fixture() throws -> (URL, LocalJournalStore, BodyStateFailure) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("BodyStateSynthetic-\(UUID())")
        let failure = BodyStateFailure()
        let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), failure: { try failure.check($0, $1) })
        return (root, store, failure)
    }

    func testCoalescedBodyTypingAndMiddleEditSurviveRelaunchAndExport() async throws {
        let (root, store, _) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        let initial = model.markdown
        for index in 0..<30 { model.updateBody(initial + "Synthetic line \(index)\n") }
        assertTrue(await model.flushBody())
        let edit = model.markdown.replacingOccurrences(of: "line 29", with: "正文 e\u{301}")
        model.updateBody(edit); assertTrue(await model.flushBody())
        let reopened = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await reopened.start()
        XCTAssertEqual(Data(reopened.markdown.utf8), Data(edit.utf8)); XCTAssertFalse(reopened.bodyDirty)
        let exported = await reopened.export(day: day)
        XCTAssertEqual(exported?.data, Data(edit.utf8))
        let versions = try await store.bodyRevisions(day); XCTAssertEqual(versions.count, 2)
    }

    func testPersistentFailureBlocksNavigationExportAndWorkflowWithoutLosingLaterTyping() async throws {
        let (root, store, failure) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        failure.set(true)
        model.updateBody(model.markdown + "First pending\n"); assertFalse(await model.flushBody())
        model.updateBody(model.markdown + "Latest pending\n"); assertFalse(await model.flushBody())
        let content = model.markdown
        assertFalse(await model.navigate(to: try JournalDay("2026-09-15")))
        assertFalse(await model.prepareDirectory()); assertNil(await model.export(day: day))
        XCTAssertEqual(model.markdown, content); XCTAssertEqual(model.selectedDay, day); XCTAssertTrue(model.bodyDirty)
        let reopened = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await reopened.start()
        XCTAssertEqual(reopened.markdown, content)
        failure.set(false)
        assertTrue(await reopened.flushBody())
        let saved = try await store.exportMarkdown(day); XCTAssertEqual(saved, Data(content.utf8))
        let versions = try await store.bodyRevisions(day)
        XCTAssertEqual(versions.count, 2)
    }

    func testRelaunchRecoversEarlierSaveThenRebasesOnlyItsLaterRetainedTyping() async throws {
        let (root, store, failure) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        failure.set(true)
        model.updateBody(model.markdown + "First pending\n"); assertFalse(await model.flushBody())
        model.updateBody(model.markdown + "Later retained\n"); assertFalse(await model.flushBody())
        let content = model.markdown
        failure.set(false)
        let reopened = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await reopened.start()
        XCTAssertEqual(reopened.markdown, content)
        assertTrue(await reopened.flushBody())
        XCTAssertNil(reopened.bodyError)
        let saved = try await store.exportMarkdown(day); XCTAssertEqual(saved, Data(content.utf8))
    }

    func testDirectoryWorkflowReloadDoesNotLetNextAutosaveOverwriteMIT() async throws {
        let (root, store, _) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        model.updateBody(model.markdown + "Before directory\n")
        assertTrue(await model.prepareDirectory())
        let event = MITEvent(day: day, action: "Synthetic action", criterion: "One result")
        let mit = try await store.prepareMIT(event, section: "🧠 Notes")
        try await store.commitMIT(mit)
        await model.directoryClosed()
        XCTAssertTrue(model.markdown.contains("Synthetic action"))
        model.updateBody(model.markdown + "After directory\n")
        assertTrue(await model.flushBody())
        let current = try await store.currentMIT(day); XCTAssertEqual(current?.id, event.id)
    }

    func testLegacyDraftVisibleAfterOpeningAndManualBodyEditingDoesNotCommitIt() async throws {
        let (root, store, _) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let source = try await store.open(day)
        let draft = JournalDraft(day: day, section: "🧠 Notes", text: "Legacy evidence", sourceHash: nil, effectiveTemplate: source.markdown)
        try await store.saveDraft(draft)
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        XCTAssertTrue(model.hasLegacyDraft); XCTAssertEqual(model.text, draft.text); XCTAssertFalse(model.markdown.contains(draft.text))
        model.updateBody(model.markdown + "Manual body\n"); assertTrue(await model.flushBody())
        let retained = try await store.open(day).draft; XCTAssertEqual(retained, draft)
        await model.reviewCurrentSource(); await model.save()
        XCTAssertFalse(model.hasLegacyDraft); XCTAssertTrue(model.markdown.contains(draft.text))
    }

    func testSourceConflictReviewKeepsVisibleBodyUntilExplicitMerge() async throws {
        let (root, store, _) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        model.updateBody(model.markdown + "Original\n"); assertTrue(await model.flushBody())
        let external = model.markdown + "Other writer\n"
        try Data(external.utf8).write(to: root.appendingPathComponent("Journals/2026-09-14.md"))
        let local = model.markdown + "Local edit\n"
        model.updateBody(local); assertFalse(await model.flushBody())
        await model.reviewBodyConflict(); XCTAssertEqual(model.markdown, local)
        XCTAssertEqual(model.bodyConflict?.source, Data(external.utf8))
        let merged = external + "Local edit\n"
        assertTrue(await model.resolveBodyConflict(with: merged))
        XCTAssertEqual(model.markdown, merged); XCTAssertNil(model.bodyError)
    }
}

private final class BodySaveGate: @unchecked Sendable {
    private let condition = NSCondition()
    private var blocked = true
    private var reached = false
    private let purpose: FilePurpose
    private let stage: FileStage
    init(purpose: FilePurpose = .bodyDiary, stage: FileStage = .beforeReplace) { self.purpose = purpose; self.stage = stage }
    func check(_ purpose: FilePurpose, _ stage: FileStage) {
        guard purpose == self.purpose, stage == self.stage else { return }
        condition.lock(); defer { condition.unlock() }
        reached = true
        while blocked { condition.wait() }
    }
    var isReached: Bool { condition.lock(); defer { condition.unlock() }; return reached }
    func release() { condition.lock(); blocked = false; condition.broadcast(); condition.unlock() }
}

extension BodyEditorStateTests {
    func testAdjacentDaysUseLiteralCalendarLabelsAcrossClockBoundaries() async throws {
        let cases = [
            ("2025-01-01", "2024-12-31", "2025-01-02", "Asia/Shanghai"),
            ("2024-02-29", "2024-02-28", "2024-03-01", "America/Los_Angeles"),
            ("2026-03-01", "2026-02-28", "2026-03-02", "Asia/Shanghai"),
            ("2026-03-08", "2026-03-07", "2026-03-09", "America/Los_Angeles"),
            ("2026-11-01", "2026-10-31", "2026-11-02", "America/Los_Angeles"),
            ("2018-11-04", "2018-11-03", "2018-11-05", "America/Sao_Paulo"),
            ("2011-12-30", "2011-12-29", "2011-12-31", "Pacific/Apia")
        ]
        for (current, previous, next, identifier) in cases {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent("NavigationCalendar-\(UUID())")
            defer { try? FileManager.default.removeItem(at: root) }
            let zone = try XCTUnwrap(TimeZone(identifier: identifier))
            let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), timeZone: zone)
            let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
            XCTAssertEqual(model.today, JournalDay(date: instant, timeZone: zone))
            let source = try JournalDay(current)
            for (direction, expected) in [(DiaryDateDirection.previous, previous), (.next, next)] {
                assertTrue(await model.navigate(to: source))
                assertTrue(await model.navigateToAdjacentDay(direction, from: source))
                XCTAssertEqual(model.selectedDay.value, expected, identifier)
                XCTAssertEqual(model.timeZone.identifier, identifier)
                XCTAssertFalse(model.isSaved, "Browsing an absent date must not create a diary")
                XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("Journals/\(expected).md").path))
                let reverse: DiaryDateDirection = direction == .previous ? .next : .previous
                assertTrue(await model.navigateToAdjacentDay(reverse, from: model.selectedDay))
                XCTAssertEqual(model.selectedDay, source, "The skipped local label remains reachable in both directions")
            }
        }
    }

    func testAdjacentDaysRejectUnsupportedYearBoundsWithoutChangingInput() async throws {
        let (root, store, _) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        model.bodyQuietNanoseconds = 60_000_000_000; model.bodyCheckpointNanoseconds = 60_000_000_000
        for (label, direction, inward) in [("0001-01-01", DiaryDateDirection.previous, "0001-01-02"), ("9999-12-31", .next, "9999-12-30")] {
            let boundary = try JournalDay(label)
            assertTrue(await model.navigate(to: boundary))
            model.updateBody(model.markdown + "Unsaved boundary input \(label)\n")
            let content = model.markdown, expected = Data(model.markdown.utf8)
            var retained: BodyEdit?
            for _ in 0..<300 {
                retained = try await store.retainedBodyEdit(boundary)
                if retained?.content == expected { break }
                try await Task.sleep(nanoseconds: 2_000_000)
            }
            let before = try XCTUnwrap(retained)
            XCTAssertEqual(before.day, boundary); XCTAssertEqual(before.content, expected); XCTAssertNil(before.before)
            XCTAssertTrue(model.bodyDirty)
            assertFalse(await model.navigateToAdjacentDay(direction, from: boundary))
            XCTAssertEqual(model.selectedDay, boundary); XCTAssertEqual(model.markdown, content)
            XCTAssertTrue(model.bodyDirty); XCTAssertFalse(model.isBusy); XCTAssertFalse(model.isSaved)
            let after = try await store.retainedBodyEdit(boundary)
            XCTAssertEqual(after, before, "An out-of-range swipe must preserve the same recovery identity and source-day binding")
            XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("Journals/\(label).md").path))
            assertTrue(await model.navigateToAdjacentDay(direction == .previous ? .next : .previous, from: boundary))
            XCTAssertEqual(model.selectedDay.value, inward); XCTAssertFalse(model.bodyDirty)
            let saved = try await store.exportMarkdown(boundary)
            XCTAssertEqual(saved, expected, "Allowed navigation must save the dirty input to its original boundary date")
        }
    }

    func testFutureMissingDayPreviewsOriginalTemplateWithoutCreatingFiles() async throws {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("AdjacentOriginal-\(UUID())")
        defer { try? FileManager.default.removeItem(at: base) }
        let root = base.appendingPathComponent("riji")
        for folder in ["daily", "templates"] {
            try FileManager.default.createDirectory(at: root.appendingPathComponent(folder), withIntermediateDirectories: true)
        }
        let template = "# Original {{date}}\r\nUnknown {{keep}}\r\n| A | B |\r\n| --- | --- |\r\n"
        let original = Data("# Existing original\r\nUnchanged e\u{301} 🧠\r\n".utf8)
        try Data(template.utf8).write(to: root.appendingPathComponent("templates/daily.md"))
        let sourceFile = root.appendingPathComponent("daily/2026-09-14.md")
        try original.write(to: sourceFile)
        try Data("# Later existing file\n".utf8).write(to: root.appendingPathComponent("daily/2026-09-17.md"))
        let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base)
        let model = DiaryViewModel(now: { self.instant }, useOriginalCollection: true, vaultStore: browser)
        await model.start(); await model.selectOriginalFolder(root)
        assertTrue(await model.navigateToAdjacentDay(.next, from: day))
        XCTAssertEqual(model.selectedDay.value, "2026-09-15")
        XCTAssertEqual(Data(model.markdown.utf8), Data(template.replacingOccurrences(of: "{{date}}", with: "2026-09-15").utf8))
        XCTAssertFalse(model.isSaved); XCTAssertFalse(model.bodyDirty)
        assertTrue(await model.navigateToAdjacentDay(.previous, from: model.selectedDay))
        XCTAssertEqual(Data(model.markdown.utf8), original)
        XCTAssertEqual(try Data(contentsOf: sourceFile), original)
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("daily/2026-09-15.md").path))
        XCTAssertEqual(try String(contentsOf: root.appendingPathComponent("templates/daily.md")), template)
    }

    func testBothDirectionsAndTabDepartureKeepFailedBodyThenSaveBeforeLeaving() async throws {
        for (direction, expectedDay) in [(DiaryDateDirection.previous, "2026-09-13"), (.next, "2026-09-15")] {
            let (root, store, failure) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
            let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
            failure.set(true)
            model.updateBody(model.markdown + "Pending 中文 e\u{301}\r\n")
            let expected = Data(model.markdown.utf8)
            assertFalse(await model.navigateToAdjacentDay(direction, from: day)); assertFalse(await model.prepareMainDestination())
            XCTAssertEqual(model.selectedDay, day); XCTAssertEqual(Data(model.markdown.utf8), expected)
            XCTAssertNotNil(model.bodyError); XCTAssertTrue(model.bodyDirty)
            let retained = try await store.retainedBodyEdit(day); XCTAssertEqual(retained?.content, expected)
            failure.set(false)
            assertTrue(await model.navigateToAdjacentDay(direction, from: day))
            XCTAssertEqual(model.selectedDay.value, expectedDay); XCTAssertFalse(model.bodyDirty)
            let saved = try await store.exportMarkdown(day); XCTAssertEqual(saved, expected)
            assertTrue(await model.prepareMainDestination())
        }
    }

    func testBothDirectionsAndTabsRejectOpenAndRetainedUnresolvedMerge() async throws {
        let (root, store, _) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        model.updateBody("# Base\n## Notes\n"); assertTrue(await model.flushBody())
        let external = Data("# Other writer\n## Notes\n".utf8)
        try external.write(to: root.appendingPathComponent("Journals/\(day.value).md"))
        model.updateBody("# Local input\n## Notes\n"); assertFalse(await model.flushBody())
        await model.reviewBodyConflict()
        let pending = model.markdown
        for retained in [false, true] {
            if retained { assertTrue(await model.dismissBodyConflict()); XCTAssertNil(model.bodyConflict) }
            XCTAssertTrue(model.hasRetainedBodyMerge)
            XCTAssertFalse(model.canNavigateAdjacentDay)
            for direction in DiaryDateDirection.allCases { assertFalse(await model.navigateToAdjacentDay(direction, from: day)) }
            assertFalse(await model.prepareMainDestination())
            XCTAssertEqual(model.selectedDay, day); XCTAssertEqual(model.markdown, pending)
            XCTAssertEqual(try Data(contentsOf: root.appendingPathComponent("Journals/\(day.value).md")), external)
        }
    }

    func testOneAdjacentTransitionOwnsSaveAndRejectsRapidAndStaleCallbacks() async throws {
        for (direction, expectedDay) in [(DiaryDateDirection.previous, "2026-09-13"), (.next, "2026-09-15")] {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent("NavigationOwnership-\(UUID())")
            let gate = BodySaveGate(); defer { gate.release(); try? FileManager.default.removeItem(at: root) }
            let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), failure: { gate.check($0, $1) })
            let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
            model.updateBody(model.markdown + "Original day input\n"); let expected = Data(model.markdown.utf8)
            let navigating = Task { await model.navigateToAdjacentDay(direction, from: self.day) }
            for _ in 0..<300 where !gate.isReached { try await Task.sleep(nanoseconds: 2_000_000) }
            XCTAssertTrue(gate.isReached); XCTAssertTrue(model.isBusy)
            for duplicate in DiaryDateDirection.allCases { assertFalse(await model.navigateToAdjacentDay(duplicate, from: day)) }
            assertFalse(await model.prepareMainDestination())
            model.updateBody("Stale in-flight callback", for: day)
            XCTAssertEqual(Data(model.markdown.utf8), expected)
            gate.release(); assertTrue(await navigating.value)
            XCTAssertEqual(model.selectedDay.value, expectedDay)
            let target = Data(model.markdown.utf8)
            for stale in DiaryDateDirection.allCases { assertFalse(await model.navigateToAdjacentDay(stale, from: day)) }
            model.updateBody("Late old-day callback", for: day)
            XCTAssertEqual(Data(model.markdown.utf8), target); XCTAssertFalse(model.bodyDirty)
            let saved = try await store.exportMarkdown(day); XCTAssertEqual(saved, expected)
        }
    }

    func testTabDepartureRetriesLegacyRetentionAndCannotHideItsFailure() async throws {
        let (root, store, failure) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        failure.set(true, purpose: .draft)
        model.updateText("Legacy input still belongs to this day")
        assertFalse(await model.prepareMainDestination()); XCTAssertNotNil(model.errorMessage)
        XCTAssertEqual(model.selectedDay, day); XCTAssertTrue(model.hasLegacyDraft)
        failure.set(false)
        assertTrue(await model.prepareMainDestination())
        XCTAssertNil(model.errorMessage)
        let retained = try await store.open(day).draft
        XCTAssertEqual(retained?.text, model.text)
    }
}

private final class BodyWriteCounts: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [String: Int] = [:]
    func record(_ purpose: FilePurpose, _ stage: FileStage) {
        guard stage == .beforeWrite else { return }
        lock.lock(); defer { lock.unlock() }; values[String(describing: purpose), default: 0] += 1
    }
    func count(_ purpose: FilePurpose) -> Int { lock.lock(); defer { lock.unlock() }; return values[String(describing: purpose), default: 0] }
}

extension BodyEditorStateTests {
    func testSuccessfulSaveWithLaterTypingKeepsTruthfulStatusAndFlushesLatestBytes() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("BodyGateSynthetic-\(UUID())")
        let gate = BodySaveGate(); defer { gate.release(); try? FileManager.default.removeItem(at: root) }
        let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), failure: { gate.check($0, $1) })
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        model.updateBody(model.markdown + "First in flight\n")
        let saving = Task { await model.flushBody() }
        for _ in 0..<300 where !gate.isReached { try await Task.sleep(nanoseconds: 2_000_000) }
        XCTAssertTrue(gate.isReached)
        model.updateBody(model.markdown + "Later typing 中 e\u{301}\n")
        let expected = Data(model.markdown.utf8)
        XCTAssertTrue(model.bodyDirty); XCTAssertNotEqual(model.bodyStatus, "已保存在手机")
        gate.release()
        assertTrue(await saving.value)
        XCTAssertFalse(model.bodyDirty); XCTAssertEqual(model.bodyStatus, "已保存在手机")
        let export = await model.export(day: day); XCTAssertEqual(export?.data, expected)
        let reopened = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await reopened.start()
        XCTAssertEqual(Data(reopened.markdown.utf8), expected)
        let history = try await store.bodyRevisions(day); XCTAssertEqual(history.count, 2)
    }

    func testPacedTypingKeepsOneDurableBufferAndBoundedHistoryWrites() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("BodyPacedSynthetic-\(UUID())")
        defer { try? FileManager.default.removeItem(at: root) }
        let counts = BodyWriteCounts()
        let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), failure: { counts.record($0, $1) })
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        let initial = model.markdown + String(repeating: "Synthetic body line 中文\n", count: 200)
        for index in 0..<30 {
            model.updateBody(initial + String(repeating: "字", count: index + 1))
            try await Task.sleep(nanoseconds: 80_000_000)
            let retained = try await store.retainedBodyEdit(day)
            XCTAssertEqual(retained?.content, Data(model.markdown.utf8))
        }
        assertTrue(await model.flushBody())
        let history = try await store.bodyRevisions(day)
        XCTAssertEqual(history.count, 1); XCTAssertEqual(counts.count(.bodyDiary), 1)
        XCTAssertEqual(counts.count(.bodyReceipt), 1); XCTAssertEqual(counts.count(.bodyRevision), 1)
        XCTAssertGreaterThanOrEqual(counts.count(.bodyDraft), 30)
        XCTAssertLessThanOrEqual(counts.count(.bodyDraft), 33)
        let revisionFiles = try FileManager.default.contentsOfDirectory(at: root.appendingPathComponent("State/body-revisions"), includingPropertiesForKeys: [.fileSizeKey])
        let revisionBytes = try revisionFiles.reduce(0) { try $0 + $1.resourceValues(forKeys: [.fileSizeKey]).fileSize! }
        XCTAssertLessThan(revisionBytes, Data(model.markdown.utf8).count * 3)
        print("Paced body typing: 30 edits at 80 ms; buffer writes=\(counts.count(.bodyDraft)), commits=\(history.count), revision bytes=\(revisionBytes)")
    }

    func testContinuousTypingCheckpointsWithoutPerKeystrokeHistory() async throws {
        let (root, store, _) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        let checkpointNanoseconds: UInt64 = 400_000_000
        model.bodyCheckpointNanoseconds = checkpointNanoseconds
        let started = DispatchTime.now().uptimeNanoseconds
        for index in 0..<16 {
            model.updateBody(model.markdown + "\(index)")
            try await Task.sleep(nanoseconds: 80_000_000)
        }
        let elapsedNanoseconds = DispatchTime.now().uptimeNanoseconds - started
        // Sleeps are minimums: allow elapsed checkpoint windows plus the final explicit flush.
        let maximumRevisions = Int((elapsedNanoseconds + checkpointNanoseconds - 1) / checkpointNanoseconds) + 1
        assertTrue(await model.flushBody())
        let history = try await store.bodyRevisions(day)
        XCTAssertGreaterThan(history.count, 1); XCTAssertLessThanOrEqual(history.count, maximumRevisions)
    }

    func testDismissedMergeSurvivesPersistenceFailureReopenAndRelaunch() async throws {
        let (root, store, failure) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        model.updateBody(model.markdown + "Base\n"); assertTrue(await model.flushBody())
        let external = model.markdown + "External\n"
        try Data(external.utf8).write(to: root.appendingPathComponent("Journals/2026-09-14.md"))
        model.updateBody(model.markdown + "Local\n"); assertFalse(await model.flushBody())
        await model.reviewBodyConflict()
        let conflict = model.bodyConflict
        failure.set(true, purpose: .bodyMerge)
        let merged = external + "Manually edited merge\n"
        model.updateBodyMerge(merged); assertFalse(await model.flushBodyMerge())
        model.closeBodyConflict(); await model.reviewBodyConflict()
        XCTAssertEqual(model.bodyMergeText, merged); XCTAssertEqual(model.bodyConflict, conflict)
        failure.set(false)
        assertTrue(await model.flushBodyMerge()); model.closeBodyConflict()
        let cold = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await cold.start()
        await cold.reviewBodyConflict()
        XCTAssertEqual(cold.bodyMergeText, merged); XCTAssertEqual(cold.bodyConflict, conflict)
        assertTrue(await cold.resolveBodyConflict(with: cold.bodyMergeText))
        XCTAssertEqual(cold.markdown, merged)
    }

    func testOldDayCallbackAndObsoleteExportStatusCannotAffectCurrentBody() async throws {
        let (root, store, _) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        model.updateBody(model.markdown + "Saved\n"); assertTrue(await model.flushBody())
        let exported = await model.export(day: day)
        let document = try XCTUnwrap(exported)
        model.exportCompleted(.success(root.appendingPathComponent("Synthetic.md")), document: document)
        XCTAssertEqual(model.status, "Markdown 已导出")
        model.updateBody(model.markdown + "New\n")
        XCTAssertNotEqual(model.status, "Markdown 已导出")
        let nextDay = try JournalDay("2026-09-15")
        assertTrue(await model.navigate(to: nextDay))
        let actual = model.markdown
        model.updateBody("Old composing callback", for: day)
        XCTAssertEqual(model.markdown, actual); XCTAssertFalse(model.bodyDirty)
    }

    func testRemovingOrDuplicatingVoiceHeadingRequiresVisibleSelection() async throws {
        let (root, store, _) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        let selected = model.section
        model.updateBody(model.markdown.replacingOccurrences(of: selected, with: "Renamed section"))
        assertTrue(await model.flushBody()); XCTAssertTrue(model.requiresSectionSelection)
        model.updateSection("Renamed section"); XCTAssertFalse(model.requiresSectionSelection)
        model.updateBody(model.markdown + "\n## Renamed section\n")
        assertTrue(await model.flushBody()); XCTAssertTrue(model.requiresSectionSelection)
        XCTAssertFalse(model.sections.contains("Renamed section"))
    }
}

private final class BodyBoundaryFault: @unchecked Sendable {
    private let lock = NSLock()
    private var boundary: (FilePurpose, FileStage, Int)?
    func arm(_ purpose: FilePurpose, _ stage: FileStage, occurrence: Int = 1) {
        lock.lock(); defer { lock.unlock() }; boundary = (purpose, stage, occurrence)
    }
    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        lock.lock(); defer { lock.unlock() }
        guard let value = boundary, value.0 == purpose, value.1 == stage else { return }
        if value.2 > 1 { boundary = (purpose, stage, value.2 - 1); return }
        boundary = nil; throw JournalError.storage
    }
}

extension BodyEditorStateTests {
    private func retained(_ expected: Data, store: LocalJournalStore) async throws -> BodyEdit {
        for _ in 0..<300 {
            if let edit = try await store.retainedBodyEdit(day), edit.content == expected { return edit }
            try await Task.sleep(nanoseconds: 2_000_000)
        }
        XCTFail("Latest editor input must be durably retained before cold launch")
        let final = try await store.retainedBodyEdit(day)
        return try XCTUnwrap(final)
    }

    func testCloseFailedMergeAndContinueTypingSurvivesEveryRecoveryBoundaryNextDay() async throws {
        for hadTransaction in [false, true] {
            var boundaries: [(FilePurpose, FileStage, Int)] = [(.bodyResolution, .beforeReplace, 1), (.bodyResolution, .afterReplace, 1),
                (.bodyReceipt, .beforeReplace, 1), (.bodyReceipt, .afterReplace, 1), (.bodyDraft, .beforeReplace, 1), (.bodyDraft, .afterReplace, 1),
                (.bodyTransaction, .beforeReplace, 1), (.bodyTransaction, .afterReplace, 1), (.bodyDiary, .beforeReplace, 1), (.bodyDiary, .afterReplace, 1),
                (.bodyReceipt, .beforeReplace, 3), (.bodyReceipt, .afterReplace, 3), (.bodyDraft, .beforeRemove, 1), (.bodyDraft, .afterRemove, 1),
                (.bodyMerge, .beforeRemove, 1), (.bodyResolution, .beforeRemove, 1), (.bodyTransaction, .beforeRemove, 1), (.bodyTransaction, .afterRemove, 1)]
            if hadTransaction { boundaries += [(.bodyTransaction, .beforeRemove, 2), (.bodyTransaction, .afterRemove, 2)] }
            for boundary in boundaries {
                let root = FileManager.default.temporaryDirectory.appendingPathComponent("BodyMergeOwnership-\(UUID())")
                defer { try? FileManager.default.removeItem(at: root) }
                let fault = BodyBoundaryFault()
                func makeStore() throws -> LocalJournalStore {
                    try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), failure: { try fault.check($0, $1) })
                }
                let store = try makeStore(), model = DiaryViewModel(now: { self.instant }, storageFactory: { store })
                await model.start(); model.bodyQuietNanoseconds = 60_000_000_000
                model.updateBody("# Base\n## Notes\n"); assertTrue(await model.flushBody())
                model.updateBody("# Local\n## Notes\n")
                if hadTransaction { fault.arm(.bodyDiary, .beforeReplace); assertFalse(await model.flushBody()) }
                let source = Data("# Other writer\n## Notes\n".utf8)
                try source.write(to: root.appendingPathComponent("Journals/2026-09-14.md"))
                assertFalse(await model.flushBody()); await model.reviewBodyConflict()
                let original = try XCTUnwrap(model.bodyConflict?.edit)
                let merge = "# Explicit merged\n## Notes\n"
                fault.arm(boundary.0, boundary.1, occurrence: boundary.2)
                assertFalse(await model.resolveBodyConflict(with: merge))
                XCTAssertNotNil(model.bodyConflict, "Failed confirmation must retain its source panel and working retry")
                XCTAssertEqual(model.bodyMergeText, merge)
                model.closeBodyConflict(); model.updateBody(model.markdown + "Later independent typing 中 e\u{301}\n")
                let latest = Data(model.markdown.utf8)
                _ = try await retained(latest, store: store)
                let coldStore = try makeStore()
                let cold = DiaryViewModel(now: { self.instant.addingTimeInterval(86400) }, storageFactory: { coldStore })
                await cold.start(); cold.bodyQuietNanoseconds = 60_000_000_000
                XCTAssertEqual(cold.selectedDay, day, "\(hadTransaction) \(boundary)")
                XCTAssertEqual(Data(cold.markdown.utf8), latest, "\(hadTransaction) \(boundary)")
                XCTAssertTrue(cold.bodyDirty)
                let diskBuffer = try await coldStore.retainedBodyEdit(day)
                XCTAssertEqual(diskBuffer?.content, latest)
                // A new explicit review owns the later input, retaining the old reviewed source as history.
                await cold.reviewBodyConflict(refreshingSource: true)
                XCTAssertEqual(Data(cold.markdown.utf8), latest)
                assertTrue(await cold.resolveBodyConflict(with: String(decoding: latest, as: UTF8.self)))
                XCTAssertNil(cold.bodyError); XCTAssertNil(cold.errorMessage)
                let final = try await coldStore.exportMarkdown(day); XCTAssertEqual(final, latest)
                do { try await coldStore.commitBodyEdit(original); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
                let history = try await coldStore.bodyRevisions(day)
                XCTAssertTrue(history.contains { $0.before == source })
            }
        }
    }

    func testFinalSnapshotFailureRetainsLatestVisibleInputForNextDayColdStartup() async throws {
        for laterTyping in [false, true] {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent("BodyFinalSnapshot-\(UUID())")
            defer { try? FileManager.default.removeItem(at: root) }
            let sourceURL = root.appendingPathComponent("Journals/2026-09-14.md")
            let external = Data("# External after receipt\n".utf8)
            let gate = BodySaveGate(purpose: .bodyReceipt, stage: .afterReplace)
            defer { gate.release() }
            let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), failure: { purpose, stage in
                if purpose == .bodyReceipt, stage == .afterReplace { gate.check(purpose, stage); try external.write(to: sourceURL) }
            })
            let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
            model.bodyQuietNanoseconds = 60_000_000_000
            model.updateBody("# Candidate before receipt\n")
            let saving = Task { await model.flushBody() }
            for _ in 0..<300 where !gate.isReached { try await Task.sleep(nanoseconds: 2_000_000) }
            XCTAssertTrue(gate.isReached)
            if laterTyping { model.updateBody(model.markdown + "Later while final verification waits\n") }
            let latest = Data(model.markdown.utf8)
            gate.release(); assertFalse(await saving.value)
            _ = try await retained(latest, store: store)
            XCTAssertTrue(model.bodyDirty); XCTAssertNotNil(model.bodyError)
            XCTAssertNil(model.errorMessage); XCTAssertNotEqual(model.bodyStatus, "已保存在手机")
            XCTAssertEqual(try Data(contentsOf: sourceURL), external)
            let coldStore = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"))
            let cold = DiaryViewModel(now: { self.instant.addingTimeInterval(86400) }, storageFactory: { coldStore }); await cold.start()
            XCTAssertEqual(cold.selectedDay, day); XCTAssertEqual(Data(cold.markdown.utf8), latest)
            await cold.reviewBodyConflict(); XCTAssertEqual(cold.bodyConflict?.source, external)
            assertTrue(await cold.resolveBodyConflict(with: String(decoding: latest, as: UTF8.self)))
            XCTAssertNil(cold.bodyError); XCTAssertNil(cold.errorMessage)
        }
    }

    func testCleanupOnlyRecoveryKeepsOriginalDayAndOffersWorkingRetry() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("BodyCleanup-\(UUID())")
        defer { try? FileManager.default.removeItem(at: root) }
        let fault = BodyBoundaryFault()
        func makeStore() throws -> LocalJournalStore { try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), failure: { try fault.check($0, $1) }) }
        let store = try makeStore(), body = BodyEdit(day: day, before: nil, content: Data("# Orphan body\n".utf8))
        fault.arm(.bodyTransaction, .beforeRemove)
        do { try await store.commitBodyEdit(body); XCTFail() } catch {}
        let buffer = try await store.retainedBodyEdit(day); XCTAssertNil(buffer)
        fault.arm(.bodyTransaction, .beforeRemove)
        let coldStore = try makeStore()
        let cold = DiaryViewModel(now: { self.instant.addingTimeInterval(86400) }, storageFactory: { coldStore }); await cold.start()
        XCTAssertEqual(cold.selectedDay, day); XCTAssertFalse(cold.bodyDirty)
        XCTAssertTrue(cold.bodyRecoveryPending); XCTAssertNotNil(cold.bodyError); XCTAssertNil(cold.errorMessage)
        assertTrue(await cold.flushBody())
        XCTAssertFalse(cold.bodyRecoveryPending); XCTAssertNil(cold.bodyError); XCTAssertNil(cold.errorMessage)
        XCTAssertEqual(Data(cold.markdown.utf8), body.content)
    }

    func testRefreshingMergeSourceRetainsExactEditedTextAndBindingOnColdRestart() async throws {
        let (root, store, _) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        model.updateBody("# Base\n"); assertTrue(await model.flushBody())
        let url = root.appendingPathComponent("Journals/2026-09-14.md")
        try Data("# First source\n".utf8).write(to: url)
        model.updateBody("# Local\n"); assertFalse(await model.flushBody()); await model.reviewBodyConflict()
        let edited = "# Hand merged\r\nChinese 中文 e\u{301}\r\n"
        model.updateBodyMerge(edited); assertTrue(await model.flushBodyMerge())
        let refreshed = Data("# Refreshed source\n".utf8); try refreshed.write(to: url)
        await model.reviewBodyConflict(refreshingSource: true)
        XCTAssertEqual(Data(model.bodyMergeText.utf8), Data(edited.utf8)); XCTAssertEqual(model.bodyConflict?.source, refreshed)
        model.closeBodyConflict()
        let coldStore = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"))
        let cold = DiaryViewModel(now: { self.instant.addingTimeInterval(86400) }, storageFactory: { coldStore }); await cold.start()
        XCTAssertEqual(cold.selectedDay, day); await cold.reviewBodyConflict()
        XCTAssertEqual(Data(cold.bodyMergeText.utf8), Data(edited.utf8)); XCTAssertEqual(cold.bodyConflict?.source, refreshed)
        assertTrue(await cold.resolveBodyConflict(with: cold.bodyMergeText))
        XCTAssertEqual(try Data(contentsOf: url), Data(edited.utf8))
    }

    func testFailedMergeConfirmationCanRetrySameIdentityAndLocksInputWhileHeld() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("BodyHeldMerge-\(UUID())")
        defer { try? FileManager.default.removeItem(at: root) }
        let gate = BodySaveGate(purpose: .bodyResolution, stage: .afterReplace); defer { gate.release() }
        let fault = BodyBoundaryFault()
        let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), failure: { purpose, stage in gate.check(purpose, stage); try fault.check(purpose, stage) })
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        model.updateBody("# Base\n"); assertTrue(await model.flushBody())
        try Data("# External\n".utf8).write(to: root.appendingPathComponent("Journals/2026-09-14.md"))
        model.updateBody("# Local\n"); assertFalse(await model.flushBody()); await model.reviewBodyConflict()
        let source = model.bodyConflict?.source, merged = "# Confirmed merge\n"
        fault.arm(.bodyDiary, .beforeReplace)
        let saving = Task { await model.resolveBodyConflict(with: merged) }
        for _ in 0..<300 where !gate.isReached { try await Task.sleep(nanoseconds: 2_000_000) }
        XCTAssertTrue(gate.isReached); XCTAssertTrue(model.isBusy); XCTAssertFalse(model.canEditBodyMerge)
        model.updateBodyMerge("Ineligible input"); model.closeBodyConflict()
        XCTAssertEqual(model.bodyMergeText, merged); XCTAssertEqual(model.bodyConflict?.source, source)
        gate.release(); assertFalse(await saving.value)
        XCTAssertTrue(model.canEditBodyMerge); XCTAssertEqual(model.bodyConflict?.source, source)
        let resolutionURL = root.appendingPathComponent("State/body-resolutions/2026-09-14.json")
        let resolution = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: resolutionURL)) as? [String: Any])
        let next = try XCTUnwrap(resolution["next"] as? [String: Any]), id = try XCTUnwrap(next["id"] as? String)
        assertTrue(await model.resolveBodyConflict(with: merged))
        let history = try await store.bodyRevisions(day)
        XCTAssertEqual(history.filter { $0.content == Data(merged.utf8) && $0.id.uuidString == id }.count, 1)
        XCTAssertNil(model.bodyConflict); XCTAssertNil(model.bodyError)
    }

    func testBodyErrorOwnershipDoesNotClearUnrelatedLegacyFailure() async throws {
        let (root, store, failure) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        failure.set(true, purpose: .draft); model.updateText("Retained legacy input")
        await model.flushDraft(); let legacyError = try XCTUnwrap(model.errorMessage)
        model.updateBody(model.markdown + "Manual text\n"); assertTrue(await model.flushBody())
        XCTAssertNil(model.bodyError); XCTAssertEqual(model.errorMessage, legacyError)
    }

    func testActualSectionChoicesUseLinearByteExactIdentityThroughVoiceTargetCreation() async throws {
        let (root, store, _) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        let composed = "Café", decomposed = "Cafe\u{301}"
        let body = "# Dense\n## \(composed)\n## \(decomposed)\n" + (0..<2000).map { "## Section \($0)\n" }.joined() + "## Duplicate\n## Duplicate\n"
        model.updateBody(body); assertTrue(await model.flushBody())
        let choices = model.sectionChoices
        XCTAssertEqual(choices.count, 2002); XCTAssertEqual(Set(choices.map(\.id)).count, 2002)
        for raw in [composed, decomposed] {
            let id = Data(raw.utf8); model.selectSectionID(id)
            XCTAssertEqual(model.selectedSectionID, id); XCTAssertFalse(model.requiresSectionSelection)
            await model.flushDraft()
            let snapshot = try await store.open(day)
            let draft = try XCTUnwrap(snapshot.draft)
            let target = try await store.voiceTarget(draft)
            XCTAssertEqual(Data(target.section.utf8), id)
        }
    }
}

extension BodyEditorStateTests {
    func testLegacySaveOwnsAcceptedInputBeforeHeldBodyFlushAndColdRecovery() async throws {
        for failureMode in ["write", "source", "success"] {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent("LegacyHeldBody-\(UUID())")
            let gate = BodySaveGate(), fault = BodyBoundaryFault()
            defer { gate.release(); try? FileManager.default.removeItem(at: root) }
            let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), failure: { purpose, stage in gate.check(purpose, stage); try fault.check(purpose, stage) })
            let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
            model.bodyQuietNanoseconds = 60_000_000_000
            model.updateText("Legacy latest accepted 中 e\u{301}")
            model.updateBody(model.markdown + "Body accepted before command\n")
            let intendedBody = Data(model.markdown.utf8), intendedText = model.text, intendedSection = model.section
            if failureMode == "write" { fault.arm(.bodyDiary, .beforeReplace) }
            let saving = Task { await model.save() }
            for _ in 0..<300 where !gate.isReached { try await Task.sleep(nanoseconds: 2_000_000) }
            XCTAssertTrue(gate.isReached); XCTAssertTrue(model.isBusy); XCTAssertFalse(model.canEditLegacy)
            model.updateText("Refused while controls disabled"); model.updateSection("Other"); model.updateBody("Refused body")
            XCTAssertEqual(model.text, intendedText); XCTAssertEqual(model.section, intendedSection); XCTAssertEqual(Data(model.markdown.utf8), intendedBody)
            if failureMode == "source" { try Data("# External source\n## Notes\n".utf8).write(to: root.appendingPathComponent("Journals/2026-09-14.md")) }
            gate.release(); await saving.value
            XCTAssertTrue(model.hasLegacyDraft); XCTAssertTrue(model.canEditLegacy)
            let retained = try await store.open(day).draft
            XCTAssertEqual(retained?.text, intendedText)
            let cold = DiaryViewModel(now: { self.instant.addingTimeInterval(86400) }, storageFactory: { store }); await cold.start()
            XCTAssertEqual(cold.selectedDay, day); XCTAssertEqual(cold.text, intendedText)
            XCTAssertEqual(Data(cold.markdown.utf8), intendedBody)
            if failureMode == "source" { await cold.reviewBodyConflict(); assertTrue(await cold.resolveBodyConflict(with: cold.markdown)) }
            await cold.reviewCurrentSource(); await cold.save()
            XCTAssertFalse(cold.hasLegacyDraft)
            let final = try await store.exportMarkdown(day)
            XCTAssertTrue(String(decoding: final, as: UTF8.self).contains(intendedText))
            XCTAssertEqual(String(decoding: final, as: UTF8.self).components(separatedBy: intendedText).count, 2)
        }
    }

    func testMergeOnlyM1CleanupM2ColdM3WriteFailureBlocksEveryReplacingTransition() async throws {
        let (root, store, failure) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let fault = BodyBoundaryFault()
        let faultStore = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), failure: { try fault.check($0, $1) })
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { faultStore }); await model.start()
        model.bodyQuietNanoseconds = 60_000_000_000
        model.updateBody("# Base\n## Notes\n"); assertTrue(await model.flushBody())
        try Data("# External\n## Notes\n".utf8).write(to: root.appendingPathComponent("Journals/2026-09-14.md"))
        model.updateBody("# Local\n## Notes\n"); assertFalse(await model.flushBody()); await model.reviewBodyConflict()
        let source = model.bodyConflict?.source
        fault.arm(.bodyMerge, .beforeRemove)
        assertFalse(await model.resolveBodyConflict(with: "# M1\n## Notes\n"))
        model.updateBodyMerge("# M2 retained\n## Notes\n"); assertTrue(await model.flushBodyMerge())
        model.closeBodyConflict()
        let cold = DiaryViewModel(now: { self.instant.addingTimeInterval(86400) }, storageFactory: { store }); await cold.start()
        XCTAssertFalse(cold.bodyDirty); XCTAssertFalse(cold.bodyRecoveryPending); XCTAssertTrue(cold.hasRetainedBodyMerge)
        await cold.reviewBodyConflict(); XCTAssertEqual(cold.bodyConflict?.source, source)
        let m3 = "# M3 not yet persisted\r\n中文 e\u{301}\r\n## Notes\r\n"
        failure.set(true, purpose: .bodyMerge)
        cold.updateBodyMerge(m3); assertFalse(await cold.flushBodyMerge())
        let binding = cold.bodyConflict
        assertFalse(await cold.dismissBodyConflict()); XCTAssertEqual(cold.bodyConflict, binding)
        cold.closeBodyConflict()
        assertFalse(await cold.navigate(to: try JournalDay("2026-09-15")))
        assertFalse(await cold.prepareDirectory()); await cold.directoryClosed()
        assertNil(await cold.export(day: day)); assertFalse(await cold.captureVoice())
        await cold.reviewCurrentSource(); await cold.retryLegacySave(); await cold.start()
        XCTAssertEqual(cold.selectedDay, day); XCTAssertEqual(Data(cold.bodyMergeText.utf8), Data(m3.utf8)); XCTAssertTrue(cold.bodyMergeDirty)
        XCTAssertNotNil(cold.bodyMergeError); XCTAssertNotEqual(cold.bodyStatus, "已保存在手机")
        await cold.reviewBodyConflict(); XCTAssertEqual(cold.bodyConflict, binding)
        failure.set(false)
        assertTrue(await cold.flushBodyMerge()); assertTrue(await cold.dismissBodyConflict())
        assertTrue(await cold.navigate(to: try JournalDay("2026-09-15")))
        let reopened = DiaryViewModel(now: { self.instant.addingTimeInterval(86400) }, storageFactory: { store }); await reopened.start()
        XCTAssertEqual(reopened.selectedDay, day); await reopened.reviewBodyConflict()
        XCTAssertEqual(Data(reopened.bodyMergeText.utf8), Data(m3.utf8)); XCTAssertEqual(reopened.bodyConflict, binding)
        await reopened.reviewBodyConflict(refreshingSource: true)
        assertTrue(await reopened.resolveBodyConflict(with: m3))
        let final = try await store.exportMarkdown(day); XCTAssertEqual(final, Data(m3.utf8))
    }

    func testCorruptRecoveryRecordsExposeExactSavedReadOnlyVersionAndRepairWithoutErasure() async throws {
        for folder in ["drafts", "merges", "transactions", "resolutions"] {
            for unreadable in [false, true] {
                let (root, store, _) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
                let saved = Data("---\r\n---\r\n# Saved 中 e\u{301}\r\n## Notes\r\n".utf8)
                try await store.commitBodyEdit(BodyEdit(day: day, before: nil, content: saved))
                let url = root.appendingPathComponent("State/body-\(folder)/2026-09-14.json")
                let corrupt = Data("{ synthetic malformed recovery".utf8)
                if unreadable { try FileManager.default.createDirectory(at: url, withIntermediateDirectories: false) }
                else { try corrupt.write(to: url) }
                let cold = DiaryViewModel(now: { self.instant.addingTimeInterval(86400) }, storageFactory: { store }); await cold.start()
                XCTAssertEqual(cold.selectedDay, day); XCTAssertTrue(cold.isReady); XCTAssertTrue(cold.bodyRecoveryUnavailable)
                XCTAssertEqual(Data(cold.markdown.utf8), saved); XCTAssertNotNil(cold.bodyError)
                XCTAssertFalse(cold.canEditBody); XCTAssertFalse(cold.canEditLegacy); XCTAssertFalse(cold.canEditBodyMerge)
                cold.updateBody("Not accepted"); cold.updateText("Not accepted")
                assertFalse(await cold.flushBody()); assertNil(await cold.export(day: day))
                let exported = await cold.exportSavedBodyVersion(); XCTAssertEqual(exported?.data, saved)
                if unreadable { XCTAssertTrue(FileManager.default.fileExists(atPath: url.path)) }
                else { XCTAssertEqual(try Data(contentsOf: url), corrupt) }
                let rejected = BodyEdit(day: day, before: saved, content: saved + Data("No overwrite\n".utf8))
                do { try await store.retainBodyEdit(rejected); XCTFail("Damaged evidence must block writes") } catch {}
                // Synthetic repair stands in for the user restoring this one damaged file.
                try FileManager.default.removeItem(at: url)
                await cold.retryBodyRecoveryRead()
                XCTAssertFalse(cold.bodyRecoveryUnavailable); XCTAssertTrue(cold.canEditBody); XCTAssertNil(cold.bodyError)
                XCTAssertEqual(Data(cold.markdown.utf8), saved)
            }
        }
    }

    func testRecoveryCorruptionAfterOpenCannotOverwriteEvidenceOrLatestAcceptedInput() async throws {
        let (root, store, _) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        model.updateBody("# Saved\n"); assertTrue(await model.flushBody())
        let url = root.appendingPathComponent("State/body-drafts/2026-09-14.json"), damaged = Data("{ damaged".utf8)
        try damaged.write(to: url)
        model.updateBody("# Latest accepted 中 e\u{301}\n"); let latest = Data(model.markdown.utf8)
        assertFalse(await model.flushBody())
        assertFalse(await model.navigate(to: try JournalDay("2026-09-15")))
        await model.start(); await model.retryBodyRecoveryRead()
        XCTAssertEqual(try Data(contentsOf: url), damaged); XCTAssertEqual(Data(model.markdown.utf8), latest)
        XCTAssertEqual(model.selectedDay, day); XCTAssertTrue(model.bodyDirty); XCTAssertTrue(model.bodyRecoveryUnavailable)
        let exported = await model.exportSavedBodyVersion(); XCTAssertEqual(exported?.data, Data("# Saved\n".utf8))
        try FileManager.default.removeItem(at: url)
        await model.retryBodyRecoveryRead()
        XCTAssertFalse(model.bodyRecoveryUnavailable); XCTAssertFalse(model.bodyDirty)
        let final = try await store.exportMarkdown(day); XCTAssertEqual(final, latest)
    }

    func testOriginalVoiceTargetRecoveryUsesExactBytesInEligibilityAndSelection() async throws {
        let (root, store, _) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        let nfc = "Café", nfd = "Cafe\u{301}"
        model.updateBody("# Voice\n## \(nfc)\n## \(nfd)\n"); assertTrue(await model.flushBody())
        model.selectSectionID(Data(nfc.utf8)); await model.flushDraft()
        let sourceDraft = try await store.open(day).draft
        let draft = try XCTUnwrap(sourceDraft)
        let target = try await store.voiceTarget(draft)
        model.selectSectionID(Data(nfd.utf8)); await model.flushDraft()
        XCTAssertTrue(model.needsOriginalVoiceTarget(target))
        assertTrue(await model.navigate(to: target.day)); model.updateSection(target.section); await model.flushDraft()
        XCTAssertFalse(model.needsOriginalVoiceTarget(target)); XCTAssertEqual(model.selectedSectionID, Data(nfc.utf8))
        let savedDraft = try await store.open(day).draft
        let actualDraft = try XCTUnwrap(savedDraft)
        let actualTarget = try await store.voiceTarget(actualDraft)
        XCTAssertEqual(Data(actualTarget.section.utf8), Data(target.section.utf8))
    }
}

extension BodyEditorStateTests {
    func testRecoveryRecordCorruptedDuringAtomicBufferWriteIsNeverReplaced() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("BodyCorruptReplace-\(UUID())")
        defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("State/body-drafts/2026-09-14.json"), corrupt = Data("{ corrupt at replacement".utf8)
        let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), failure: { purpose, stage in
            if purpose == .bodyDraft && stage == .beforeReplace { try corrupt.write(to: url) }
        })
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }); await model.start()
        model.updateBody("# Accepted before corruption\n")
        assertFalse(await model.flushBody())
        XCTAssertEqual(try Data(contentsOf: url), corrupt)
        XCTAssertEqual(model.markdown, "# Accepted before corruption\n"); XCTAssertTrue(model.bodyDirty)
        XCTAssertTrue(model.bodyRecoveryUnavailable); XCTAssertNotNil(model.bodyError)
    }
}

extension BodyEditorStateTests {
    func testReadOnlyRecoveryAllowsOtherDaysAndCapturesSavedExportOwnership() async throws {
        let (root, store, _) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let saved = Data("# Saved original day\n".utf8), other = try JournalDay("2026-09-13")
        try await store.commitBodyEdit(BodyEdit(day: day, before: nil, content: saved))
        try await store.commitBodyEdit(BodyEdit(day: other, before: nil, content: Data("# Another healthy day\n".utf8)))
        let url = root.appendingPathComponent("State/body-drafts/2026-09-14.json"), damaged = Data("{ retained damage".utf8)
        try damaged.write(to: url)
        let model = DiaryViewModel(now: { self.instant.addingTimeInterval(86400) }, storageFactory: { store }); await model.start()
        XCTAssertTrue(model.bodyRecoveryUnavailable); XCTAssertFalse(model.bodyDirty)
        assertTrue(await model.prepareDirectory())
        let export = Task { await model.exportSavedBodyVersion() }
        _ = await model.navigate(to: other)
        let captured = await export.value
        let document = try XCTUnwrap(captured)
        XCTAssertEqual(document.filename, "2026-09-14.md"); XCTAssertEqual(document.data, saved)
        XCTAssertTrue(document.includesOnlySavedVersion)
        if model.selectedDay != other { assertTrue(await model.navigate(to: other)) }
        XCTAssertFalse(model.bodyRecoveryUnavailable); XCTAssertEqual(model.markdown, "# Another healthy day\n")
        model.exportCompleted(.success(root.appendingPathComponent("captured.md")), document: document)
        XCTAssertTrue(model.status.contains("不含待恢复修改"))
        assertTrue(await model.navigate(to: day))
        XCTAssertTrue(model.bodyRecoveryUnavailable); XCTAssertEqual(Data(model.markdown.utf8), saved)
        XCTAssertEqual(try Data(contentsOf: url), damaged)
        assertTrue(await model.navigate(to: other))
        let cold = DiaryViewModel(now: { self.instant.addingTimeInterval(86400) }, storageFactory: { store }); await cold.start()
        XCTAssertEqual(cold.selectedDay, day); XCTAssertEqual(try Data(contentsOf: url), damaged)
    }
}

extension BodyEditorStateTests {
    func testMismatchedReferencedReceiptShowsSavedReadOnlyBodyUntilExactEvidenceIsRestored() async throws {
        for folder in ["transactions", "resolutions"] {
            let (root, store, _) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
            let edit = BodyEdit(day: day, before: nil, content: Data("# Saved Unicode 中 e\u{301}\r\n".utf8))
            try await store.commitBodyEdit(edit)
            let receiptURL = root.appendingPathComponent("State/body-receipts/\(edit.id.uuidString).json")
            let validReceipt = try Data(contentsOf: receiptURL)
            var fields = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(edit)) as? [String: Any])
            let operation: Any = folder == "transactions" ? fields : ["conflict": ["edit": fields, "source": NSNull()], "next": fields]
            let operationBytes = try JSONSerialization.data(withJSONObject: operation, options: .sortedKeys)
            let operationURL = root.appendingPathComponent("State/body-\(folder)/\(day.value).json")
            try operationBytes.write(to: operationURL)
            fields["content"] = Data("Different content with same id\n".utf8).base64EncodedString()
            let badReceipt = try JSONSerialization.data(withJSONObject: ["edit": fields], options: .sortedKeys)
            try badReceipt.write(to: receiptURL)
            let model = DiaryViewModel(now: { self.instant.addingTimeInterval(86400) }, storageFactory: { store }); await model.start()
            XCTAssertEqual(model.selectedDay, day); XCTAssertTrue(model.bodyRecoveryUnavailable)
            XCTAssertFalse(model.canEditBody); XCTAssertFalse(model.canEditLegacy); XCTAssertNotNil(model.bodyError)
            XCTAssertEqual(Data(model.markdown.utf8), edit.content)
            let exported = await model.exportSavedBodyVersion(); XCTAssertEqual(exported?.data, edit.content)
            assertNil(await model.export(day: day)); await model.retryBodyRecoveryRead()
            XCTAssertEqual(try Data(contentsOf: receiptURL), badReceipt)
            XCTAssertEqual(try Data(contentsOf: operationURL), operationBytes)
            // Restore only the injected receipt to its original exact bytes in this fixture.
            try validReceipt.write(to: receiptURL)
            await model.retryBodyRecoveryRead()
            XCTAssertFalse(model.bodyRecoveryUnavailable); XCTAssertTrue(model.canEditBody); XCTAssertNil(model.bodyError)
            XCTAssertEqual(Data(model.markdown.utf8), edit.content)
            XCTAssertEqual(try Data(contentsOf: receiptURL), validReceipt)
        }
    }
}
