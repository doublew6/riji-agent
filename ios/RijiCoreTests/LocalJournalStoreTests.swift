import Foundation
import XCTest
@testable import RijiCore

private final class FailureSwitch: @unchecked Sendable {
    private let lock = NSLock()
    private var armed: (FilePurpose, FileStage)?

    func arm(_ purpose: FilePurpose, _ stage: FileStage) {
        lock.lock(); defer { lock.unlock() }
        armed = (purpose, stage)
    }

    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        lock.lock(); defer { lock.unlock() }
        if let value = armed, value.0 == purpose, value.1 == stage {
            armed = nil
            throw JournalError.storage
        }
    }
}

final class LocalJournalStoreTests: XCTestCase {
    private var root: URL!
    private let day = try! JournalDay("2026-09-12")
    private let zone = TimeZone(identifier: "Asia/Shanghai")!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("RijiTests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    private func store(failure: FailureSwitch? = nil, timeZone: TimeZone? = nil) throws -> LocalJournalStore {
        try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"),
                              stateDirectory: root.appendingPathComponent("Internal"),
                              timeZone: timeZone ?? zone, failure: { try failure?.check($0, $1) })
    }

    private func draft(_ store: LocalJournalStore, text: String = "Synthetic entry.",
                       section: String = "🧠 Notes") async throws -> JournalDraft {
        let snapshot = try await store.open(day)
        var draft = try await store.makeDraft(for: snapshot, section: section)
        draft.text = text
        return draft
    }

    private func writeDiary(_ text: String) throws {
        try Data(text.utf8).write(to: root.appendingPathComponent("Journals/\(day.value).md"))
    }

    func testFreshOpeningDisplaysTemplateWithoutCreatingDiary() async throws {
        let store = try store()
        let snapshot = try await store.open(day)
        XCTAssertTrue(snapshot.markdown.contains("# 2026-09-12"))
        XCTAssertNil(snapshot.sourceHash)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: root.appendingPathComponent("Journals").path), [])
    }

    func testDraftSurvivesRelaunchWithDaySectionVersionAndEffectiveTemplate() async throws {
        let store = try store()
        let value = try await draft(store, text: "  Keep whitespace.\nSecond line.  ", section: "🌆 Evening")
        try await store.saveDraft(value)
        let reopened = try self.store()
        let snapshot = try await reopened.open(day)
        XCTAssertEqual(snapshot.draft, value)
        XCTAssertFalse(snapshot.markdown.contains(value.text))
        let tomorrow = ISO8601DateFormatter().date(from: "2026-09-13T14:00:00Z")!
        let initial = try await reopened.initialDay(now: tomorrow)
        XCTAssertEqual(initial, day)
    }

    func testExactTextAppendAndMarkdownOnlyExport() async throws {
        let store = try store()
        let original = "---\nunknown: keep\n---\n# Day\n\n## 🧠 Notes\nExisting.\n\n## Other\nTail\n"
        try writeDiary(original)
        let text = "  Literal *text*  \n\nSecond line.\t"
        let value = try await draft(store, text: text)
        let receipt = try await store.commit(value)
        let exported = try await store.exportMarkdown(day)
        let expected = original.replacingOccurrences(of: "## Other", with: text + "\n## Other")
        XCTAssertEqual(exported, Data(expected.utf8))
        XCTAssertEqual(receipt.afterHash, SourceHash.digest(exported))
        let snapshot = try await store.open(day)
        XCTAssertNil(snapshot.draft)
        XCTAssertFalse(String(decoding: exported, as: UTF8.self).contains("operationID"))
    }

    func testSameOperationRetryReturnsReceiptWithoutAppendingAgain() async throws {
        let store = try store()
        let value = try await draft(store)
        let first = try await store.commit(value)
        let second = try await store.commit(value)
        XCTAssertEqual(first, second)
        let exported = try await store.exportMarkdown(day)
        XCTAssertEqual(String(decoding: exported, as: UTF8.self).components(separatedBy: value.text).count, 2)
    }

    func testChangedPayloadForExistingIdentifierIsRejected() async throws {
        let store = try store()
        var value = try await draft(store)
        _ = try await store.commit(value)
        value.text = "Different text"
        do { _ = try await store.commit(value); XCTFail("Expected rejection") }
        catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
    }

    func testIndependentIdenticalTextIsSavedTwice() async throws {
        let store = try store()
        let first = try await draft(store)
        _ = try await store.commit(first)
        let second = try await draft(store)
        _ = try await store.commit(second)
        XCTAssertNotEqual(first.operationID, second.operationID)
        let exported = try await store.exportMarkdown(day)
        XCTAssertEqual(String(decoding: exported, as: UTF8.self).components(separatedBy: first.text).count, 3)
        let oldReceipt = try await store.commit(first)
        XCTAssertEqual(oldReceipt.operationID, first.operationID)
    }

    func testConcurrentSameOperationIsSerialized() async throws {
        let store = try store()
        let value = try await draft(store)
        async let first = store.commit(value)
        async let second = store.commit(value)
        let results = try await (first, second)
        XCTAssertEqual(results.0, results.1)
    }

    func testChangedSourcePreservesFileAndDraft() async throws {
        let store = try store()
        try writeDiary("## 🧠 Notes\nOriginal\n")
        let value = try await draft(store)
        let changed = "## 🧠 Notes\nChanged elsewhere\n"
        try writeDiary(changed)
        do { _ = try await store.commit(value); XCTFail("Expected source conflict") }
        catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        let snapshot = try await store.open(day)
        XCTAssertEqual(snapshot.markdown, changed)
        XCTAssertEqual(snapshot.draft, value)
    }

    func testMissingAndDuplicateHeadingsPreserveDraft() async throws {
        let store = try store()
        for original in ["## Notes\n", "## 🧠 Notes\nOne\n## 🧠 Notes\nTwo\n"] {
            try writeDiary(original)
            let value = try await draft(store)
            do { _ = try await store.commit(value); XCTFail("Expected heading rejection") }
            catch { XCTAssertTrue([JournalError.missingSection, .ambiguousSection].contains(error as? JournalError ?? .storage)) }
            let snapshot = try await store.open(day)
            XCTAssertEqual(snapshot.markdown, original)
            XCTAssertEqual(snapshot.draft, value)
        }
    }

    func testRecoveryAcrossEveryPersistenceBoundary() async throws {
        let cases: [(FilePurpose, FileStage)] = [
            (.transaction, .beforeWrite), (.transaction, .beforeReplace), (.transaction, .afterReplace),
            (.diary, .beforeWrite), (.diary, .beforeReplace), (.diary, .afterReplace),
            (.receipt, .beforeWrite), (.receipt, .beforeReplace), (.receipt, .afterReplace),
            (.draft, .beforeRemove), (.draft, .afterRemove),
            (.transaction, .beforeRemove), (.transaction, .afterRemove)
        ]
        for (purpose, stage) in cases {
            let separateRoot = root.appendingPathComponent(UUID().uuidString)
            let failure = FailureSwitch()
            let journals = separateRoot.appendingPathComponent("Journals")
            let state = separateRoot.appendingPathComponent("Internal")
            let store = try LocalJournalStore(journalDirectory: journals, stateDirectory: state, timeZone: zone,
                                              failure: { try failure.check($0, $1) })
            let value = try await draft(store)
            failure.arm(purpose, stage)
            do { _ = try await store.commit(value); XCTFail("Expected injected failure") } catch {}
            let reopened = try LocalJournalStore(journalDirectory: journals, stateDirectory: state, timeZone: zone)
            _ = try await reopened.recover(day)
            _ = try await reopened.commit(value)
            let data = try await reopened.exportMarkdown(day)
            XCTAssertEqual(String(decoding: data, as: UTF8.self).components(separatedBy: value.text).count, 2,
                           "Failed \(purpose) / \(stage)")
            let snapshot = try await reopened.open(day)
            XCTAssertNil(snapshot.draft)
        }
    }

    func testFailedDraftReplaceRetainsPreviousDraftAndDiary() async throws {
        let failure = FailureSwitch()
        let store = try store(failure: failure)
        let first = try await draft(store, text: "First retained draft")
        try await store.saveDraft(first)
        let second = first.updating(text: "Visible new text", section: first.section)
        failure.arm(.draft, .beforeReplace)
        do { try await store.saveDraft(second); XCTFail("Expected failure") } catch {}
        let snapshot = try await store.open(day)
        XCTAssertEqual(snapshot.draft, first)
        XCTAssertNil(snapshot.sourceHash)
        try await store.saveDraft(second)
        let retried = try await store.open(day)
        XCTAssertEqual(retried.draft, second)
    }

    func testChangedPayloadIsRejectedEvenBeforeTransactionPersistence() async throws {
        let failure = FailureSwitch()
        let store = try store(failure: failure)
        var value = try await draft(store)
        failure.arm(.transaction, .beforeWrite)
        do { _ = try await store.commit(value); XCTFail("Expected persistence failure") } catch {}
        value.text = "Changed payload with the same identity"
        do { _ = try await store.commit(value); XCTFail("Expected identity rejection") }
        catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
        let revised = value.updating(text: "A new deliberate confirmation", section: value.section)
        XCTAssertNotEqual(revised.operationID, value.operationID)
        _ = try await store.commit(revised)
    }

    func testUnknownRecoveryStateNeverWrites() async throws {
        let failure = FailureSwitch()
        let store = try store(failure: failure)
        let value = try await draft(store)
        failure.arm(.diary, .beforeReplace)
        do { _ = try await store.commit(value); XCTFail("Expected failure") } catch {}
        let unknown = "## 🧠 Notes\nUnrecognized file state.\n"
        try writeDiary(unknown)
        let reopened = try self.store()
        do { _ = try await reopened.recover(day); XCTFail("Expected conflict") }
        catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        let snapshot = try await reopened.open(day)
        XCTAssertEqual(snapshot.markdown, unknown)
        XCTAssertEqual(snapshot.draft, value)
        XCTAssertTrue(snapshot.isPending)
    }

    func testFailedReplacementPreservesExistingDiaryThenRecovers() async throws {
        let failure = FailureSwitch()
        let store = try store(failure: failure)
        let original = "## 🧠 Notes\nExisting bytes\n## Tail\nKeep me\n"
        try writeDiary(original)
        let value = try await draft(store)
        failure.arm(.diary, .beforeReplace)
        do { _ = try await store.commit(value); XCTFail("Expected failure") } catch {}
        let interrupted = try await store.open(day)
        XCTAssertEqual(interrupted.markdown, original)
        XCTAssertEqual(interrupted.draft, value)
        let reopened = try self.store()
        _ = try await reopened.recover(day)
        let exported = try await reopened.exportMarkdown(day)
        XCTAssertEqual(String(decoding: exported, as: UTF8.self),
                       original.replacingOccurrences(of: "## Tail", with: value.text + "\n## Tail"))
    }

    func testRelaunchRetainsPendingDayAfterDraftCleanup() async throws {
        let failure = FailureSwitch()
        let store = try store(failure: failure)
        let value = try await draft(store)
        failure.arm(.draft, .afterRemove)
        do { _ = try await store.commit(value); XCTFail("Expected cleanup failure") } catch {}
        let reopened = try self.store()
        let tomorrow = ISO8601DateFormatter().date(from: "2026-09-13T14:00:00Z")!
        let selected = try await reopened.initialDay(now: tomorrow)
        XCTAssertEqual(selected, day)
        let receipt = try await reopened.recover(selected)
        XCTAssertEqual(receipt?.operationID, value.operationID)
        let snapshot = try await reopened.open(day)
        XCTAssertNil(snapshot.draft)
        XCTAssertTrue(snapshot.markdown.contains(value.text))
    }

    func testOldReceiptRetryDoesNotDeleteANewerDraft() async throws {
        let store = try store()
        let first = try await draft(store)
        _ = try await store.commit(first)
        let next = try await draft(store, text: "New unconfirmed record")
        try await store.saveDraft(next)
        _ = try await store.commit(first)
        let snapshot = try await store.open(day)
        XCTAssertEqual(snapshot.draft, next)
        XCTAssertFalse(snapshot.markdown.contains(next.text))
    }

    func testNewDiaryCollisionPreservesBothFileAndDraft() async throws {
        let store = try store()
        let value = try await draft(store)
        let appeared = "## 🧠 Notes\nAppeared after preview\n"
        try writeDiary(appeared)
        do { _ = try await store.commit(value); XCTFail("Expected collision") }
        catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        let snapshot = try await store.open(day)
        XCTAssertEqual(snapshot.markdown, appeared)
        XCTAssertEqual(snapshot.draft, value)
    }

    func testPendingOperationRejectsEditsAndNewOperation() async throws {
        let failure = FailureSwitch()
        let store = try store(failure: failure)
        var value = try await draft(store)
        failure.arm(.diary, .beforeReplace)
        do { _ = try await store.commit(value); XCTFail("Expected failure") } catch {}
        value.text = "Unconfirmed change"
        do { try await store.saveDraft(value); XCTFail("Expected rejection") }
        catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
        let independent = try await draft(store)
        do { try await store.saveDraft(independent); XCTFail("Expected rejection") }
        catch { XCTAssertEqual(error as? JournalError, .pendingOperation) }
    }

    func testExportFailureLeavesDestinationDiaryAndDraftIntact() async throws {
        let failure = FailureSwitch()
        let store = try store(failure: failure)
        let saved = try await draft(store)
        _ = try await store.commit(saved)
        let pending = try await draft(store, text: "Still unconfirmed")
        try await store.saveDraft(pending)
        let before = try await store.exportMarkdown(day)
        let destination = root.appendingPathComponent("export.md")
        try Data("Existing export".utf8).write(to: destination)
        failure.arm(.export, .beforeReplace)
        do { try await store.exportMarkdown(day, to: destination); XCTFail("Expected failure") } catch {}
        XCTAssertEqual(try Data(contentsOf: destination), Data("Existing export".utf8))
        let after = try await store.exportMarkdown(day)
        let snapshot = try await store.open(day)
        XCTAssertEqual(after, before)
        XCTAssertEqual(snapshot.draft, pending)
    }

    func testTimezonePersistsAcrossDeviceZoneChangeAndMidnight() async throws {
        _ = try store()
        let reopened = try store(timeZone: TimeZone(identifier: "America/Los_Angeles")!)
        XCTAssertEqual(reopened.timeZone.identifier, zone.identifier)
        let date = ISO8601DateFormatter().date(from: "2026-09-12T16:01:00Z")!
        let day = try await reopened.initialDay(now: date)
        XCTAssertEqual(day.value, "2026-09-13")
    }

    func testDirectoryContainsDraftsAndSavedDaysOnly() async throws {
        let store = try store()
        let value = try await draft(store)
        try await store.saveDraft(value)
        let list = try await store.directory()
        XCTAssertEqual(list.count, 1)
        XCTAssertEqual(list.first?.day, day)
        XCTAssertEqual(list.first?.hasDraft, true)
        XCTAssertEqual(list.first?.hasDiary, false)
    }

    func testDateValidationRejectsTraversalAndImpossibleDates() throws {
        for value in ["../x", "2026-02-30", "2026-2-01", "2026-13-01"] {
            XCTAssertThrowsError(try JournalDay(value))
        }
        XCTAssertEqual(try JournalDay("2024-02-29").value, "2024-02-29")
        XCTAssertThrowsError(try JSONDecoder().decode(JournalDay.self, from: Data("\"../../secret\"".utf8)))
    }

    func testTemplateReplacesOnlySupportedPlaceholders() throws {
        XCTAssertEqual(try DailyTemplate.instantiate(day: day, template: "{{date}} / {{title}}"), "2026-09-12 / 2026-09-12")
        XCTAssertThrowsError(try DailyTemplate.instantiate(day: day, template: "{{unknown}}"))
        XCTAssertThrowsError(try DailyTemplate.instantiate(day: day, template: "<% script() %>"))
    }

    func testExactHeadingFencesNestedBoundariesAndBytePreservation() throws {
        let prefix = "---\r\n# 🧠 Notes\r\n---\r\n# Day\r\n```md\r\n## 🧠 Notes\r\n```\r\n## 🧠 Notes Extra\r\nX\r\n## 🧠 Notes\r\nKeep\r\n### Child\r\nNested\r\n~~~~\r\n## 🧠 Notes\r\n~~~~\r\n"
        let suffix = "## Tail\r\nUnrelated café 🐱\r\n"
        let original = Data((prefix + suffix).utf8)
        let output = try MarkdownSectionAppender.append(text: "  Exact\ninput  ", to: "🧠 Notes", in: original)
        XCTAssertEqual(output, Data((prefix + "  Exact\ninput  \r\n" + suffix).utf8))
    }

    func testUnterminatedFenceAndClosingHashes() throws {
        let original = "## Notes ###\nKeep\n```\n## Notes\n"
        let output = try MarkdownSectionAppender.append(text: "New", to: "Notes", in: Data(original.utf8))
        XCTAssertEqual(output, Data((original + "New\n").utf8))
        XCTAssertThrowsError(try MarkdownSectionAppender.append(text: "X", to: "Notes", in: Data("```\n## Notes\n".utf8)))
    }
}

