import XCTest
import Foundation
import Combine
@testable import RijiCore
@testable import RijiState

private final class AIRecoveryGate: @unchecked Sendable {
    let entered = XCTestExpectation(description: "AI operation reached its held store boundary")
    private let lock = NSLock()
    private let semaphore = DispatchSemaphore(value: 0)
    private var boundary: (FilePurpose, FileStage, Int)?
    private var failure: (FilePurpose, FileStage)?
    private(set) var timedOut = false
    func arm(_ purpose: FilePurpose, _ stage: FileStage, occurrence: Int = 1) {
        lock.lock(); defer { lock.unlock() }; boundary = (purpose, stage, occurrence)
    }
    func failOnce(_ purpose: FilePurpose, _ stage: FileStage) {
        lock.lock(); defer { lock.unlock() }; failure = (purpose, stage)
    }
    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        lock.lock()
        var pause = false
        if let value = boundary, value.0 == purpose, value.1 == stage {
            if value.2 == 1 { pause = true; boundary = nil }
            else { boundary = (purpose, stage, value.2 - 1) }
        }
        let fail = failure?.0 == purpose && failure?.1 == stage
        if fail { failure = nil }
        lock.unlock()
        if pause { hold() }
        if fail { throw JournalError.storage }
    }
    func hold() {
        entered.fulfill()
        if semaphore.wait(timeout: .now() + 8) == .timedOut {
            lock.lock(); timedOut = true; lock.unlock()
        }
    }
    func release() { semaphore.signal() }
}

private extension LocalJournalStore {
    func holdAIRecoveryReads(_ gate: AIRecoveryGate) { gate.hold() }
}

