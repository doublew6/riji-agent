import XCTest
@testable import RijiCore

private final class BodyFaults: @unchecked Sendable {
    let lock = NSLock()
    var purpose: FilePurpose?
    var stage: FileStage?
    func arm(_ purpose: FilePurpose, _ stage: FileStage) { lock.lock(); defer { lock.unlock() }; self.purpose = purpose; self.stage = stage }
    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        lock.lock(); defer { lock.unlock() }
        if self.purpose == purpose && self.stage == stage { self.purpose = nil; throw JournalError.storage }
    }
}

private struct BodyFixture {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("BodySynthetic-\(UUID())")
    let day = try! JournalDay("2026-09-14")
    let faults = BodyFaults()
    var source: URL { root.appendingPathComponent("Journals/2026-09-14.md") }
    func store() throws -> LocalJournalStore {
        try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"),
                              failure: { [faults] in try faults.check($0, $1) })
    }
    func clean() { try? FileManager.default.removeItem(at: root) }
}

final class BodyRevisionStoreTests: XCTestCase {
    func testExactTemplateAndMiddleReplacementRetainMetadataUnicodeAndCRLF() async throws {
        let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
        let raw = Data("---\r\nunknown: literal\r\n---\r\n# {{date}} {{date:dddd}}\r\n## Notes\r\ne\u{301} 原文\r\n<% opaque {{date}} %>\r\n".utf8)
        let template = try TemplateVersion(kind: .daily, raw: raw)
        try await store.importTemplate(template, operationID: UUID()); try await store.selectTemplate(template)
        let snapshot = try await store.open(f.day)
        let expected = raw.replacing(Data("{{date}} {{date:dddd}}".utf8), with: Data("2026-09-14 星期一".utf8))
        XCTAssertEqual(Data(snapshot.markdown.utf8), expected)
        let first = BodyEdit(day: f.day, before: nil, content: expected)
        try await store.commitBodyEdit(first)
        let changed = expected.replacing(Data("原文".utf8), with: Data("修改后的正文".utf8))
        let edit = BodyEdit(day: f.day, before: expected, content: changed)
        try await store.commitBodyEdit(edit)
        XCTAssertEqual(try Data(contentsOf: f.source), changed)
        let revisions = try await store.bodyRevisions(f.day)
        XCTAssertEqual(revisions.map(\.id), [first.id, edit.id]); XCTAssertEqual(revisions.last?.before, expected)
        let selected = try await store.selectedTemplate(.daily)
        XCTAssertEqual(selected.raw, raw)
    }

    func testInterruptedReplaceReceiptAndCleanupRecoverSameRevision() async throws {
        for fault in [(FilePurpose.bodyTransaction, FileStage.afterReplace), (.bodyDiary, .beforeReplace), (.bodyDiary, .afterReplace),
                      (.bodyReceipt, .beforeReplace), (.bodyReceipt, .afterReplace), (.bodyDraft, .beforeRemove), (.bodyTransaction, .beforeRemove)] {
            let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
            let before = Data("# Synthetic\nOriginal\n".utf8); try before.write(to: f.source)
            let edit = BodyEdit(day: f.day, before: before, content: Data("# Synthetic\nEdited\n".utf8))
            f.faults.arm(fault.0, fault.1)
            do { try await store.commitBodyEdit(edit); XCTFail("Injected failure must not report success") } catch {}
            let relaunched = try f.store()
            try await relaunched.recoverBodyEdit(f.day)
            try await relaunched.commitBodyEdit(edit)
            XCTAssertEqual(try Data(contentsOf: f.source), edit.content)
            let revisions = try await relaunched.bodyRevisions(f.day), retained = try await relaunched.retainedBodyEdit(f.day)
            XCTAssertEqual(revisions.map(\.id), [edit.id]); XCTAssertNil(retained)
        }
    }