extension LocalJournalStoreTests {
    func testUnicodeEquivalentEditsKeepExactNewUTF8Bytes() async throws {
        let store = try store()
        let original = try await draft(store, text: "\u{00E9}")
        try await store.saveDraft(original)
        let changed = original.updating(text: "e\u{0301}", section: original.section)
        XCTAssertNotEqual(changed.operationID, original.operationID)
        XCTAssertEqual(Data(changed.text.utf8), Data([0x65, 0xcc, 0x81]))
        try await store.saveDraft(changed)
        _ = try await store.commit(changed)
        let exported = try await store.exportMarkdown(day)
        XCTAssertTrue(exported.range(of: Data([0x65, 0xcc, 0x81])) != nil)
        XCTAssertNil(exported.range(of: Data([0xc3, 0xa9])))
    }

    func testUnicodeEquivalentSameIDDraftMutationIsRejected() async throws {
        let store = try store()
        let original = try await draft(store, text: "\u{00E9}")
        try await store.saveDraft(original)
        var changed = original
        changed.text = "e\u{0301}"
        XCTAssertNotEqual(original, changed)
        do { try await store.saveDraft(changed); XCTFail("Expected byte-level binding") }
        catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
        let retained = try await store.open(day)
        XCTAssertEqual(Data(retained.draft!.text.utf8), Data(original.text.utf8))
    }