@MainActor
final class AIResultRecoveryStateTests: XCTestCase {
    private let day = try! JournalDay("2026-09-20")
    private let instant = ISO8601DateFormatter().date(from: "2026-09-20T08:00:00Z")!
    private struct Fixture {
        let root: URL
        let store: LocalJournalStore
        let model: DiaryViewModel
        let gate: AIRecoveryGate
    }
    private func fixture() async throws -> Fixture {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("AIRecoveryAdmission-\(UUID())")
        let gate = AIRecoveryGate()
        let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), failure: { try gate.check($0, $1) })
        let browser = VaultBrowserStore(stateDirectory: root.appendingPathComponent("Browser"), appRoot: root, failure: { try gate.check($0, $1) })
        let model = DiaryViewModel(now: { self.instant }, storageFactory: { store }, vaultStore: browser)
        model.bodyQuietNanoseconds = 3_600_000_000_000; model.bodyCheckpointNanoseconds = 3_600_000_000_000
        await model.start()
        return Fixture(root: root, store: store, model: model, gate: gate)
    }
    private func result(id: String = "result-one", conversationID: String = "problem-one") -> MobileResultValidation {
        MobileResultValidation(resultId: id, text: "Selected AI advice", provenance: DiscussionProvenance(resultId: id, problemId: conversationID, discussionId: "run-one", artifactIds: ["artifact-one"], inputRevision: 1, summaryVersion: 1, correctionVersion: 0, recordedAt: "2026-09-20T00:00:00Z", mentors: ["host"], topic: "Synthetic", savedDate: "", editedByUser: false), binding: String(repeating: "a", count: 64), validatedAt: Date().timeIntervalSince1970)
    }
    private func nextSource(_ f: Fixture) throws -> URL {
        let root = f.root.appendingPathComponent("OtherVault")
        for folder in ["daily", "templates"] { try FileManager.default.createDirectory(at: root.appendingPathComponent(folder), withIntermediateDirectories: true) }
        try Data("## 🧠 Notes\n".utf8).write(to: root.appendingPathComponent("templates/daily.md"))
        try Data("## 🧠 Notes\nOther directory, same date\n".utf8).write(to: root.appendingPathComponent("daily/\(day.value).md"))
        return root
    }
    private func changeSource(_ f: Fixture) async throws {
        let root = try nextSource(f)
        await f.model.selectOriginalFolder(root)
        XCTAssertTrue(f.model.isOriginalSource)
        XCTAssertEqual(f.model.selectedDay, day)
        XCTAssertEqual(f.model.markdown, "## 🧠 Notes\nOther directory, same date\n")
        XCTAssertTrue(f.model.canEditBody)
    }
    private func assertNewSourceUnchanged(_ f: Fixture, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(f.model.isOriginalSource, file: file, line: line)
        XCTAssertEqual(f.model.markdown, "## 🧠 Notes\nOther directory, same date\n", file: file, line: line)
        XCTAssertTrue(f.model.localAIResultPreviews.isEmpty, file: file, line: line)
        XCTAssertFalse(f.model.aiResultPending, file: file, line: line)
        XCTAssertNil(f.model.aiResultPreview, file: file, line: line)
        XCTAssertNil(f.model.aiResultStatus, file: file, line: line)
        XCTAssertTrue(f.model.canEditBody, file: file, line: line)
        XCTAssertFalse(f.gate.timedOut, file: file, line: line)
    }
    func testOldRecoveryAndRestoreReadsCannotPublishAfterSameDayDirectoryChange() async throws {
        for mode in 0..<4 {
            let f = try await fixture(); defer { f.gate.release(); try? FileManager.default.removeItem(at: f.root) }
            let value = result()
            _ = try await f.store.prepareAIResult(value, account: "account", day: day, text: value.text)
            let held = Task.detached { await f.store.holdAIRecoveryReads(f.gate) }
            await fulfillment(of: [f.gate.entered], timeout: 2)
            let started = expectation(description: "The old request has started on the main actor")
            let pending = Task {
                started.fulfill()
                if mode == 0 { await f.model.refreshLocalAIRecovery() }
                else if mode == 1 { await f.model.restorePendingAIResults(account: "account") }
                else if mode == 2 { await f.model.restorePendingAIResults(account: "account", conversationID: value.provenance.problemId) }
                else { _ = await f.model.restoreAIResult(account: "account", resultID: value.resultId) }
            }
            // This task cannot resume until the request captures admission and awaits its store.
            await fulfillment(of: [started], timeout: 2)
            try await changeSource(f)
            f.gate.release(); await held.value; await pending.value
            assertNewSourceUnchanged(f); XCTAssertNil(f.model.aiResultPreview)
            let retained = try await f.store.aiPreview(account: "account", resultID: value.resultId)
            XCTAssertNotNil(retained)
        }
    }
    func testConversationRestoreSelectsEachRetainedPreviewAcrossDifferentDates() async throws {
        let f = try await fixture(); defer { try? FileManager.default.removeItem(at: f.root) }
        let firstResult = result(conversationID: "group-one")
        let secondResult = result(id: "result-two", conversationID: "group-two")
        let nextDay = try JournalDay("2026-09-21")
        let first = try await f.store.prepareAIResult(firstResult, account: "account", day: day, text: "First group edit")
        let second = try await f.store.prepareAIResult(secondResult, account: "account", day: nextDay, text: "Second group edit")
        f.gate.failOnce(.bodyDiary, .beforeReplace)
        do {
            _ = try await f.store.confirmAIResult(second, account: "account", validation: secondResult)
            XCTFail("The interrupted write should remain unresolved")
        } catch { XCTAssertEqual(error as? JournalError, .storage) }

        await f.model.restorePendingAIResults(account: "account")
        XCTAssertEqual(f.model.aiResultPreview, first)
        for preview in [second, first] {
            await f.model.restorePendingAIResults(account: "account", conversationID: preview.result.provenance.problemId)
            XCTAssertEqual(f.model.aiResultPreview, preview)
            XCTAssertEqual(f.model.localAIResultPreviews, [first])
            XCTAssertTrue(f.model.aiResultPending)
            XCTAssertFalse(f.model.canEditBody)
        }
        await f.model.restorePendingAIResults(account: "account", conversationID: "group-without-preview")
        XCTAssertNil(f.model.aiResultPreview)
        let retained = try await f.store.pendingAIResults(account: "account")
        XCTAssertEqual(retained, [first, second])
        let receipt = try await f.store.aiResultReceipt(account: "account", resultID: secondResult.resultId)
        XCTAssertNil(receipt)
        for preview in retained {
            let held = try await f.store.hasAIResultPending(preview.day)
            XCTAssertTrue(held)
            XCTAssertFalse(FileManager.default.fileExists(atPath: f.root.appendingPathComponent("Journals/\(preview.day.value).md").path))
        }
    }
    func testPreparedPreviewRemainsInOldStoreWhenSourceChangesDuringPersistence() async throws {
        let f = try await fixture(); defer { f.gate.release(); try? FileManager.default.removeItem(at: f.root) }
        let value = result()
        f.gate.arm(.aiPreview, .afterReplace, occurrence: 2)
        let pending = Task { await f.model.prepareAIResult(value, account: "account", day: self.day, text: value.text) }
        await fulfillment(of: [f.gate.entered], timeout: 2)
        XCTAssertNil(f.model.aiResultPreview)
        try await changeSource(f)
        f.gate.release(); await pending.value
        assertNewSourceUnchanged(f); XCTAssertNil(f.model.aiResultPreview)
        let retained = try await f.store.aiPreview(account: "account", resultID: value.resultId)
        XCTAssertEqual(retained?.text, value.text)
        XCTAssertFalse(FileManager.default.fileExists(atPath: f.root.appendingPathComponent("Journals/\(day.value).md").path))
    }
    func testPreviewAndTargetDayLockPublishTogetherAfterManualDraftFlush() async throws {
        for sameDay in [true, false] {
            let f = try await fixture(); defer { f.gate.release(); try? FileManager.default.removeItem(at: f.root) }
            let target = sameDay ? day : try JournalDay("2026-09-21")
            let body = f.model.markdown + "\nManual body remains exact\n"
            f.model.updateBody(body)
            let value = result()
            var publications = 0
            let observer = f.model.$aiResultPreview.compactMap { $0 }.sink { preview in
                publications += 1
                XCTAssertEqual(preview.day, target)
                XCTAssertEqual(f.model.aiResultPending, sameDay)
                XCTAssertEqual(f.model.canEditBody, !sameDay)
                XCTAssertEqual(f.model.localAIResultPreviews.map(\.id), sameDay ? [preview.id] : [])
                XCTAssertEqual(f.model.markdown, body)
                XCTAssertFalse(f.model.bodyDirty)
            }
            defer { observer.cancel() }
            f.gate.arm(.aiPreview, .afterReplace, occurrence: 2)
            let pending = Task { await f.model.prepareAIResult(value, account: "account", day: target, text: value.text) }
            await fulfillment(of: [f.gate.entered], timeout: 2)
            XCTAssertEqual(publications, 0); XCTAssertNil(f.model.aiResultPreview)
            XCTAssertEqual(f.model.markdown, body); XCTAssertFalse(f.model.bodyDirty)
            let source = f.root.appendingPathComponent("Journals/\(day.value).md")
            XCTAssertEqual(try Data(contentsOf: source), Data(body.utf8))
            f.gate.release(); await pending.value
            XCTAssertEqual(publications, 1); XCTAssertFalse(f.gate.timedOut)
        }
    }
    func testNewerCancellationInvalidatesQueuedRestorePublication() async throws {
        let f = try await fixture(); defer { f.gate.release(); try? FileManager.default.removeItem(at: f.root) }
        let value = result(), preview = try await f.store.prepareAIResult(result(), account: "account", day: day, text: value.text)
        let held = Task.detached { await f.store.holdAIRecoveryReads(f.gate) }
        await fulfillment(of: [f.gate.entered], timeout: 2)
        var restored = false
        let observer = f.model.$aiResultPreview.sink { if $0 != nil { restored = true } }
        defer { observer.cancel() }
        let firstStarted = expectation(description: "Restore has captured admission")
        let first = Task { firstStarted.fulfill(); await f.model.restorePendingAIResults(account: "account") }
        await fulfillment(of: [firstStarted], timeout: 2)
        let secondStarted = expectation(description: "Newer cancellation has captured admission")
        let second = Task { secondStarted.fulfill(); await f.model.cancelLocalAIResult(preview) }
        await fulfillment(of: [secondStarted], timeout: 2)
        f.gate.release(); await held.value; await first.value; await second.value
        XCTAssertFalse(restored); XCTAssertNil(f.model.aiResultPreview)
        XCTAssertFalse(f.model.aiResultPending); XCTAssertTrue(f.model.canEditBody)
        let retained = try await f.store.aiPreview(account: "account", resultID: value.resultId); XCTAssertNil(retained)
        XCTAssertFalse(f.gate.timedOut)
    }
    func testCompletedWriteStaysRecoverableInOldDirectoryWithoutPublishingIntoNewDirectory() async throws {
        let f = try await fixture(); defer { f.gate.release(); try? FileManager.default.removeItem(at: f.root) }
        let value = result()
        await f.model.prepareAIResult(value, account: "account", day: day, text: value.text)
        let preview = try XCTUnwrap(f.model.aiResultPreview)
        f.gate.arm(.bodyDiary, .afterReplace)
        let pending = Task { await f.model.confirmAIResult(account: "account", validation: value) }
        await fulfillment(of: [f.gate.entered], timeout: 2)
        try await changeSource(f)
        f.gate.release(); await pending.value
        assertNewSourceUnchanged(f)
        XCTAssertEqual(try Data(contentsOf: f.root.appendingPathComponent("Journals/\(day.value).md")), preview.after)
        let receipt = try await f.store.aiResultReceipt(account: "account", resultID: value.resultId); XCTAssertNotNil(receipt)
        let cold = DiaryViewModel(now: { self.instant }, storageFactory: { f.store }); await cold.start()
        let restored = await cold.restoreAIResult(account: "account", resultID: value.resultId)
        XCTAssertTrue(restored); XCTAssertTrue(cold.aiResultStatus?.hasPrefix("已保存到") == true)
    }
    func testReceiptWriteFailureAfterDiaryWriteIsReconciledAndNeverCalledUnsaved() async throws {
        let f = try await fixture(); defer { f.gate.release(); try? FileManager.default.removeItem(at: f.root) }
        let value = result()
        await f.model.prepareAIResult(value, account: "account", day: day, text: value.text)
        let preview = try XCTUnwrap(f.model.aiResultPreview)
        f.gate.failOnce(.aiReceipt, .beforeReplace)
        await f.model.confirmAIResult(account: "account", validation: value)
        XCTAssertEqual(f.model.markdown, String(decoding: preview.after, as: UTF8.self))
        XCTAssertEqual(f.model.aiResultStatus, "已保存到 \(day.value)，没有重复追加。")
        XCTAssertNil(f.model.aiResultPreview); XCTAssertFalse(f.model.aiResultPending)
        XCTAssertTrue(f.model.localAIResultPreviews.isEmpty)
        let receipt = try await f.store.aiResultReceipt(account: "account", resultID: value.resultId); XCTAssertNotNil(receipt)
    }
    func testOnlyCommittedSourceChangeClearsPublishedPreviewAndStatus() async throws {
        let f = try await fixture(); defer { try? FileManager.default.removeItem(at: f.root) }
        let value = result()
        await f.model.prepareAIResult(value, account: "account", day: day, text: value.text)
        await f.model.prepareAIResult(value, account: "account", day: day, text: value.text)
        let preview = try XCTUnwrap(f.model.aiResultPreview), status = try XCTUnwrap(f.model.aiResultStatus)
        let body = f.model.markdown, identity = f.model.sourceIdentity
        let next = try nextSource(f)
        f.gate.failOnce(.metadata, .beforeWrite)
        await f.model.selectOriginalFolder(next)
        XCTAssertEqual(f.model.sourceIdentity, identity); XCTAssertEqual(f.model.markdown, body)
        XCTAssertNotNil(f.model.errorMessage)
        XCTAssertEqual(f.model.aiResultPreview, preview); XCTAssertEqual(f.model.aiResultStatus, status)
        XCTAssertTrue(f.model.aiResultPending); XCTAssertFalse(f.model.canEditBody)

        await f.model.selectOriginalFolder(next)
        assertNewSourceUnchanged(f)
        let retained = try await f.store.aiPreview(account: "account", resultID: value.resultId)
        XCTAssertEqual(retained, preview)
        // Switching back restores the local day lock and recoverable record, without
        // implicitly selecting it for a new service-authorized write.
        await f.model.useLocalDiaries()
        XCTAssertEqual(f.model.sourceIdentity, identity)
        XCTAssertNil(f.model.aiResultPreview); XCTAssertNil(f.model.aiResultStatus)
        XCTAssertTrue(f.model.aiResultPending); XCTAssertEqual(f.model.localAIResultPreviews, [preview])
    }
    func testCancellationFinishesInOldStoreWithoutPublishingAfterSourceChange() async throws {
        let f = try await fixture(); defer { f.gate.release(); try? FileManager.default.removeItem(at: f.root) }
        let value = result()
        await f.model.prepareAIResult(value, account: "account", day: day, text: value.text)
        let preview = try XCTUnwrap(f.model.aiResultPreview)
        let held = Task.detached { await f.store.holdAIRecoveryReads(f.gate) }
        await fulfillment(of: [f.gate.entered], timeout: 2)
        let started = expectation(description: "Cancellation has captured the old store")
        let pending = Task { started.fulfill(); await f.model.cancelLocalAIResult(preview) }
        await fulfillment(of: [started], timeout: 2)
        try await changeSource(f)
        f.gate.release(); await held.value; await pending.value
        assertNewSourceUnchanged(f)
        let retained = try await f.store.aiPreview(account: "account", resultID: value.resultId)
        XCTAssertNil(retained)
        XCTAssertFalse(FileManager.default.fileExists(atPath: f.root.appendingPathComponent("Journals/\(day.value).md").path))
    }
    func testAutomaticRebuildUpdatesExactBlockAndDateWithoutWritingDiary() async throws {
        let f = try await fixture(); defer { try? FileManager.default.removeItem(at: f.root) }
        let value = result(), nextDay = try JournalDay("2026-09-21")
        let firstValue = await f.model.rebuildAIResultPreview(value, account: "account", day: day, text: value.text)
        let first = try XCTUnwrap(firstValue)
        let nextValue = await f.model.rebuildAIResultPreview(value, account: "account", day: nextDay, text: "Edited advice")
        let next = try XCTUnwrap(nextValue)
        XCTAssertEqual(next.id, first.id); XCTAssertEqual(next.day, nextDay)
        XCTAssertEqual(next.text, "Edited advice"); XCTAssertNotEqual(next.block, first.block)
        XCTAssertTrue(next.block.contains("> Edited advice")); XCTAssertTrue(next.block.contains("用户编辑：是"))
        XCTAssertTrue(next.block.contains("保存日期：2026-09-21"))
        XCTAssertEqual(f.model.aiResultPreview, next)
        let persisted = try await f.store.aiPreview(account: "account", resultID: value.resultId)
        XCTAssertEqual(persisted, next)
        let oldLock = try await f.store.hasAIResultPending(day), nextLock = try await f.store.hasAIResultPending(nextDay)
        XCTAssertFalse(oldLock); XCTAssertTrue(nextLock)
        for target in [day, nextDay] {
            XCTAssertFalse(FileManager.default.fileExists(atPath: f.root.appendingPathComponent("Journals/\(target.value).md").path))
        }
    }
    func testEditingImmediatelyRetiresExactPreviewConfirmation() async throws {
        let f = try await fixture(); defer { try? FileManager.default.removeItem(at: f.root) }
        let value = result()
        let previewValue = await f.model.rebuildAIResultPreview(value, account: "account", day: day, text: value.text)
        let preview = try XCTUnwrap(previewValue)
        f.model.invalidateAIResultPreparation()
        await f.model.confirmAIResult(account: "account", validation: value, expectedPreview: preview)
        XCTAssertEqual(f.model.aiResultPreview, preview)
        XCTAssertEqual(f.model.aiResultStatus, "保存预览已更新，请查看当前内容后重新确认。")
        XCTAssertFalse(FileManager.default.fileExists(atPath: f.root.appendingPathComponent("Journals/\(day.value).md").path))
    }
    func testRebuiltPreviewRejectsOlderConfirmationThenAppendsOnlyCurrentPatch() async throws {
        let f = try await fixture(); defer { try? FileManager.default.removeItem(at: f.root) }
        let value = result()
        let oldValue = await f.model.rebuildAIResultPreview(value, account: "account", day: day, text: value.text)
        let old = try XCTUnwrap(oldValue)
        let currentValue = await f.model.rebuildAIResultPreview(value, account: "account", day: day, text: "Newest exact advice")
        let current = try XCTUnwrap(currentValue)
        await f.model.confirmAIResult(account: "account", validation: value, expectedPreview: old)
        XCTAssertEqual(f.model.aiResultPreview, current)
        let path = f.root.appendingPathComponent("Journals/\(day.value).md")
        XCTAssertFalse(FileManager.default.fileExists(atPath: path.path))
        await f.model.confirmAIResult(account: "account", validation: value, expectedPreview: current)
        XCTAssertEqual(try Data(contentsOf: path), current.after)
        let text = try String(contentsOf: path, encoding: .utf8)
        XCTAssertEqual(text.components(separatedBy: "Newest exact advice").count - 1, 1)
        XCTAssertNil(f.model.aiResultPreview)
    }
    func testDelayedOlderRebuildCannotPublishAfterNewInput() async throws {
        let f = try await fixture(); defer { f.gate.release(); try? FileManager.default.removeItem(at: f.root) }
        let value = result()
        f.gate.arm(.aiPreview, .afterReplace, occurrence: 2)
        let old = Task { await f.model.rebuildAIResultPreview(value, account: "account", day: self.day, text: "Older input") }
        await fulfillment(of: [f.gate.entered], timeout: 2)
        f.model.invalidateAIResultPreparation()
        let started = expectation(description: "New input has captured a newer preparation request")
        let next = Task {
            started.fulfill()
            return await f.model.rebuildAIResultPreview(value, account: "account", day: self.day, text: "Latest input")
        }
        await fulfillment(of: [started], timeout: 2)
        f.gate.release()
        let oldResult = await old.value, newResult = await next.value
        XCTAssertNil(oldResult); XCTAssertEqual(newResult?.text, "Latest input")
        XCTAssertEqual(f.model.aiResultPreview, newResult); XCTAssertFalse(f.gate.timedOut)
        let persisted = try await f.store.aiPreview(account: "account", resultID: value.resultId)
        XCTAssertEqual(persisted, newResult)
        XCTAssertFalse(FileManager.default.fileExists(atPath: f.root.appendingPathComponent("Journals/\(day.value).md").path))
    }
    func testUnknownChangedTransactionIsRetainedInsteadOfAutomaticallyRebuilt() async throws {
        let f = try await fixture(); defer { try? FileManager.default.removeItem(at: f.root) }
        let value = result()
        let previewValue = await f.model.rebuildAIResultPreview(value, account: "account", day: day, text: value.text)
        let preview = try XCTUnwrap(previewValue)
        f.gate.failOnce(.bodyDiary, .beforeReplace)
        await f.model.confirmAIResult(account: "account", validation: value, expectedPreview: preview)
        let path = f.root.appendingPathComponent("Journals/\(day.value).md")
        let external = Data("## 🧠 Notes\nExternal change while outcome was unknown\n".utf8)
        try external.write(to: path)
        let rebuilt = await f.model.rebuildAIResultPreview(value, account: "account", day: day, text: "Do not replace unknown state")
        XCTAssertNil(rebuilt); XCTAssertEqual(try Data(contentsOf: path), external)
        let retained = try await f.store.aiPreview(account: "account", resultID: value.resultId)
        XCTAssertEqual(retained, preview); XCTAssertEqual(f.model.aiResultPreview, preview)
        let pending = try await f.store.hasAIResultPending(day)
        XCTAssertTrue(pending); XCTAssertTrue(f.model.aiResultPending)
    }
    func testExternalTargetChangeRejectsStalePatchAndRebuildRequiresNewConfirmation() async throws {
        let f = try await fixture(); defer { try? FileManager.default.removeItem(at: f.root) }
        let value = result()
        let previewValue = await f.model.rebuildAIResultPreview(value, account: "account", day: day, text: value.text)
        let preview = try XCTUnwrap(previewValue)
        let path = f.root.appendingPathComponent("Journals/\(day.value).md")
        let external = Data("## 🧠 Notes\nExternal bytes stay exact\n".utf8)
        try external.write(to: path)
        await f.model.confirmAIResult(account: "account", validation: value, expectedPreview: preview)
        XCTAssertEqual(try Data(contentsOf: path), external)
        let nextValue = await f.model.rebuildAIResultPreview(value, account: "account", day: day, text: "Revalidated advice")
        let next = try XCTUnwrap(nextValue)
        XCTAssertEqual(next.before, external); XCTAssertEqual(try Data(contentsOf: path), external)
        await f.model.confirmAIResult(account: "account", validation: value, expectedPreview: preview)
        XCTAssertEqual(try Data(contentsOf: path), external)
        await f.model.confirmAIResult(account: "account", validation: value, expectedPreview: next)
        XCTAssertEqual(try Data(contentsOf: path), next.after)
    }
    func testAutomaticPreparationAfterReceiptNeverDuplicatesOrMovesSavedAdvice() async throws {
        let f = try await fixture(); defer { try? FileManager.default.removeItem(at: f.root) }
        let value = result(), nextDay = try JournalDay("2026-09-21")
        let previewValue = await f.model.rebuildAIResultPreview(value, account: "account", day: day, text: value.text)
        let preview = try XCTUnwrap(previewValue)
        await f.model.confirmAIResult(account: "account", validation: value, expectedPreview: preview)
        let next = await f.model.rebuildAIResultPreview(value, account: "account", day: nextDay, text: "Cannot resave a received result")
        XCTAssertNil(next); XCTAssertNil(f.model.aiResultPreview)
        XCTAssertEqual(f.model.aiResultStatus, "已保存到 \(day.value)，没有重复追加。")
        XCTAssertEqual(try Data(contentsOf: f.root.appendingPathComponent("Journals/\(day.value).md")), preview.after)
        XCTAssertFalse(FileManager.default.fileExists(atPath: f.root.appendingPathComponent("Journals/\(nextDay.value).md").path))
    }

    func testFailedTargetRebuildKeepsPreviousDatePreviewAndLocks() async throws {
        let f = try await fixture(); defer { try? FileManager.default.removeItem(at: f.root) }
        let value = result(), nextDay = try JournalDay("2026-09-21")
        let firstValue = await f.model.rebuildAIResultPreview(value, account: "account", day: day, text: value.text)
        let first = try XCTUnwrap(firstValue)
        let target = f.root.appendingPathComponent("Journals/\(nextDay.value).md")
        let external = Data("# Existing target has no Notes section\n".utf8)
        try external.write(to: target)
        let rebuilt = await f.model.rebuildAIResultPreview(value, account: "account", day: nextDay, text: "Preserve this edit in the sheet")
        XCTAssertNil(rebuilt); XCTAssertEqual(f.model.aiResultPreview, first)
        let persisted = try await f.store.aiPreview(account: "account", resultID: value.resultId)
        XCTAssertEqual(persisted, first)
        let oldLock = try await f.store.hasAIResultPending(day), nextLock = try await f.store.hasAIResultPending(nextDay)
        XCTAssertTrue(oldLock); XCTAssertFalse(nextLock)
        XCTAssertEqual(try Data(contentsOf: target), external)
        XCTAssertFalse(FileManager.default.fileExists(atPath: f.root.appendingPathComponent("Journals/\(day.value).md").path))
        await f.model.confirmAIResult(account: "account", validation: value, expectedPreview: first)
        XCTAssertFalse(FileManager.default.fileExists(atPath: f.root.appendingPathComponent("Journals/\(day.value).md").path))
    }
    func testFailedReplacementPreservesKnownUnappliedPreviewAndTransfersDateLockOnlyOnSuccess() async throws {
        let f = try await fixture(); defer { try? FileManager.default.removeItem(at: f.root) }
        let value = result(), nextDay = try JournalDay("2026-09-21")
        let firstValue = await f.model.rebuildAIResultPreview(value, account: "account", day: day, text: value.text)
        let first = try XCTUnwrap(firstValue)
        f.gate.failOnce(.bodyDiary, .beforeReplace)
        await f.model.confirmAIResult(account: "account", validation: value, expectedPreview: first)
        f.gate.failOnce(.aiPreview, .beforeReplace)
        let failed = await f.model.rebuildAIResultPreview(value, account: "account", day: nextDay, text: "New date and text")
        XCTAssertNil(failed); XCTAssertEqual(f.model.aiResultPreview, first)
        let retained = try await f.store.aiPreview(account: "account", resultID: value.resultId)
        XCTAssertEqual(retained, first)
        let oldHeld = try await f.store.hasAIResultPending(day), newHeld = try await f.store.hasAIResultPending(nextDay)
        XCTAssertTrue(oldHeld); XCTAssertFalse(newHeld)
        let nextValue = await f.model.rebuildAIResultPreview(value, account: "account", day: nextDay, text: "New date and text")
        let next = try XCTUnwrap(nextValue)
        XCTAssertEqual(next.id, first.id); XCTAssertEqual(next.day, nextDay)
        let oldReleased = try await f.store.hasAIResultPending(day), newLocked = try await f.store.hasAIResultPending(nextDay)
        XCTAssertFalse(oldReleased); XCTAssertTrue(newLocked)
        for target in [day, nextDay] {
            XCTAssertFalse(FileManager.default.fileExists(atPath: f.root.appendingPathComponent("Journals/\(target.value).md").path))
        }
    }

}