    func testConflictKeepsBothVersionsAndRechecksReviewedSource() async throws {
        let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
        let base = Data("# Original\n".utf8); try base.write(to: f.source)
        let edit = BodyEdit(day: f.day, before: base, content: Data("# Local\n".utf8))
        let external = Data("# External\n".utf8); try external.write(to: f.source)
        do { try await store.commitBodyEdit(edit); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        let conflict = try await store.bodyConflict(f.day)!
        XCTAssertEqual(conflict.edit, edit); XCTAssertEqual(conflict.source, external)
        let newer = Data("# External newer\n".utf8); try newer.write(to: f.source)
        do { _ = try await store.resolveBodyConflict(conflict, content: edit.content); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        let latest = try await store.bodyConflict(f.day)!
        let merged = try await store.resolveBodyConflict(latest, content: Data("# Explicit merge\n".utf8))
        try await store.commitBodyEdit(merged)
        let versions = try await store.bodyRevisions(f.day)
        XCTAssertTrue(versions.contains { $0.before == external && $0.content == edit.content })
        XCTAssertTrue(versions.contains { $0.before == newer })
        XCTAssertTrue(versions.contains { $0.id == edit.id })
    }

    func testPendingWorkflowAndLegacyOperationBlockBodyWhilePlainDraftSurvives() async throws {
        let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
        let snapshot = try await store.open(f.day)
        let draft = JournalDraft(day: f.day, section: "🧠 Notes", text: "Legacy input", sourceHash: nil, effectiveTemplate: snapshot.markdown)
        try await store.saveDraft(draft)
        let body = BodyEdit(day: f.day, before: nil, content: Data((snapshot.markdown + "Manual text\n").utf8))
        try await store.commitBodyEdit(body)
        let legacy = try await store.open(f.day).draft
        XCTAssertEqual(legacy, draft)
        let nextDraft = JournalDraft(day: f.day, section: "🧠 Notes", text: "Confirmed pending", sourceHash: SourceHash.digest(body.content), effectiveTemplate: snapshot.markdown)
        f.faults.arm(.diary, .beforeReplace)
        do { _ = try await store.commit(nextDraft); XCTFail() } catch {}
        let blocked = BodyEdit(day: f.day, before: body.content, content: Data("New body".utf8))
        do { try await store.commitBodyEdit(blocked); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .pendingOperation) }
    }

    func testMetadataAndProvenanceAreProtectedAndFailedEditIsRetained() async throws {
        let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
        let before = Data("---\nunknown: keep\n---\n# Notes\n<!-- riji-record:ai:synthetic -->\nAI suggestion\n".utf8)
        try before.write(to: f.source)
        let after = before.replacing(Data("unknown: keep".utf8), with: Data("removed: value".utf8))
        let edit = BodyEdit(day: f.day, before: before, content: after)
        do { try await store.commitBodyEdit(edit); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .protectedMetadata) }
        XCTAssertEqual(try Data(contentsOf: f.source), before)
        let retained = try await store.retainedBodyEdit(f.day); XCTAssertEqual(retained, edit)
    }
}

private extension Data {
    func replacing(_ source: Data, with value: Data) -> Data {
        guard let range = range(of: source) else { return self }
        var copy = self; copy.replaceSubrange(range, with: value); return copy
    }
}

extension BodyRevisionStoreTests {
    func testExternalChangeAtReplacementBoundaryIsNeverOverwritten() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("BodyBoundarySynthetic-\(UUID())")
        defer { try? FileManager.default.removeItem(at: root) }
        let day = try JournalDay("2026-09-14")
        let url = root.appendingPathComponent("Journals/2026-09-14.md")
        let external = Data("# External version at replacement\n".utf8)
        let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"), failure: { purpose, stage in
            if purpose == .bodyDiary && stage == .beforeReplace { try external.write(to: url) }
        })
        let before = Data("# Original\n".utf8); try before.write(to: url)
        let edit = BodyEdit(day: day, before: before, content: Data("# Local candidate\n".utf8))
        do { try await store.commitBodyEdit(edit); XCTFail() } catch {}
        XCTAssertEqual(try Data(contentsOf: url), external)
        let retained = try await store.retainedBodyEdit(day); XCTAssertEqual(retained, edit)
        let history = try await store.bodyRevisions(day)
        XCTAssertTrue(history.contains { $0.before == external && $0.content == edit.content })
    }

    func testUnknownDailyWorkflowBlocksBodyAndBodyPendingBlocksOtherWriters() async throws {
        let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
        let blank = try await store.prepareBlankDay(f.day)
        f.faults.arm(.diary, .beforeReplace)
        do { try await store.createBlankDay(blank); XCTFail() } catch {}
        let edit = BodyEdit(day: f.day, before: nil, content: Data(blank.preview.utf8))
        do { try await store.commitBodyEdit(edit); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .pendingOperation) }
        try await store.recoverWorkflows()
        let before = try await store.exportMarkdown(f.day)
        let pending = BodyEdit(day: f.day, before: before, content: before + Data("Pending manual\n".utf8))
        f.faults.arm(.bodyDiary, .beforeReplace)
        do { try await store.commitBodyEdit(pending); XCTFail() } catch {}
        let draft = JournalDraft(day: f.day, section: "🧠 Notes", text: "Append", sourceHash: SourceHash.digest(before), effectiveTemplate: blank.preview)
        do { _ = try await store.commit(draft); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .pendingOperation) }
        let event = MITEvent(day: f.day, action: "Synthetic MIT", criterion: "One result")
        let mit = try await store.prepareMIT(event, section: "🧠 Notes")
        do { try await store.commitMIT(mit); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .pendingOperation) }
    }
}