    func testCommentContainedHeadingsCannotReceiveAppends() throws {
        let original = "<!--\n## Notes\n-->\n## Real\nKept\n"
        XCTAssertThrowsError(try MarkdownSectionAppender.append(text: "Visible", to: "Notes", in: Data(original.utf8))) {
            XCTAssertEqual($0 as? JournalError, .missingSection)
        }
        let real = try MarkdownSectionAppender.append(text: "Visible", to: "Real", in: Data(original.utf8))
        XCTAssertEqual(real, Data((original + "Visible\n").utf8))
    }

    func testAppendRejectsFencesCommentsAndDuplicateExistingAnchors() async throws {
        let store = try store()
        let original = "## First\nKeep\n## Later\nStill writable\n"
        for text in ["```\nUnclosed code", "~~~\nUnclosed code", "<!--\nUnclosed comment", "## Later\nAmbiguous"] {
            try writeDiary(original)
            let value = try await draft(store, text: text, section: "First")
            do { _ = try await store.commit(value); XCTFail("Expected anchor protection") }
            catch { XCTAssertTrue([JournalError.invalidMarkdown, .ambiguousSection].contains(error as? JournalError ?? .storage)) }
            let snapshot = try await store.open(day)
            XCTAssertEqual(Data(snapshot.markdown.utf8), Data(original.utf8))
            XCTAssertEqual(Data(snapshot.draft!.text.utf8), Data(text.utf8))
        }
    }

