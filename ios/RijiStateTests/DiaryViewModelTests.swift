import XCTest
import Foundation
import RijiCore
@testable import RijiState

private final class FileProbe: @unchecked Sendable {
    private let lock = NSLock()
    private var fault: (FilePurpose, FileStage)?
    private var paused = false
    private var writes = 0
    let entered = DispatchSemaphore(value: 0)
    let release = DispatchSemaphore(value: 0)

    var draftWrites: Int { lock.lock(); defer { lock.unlock() }; return writes }
    func fail(_ purpose: FilePurpose, _ stage: FileStage) { lock.lock(); fault = (purpose, stage); lock.unlock() }
    func pauseNextDraft() { lock.lock(); paused = true; lock.unlock() }

    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        lock.lock()
        if purpose == .draft && stage == .beforeWrite { writes += 1 }
        let shouldPause = paused && purpose == .draft && stage == .beforeWrite
        if shouldPause { paused = false }
        let shouldFail = fault.map { $0.0 == purpose && $0.1 == stage } ?? false
        if shouldFail { fault = nil }
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
}
