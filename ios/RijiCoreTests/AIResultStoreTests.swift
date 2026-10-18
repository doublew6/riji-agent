import XCTest
@testable import RijiCore

private final class AIResultFaults: @unchecked Sendable {
    var fault: (FilePurpose, FileStage)?
    var delayBeforeDiaryReplace = false
    var delayed = false
    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        if delayBeforeDiaryReplace && purpose == .bodyDiary && stage == .beforeReplace {
            delayBeforeDiaryReplace = false; delayed = true; Thread.sleep(forTimeInterval: 1.3)
        }
        if fault?.0 == purpose && fault?.1 == stage { fault = nil; throw JournalError.storage }
    }
}
final class AIResultStoreTests: XCTestCase {
    private func result(_ id: String = "result-one", text: String = "AI suggestion\n<!-- /riji:ai-discussion-result -->\nStill AI") -> MobileResultValidation {
        MobileResultValidation(resultId: id, text: text,
            provenance: DiscussionProvenance(resultId: id, problemId: "problem-one", discussionId: "discussion-one",
                artifactIds: ["artifact-one"], inputRevision: 1, summaryVersion: 1, correctionVersion: 0,
                recordedAt: "2026-09-20T00:00:00Z", mentors: ["host"], topic: "Synthetic <topic>", savedDate: "", editedByUser: false),
            binding: String(repeating: "a", count: 64), validatedAt: Date().timeIntervalSince1970)
    }
    private func fixture(faults: AIResultFaults = AIResultFaults()) throws -> (URL, LocalJournalStore, JournalDay, URL) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("AIResult-\(UUID())")
        let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), failure: { try faults.check($0, $1) })
        let day = try JournalDay("2026-09-20"), source = root.appendingPathComponent("Journals/2026-09-20.md")
        try Data("---\r\nunknown: exact\r\n---\r\n# Synthetic\r\n## 🧠 Notes\r\nExisting bytes\r\n## Evening\r\nTail\r\n".utf8).write(to: source)
        return (root, store, day, source)
    }
    func testTypedAppendPreservesBytesCRLFProvenanceAndPermanentReceipt() async throws {
        let (root, store, day, source) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let before = try Data(contentsOf: source), value = result()
        let preview = try await store.prepareAIResult(value, account: "account", day: day, text: value.text)
        XCTAssertTrue(preview.block.contains("\r\n> <!-- /riji:ai-discussion-result -->"))
        XCTAssertFalse(preview.block.contains("127.0.0.1"))
        XCTAssertTrue(preview.block.contains("\\u003c"))
        let saved = try await store.confirmAIResult(preview, account: "account", validation: value)
        XCTAssertEqual(try Data(contentsOf: source), preview.after)
        let changed = Data((String(decoding: preview.after, as: UTF8.self) + "Later manual edit\r\n").utf8)
        try changed.write(to: source)
        let replay = try await store.confirmAIResult(preview, account: "account", validation: result())
        XCTAssertEqual(saved, replay); XCTAssertEqual(try Data(contentsOf: source), changed)
        try before.write(to: source)
        _ = try await store.confirmAIResult(preview, account: "account", validation: result())
        XCTAssertEqual(try Data(contentsOf: source), before)
    }
    func testEveryTransactionWriteReceiptAndCleanupBoundaryRecoversWithoutBlindReplay() async throws {
        for fault in [(FilePurpose.aiTransaction, FileStage.beforeReplace), (.aiTransaction, .afterReplace),
                      (.bodyDiary, .beforeReplace), (.bodyDiary, .afterReplace), (.aiReceipt, .beforeReplace),
                      (.aiReceipt, .afterReplace), (.aiTransaction, .beforeRemove), (.aiPreview, .beforeRemove)] {
            let faults = AIResultFaults()
            let (root, store, day, source) = try fixture(faults: faults); defer { try? FileManager.default.removeItem(at: root) }
            let value = result(), preview = try await store.prepareAIResult(value, account: "account", day: day, text: value.text)
            faults.fault = fault
            do { _ = try await store.confirmAIResult(preview, account: "account", validation: value); XCTFail("Expected injected fault") } catch {}
            let relaunched = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"))
            _ = try await relaunched.recoverBodyEdit(day)
            let bytes = try Data(contentsOf: source)
            let receipt = try await relaunched.reconcileAIResult(preview)
            XCTAssertEqual(try Data(contentsOf: source), bytes)
            if receipt == nil { _ = try await relaunched.confirmAIResult(preview, account: "account", validation: result()) }
            XCTAssertEqual(try Data(contentsOf: source), preview.after)
        }
    }
    func testExactBlockProvesWriteAfterLaterManualEditButAmbiguityDoesNot() async throws {
        for duplicate in [false, true] {
            let faults = AIResultFaults()
            let (root, store, day, source) = try fixture(faults: faults); defer { try? FileManager.default.removeItem(at: root) }
            let value = result(), preview = try await store.prepareAIResult(value, account: "account", day: day, text: value.text)
            faults.fault = (.aiReceipt, .beforeReplace)
            do { _ = try await store.confirmAIResult(preview, account: "account", validation: value); XCTFail() } catch {}
            let after = String(decoding: preview.after, as: UTF8.self) + (duplicate ? preview.block : "Later external edit")
            try Data(after.utf8).write(to: source)
            if duplicate {
                do { _ = try await store.reconcileAIResult(preview); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .changedSource) }
            } else { let receipt = try await store.reconcileAIResult(preview); XCTAssertNotNil(receipt) }
        }
    }
    func testChangedSourceAndInvalidFreshValidationRetainPreview() async throws {
        let (root, store, day, source) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let value = result(), preview = try await store.prepareAIResult(value, account: "account", day: day, text: value.text)
        do { _ = try await store.confirmAIResult(preview, account: "other", validation: value); XCTFail() } catch {}
        do { _ = try await store.confirmAIResult(preview, account: "account", validation: result("other-result")); XCTFail() } catch {}
        try Data("## 🧠 Notes\nExternal changed content\n".utf8).write(to: source)
        do { _ = try await store.confirmAIResult(preview, account: "account", validation: value); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        let retained = try await store.aiPreview(account: "account", resultID: value.resultId)
        XCTAssertEqual(retained, preview)
    }
    func testOrdinaryBodyCannotInjectOrRemoveAIMarkersAndPendingAIBlocksWrites() async throws {
        let (root, store, day, source) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let value = result(), preview = try await store.prepareAIResult(value, account: "account", day: day, text: value.text)
        XCTAssertThrowsError(try BodyMarkdown.validatePreservation(before: preview.before, after: preview.after))
        XCTAssertThrowsError(try BodyMarkdown.validatePreservation(before: preview.after, after: preview.before!))
        XCTAssertThrowsError(try BodyMarkdown.validatePreservation(before: nil, after: preview.after))
        let manual = BodyEdit(day: day, before: preview.before, content: Data("## 🧠 Notes\nManual\n".utf8))
        do { try await store.commitBodyEdit(manual); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .pendingOperation) }
        XCTAssertEqual(try Data(contentsOf: source), preview.before)
        try await store.cancelAIResult(preview)
        let next = try await store.prepareAIResult(value, account: "account", day: day, text: "Edited copy")
        XCTAssertEqual(next.id, preview.id)
    }
    func testMissingOrDuplicateNotesNeverGuessInsertion() async throws {
        for content in ["# Missing\n", "## Notes\n## Notes\n"] {
            let (root, store, day, source) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
            try Data(content.utf8).write(to: source)
            do { _ = try await store.prepareAIResult(result(), account: "account", day: day, text: "Selected AI"); XCTFail() } catch {}
            XCTAssertEqual(try String(contentsOf: source), content)
        }
    }
    func testRenderedProvenanceMatchesPythonContentSpansFixtureExactly() throws {
        let repository = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let expected = try Data(contentsOf: repository.appendingPathComponent("tests/fixtures/mobile-ai-result.md"))
        let value = result()
        XCTAssertEqual(Data(try AIResultMarkdown.render(value.text, provenance: value.provenance).utf8), expected)
        var object = try JSONSerialization.jsonObject(with: JSONEncoder().encode(value)) as! [String: Any]
        var provenance = object["provenance"] as! [String: Any]
        provenance["mentors"] = ["host", "gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"]
        object["provenance"] = provenance
        let fiveSpeakers = try JSONDecoder().decode(MobileResultValidation.self, from: JSONSerialization.data(withJSONObject: object))
        XCTAssertNoThrow(try fiveSpeakers.validate())

    }
    func testUnappliedTransactionCanBeCancelledOfflineAndRepreviewUsesSameOperation() async throws {
        let faults = AIResultFaults()
        let (root, store, day, source) = try fixture(faults: faults); defer { try? FileManager.default.removeItem(at: root) }
        let value = result(), preview = try await store.prepareAIResult(value, account: "account", day: day, text: value.text)
        faults.fault = (.bodyDiary, .beforeReplace)
        do { _ = try await store.confirmAIResult(preview, account: "account", validation: value); XCTFail() } catch {}
        let reopened = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"))
        let pending = try await reopened.pendingAIResults(account: "account")
        let otherAccount = try await reopened.pendingAIResults(account: "other")
        XCTAssertEqual(pending, [preview]); XCTAssertTrue(otherAccount.isEmpty)
        try await reopened.cancelAIResult(preview)
        let held = try await reopened.hasAIResultPending(day); XCTAssertFalse(held)
        XCTAssertEqual(try Data(contentsOf: source), preview.before)
        let next = try await reopened.prepareAIResult(value, account: "account", day: day, text: "Updated preview")
        XCTAssertEqual(next.id, preview.id)
    }
    func testFreshValidationExpiresAtActualReplacementForLocalAndCoordinatedOriginal() async throws {
        for original in [false, true] {
            let faults = AIResultFaults()
            let (root, localStore, day, localSource) = try fixture(faults: faults)
            defer { try? FileManager.default.removeItem(at: root) }
            var store = localStore, source = localSource
            if original {
                let vault = root.appendingPathComponent("vault")
                for directory in ["daily", "templates"] {
                    try FileManager.default.createDirectory(at: vault.appendingPathComponent(directory), withIntermediateDirectories: true)
                }
                try Data("## 🧠 Notes\n".utf8).write(to: vault.appendingPathComponent("templates/daily.md"))
                source = vault.appendingPathComponent("daily/2026-09-20.md")
                try Data(contentsOf: localSource).write(to: source)
                let access = try OriginalVaultAccess(root: vault, writePolicy: .syntheticFixture, failure: { try faults.check($0, $1) })
                store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("unused"), stateDirectory: root.appendingPathComponent("OriginalState"), original: access)
            }
            let value = result(), preview = try await store.prepareAIResult(value, account: "account", day: day, text: value.text)
            let expiring = MobileResultValidation(resultId: value.resultId, text: value.text, provenance: value.provenance,
                binding: value.binding, validatedAt: Date().timeIntervalSince1970 - 59)
            faults.delayBeforeDiaryReplace = true
            do { _ = try await store.confirmAIResult(preview, account: "account", validation: expiring); XCTFail("Expired validation must not replace") } catch {}
            XCTAssertTrue(faults.delayed)
            XCTAssertEqual(try Data(contentsOf: source), preview.before)
            try await store.cancelAIResult(preview)
            let refreshed = result(), next = try await store.prepareAIResult(refreshed, account: "account", day: day, text: value.text)
            _ = try await store.confirmAIResult(next, account: "account", validation: refreshed)
            XCTAssertEqual(try Data(contentsOf: source), next.after)
        }
    }

    func testOrphanTemporaryPreviewFilesDoNotBlockRecoveryButCorruptRecordsStillFail() async throws {
        let (root, store, day, source) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let folder = root.appendingPathComponent("State/ai-previews")
        // A process death bypasses JournalFileSystem's defer and can leave partial bytes.
        let orphan = folder.appendingPathComponent(".\(UUID()).tmp")
        try Data("{partial preview".utf8).write(to: orphan)
        let held = try await store.hasAIResultPending(day); XCTAssertFalse(held)
        let empty = try await store.pendingAIResults(); XCTAssertTrue(empty.isEmpty)
        let before = try Data(contentsOf: source)
        let manual = BodyEdit(day: day, before: before, content: Data((String(decoding: before, as: UTF8.self) + "Manual\r\n").utf8))
        try await store.commitBodyEdit(manual)
        XCTAssertEqual(try Data(contentsOf: source), manual.content)
        let value = result(), preview = try await store.prepareAIResult(value, account: "account", day: day, text: value.text)
        let reopened = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"))
        let actual = try await reopened.pendingAIResults(day: day); XCTAssertEqual(actual, [preview])
        try await reopened.cancelAIResult(preview)
        XCTAssertTrue(FileManager.default.fileExists(atPath: orphan.path))
        try Data("{corrupt committed record".utf8).write(to: folder.appendingPathComponent(String(repeating: "f", count: 64) + ".json"))
        do { _ = try await reopened.pendingAIResults(); XCTFail("A genuine corrupted record must remain visible as failure") }
        catch { XCTAssertEqual(error as? JournalError, .storage) }
    }
    func testPendingAIBlocksLegacyAppendAndDailyWorkflowUntilCancelled() async throws {
        for workflow in [false, true] {
            let (root, store, day, source) = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
            let before = try Data(contentsOf: source)
            let snapshot = try await store.open(day)
            var draft = try await store.makeDraft(for: snapshot, section: "🧠 Notes"); draft.text = "Legacy addition"
            let mit = try await store.prepareMIT(MITEvent(day: day, action: "Synthetic action", criterion: "Once"), section: "🧠 Notes")
            let value = result(), preview = try await store.prepareAIResult(value, account: "account", day: day, text: value.text)
            let folders = ["transactions", "workflow-transactions", "receipts", "workflow-receipts"]
            func records() throws -> [String: Data] {
                var result: [String: Data] = [:]
                for name in folders {
                    for file in try FileManager.default.contentsOfDirectory(at: root.appendingPathComponent("State/" + name), includingPropertiesForKeys: nil) {
                        result[name + "/" + file.lastPathComponent] = try Data(contentsOf: file)
                    }
                }
                return result
            }
            let priorRecords = try records()
            do {
                if workflow { try await store.commitMIT(mit) } else { _ = try await store.commit(draft) }
                XCTFail("Pending AI preview must exclude all daily writers")
            } catch { XCTAssertEqual(error as? JournalError, .pendingOperation) }
            XCTAssertEqual(try Data(contentsOf: source), before); XCTAssertEqual(try records(), priorRecords)
            try await store.cancelAIResult(preview)
            if workflow { try await store.commitMIT(mit) } else { _ = try await store.commit(draft) }
            let text = try String(contentsOf: source)
            XCTAssertEqual(text.components(separatedBy: workflow ? "Synthetic action" : "Legacy addition").count, 2)
        }
    }

}