extension BodyRevisionStoreTests {
    func testCompletedOrphanAppendCleansBeforeBodyMutualExclusion() async throws {
        let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
        let initial = try await store.open(f.day)
        let draft = JournalDraft(day: f.day, section: "🧠 Notes", text: "Confirmed append", sourceHash: nil, effectiveTemplate: initial.markdown)
        f.faults.arm(.transaction, .beforeRemove)
        do { _ = try await store.commit(draft); XCTFail() } catch {}
        let orphaned = try await store.open(f.day)
        XCTAssertNil(orphaned.draft); XCTAssertTrue(orphaned.isPending)
        let before = try await store.exportMarkdown(f.day)
        let edit = BodyEdit(day: f.day, before: before, content: before + Data("Manual change\n".utf8))
        try await store.retainBodyEdit(edit)
        let cold = try f.store()
        _ = try await cold.recover(f.day)
        try await cold.commitBodyEdit(edit)
        XCTAssertEqual(try Data(contentsOf: f.source), edit.content)
        let result = try await cold.open(f.day); XCTAssertFalse(result.isPending)
    }

    func testCompletedBodyReceiptRechecksActualSourceAndRetainsCandidate() async throws {
        let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
        let before = Data("# Original\n".utf8); try before.write(to: f.source)
        let edit = BodyEdit(day: f.day, before: before, content: Data("# Local\n".utf8))
        f.faults.arm(.bodyTransaction, .beforeRemove)
        do { try await store.commitBodyEdit(edit); XCTFail() } catch {}
        let external = Data("# Changed after receipt\n".utf8); try external.write(to: f.source)
        let cold = try f.store()
        do { try await cold.commitBodyEdit(edit); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        let conflict = try await cold.bodyConflict(f.day)
        XCTAssertEqual(conflict?.edit, edit); XCTAssertEqual(conflict?.source, external)
        XCTAssertEqual(try Data(contentsOf: f.source), external)
    }

    func testPendingConflictReplacementSurvivesSupersessionAndCleanupFailures() async throws {
        let faults: [(FilePurpose, FileStage)] = [(.bodyResolution, .afterReplace), (.bodyReceipt, .beforeReplace),
            (.bodyReceipt, .afterReplace), (.bodyTransaction, .beforeRemove), (.bodyTransaction, .afterRemove),
            (.bodyDraft, .beforeReplace), (.bodyDraft, .afterReplace)]
        for fault in faults {
            let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
            let before = Data("# Base\n".utf8); try before.write(to: f.source)
            let old = BodyEdit(day: f.day, before: before, content: Data("# Local old\n".utf8))
            f.faults.arm(.bodyDiary, .beforeReplace)
            do { try await store.commitBodyEdit(old); XCTFail() } catch {}
            let source = Data("# External\n".utf8); try source.write(to: f.source)
            let conflict = try await store.bodyConflict(f.day)!
            let merged = Data("# Explicit merged text\n".utf8)
            try await store.retainBodyMerge(BodyMergeDraft(conflict: conflict, content: merged))
            f.faults.arm(fault.0, fault.1)
            do { _ = try await store.resolveBodyConflict(conflict, content: merged); XCTFail("Expected \(fault)") } catch {}
            let cold = try f.store()
            let recovered = try await cold.recoverBodyEdit(f.day)
            XCTAssertEqual(recovered?.content, merged)
            XCTAssertEqual(try Data(contentsOf: f.source), merged)
            do { try await cold.commitBodyEdit(old); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
            let history = try await cold.bodyRevisions(f.day)
            XCTAssertTrue(history.contains { $0.id == old.id }); XCTAssertTrue(history.contains { $0.before == source && $0.content == merged })
        }
    }

    func testNativeAdmissionAndStoragePreserveFrontmatterAndStandaloneMIT() async throws {
        let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
        let initial = try await store.open(f.day)
        try await store.commitBodyEdit(BodyEdit(day: f.day, before: nil, content: Data(initial.markdown.utf8)))
        let event = MITEvent(day: f.day, action: "Synthetic action", criterion: "Synthetic result")
        let preview = try await store.prepareMIT(event, section: "🧠 Notes")
        try await store.commitMIT(preview)
        let source = try await store.exportMarkdown(f.day), raw = String(decoding: source, as: UTF8.self)
        let prefix = (raw as NSString).range(of: "<!-- riji-record:")
        for edit in [(NSRange(location: 0, length: 0), "prefix"), (NSRange(location: prefix.location, length: 0), "prefix"),
                     (NSRange(location: prefix.location - 1, length: 1), "")] {
            let candidate = (raw as NSString).replacingCharacters(in: edit.0, with: edit.1)
            let allowed = BodyMarkdown.permitsEdit(in: raw, range: edit.0, replacement: edit.1)
            let valid = (try? BodyMarkdown.validatePreservation(before: source, after: Data(candidate.utf8))) != nil
            XCTAssertEqual(allowed, valid)
            if edit.0.location != 0 || raw.hasPrefix("---") { XCTAssertFalse(allowed) }
        }
        let current = try await store.currentMIT(f.day); XCTAssertEqual(current?.id, event.id)
        let frontmatter = "\u{FEFF}---\r\nunknown: keep\r\n---\r\n# Body\r\n"
        XCTAssertFalse(BodyMarkdown.permitsEdit(in: frontmatter, range: NSRange(location: 0, length: 0), replacement: "x"))
        XCTAssertTrue(BodyMarkdown.permitsEdit(in: frontmatter, range: NSRange(location: frontmatter.utf16.count, length: 0), replacement: "正文"))
    }

    func testDenseHeadingByteToUTF16TraversalPreservesEveryRawLine() {
        let raw = (0..<5000).map { "## 标题 \($0) e\u{301} 🧠\r\n正文\r\n" }.joined()
        let ranges = BodyMarkdown.headingRanges(in: raw)
        XCTAssertEqual(ranges.count, 5000)
        for (index, value) in ranges.enumerated() {
            XCTAssertEqual((raw as NSString).substring(with: value.1), "## 标题 \(index) e\u{301} 🧠\r\n")
        }
    }
}

extension BodyRevisionStoreTests {
    func testChangedSourceDuringInterruptedResolutionCanBeExplicitlyMergedAgain() async throws {
        let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
        let base = Data("# Base\n".utf8); try base.write(to: f.source)
        let local = BodyEdit(day: f.day, before: base, content: Data("# Local\n".utf8))
        f.faults.arm(.bodyDiary, .beforeReplace)
        do { try await store.commitBodyEdit(local); XCTFail() } catch {}
        let firstSource = Data("# First external\n".utf8); try firstSource.write(to: f.source)
        let first = try await store.bodyConflict(f.day)!
        f.faults.arm(.bodyResolution, .afterReplace)
        do { _ = try await store.resolveBodyConflict(first, content: Data("# First merge\n".utf8)); XCTFail() } catch {}
        let secondSource = Data("# Second external\n".utf8); try secondSource.write(to: f.source)
        let cold = try f.store()
        do { _ = try await cold.recoverBodyEdit(f.day); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        let second = try await cold.bodyConflict(f.day)!
        let merged = Data("# Revised explicit merge\n".utf8)
        try await cold.retainBodyMerge(BodyMergeDraft(conflict: second, content: merged))
        f.faults.arm(.bodyReceipt, .afterReplace)
        do { _ = try await cold.resolveBodyConflict(second, content: merged); XCTFail() } catch {}
        let final = try f.store()
        let recovered = try await final.recoverBodyEdit(f.day)
        XCTAssertEqual(recovered?.content, merged); XCTAssertEqual(try Data(contentsOf: f.source), merged)
        let histories = try FileManager.default.contentsOfDirectory(at: f.root.appendingPathComponent("State/body-resolution-history"), includingPropertiesForKeys: nil)
        XCTAssertEqual(histories.count, 1)
        let revisions = try await final.bodyRevisions(f.day)
        let oldMerge = try XCTUnwrap(revisions.first { $0.content == Data("# First merge\n".utf8) })
        XCTAssertEqual(oldMerge.before, firstSource)
        do { try await final.commitBodyEdit(oldMerge); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
        XCTAssertTrue(revisions.contains { $0.before == secondSource && $0.content == merged })
    }
}

extension BodyRevisionStoreTests {
    func testConfirmedMergeFinalCleanupRetriesOriginalIdentity() async throws {
        for fault in [(FilePurpose.bodyMerge, FileStage.beforeRemove), (.bodyResolution, .beforeRemove), (.bodyTransaction, .beforeRemove)] {
            let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
            let before = Data("# Before\n".utf8); try before.write(to: f.source)
            let edit = BodyEdit(day: f.day, before: before, content: Data("# Local\n".utf8))
            let external = Data("# External\n".utf8); try external.write(to: f.source)
            do { try await store.commitBodyEdit(edit); XCTFail() } catch {}
            let conflict = try await store.bodyConflict(f.day)!
            let content = Data("# Confirmed merge\n".utf8)
            try await store.retainBodyMerge(BodyMergeDraft(conflict: conflict, content: content))
            let next = try await store.resolveBodyConflict(conflict, content: content)
            f.faults.arm(fault.0, fault.1)
            do { try await store.commitBodyEdit(next); XCTFail() } catch {}
            let cold = try f.store()
            let recovered = try await cold.recoverBodyEdit(f.day)
            XCTAssertEqual(recovered?.id, next.id)
            let merge = try await cold.retainedBodyMerge(f.day); XCTAssertNil(merge)
            let history = try await cold.bodyRevisions(f.day)
            XCTAssertEqual(history.filter { $0.id == next.id }.count, 1)
            XCTAssertEqual(try Data(contentsOf: f.source), content)
        }
    }
}

extension BodyRevisionStoreTests {
    func testOriginalPreTransactionConflictIdentityIsPermanentlyRetired() async throws {
        let faults: [(FilePurpose, FileStage)?] = [nil, (.bodyResolution, .afterReplace), (.bodyReceipt, .beforeReplace), (.bodyReceipt, .afterReplace)]
        for fault in faults {
            let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
            let base = Data("# Original base\n".utf8), external = Data("# Other writer\n".utf8)
            try external.write(to: f.source)
            let original = BodyEdit(day: f.day, before: base, content: Data("# Local conflict\n".utf8))
            do { try await store.commitBodyEdit(original); XCTFail() } catch {}
            let conflict = try await store.bodyConflict(f.day)!
            let merged = Data("# Explicit merged\n".utf8)
            if let fault { f.faults.arm(fault.0, fault.1) }
            do {
                let next = try await store.resolveBodyConflict(conflict, content: merged)
                try await store.commitBodyEdit(next)
            } catch { XCTAssertNotNil(fault) }
            // Even before supersession cleanup completes, a durable resolution retires the old ID.
            do { try await store.commitBodyEdit(original); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
            let cold = try f.store(); _ = try await cold.recoverBodyEdit(f.day)
            try base.write(to: f.source)
            do { try await cold.commitBodyEdit(original); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
            XCTAssertEqual(try Data(contentsOf: f.source), base)
            let history = try await cold.bodyRevisions(f.day)
            XCTAssertTrue(history.contains { $0.id == original.id })
        }
    }

    func testResolutionRecoveryNeverReplacesAnIndependentLaterBuffer() async throws {
        for pending in [false, true] {
            for fault in [(FilePurpose.bodyResolution, FileStage.afterReplace), (.bodyReceipt, .beforeReplace), (.bodyReceipt, .afterReplace), (.bodyDraft, .beforeReplace)] {
                let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
                let base = Data("# Base\n".utf8), external = Data("# External\n".utf8)
                try base.write(to: f.source)
                let old = BodyEdit(day: f.day, before: base, content: Data("# Old candidate\n".utf8))
                if pending { f.faults.arm(.bodyDiary, .beforeReplace); do { try await store.commitBodyEdit(old); XCTFail() } catch {} }
                try external.write(to: f.source); try await store.retainBodyEdit(old)
                let conflict = try await store.bodyConflict(f.day)!
                f.faults.arm(fault.0, fault.1)
                do { _ = try await store.resolveBodyConflict(conflict, content: Data("# Reviewed merge\n".utf8)); XCTFail() } catch {}
                let later = BodyEdit(day: f.day, before: base, content: Data("# Later independent input 中 e\u{301}\n".utf8))
                try await store.retainBodyEdit(later)
                let cold = try f.store(); _ = try await cold.recoverBodyEdit(f.day)
                let retained = try await cold.retainedBodyEdit(f.day)
                XCTAssertEqual(retained, later, "\(pending) \(fault)")
                XCTAssertEqual(try Data(contentsOf: f.source), Data("# Reviewed merge\n".utf8))
                do { try await cold.commitBodyEdit(later); XCTFail() } catch { XCTAssertEqual(error as? JournalError, .changedSource) }
                let review = try await cold.bodyConflict(f.day)
                XCTAssertEqual(review?.edit, later); XCTAssertEqual(review?.source, Data("# Reviewed merge\n".utf8))
            }
        }
    }

    func testEmptyFrontmatterLFAndCRLFUseSameNativeAndStoreAdmission() async throws {
        for newline in ["\n", "\r\n"] {
            for closer in ["---", "..."] {
                let before = "---\(newline)\(closer)\(newline)# Body\(newline)Original\(newline)"
                let range = NSRange(location: 0, length: 3)
                XCTAssertFalse(BodyMarkdown.permitsEdit(in: before, range: range, replacement: ""))
                XCTAssertEqual(BodyMarkdown.protectedRanges(in: before).count, 1)
                let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
                try Data(before.utf8).write(to: f.source)
                let content = Data(before.replacingOccurrences(of: "Original", with: "Manual body").utf8)
                try await store.commitBodyEdit(BodyEdit(day: f.day, before: Data(before.utf8), content: content))
                XCTAssertEqual(try Data(contentsOf: f.source), content)
            }
        }
    }
}

extension BodyRevisionStoreTests {
    func testRetainedMergeAloneRestoresItsDayWithoutAnyAppendOrBodyDraft() async throws {
        let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
        let before = Data("# Retained source\n".utf8), local = Data("# Retained candidate\n".utf8)
        let edit = BodyEdit(day: f.day, before: before, content: local)
        let merge = BodyMergeDraft(conflict: BodyConflict(edit: edit, source: before), content: Data("# Retained edited merge\n".utf8))
        try await store.retainBodyMerge(merge)
        let nextDay = try JournalDay("2026-09-15")
        try await store.select(nextDay)
        let cold = try f.store(), selected = try await cold.initialDay(now: nextDay.date(in: store.timeZone))
        XCTAssertEqual(selected, f.day)
        let buffer = try await cold.retainedBodyEdit(f.day), append = try await cold.open(f.day).draft
        XCTAssertNil(buffer); XCTAssertNil(append)
        let recovered = try await cold.retainedBodyMerge(f.day); XCTAssertEqual(recovered, merge)
    }
}

extension BodyRevisionStoreTests {
    func testReferencedReceiptRequiresEveryPendingEditFieldAndPreservesMismatchedEvidence() async throws {
        for folder in ["transactions", "resolutions"] {
            for field in ["id", "day", "before", "content", "createdAt"] {
                let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
                let edit = BodyEdit(day: f.day, before: nil, content: Data("# Saved exact source\n".utf8))
                try await store.commitBodyEdit(edit)
                let fields = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(edit)) as? [String: Any])
                let record: Any = folder == "transactions" ? fields : ["conflict": ["edit": fields, "source": NSNull()], "next": fields]
                let operation = try JSONSerialization.data(withJSONObject: record, options: .sortedKeys)
                let operationURL = f.root.appendingPathComponent("State/body-\(folder)/\(f.day.value).json")
                try operation.write(to: operationURL)
                var mismatched = fields
                switch field {
                case "id": mismatched[field] = UUID().uuidString
                case "day": mismatched[field] = try JSONSerialization.jsonObject(with: JSONEncoder().encode(JournalDay("2026-09-15")), options: .fragmentsAllowed)
                case "before": mismatched[field] = Data("Different base\n".utf8).base64EncodedString()
                case "content": mismatched[field] = Data("Different content\n".utf8).base64EncodedString()
                default: mismatched[field] = (try XCTUnwrap(fields[field] as? Double)) + 1
                }
                let receipt = try JSONSerialization.data(withJSONObject: ["edit": mismatched], options: .sortedKeys)
                let receiptURL = f.root.appendingPathComponent("State/body-receipts/\(edit.id.uuidString).json")
                try receipt.write(to: receiptURL)
                let recovery = await store.bodyRecoveryState(f.day)
                XCTAssertFalse(recovery.isReadable, "\(folder): \(field)"); XCTAssertTrue(recovery.hasOperation)
                do { _ = try await store.recoverBodyEdit(f.day); XCTFail("Mismatched recovery must stop before cleanup") } catch {}
                do { try await store.commitBodyEdit(edit); XCTFail("Mismatched receipt must block commit") } catch {}
                do { try await store.retainBodyEdit(edit); XCTFail("Mismatched evidence must block replacement") } catch {}
                XCTAssertEqual(try Data(contentsOf: receiptURL), receipt)
                XCTAssertEqual(try Data(contentsOf: operationURL), operation)
                XCTAssertEqual(try Data(contentsOf: f.source), edit.content)
            }
        }
    }
}

extension BodyRevisionStoreTests {
    func testMergeRetirementFailureKeepsExactHandwritingRecoverableAndRetryable() async throws {
        for (purpose, stage) in [(FilePurpose.bodyRevision, FileStage.beforeWrite), (.bodyMerge, .beforeRemove), (.bodyMerge, .afterRemove)] {
            let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
            let base = Data("Original\r\n".utf8); try base.write(to: f.source)
            let edit = BodyEdit(day: f.day, before: base, content: Data("Local\r\n".utf8))
            let merge = BodyMergeDraft(conflict: BodyConflict(edit: edit, source: base), content: Data("Handwritten e\u{301}\r\n".utf8))
            try await store.retainBodyMerge(merge)
            f.faults.arm(purpose, stage)
            do { try await store.retireBodyMerge(merge); XCTFail("Injected retirement failure") } catch {}
            let cold = try f.store(), retained = try await cold.retainedBodyMerge(f.day), history = try await cold.bodyRevisions(f.day)
            if purpose == .bodyRevision { XCTAssertEqual(retained, merge); XCTAssertTrue(history.isEmpty) }
            else { XCTAssertTrue(history.contains { $0.content == merge.content && $0.before == base }) }
            XCTAssertTrue(retained == merge || history.contains { $0.content == merge.content })
            try await cold.retireBodyMerge(merge)
            let final = try await cold.retainedBodyMerge(f.day), archived = try await cold.bodyRevisions(f.day)
            XCTAssertNil(final); XCTAssertTrue(archived.contains { $0.content == merge.content })
            XCTAssertEqual(try Data(contentsOf: f.source), base)
        }
    }

    func testMergeRetirementRefusesDifferentOwnerAndUncertainOperation() async throws {
        for uncertain in [false, true] {
            let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
            let base = Data("Original\r\n".utf8); try base.write(to: f.source)
            let edit = BodyEdit(day: f.day, before: base, content: Data("Local\r\n".utf8))
            let conflict = BodyConflict(edit: edit, source: base)
            let current = BodyMergeDraft(conflict: conflict, content: Data("Current handwriting\r\n".utf8))
            try await store.retainBodyMerge(current)
            let requested: BodyMergeDraft
            if uncertain {
                f.faults.arm(.bodyDiary, .beforeReplace)
                do { try await store.commitBodyEdit(edit); XCTFail("Retain the uncertain transaction") } catch {}
                requested = current
            } else {
                requested = BodyMergeDraft(conflict: conflict, content: Data("Older handwriting\r\n".utf8))
            }
            do { try await store.retireBodyMerge(requested); XCTFail("Do not retire a different or uncertain owner") }
            catch { XCTAssertEqual(error as? JournalError, uncertain ? .pendingOperation : .operationMismatch) }
            let retained = try await store.retainedBodyMerge(f.day)
            XCTAssertEqual(retained, current); XCTAssertEqual(try Data(contentsOf: f.source), base)
        }
    }

    func testBufferRetirementArchivesExactOwnerWithoutWritingChangedSource() async throws {
        let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
        let base = Data("Original e\u{301}\r\n".utf8), external = Data("Changed externally\r\n".utf8)
        try base.write(to: f.source)
        let edit = BodyEdit(day: f.day, before: base, content: base)
        try await store.retainBodyEdit(edit)
        try external.write(to: f.source)
        try await store.retireBodyBuffer(edit)
        let retained = try await store.retainedBodyEdit(f.day), history = try await store.bodyRevisions(f.day)
        XCTAssertNil(retained); XCTAssertEqual(history, [edit]); XCTAssertEqual(try Data(contentsOf: f.source), external)
        let newer = BodyEdit(day: f.day, before: external, content: external + Data("New input".utf8))
        try await store.retainBodyEdit(newer)
        do { try await store.retireBodyBuffer(edit); XCTFail("A stale owner must not retire new input") }
        catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
        let current = try await store.retainedBodyEdit(f.day); XCTAssertEqual(current, newer)
    }

    func testBufferRetirementFailurePreservesDraftOrArchivedRecoveryAndCanRetry() async throws {
        for (purpose, stage) in [(FilePurpose.bodyRevision, FileStage.beforeWrite), (.bodyDraft, .beforeRemove), (.bodyDraft, .afterRemove)] {
            let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
            let base = Data("Original\r\n".utf8); try base.write(to: f.source)
            let edit = BodyEdit(day: f.day, before: base, content: base)
            try await store.retainBodyEdit(edit); f.faults.arm(purpose, stage)
            do { try await store.retireBodyBuffer(edit); XCTFail("Injected retirement failure") } catch {}
            let cold = try f.store(), retained = try await cold.retainedBodyEdit(f.day), history = try await cold.bodyRevisions(f.day)
            XCTAssertTrue(retained == edit || history.contains(edit))
            XCTAssertEqual(try Data(contentsOf: f.source), base)
            if retained == nil { try await cold.retainBodyEdit(edit) }
            try await cold.retireBodyBuffer(edit)
            let final = try await cold.retainedBodyEdit(f.day); XCTAssertNil(final)
        }
    }

    func testBufferRetirementRefusesMergeAndUncertainTransactions() async throws {
        for operation in ["merge", "transaction"] {
            let f = BodyFixture(); defer { f.clean() }; let store = try f.store()
            let base = Data("Base\r\n".utf8); try base.write(to: f.source)
            let pending = BodyEdit(day: f.day, before: base, content: base + Data("Pending\r\n".utf8))
            try await store.retainBodyEdit(pending)
            if operation == "transaction" {
                f.faults.arm(.bodyDiary, .beforeReplace)
                do { try await store.commitBodyEdit(pending); XCTFail() } catch {}
            } else {
                try await store.retainBodyMerge(BodyMergeDraft(conflict: BodyConflict(edit: pending, source: base), content: pending.content))
            }
            let undo = BodyEdit(day: f.day, before: base, content: base)
            try await store.retainBodyEdit(undo)
            do { try await store.retireBodyBuffer(undo); XCTFail("Uncertain operation must keep its owner") }
            catch { XCTAssertEqual(error as? JournalError, .pendingOperation) }
            let retained = try await store.retainedBodyEdit(f.day); XCTAssertEqual(retained, undo)
            XCTAssertEqual(try Data(contentsOf: f.source), base)
        }
    }
}