    func testClosedFenceAndCommentKeepLaterAnchorWritable() throws {
        let source = Data("## First\nKeep\n## Later\nTail\n".utf8)
        let text = "```\n## Later\n```\n<!--\n## Later\n-->"
        let after = try MarkdownSectionAppender.append(text: text, to: "First", in: source)
        let second = try MarkdownSectionAppender.append(text: "Second", to: "Later", in: after)
        XCTAssertTrue(String(decoding: second, as: UTF8.self).hasSuffix("Tail\nSecond\n"))
    }

    func testClearedAndSectionOnlyDraftsDoNotAppearInDirectory() async throws {
        let store = try store()
        let typed = try await draft(store)
        try await store.saveDraft(typed)
        try await store.saveDraft(typed.updating(text: "", section: "🌆 Evening"))
        let empty = try await store.directory()
        XCTAssertTrue(empty.isEmpty)
        let sectionOnly = try await draft(store, text: "", section: "🌅 Morning")
        try await store.saveDraft(sectionOnly)
        let changedSection = try await store.directory()
        XCTAssertTrue(changedSection.isEmpty)
        let saved = try await draft(store)
        _ = try await store.commit(saved)
        let blank = try await draft(store, text: "")
        try await store.saveDraft(blank)
        let existingDiary = try await store.directory()
        XCTAssertEqual(existingDiary.count, 1)
        XCTAssertTrue(existingDiary[0].hasDiary)
        XCTAssertFalse(existingDiary[0].hasDraft)
    }

    func testDirectoryRetainsEmptyPendingOperations() async throws {
        let failure = FailureSwitch()
        let store = try store(failure: failure)
        let empty = try await draft(store, text: "")
        failure.arm(.diary, .beforeReplace)
        do { _ = try await store.commit(empty); XCTFail("Expected pending operation") } catch {}
        let entries = try await store.directory()
        XCTAssertEqual(entries.count, 1)
        XCTAssertTrue(entries[0].hasDraft)
        XCTAssertFalse(entries[0].hasDiary)
    }
}
