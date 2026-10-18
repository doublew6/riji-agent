import XCTest
import Darwin
@testable import RijiCore

private final class VoiceFaults: @unchecked Sendable {
    private let lock = NSLock()
    private var fault: (FilePurpose, FileStage)?
    func arm(_ purpose: FilePurpose, _ stage: FileStage) { lock.lock(); fault = (purpose, stage); lock.unlock() }
    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        lock.lock(); defer { lock.unlock() }
        if let value = fault, value.0 == purpose, value.1 == stage { fault = nil; throw JournalError.storage }
    }
}

private struct VoiceFixture {
    let root: URL
    let store: LocalJournalStore
    let faults: VoiceFaults
    let day = try! JournalDay("2026-09-12")
    init() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("VoiceTests-\(UUID())")
        let faults = VoiceFaults(); self.faults = faults
        store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"),
                                      stateDirectory: root.appendingPathComponent("State"),
                                      failure: { try faults.check($0, $1) })
    }
    func cleanup() { try? FileManager.default.removeItem(at: root) }
    func seed(text: String = "Existing typed text") async throws -> (JournalDraft, VoiceRecording) {
        let snapshot = try await store.open(day)
        var draft = try await store.makeDraft(for: snapshot, section: "🧠 Notes")
        draft = draft.updating(text: text, section: draft.section)
        try await store.saveDraft(draft)
        let target = try await store.voiceTarget(draft)
        let recording = try await store.createRecording(id: UUID(), target: target)
        let segment = UUID()
        let url = try await store.reserveVoiceSegment(recording.id, id: segment)
        try Data("synthetic controlled media".utf8).write(to: url)
        try await store.finalizeVoiceSegment(recording.id, id: segment, duration: 2.5)
        try await store.retainVoiceCorrection(recording.id, correction: VoiceCorrection(text: "Corrected exact text\n第二行"))
        return (draft, try await store.recording(recording.id))
    }
}

final class VoiceStoreTests: XCTestCase {
    func testExactInsertionReceiptCleanupRetryAndLegacyNilEncoding() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, recording) = try await f.seed()
        let originalEncoding = try JSONEncoder().encode(base)
        XCTAssertFalse(String(decoding: originalEncoding, as: UTF8.self).contains("voiceInsertions"))
        let candidate = try await f.store.prepareVoiceInsertion(recording.id, draft: base)
        let first = try await f.store.insertVoice(candidate)
        XCTAssertEqual(first, .inserted)
        let inserted = try await f.store.open(f.day).draft!
        XCTAssertEqual(inserted.text, base.text + "\n\n" + candidate.preview)
        XCTAssertEqual(inserted.voiceInsertions?.count, 1)
        let updated = inserted.updating(text: inserted.text + "\nLater typing", section: inserted.section)
        XCTAssertEqual(updated.voiceInsertions, inserted.voiceInsertions)
        try await f.store.saveDraft(updated)
        let receipt = try await f.store.commit(updated)
        XCTAssertEqual(receipt.voiceInsertions, inserted.voiceInsertions)
        let bytes = try await f.store.exportMarkdown(f.day)
        let repeated = try await f.store.insertVoice(candidate)
        XCTAssertEqual(repeated, .alreadyInserted)
        let after = try await f.store.exportMarkdown(f.day)
        XCTAssertEqual(after, bytes)
        XCTAssertTrue(String(decoding: after, as: UTF8.self).contains(updated.text))
    }

    func testInsertionFailureBoundaryMatrixRetainsSameIntentAndExactlyOnceText() async throws {
        for purpose in [FilePurpose.voiceIntent, .draft] {
            for stage in [FileStage.beforeWrite, .beforeReplace, .afterReplace, .beforeRemove, .afterRemove] {
                if purpose == .draft && [FileStage.beforeRemove, .afterRemove].contains(stage) { continue }
                let f = try VoiceFixture(); defer { f.cleanup() }
                let (base, recording) = try await f.seed()
                let candidate = try await f.store.prepareVoiceInsertion(recording.id, draft: base)
                f.faults.arm(purpose, stage)
                do { _ = try await f.store.insertVoice(candidate) } catch {}
                if try await f.store.pendingVoiceInsertion(f.day) != nil {
                    await expectFailure { try await f.store.saveDraft(base) }
                }
                _ = try await f.store.insertVoice(candidate)
                let actual = try await f.store.open(f.day).draft!
                XCTAssertEqual(actual.text, base.text + "\n\n" + candidate.preview, "\(purpose) \(stage)")
                XCTAssertEqual(actual.voiceInsertions?.count, 1)
                let pending = try await f.store.pendingVoiceInsertion(f.day)
                XCTAssertNil(pending)
            }
        }
    }

    func testChangedCorrectionSourceMediaAndPayloadFailBeforeInsertion() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, recording) = try await f.seed()
        let candidate = try await f.store.prepareVoiceInsertion(recording.id, draft: base)
        try await f.store.retainVoiceCorrection(recording.id, correction: VoiceCorrection(text: "Later correction"))
        await expectFailure { _ = try await f.store.insertVoice(candidate) }
        let current = try await f.store.prepareVoiceInsertion(recording.id, draft: base)
        let segment = recording.segments[0]
        let url = try await f.store.verifiedVoiceURL(recording.id, segmentID: segment.id)
        try Data("changed media".utf8).write(to: url)
        await expectFailure { _ = try await f.store.insertVoice(current) }
        let actual = try await f.store.open(f.day).draft
        XCTAssertEqual(actual, base)
    }

    func testOrphanOrdinaryAndWorkflowTransactionsBlockInsertion() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, recording) = try await f.seed()
        let candidate = try await f.store.prepareVoiceInsertion(recording.id, draft: base)
        f.faults.arm(.diary, .beforeWrite)
        do { _ = try await f.store.commit(base) } catch {}
        try FileManager.default.removeItem(at: f.root.appendingPathComponent("State/drafts/\(f.day.value).json"))
        await expectFailure { _ = try await f.store.voiceTarget(base) }
        await expectFailure { _ = try await f.store.insertVoice(candidate) }
        _ = try await f.store.recover(f.day)
        let next = try await f.store.open(f.day)
        let draft = try await f.store.makeDraft(for: next, section: "🧠 Notes")
        try await f.store.saveDraft(draft)
        let transaction = WorkflowTransaction(id: UUID(), payloadHash: "synthetic", target: .daily(f.day),
                                              beforeHash: next.sourceHash, afterHash: "synthetic", after: Data())
        try JSONEncoder().encode(transaction).write(to: f.root.appendingPathComponent("State/workflow-transactions/\(transaction.id).json"))
        await expectFailure { _ = try await f.store.voiceTarget(draft) }
    }

    func testBadIncompletePieceDoesNotBlockOrderedPlayableReferences() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, recording) = try await f.seed()
        let bad = UUID()
        let url = try await f.store.reserveVoiceSegment(recording.id, id: bad)
        try Data().write(to: url)
        let references = try await f.store.voiceReferences(recording.id)
        XCTAssertEqual(references.map(\.segmentID), recording.segments.map(\.id))
        let actual = try await f.store.recording(recording.id)
        XCTAssertEqual(actual.segments.count, 2)
        XCTAssertEqual(actual.lastPlayable?.id, recording.segments[0].id)
        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
        await expectFailure { try await f.store.finalizeVoiceSegment(recording.id, id: bad, duration: 0) }
    }

    func testFinalizeFailureRetriesAndPathsNeverOverwriteOriginals() async throws {
        for stage in [FileStage.beforeWrite, .beforeReplace, .afterReplace] {
            let f = try VoiceFixture(); defer { f.cleanup() }
            let (_, recording) = try await f.seed()
            let id = UUID(); let url = try await f.store.reserveVoiceSegment(recording.id, id: id)
            let bytes = Data("second original".utf8); try bytes.write(to: url)
            await expectFailure { _ = try await f.store.reserveVoiceSegment(recording.id, id: id) }
            f.faults.arm(.voice, stage)
            do { try await f.store.finalizeVoiceSegment(recording.id, id: id, duration: 3) } catch {}
            try await f.store.finalizeVoiceSegment(recording.id, id: id, duration: 3)
            let exported = try await f.store.exportVoiceAudio(recording.id, segmentID: id)
            XCTAssertEqual(exported, bytes)
        }
    }

    func testTombstoneRemovalRetryPreservesCorrectionsAndRejectsLateCallbacks() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, recording) = try await f.seed()
        let candidate = try await f.store.prepareVoiceInsertion(recording.id, draft: base)
        let references = try await f.store.voiceReferences(recording.id)
        f.faults.arm(.audio, .beforeRemove)
        await expectFailure { try await f.store.deleteVoiceAudio(recording.id) }
        let tombstone = try await f.store.recording(recording.id)
        XCTAssertTrue(tombstone.deletionRequested)
        XCTAssertEqual(tombstone.correctedText, recording.correctedText)
        await expectFailure { _ = try await f.store.insertVoice(candidate) }
        await expectFailure { try await f.store.retainVoiceTranscript(recording.id, proposal: VoiceTranscript(id: UUID(), media: references, text: "late")) }
        await expectFailure { _ = try await f.store.verifiedVoiceURL(recording.id, segmentID: references[0].segmentID) }
        try await f.store.deleteVoiceAudio(recording.id)
        let after = try await f.store.open(f.day).draft
        XCTAssertEqual(after, base)
    }

    func testDeleteAfterInsertionKeepsSavedTextAndDedupEvidence() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, recording) = try await f.seed()
        let candidate = try await f.store.prepareVoiceInsertion(recording.id, draft: base)
        _ = try await f.store.insertVoice(candidate)
        let draft = try await f.store.open(f.day).draft!
        _ = try await f.store.commit(draft)
        let before = try await f.store.exportMarkdown(f.day)
        try await f.store.deleteVoiceAudio(recording.id)
        let result = try await f.store.insertVoice(candidate)
        XCTAssertEqual(result, .alreadyInserted)
        let after = try await f.store.exportMarkdown(f.day)
        XCTAssertEqual(after, before)
    }

    func testCorruptManifestLeavesOtherRecordsAccessibleAndCorruptReceiptFailsClosed() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, recording) = try await f.seed()
        try Data("broken".utf8).write(to: f.root.appendingPathComponent("State/voice/\(UUID()).json"))
        let library = try await f.store.voiceLibrary()
        XCTAssertEqual(library.recordings.map(\.id), [recording.id]); XCTAssertEqual(library.unreadableCount, 1)
        let candidate = try await f.store.prepareVoiceInsertion(recording.id, draft: base)
        try Data("broken".utf8).write(to: f.root.appendingPathComponent("State/receipts/\(UUID()).json"))
        await expectFailure { _ = try await f.store.insertVoice(candidate) }
    }

    func testTranscriptProposalsAndCorrectionCheckpointsNeverReplaceLaterEditing() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, recording) = try await f.seed()
        try await f.store.checkpointVoiceCorrection(recording.id)
        let references = try await f.store.voiceReferences(recording.id)
        let proposal = VoiceTranscript(id: UUID(), media: references, text: "original recognized text")
        for index in 0..<100 {
            try await f.store.retainVoiceCorrection(recording.id, correction: VoiceCorrection(text: "Correction \(index)"))
        }
        try await f.store.retainVoiceTranscript(recording.id, proposal: proposal)
        try await f.store.retainVoiceTranscript(recording.id, proposal: proposal)
        let actual = try await f.store.recording(recording.id)
        XCTAssertEqual(actual.transcripts.count, 1); XCTAssertEqual(actual.correctedText, "Correction 99")
        XCTAssertEqual(actual.corrections.count, 1)
        try await f.store.checkpointVoiceCorrection(recording.id)
        let checkpoint = try await f.store.recording(recording.id)
        XCTAssertEqual(checkpoint.corrections.count, 2)
    }

    func testExplicitSourceRefreshPreservesCapturedDayHeadingAndNamespace() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, recording) = try await f.seed()
        _ = try await f.store.commit(base)
        let current = try await f.store.open(f.day)
        let draft = try await f.store.makeDraft(for: current, section: base.section)
        try await f.store.saveDraft(draft)
        await expectFailure { _ = try await f.store.prepareVoiceInsertion(recording.id, draft: draft) }
        try await f.store.refreshVoiceSource(recording.id, draft: draft)
        let candidate = try await f.store.prepareVoiceInsertion(recording.id, draft: draft)
        XCTAssertEqual(candidate.target.day, recording.target.day)
        XCTAssertEqual(candidate.target.section, recording.target.section)
        XCTAssertEqual(candidate.target.sourceHash, current.sourceHash)
        let other = try VoiceFixture(); defer { other.cleanup() }
        await expectFailure { _ = try await other.store.insertVoice(candidate) }
        let original = try await f.store.recording(recording.id)
        XCTAssertNil(original.target.sourceHash)
    }

    func testSymlinkAndOversizeMediaAreRejectedWithoutReadingTarget() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, recording) = try await f.seed()
        let segment = UUID(); let url = try await f.store.reserveVoiceSegment(recording.id, id: segment)
        let target = f.root.appendingPathComponent("synthetic-target")
        try Data("not audio".utf8).write(to: target)
        try FileManager.default.createSymbolicLink(at: url, withDestinationURL: target)
        await expectFailure { try await f.store.finalizeVoiceSegment(recording.id, id: segment, duration: 1) }
        XCTAssertEqual(try Data(contentsOf: target), Data("not audio".utf8))
    }

    func testLegacyDraftAndReceiptAbsentVoiceFieldsKeepExactSortedBytesAndHashes() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (draft, _) = try await f.seed()
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let oldDraftBytes = try encoder.encode(draft)
        let decoded = try JSONDecoder().decode(JournalDraft.self, from: oldDraftBytes)
        XCTAssertNil(decoded.voiceInsertions)
        XCTAssertEqual(try encoder.encode(decoded), oldDraftBytes)
        let receipt = try await f.store.commit(decoded)
        let bytes = try encoder.encode(receipt)
        XCTAssertFalse(String(decoding: bytes, as: UTF8.self).contains("voiceInsertions"))
        XCTAssertEqual(receipt.payloadHash, SourceHash.digest(oldDraftBytes))
        let oldReceipt = try JSONDecoder().decode(JournalReceipt.self, from: bytes)
        XCTAssertEqual(try encoder.encode(oldReceipt), bytes)
    }

    func testOversizeSparsePieceAndChangedInsertionIDAreRejected() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, recording) = try await f.seed()
        let candidate = try await f.store.prepareVoiceInsertion(recording.id, draft: base)
        let another = try await f.store.prepareVoiceInsertion(recording.id, draft: base)
        _ = try await f.store.insertVoice(candidate)
        await expectFailure { _ = try await f.store.insertVoice(another) }
        let anotherRecording = try await f.store.createRecording(id: UUID(), target: recording.target)
        let id = UUID(); let url = try await f.store.reserveVoiceSegment(anotherRecording.id, id: id)
        XCTAssertTrue(FileManager.default.createFile(atPath: url.path, contents: nil))
        let file = try FileHandle(forWritingTo: url)
        try file.truncate(atOffset: 33 * 1024 * 1024); try file.close()
        await expectFailure { try await f.store.finalizeVoiceSegment(anotherRecording.id, id: id, duration: 1) }
        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
    }

    func testDeletionFailureBoundaryMatrixRetainsTextAndExactRemovalRetry() async throws {
        let boundaries: [(FilePurpose, FileStage)] = [(.voice, .beforeWrite), (.voice, .beforeReplace),
            (.voice, .afterReplace), (.audio, .beforeRemove), (.audio, .afterRemove)]
        for (purpose, stage) in boundaries {
            let f = try VoiceFixture(); defer { f.cleanup() }
            let (_, recording) = try await f.seed()
            let segment = recording.segments[0]
            let original = try await f.store.verifiedVoiceURL(recording.id, segmentID: segment.id)
            f.faults.arm(purpose, stage)
            do { try await f.store.deleteVoiceAudio(recording.id) } catch {}
            try await f.store.deleteVoiceAudio(recording.id)
            let value = try await f.store.recording(recording.id)
            XCTAssertTrue(value.deletionRequested)
            XCTAssertEqual(value.correctedText, recording.correctedText)
            XCTAssertFalse(FileManager.default.fileExists(atPath: original.path))
        }
    }


    func testPreflightRejectsReservedMarkerAndEncodedOversizeWithoutIntentOrDraftMutation() async throws {
        for oversize in [false, true] {
            let f = try VoiceFixture(); defer { f.cleanup() }
            let (base, recording) = try await f.seed(text: oversize ? String(repeating: "b", count: 3_000_000) : "Visible original")
            let correction = oversize ? String(repeating: "c", count: 499_000) : "<!-- riji-record:reserved -->"
            try await f.store.retainVoiceCorrection(recording.id, correction: VoiceCorrection(text: correction))
            let url = f.root.appendingPathComponent("State/drafts/\(f.day.value).json")
            let before = try Data(contentsOf: url)
            let candidate = try await f.store.prepareVoiceInsertion(recording.id, draft: base)
            do { _ = try await f.store.insertVoice(candidate); XCTFail("Expected preflight rejection") }
            catch { if oversize { XCTAssertEqual(error as? VoiceError, .limit) } else { XCTAssertEqual(error as? JournalError, .invalidMarkdown) } }
            let pending = try await f.store.pendingVoiceInsertion(f.day)
            XCTAssertNil(pending)
            XCTAssertEqual(try Data(contentsOf: url), before)
            let original = try await f.store.open(f.day).draft
            XCTAssertEqual(original, base)
        }
    }

    func testPendingVoiceIntentExcludesMITAndRecoversOriginalInsertion() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let event = MITEvent(day: f.day, action: "Original MIT", criterion: "Synthetic criterion")
        let initial = try await f.store.prepareMIT(event, section: "🧠 Notes")
        try await f.store.commitMIT(initial)
        let (base, recording) = try await f.seed()
        let replacement = MITEvent(day: f.day, replaces: event.id, action: "Replacement", criterion: "Retained criterion")
        let mit = try await f.store.prepareMIT(replacement, section: "🧠 Notes")
        let before = try await f.store.exportMarkdown(f.day)
        let history = try await f.store.mitHistory(f.day)
        let candidate = try await f.store.prepareVoiceInsertion(recording.id, draft: base)
        f.faults.arm(.draft, .beforeWrite)
        await expectFailure { _ = try await f.store.insertVoice(candidate) }
        do { try await f.store.commitMIT(mit); XCTFail("Expected pending insertion exclusion") }
        catch { XCTAssertEqual(error as? JournalError, .pendingOperation) }
        let after = try await f.store.exportMarkdown(f.day)
        let afterHistory = try await f.store.mitHistory(f.day)
        XCTAssertEqual(after, before); XCTAssertEqual(afterHistory, history)
        try await f.store.recoverVoiceInsertion(f.day)
        let merged = try await f.store.open(f.day).draft!
        XCTAssertEqual(merged.text, base.text + "\n\n" + candidate.preview)
        let recorded = try await f.store.voiceInsertionRecorded(candidate)
        XCTAssertTrue(recorded)
    }

    func testReadOnlyInsertionQuerySurvivesReceiptCleanupAndDeletionAndRejectsInconsistentEvidence() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, recording) = try await f.seed()
        let absent = try await f.store.voiceInsertion(for: recording.id)
        XCTAssertNil(absent)
        let candidate = try await f.store.prepareVoiceInsertion(recording.id, draft: base)
        _ = try await f.store.insertVoice(candidate)
        let inDraft = try await f.store.voiceInsertion(for: recording.id)
        XCTAssertEqual(inDraft?.insertionID, candidate.id)
        let merged = try await f.store.open(f.day).draft!
        var receipt = try await f.store.commit(merged)
        let inReceipt = try await f.store.voiceInsertion(for: recording.id)
        XCTAssertEqual(inReceipt, inDraft)
        try await f.store.deleteVoiceAudio(recording.id)
        let deleted = try await f.store.voiceInsertion(for: recording.id)
        XCTAssertEqual(deleted, inDraft)
        receipt.voiceInsertions = [VoiceInsertionStamp(recordingID: recording.id, insertionID: UUID(), payloadHash: String(repeating: "0", count: 64))]
        var conflicting = JournalDraft(day: f.day, section: base.section, sourceHash: receipt.afterHash, effectiveTemplate: base.effectiveTemplate)
        conflicting.voiceInsertions = [inDraft!]
        try JSONEncoder().encode(conflicting).write(to: f.root.appendingPathComponent("State/drafts/\(f.day.value).json"))
        try JSONEncoder().encode(receipt).write(to: f.root.appendingPathComponent("State/receipts/\(receipt.operationID).json"))
        do { _ = try await f.store.voiceInsertion(for: recording.id); XCTFail("Expected conflict") }
        catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
    }

    func testUnfinishedOriginalExportAndDeletionStatusPreserveExactBytes() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, recording) = try await f.seed()
        let id = UUID(); let url = try await f.store.reserveVoiceSegment(recording.id, id: id)
        let damaged = Data([0, 255, 1, 67, 0, 128]); try damaged.write(to: url)
        let exported = try await f.store.exportVoiceAudio(recording.id, segmentID: id)
        XCTAssertEqual(exported, damaged)
        await expectFailure { _ = try await f.store.verifiedVoiceURL(recording.id, segmentID: id) }
        f.faults.arm(.audio, .beforeRemove)
        await expectFailure { try await f.store.deleteVoiceAudio(recording.id) }
        let pending = try await f.store.voiceAudioRemovalComplete(recording.id)
        XCTAssertFalse(pending)
        XCTAssertEqual(try Data(contentsOf: url), damaged)
        await expectFailure { _ = try await f.store.exportVoiceAudio(recording.id, segmentID: id) }
        try await f.store.deleteVoiceAudio(recording.id)
        let complete = try await f.store.voiceAudioRemovalComplete(recording.id)
        XCTAssertTrue(complete)
    }

    func testAggregateLibraryLimitLeavesIndividualRecordsAndOriginalsReadable() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, recording) = try await f.seed()
        let original = try await f.store.exportVoiceAudio(recording.id, segmentID: recording.segments[0].id)
        let text = String(repeating: "x", count: 499_000)
        for _ in 0..<8 {
            var value = try await f.store.createRecording(id: UUID(), target: recording.target)
            value.corrections = (0..<6).map { _ in VoiceCorrection(text: text) }
            let data = try JSONEncoder().encode(value)
            XCTAssertLessThan(data.count, 4 * 1024 * 1024)
            try data.write(to: f.root.appendingPathComponent("State/voice/\(value.id).json"))
        }
        do { _ = try await f.store.voiceLibrary(); XCTFail("Expected aggregate limit") }
        catch { XCTAssertEqual(error as? VoiceError, .limit) }
        let retained = try await f.store.recording(recording.id)
        let after = try await f.store.exportVoiceAudio(recording.id, segmentID: recording.segments[0].id)
        XCTAssertEqual(retained, recording); XCTAssertEqual(after, original)
    }

    func testTranscriptRetryIdentityIsUTF8ExactIncludingRetainedPiecesAndLegacyOmission() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, recording) = try await f.seed()
        let encoded = try JSONEncoder().encode(recording)
        XCTAssertFalse(String(decoding: encoded, as: UTF8.self).contains("segmentTranscripts"))
        let references = try await f.store.voiceReferences(recording.id)
        let id = UUID()
        let first = VoiceTranscript(id: id, media: references, text: "caf\u{e9}")
        let changed = VoiceTranscript(id: id, media: references, text: "cafe\u{301}")
        try await f.store.retainVoiceTranscript(recording.id, proposal: first)
        do { try await f.store.retainVoiceTranscript(recording.id, proposal: changed); XCTFail("Expected byte mismatch") }
        catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
        let piece = VoiceSegmentTranscript(media: references[0], text: first.text)
        try await f.store.retainVoiceSegmentTranscript(recording.id, piece: piece)
        do { try await f.store.retainVoiceSegmentTranscript(recording.id, piece: VoiceSegmentTranscript(media: references[0], text: changed.text)); XCTFail("Expected byte mismatch") }
        catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
        let retained = try await f.store.recording(recording.id)
        XCTAssertEqual(Data(retained.transcripts[0].text.utf8), Data(first.text.utf8))
        XCTAssertEqual(retained.segmentTranscripts, [piece])
    }

    private func expectFailure(_ work: () async throws -> Void, file: StaticString = #filePath, line: UInt = #line) async {
        do { try await work(); XCTFail("Expected safe rejection", file: file, line: line) } catch {}
    }
}

extension VoiceStoreTests {
    func testLoopTwoPagedColdInventoryKeepsLaterReadableOriginalBeyondTwentyMiB() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, seeded) = try await f.seed()
        let folder = f.root.appendingPathComponent("State/voice")
        let encoder = JSONEncoder()
        let text = String(repeating: "a", count: 490000)
        for index in 0..<24 {
            let id = UUID(uuidString: String(format: "00000000-0000-0000-0000-%012d", index))!
            var value = try await f.store.createRecording(id: id, target: seeded.target)
            value.corrections = (0..<3).map { _ in VoiceCorrection(text: text) }
            // An actual legacy inline file exercises cold decoding, not the new compact writer.
            try encoder.encode(value).write(to: folder.appendingPathComponent("\(id.uuidString).json"))
        }
        try Data("broken".utf8).write(to: folder.appendingPathComponent("00000000-0000-0000-0000-000000000005.json"))
        let first = try await f.store.voiceLibraryPage()
        XCTAssertLessThanOrEqual(first.recordings.count, 20)
        XCTAssertEqual(first.unreadableCount, 1)
        let second = try await f.store.voiceLibraryPage(after: first.nextCursor)
        XCTAssertTrue(second.recordings.contains { $0.id == seeded.id })
        XCTAssertTrue(Set(first.recordings.map(\.id)).isDisjoint(with: second.recordings.map(\.id)))
        let restored = try await f.store.recording(seeded.id)
        let original = try await f.store.exportVoiceAudio(restored.id, segmentID: restored.segments[0].id)
        XCTAssertEqual(original, Data("synthetic controlled media".utf8))
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil).count, 25)
    }

    func testLoopTwoStreamingEvidenceAcceptsLargeOrdinaryBodiesAndFindsTrailingStamps() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, recording) = try await f.seed()
        for index in 1...5 {
            let day = try JournalDay(String(format: "2026-08-%02d", index))
            let snapshot = try await f.store.open(day)
            let draft = try await f.store.makeDraft(for: snapshot, section: "🧠 Notes")
                .updating(text: String(repeating: "正文𐀀e\u{301}\\\"", count: 450000), section: "🧠 Notes")
            try await f.store.saveDraft(draft)
        }
        let before = try await f.store.voiceInsertion(for: recording.id)
        XCTAssertNil(before)
        let candidate = try await f.store.prepareVoiceInsertion(recording.id, draft: base)
        _ = try await f.store.insertVoice(candidate)
        let inserted = try await f.store.voiceInsertion(for: recording.id)
        XCTAssertEqual(inserted?.insertionID, candidate.id)
        let day = try JournalDay("2026-08-01")
        let url = f.root.appendingPathComponent("State/drafts/\(day.value).json")
        var object = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [String: Any]
        object["voiceInsertions"] = [["recordingID": recording.id.uuidString, "insertionID": candidate.id.uuidString, "payloadHash": inserted!.payloadHash]]
        try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]).write(to: url)
        let repeated = try await f.store.voiceInsertion(for: recording.id)
        XCTAssertEqual(repeated, inserted)
        var corrupt = try Data(contentsOf: url); corrupt.removeLast()
        try corrupt.write(to: url)
        await expectFailure { _ = try await f.store.voiceInsertion(for: recording.id) }
    }

    func testLoopTwoEvidenceRejectsEscapedDuplicateIdentityAndInvalidUnicode() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, recording) = try await f.seed()
        let url = f.root.appendingPathComponent("State/drafts/2026-09-12.json")
        let bytes = try Data(contentsOf: url)
        var json = String(decoding: bytes, as: UTF8.self)
        json.removeLast(); json += ",\"da\\u0079\":\"2026-09-13\"}"
        try Data(json.utf8).write(to: url)
        await expectFailure { _ = try await f.store.voiceInsertion(for: recording.id) }
        var bad = bytes; bad.insert(contentsOf: [0xED, 0xA0, 0x80], at: 2)
        try bad.write(to: url)
        await expectFailure { _ = try await f.store.voiceInsertion(for: recording.id) }
        try bytes.write(to: url)
        let asyncValue1 = try await f.store.voiceInsertion(for: recording.id)
        XCTAssertNil(asyncValue1)
    }

    func testLoopTwoCorrectionSidecarsRetainTenNearLimitRevisionsAndLegacyBytes() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, recording) = try await f.seed()
        var versions: [VoiceCorrection] = []
        for index in 0..<10 {
            let correction = VoiceCorrection(text: String(index) + String(repeating: "e\u{301}", count: 160000))
            versions.append(correction)
            try await f.store.retainVoiceCorrection(recording.id, correction: correction)
            try await f.store.checkpointVoiceCorrection(recording.id)
        }
        let reopened = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"))
        let current = try await reopened.recording(recording.id)
        XCTAssertTrue(current.correctedText.utf8.elementsEqual(versions.last!.text.utf8))
        let old = try await reopened.voiceCorrection(recording.id, version: versions[0].id)
        XCTAssertEqual(old, versions[0])
        let page = try await reopened.voiceCorrectionHistory(recording.id)
        XCTAssertEqual(page.references.count, 10)
        let candidate = try await reopened.prepareVoiceInsertion(recording.id, draft: base)
        XCTAssertEqual(candidate.correction, versions.last)
        _ = try await reopened.insertVoice(candidate)
        let merged = try await reopened.open(f.day).draft!
        XCTAssertTrue(merged.text.hasSuffix(candidate.preview))
        let manifest = try Data(contentsOf: f.root.appendingPathComponent("State/voice/\(recording.id.uuidString).json"))
        XCTAssertLessThan(manifest.count, 20000)
    }

    func testLoopTwoLegacyInlineCorrectionMigrationAndSidecarFailureBoundaries() async throws {
        for purpose in [FilePurpose.voiceCorrection, .voice] {
            for stage in [FileStage.beforeWrite, .beforeReplace, .afterReplace] {
                let f = try VoiceFixture(); defer { f.cleanup() }
                let (_, seeded) = try await f.seed()
                var legacy = seeded
                let old = VoiceCorrection(text: "Legacy e\u{301} 原字节")
                legacy.corrections = [old]; legacy.correctionDraft = nil
                legacy.correctionReferences = nil; legacy.currentCorrectionReference = nil
                let manifestURL = f.root.appendingPathComponent("State/voice/\(legacy.id.uuidString).json")
                try JSONEncoder().encode(legacy).write(to: manifestURL)
                let asyncValue2 = try await f.store.recording(legacy.id).correctedText
                XCTAssertEqual(asyncValue2, old.text)
                let next = VoiceCorrection(text: "Visible new correction")
                f.faults.arm(purpose, stage)
                await expectFailure { try await f.store.retainVoiceCorrection(legacy.id, correction: next) }
                try await f.store.retainVoiceCorrection(legacy.id, correction: next)
                try await f.store.checkpointVoiceCorrection(legacy.id)
                let asyncValue3 = try await f.store.voiceCorrection(legacy.id, version: old.id)
                XCTAssertEqual(asyncValue3, old)
                let asyncValue4 = try await f.store.recording(legacy.id).correctedText
                XCTAssertEqual(asyncValue4, next.text)
            }
        }
    }

    func testLoopTwoUntouchedRecordingProducesStableAudioOnlyCandidate() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, recording) = try await f.seed()
        let fresh = try await f.store.createRecording(id: UUID(), target: recording.target)
        let segment = UUID(), bytes = Data("original".utf8)
        let url = try await f.store.reserveVoiceSegment(fresh.id, id: segment)
        try bytes.write(to: url)
        try await f.store.finalizeVoiceSegment(fresh.id, id: segment, duration: 1)
        let candidate = try await f.store.prepareVoiceInsertion(fresh.id, draft: base)
        XCTAssertEqual(candidate.correction.text, "")
        XCTAssertTrue(candidate.preview.hasPrefix("[原录音 1]"))
        _ = try await f.store.insertVoice(candidate)
        let asyncValue5 = try await f.store.insertVoice(candidate)
        XCTAssertEqual(asyncValue5, .alreadyInserted)
    }

    func testLoopTwoAbandonUnappliedInsertionRetainsDataAndRejectsOldIdentityForever() async throws {
        for purpose in [FilePurpose.voiceAbandonment, .voiceIntent] {
            let stages: [FileStage] = purpose == .voiceAbandonment ? [.beforeWrite, .afterReplace] : [.beforeRemove, .afterRemove]
            for stage in stages {
                let f = try VoiceFixture(); defer { f.cleanup() }
                let (base, recording) = try await f.seed()
                let candidate = try await f.store.prepareVoiceInsertion(recording.id, draft: base)
                f.faults.arm(.draft, .beforeWrite)
                await expectFailure { _ = try await f.store.insertVoice(candidate) }
                let audio = try await f.store.verifiedVoiceURL(recording.id, segmentID: recording.segments[0].id)
                try Data("externally changed original".utf8).write(to: audio)
                await expectFailure { try await f.store.recoverVoiceInsertion(f.day) }
                let asyncValue6 = try await f.store.abandonableVoiceInsertion(f.day)
                XCTAssertEqual(asyncValue6, candidate)
                f.faults.arm(purpose, stage)
                await expectFailure { try await f.store.abandonVoiceInsertion(candidate) }
                try await f.store.abandonVoiceInsertion(candidate)
                let asyncValue7 = try await f.store.pendingVoiceInsertion(f.day)
                XCTAssertNil(asyncValue7)
                let asyncValue8 = try await f.store.open(f.day).draft
                XCTAssertEqual(asyncValue8, base)
                await expectFailure { _ = try await f.store.insertVoice(candidate) }
                try Data("synthetic controlled media".utf8).write(to: audio)
                let nextBase = base.updating(text: "Later legitimate input", section: base.section)
                try await f.store.saveDraft(nextBase)
                let next = try await f.store.prepareVoiceInsertion(recording.id, draft: nextBase)
                XCTAssertNotEqual(next.id, candidate.id)
                f.faults.arm(.draft, .beforeWrite)
                await expectFailure { _ = try await f.store.insertVoice(next) }
                try await f.store.abandonVoiceInsertion(candidate)
                let asyncValue9 = try await f.store.pendingVoiceInsertion(f.day)
                XCTAssertEqual(asyncValue9, next)
                _ = try await f.store.insertVoice(next)
                let bytes = try await f.store.open(f.day).draft!.text
                XCTAssertTrue(bytes.hasPrefix("Later legitimate input"))
                try await f.store.abandonVoiceInsertion(candidate)
                await expectFailure { try await f.store.abandonVoiceInsertion(next) }
            }
        }
    }
}

private final class VoiceReadCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    func check(_ purpose: FilePurpose, _ stage: FileStage) {
        if purpose == .audio && stage == .beforeRead { lock.lock(); count += 1; lock.unlock() }
    }
    func reset() { lock.lock(); count = 0; lock.unlock() }
    var value: Int { lock.lock(); defer { lock.unlock() }; return count }
}

extension VoiceStoreTests {
    func testLoopTwoRetainingSegmentTranscriptsReadsEachCurrentOriginalOnce() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, value) = try await f.seed()
        for _ in 0..<12 {
            let id = UUID(), url = try await f.store.reserveVoiceSegment(value.id, id: id)
            try Data("synthetic next piece".utf8).write(to: url)
            try await f.store.finalizeVoiceSegment(value.id, id: id, duration: 1)
        }
        let counter = VoiceReadCounter()
        let store = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"),
            stateDirectory: f.root.appendingPathComponent("State"), failure: { counter.check($0, $1) })
        let refs = try await store.voiceReferences(value.id)
        counter.reset()
        for (index, ref) in refs.enumerated() {
            try await store.retainVoiceSegmentTranscript(value.id, piece: VoiceSegmentTranscript(media: ref, text: "Piece \(index)"))
        }
        XCTAssertEqual(counter.value, refs.count)
        let asyncValue1 = try await store.recording(value.id).segmentTranscripts?.count
        XCTAssertEqual(asyncValue1, refs.count)
    }

    func testLoopTwoDurableAbandonmentCleansOldIntentAfterLegitimateBaseChange() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, recording) = try await f.seed()
        let candidate = try await f.store.prepareVoiceInsertion(recording.id, draft: base)
        f.faults.arm(.draft, .beforeWrite)
        await expectFailure { _ = try await f.store.insertVoice(candidate) }
        f.faults.arm(.voiceIntent, .beforeRemove)
        await expectFailure { try await f.store.abandonVoiceInsertion(candidate) }
        let updated = base.updating(text: "Later retained editor", section: base.section)
        let url = f.root.appendingPathComponent("State/drafts/\(f.day.value).json")
        try JSONEncoder().encode(updated).write(to: url)
        try await f.store.recoverVoiceInsertion(f.day)
        let asyncValue2 = try await f.store.pendingVoiceInsertion(f.day)
        XCTAssertNil(asyncValue2)
        let asyncValue3 = try await f.store.open(f.day).draft
        XCTAssertEqual(asyncValue3, updated)
        await expectFailure { _ = try await f.store.insertVoice(candidate) }
    }
}

private final class VoiceEvidenceReadCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var reads = 0
    func check(_ purpose: FilePurpose, _ stage: FileStage) {
        if purpose == .voiceEvidence && stage == .beforeRead { lock.lock(); reads += 1; lock.unlock() }
    }
    var count: Int { lock.lock(); defer { lock.unlock() }; return reads }
}

extension VoiceStoreTests {
    func testLoopThreeDamagedHistoryAndCurrentTextAreIndependentFromOriginalRecovery() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, value) = try await f.seed()
        let old = VoiceCorrection(text: "Old exact e\u{301} history")
        try await f.store.retainVoiceCorrection(value.id, correction: old)
        try await f.store.checkpointVoiceCorrection(value.id)
        let current = VoiceCorrection(text: "Current exact readable correction")
        try await f.store.retainVoiceCorrection(value.id, correction: current)
        let oldURL = f.root.appendingPathComponent("State/voice-corrections/\(value.id)/\(old.id).json")
        try Data("damaged history".utf8).write(to: oldURL)
        let selected = try await f.store.recording(value.id)
        XCTAssertEqual(selected.correctedText, current.text)
        XCTAssertTrue(selected.unavailableHistory?.contains(old.id) == true)
        let audio = try await f.store.exportVoiceAudio(value.id, segmentID: value.segments[0].id)
        XCTAssertEqual(audio, Data("synthetic controlled media".utf8))
        let history = try await f.store.voiceCorrectionHistory(value.id)
        XCTAssertTrue(history.references.contains { $0.id == old.id })
        await expectFailure { _ = try await f.store.voiceCorrection(value.id, version: old.id) }
        let readable = try await f.store.voiceCorrection(value.id, version: current.id)
        XCTAssertEqual(readable, current)
        let candidate = try await f.store.prepareVoiceInsertion(value.id, draft: base)
        XCTAssertEqual(candidate.correction, current)
        let currentURL = f.root.appendingPathComponent("State/voice-correction-drafts/\(value.id)/\(current.id).json")
        try Data("damaged current".utf8).write(to: currentURL)
        let damaged = try await f.store.recording(value.id)
        XCTAssertEqual(damaged.currentCorrectionReference?.id, current.id)
        XCTAssertEqual(damaged.currentCorrectionUnavailable, true)
        await expectFailure { try await f.store.retainVoiceCorrection(value.id, correction: VoiceCorrection(text: "")) }
        await expectFailure { _ = try await f.store.prepareVoiceInsertion(value.id, draft: base) }
        let playback = try await f.store.verifiedVoicePlayback(value.id, segmentID: value.segments[0].id)
        XCTAssertEqual(playback, audio)
        try await f.store.deleteVoiceAudio(value.id)
        let deleted = try await f.store.recording(value.id)
        XCTAssertTrue(deleted.deletionRequested)
        XCTAssertEqual(try Data(contentsOf: currentURL), Data("damaged current".utf8))
        XCTAssertEqual(try Data(contentsOf: oldURL), Data("damaged history".utf8))
    }

    func testLoopThreeHundredsOfCorrectionAutosavesBoundTemporaryFilesAndKeepCheckpoints() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, value) = try await f.seed()
        var checkpoints: [VoiceCorrection] = []
        var last = VoiceCorrection(text: "")
        for index in 0..<240 {
            last = VoiceCorrection(text: String(repeating: "substantial original e\u{301} ", count: 1400) + "\(index)")
            try await f.store.retainVoiceCorrection(value.id, correction: last)
            if index % 50 == 0 { try await f.store.checkpointVoiceCorrection(value.id); checkpoints.append(last) }
        }
        let folder = f.root.appendingPathComponent("State/voice-correction-drafts/\(value.id)")
        XCTAssertLessThanOrEqual(try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil).count, 1)
        let reopened = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"))
        let current = try await reopened.recording(value.id)
        XCTAssertEqual(Data(current.correctedText.utf8), Data(last.text.utf8))
        for checkpoint in checkpoints {
            let retained = try await reopened.voiceCorrection(value.id, version: checkpoint.id)
            XCTAssertEqual(retained, checkpoint)
        }
        let candidate = try await reopened.prepareVoiceInsertion(value.id, draft: base)
        XCTAssertEqual(candidate.correction, last)
        for stage in [FileStage.beforeWrite, .beforeReplace, .afterReplace, .beforeRemove, .afterRemove] {
            let correction = VoiceCorrection(text: "Retry boundary \(stage)")
            f.faults.arm(.voiceCorrection, stage)
            do { try await f.store.retainVoiceCorrection(value.id, correction: correction) } catch {}
            try await f.store.retainVoiceCorrection(value.id, correction: correction)
            let retained = try await f.store.recording(value.id)
            XCTAssertEqual(retained.effectiveCorrection, correction)
        }
        for stage in [FileStage.beforeWrite, .beforeReplace, .afterReplace] {
            let correction = VoiceCorrection(text: "Reference boundary \(stage)")
            f.faults.arm(.voice, stage)
            do { try await f.store.retainVoiceCorrection(value.id, correction: correction) } catch {}
            try await f.store.retainVoiceCorrection(value.id, correction: correction)
            let retained = try await f.store.recording(value.id)
            XCTAssertEqual(retained.effectiveCorrection, correction)
        }
        XCTAssertLessThanOrEqual(try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil).count, 1)
    }

    func testLoopThreeProposalSidecarsExceedOldManifestCapacityAndPageExactOriginals() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, value) = try await f.seed()
        let media = try await f.store.voiceReferences(value.id)
        let firstPiece = VoiceSegmentTranscript(media: media[0], text: "Original segment e\u{301}")
        try await f.store.retainVoiceSegmentTranscript(value.id, piece: firstPiece)
        var proposals: [VoiceTranscript] = []
        for index in 0..<24 {
            var proposal = VoiceTranscript(id: UUID(), media: media, text: "Version \(index) " + String(repeating: "exact text ", count: 20000))
            proposal.inventory = try await f.store.voiceSourceInventory(value.id)
            try await f.store.retainVoiceTranscript(value.id, proposal: proposal)
            proposals.append(proposal)
        }
        let manifest = f.root.appendingPathComponent("State/voice/\(value.id).json")
        XCTAssertLessThan(try Data(contentsOf: manifest).count, 20000)
        let reopened = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"))
        let page = try await reopened.voiceTranscriptHistory(value.id)
        XCTAssertEqual(page.references.count, 20)
        let next = try await reopened.voiceTranscriptHistory(value.id, offset: page.nextOffset!)
        XCTAssertEqual(next.references.count, 4)
        for proposal in proposals {
            let retained = try await reopened.voiceTranscript(value.id, version: proposal.id)
            XCTAssertEqual(retained, proposal)
        }
        let latest = try await reopened.recording(value.id)
        XCTAssertEqual(latest.transcripts.last, proposals.last)
        let original = try await reopened.voiceSegmentTranscript(value.id, segmentID: media[0].segmentID)
        XCTAssertEqual(original, firstPiece)
        for purpose in [FilePurpose.voiceTranscript, .voice] {
            for stage in [FileStage.beforeWrite, .beforeReplace, .afterReplace] {
                let proposal = VoiceTranscript(id: UUID(), media: media, text: "Fault \(purpose) \(stage)")
                f.faults.arm(purpose, stage)
                do { try await f.store.retainVoiceTranscript(value.id, proposal: proposal) } catch {}
                try await f.store.retainVoiceTranscript(value.id, proposal: proposal)
                let retained = try await f.store.voiceTranscript(value.id, version: proposal.id)
                XCTAssertEqual(retained, proposal)
            }
        }
        try await f.store.endRecording(value.id)
        let audio = try await f.store.exportVoiceAudio(value.id, segmentID: media[0].segmentID)
        XCTAssertEqual(audio, Data("synthetic controlled media".utf8))
        var oversized = VoiceTranscript(id: UUID(), media: media, text: String(repeating: "z", count: 500001))
        oversized.inventory = try await f.store.voiceSourceInventory(value.id)
        await expectFailure { try await f.store.retainVoiceTranscript(value.id, proposal: oversized) }
        let stillReadable = try await f.store.voiceTranscript(value.id, version: proposals[0].id)
        XCTAssertEqual(stillReadable, proposals[0])
    }

    func testLoopThreeValidatedEvidenceCacheAvoidsHotBodiesAndInvalidatesAllChangedSources() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, value) = try await f.seed()
        let huge = base.updating(text: String(repeating: "a", count: 5 * 1024 * 1024), section: base.section)
        try await f.store.saveDraft(huge)
        let counter = VoiceEvidenceReadCounter()
        let store = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"), failure: { counter.check($0, $1) })
        _ = try await store.voiceInsertion(for: value.id)
        XCTAssertEqual(counter.count, 1)
        for index in 0..<12 {
            try await store.retainVoiceCorrection(value.id, correction: VoiceCorrection(text: "Hot correction \(index)"))
            _ = try await store.reserveVoiceSegment(value.id, id: UUID())
            _ = try await store.voiceInsertion(for: value.id)
        }
        XCTAssertEqual(counter.count, 1)
        let url = f.root.appendingPathComponent("State/drafts/\(f.day.value).json")
        let old = try Data(contentsOf: url)
        var changed = old; changed.removeLast(); changed.append(Data(",\"extra\":true}".utf8))
        try changed.write(to: url)
        _ = try await store.voiceInsertion(for: value.id)
        XCTAssertEqual(counter.count, 2)
        try old.write(to: url, options: .atomic)
        _ = try await store.voiceInsertion(for: value.id)
        XCTAssertEqual(counter.count, 3)
        let wrong = url.deletingLastPathComponent().appendingPathComponent("2026-09-11.json")
        try old.write(to: wrong)
        await expectFailure { _ = try await store.voiceInsertion(for: value.id) }
        try FileManager.default.removeItem(at: wrong)
        let count = counter.count
        _ = try await store.voiceInsertion(for: value.id)
        XCTAssertEqual(counter.count, count)
        try Data("{\"day\":".utf8).write(to: url)
        await expectFailure { _ = try await store.voiceInsertion(for: value.id) }
        try old.write(to: url)
        _ = try await store.voiceInsertion(for: value.id)
        XCTAssertGreaterThan(counter.count, count)
    }

    func testLoopThreeNonregularEvidenceAndAudioNeverBlockBeforeValidation() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, value) = try await f.seed()
        let fifo = f.root.appendingPathComponent("State/receipts/\(UUID()).json")
        XCTAssertEqual(mkfifo(fifo.path, 0o600), 0)
        let start = Date()
        await expectFailure { _ = try await f.store.voiceInsertion(for: value.id) }
        XCTAssertLessThan(Date().timeIntervalSince(start), 1)
        try FileManager.default.removeItem(at: fifo)
        let original = try await f.store.verifiedVoiceURL(value.id, segmentID: value.segments[0].id)
        try FileManager.default.removeItem(at: original)
        XCTAssertEqual(mkfifo(original.path, 0o600), 0)
        await expectFailure { _ = try await f.store.exportVoiceAudio(value.id, segmentID: value.segments[0].id) }
        await expectFailure { _ = try await f.store.verifiedVoiceURL(value.id, segmentID: value.segments[0].id) }
        await expectFailure { try await f.store.finalizeVoiceSegment(value.id, id: value.segments[0].id, duration: 1) }
        XCTAssertLessThan(Date().timeIntervalSince(start), 1)
    }

    func testLoopThreeCompletenessInventoryKeepsDamagedAndMissingPiecesAndRejectsStalePreview() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, value) = try await f.seed()
        let middle = UUID(), last = UUID(), missing = UUID()
        let damaged = try await f.store.reserveVoiceSegment(value.id, id: middle)
        try Data("damaged original bytes".utf8).write(to: damaged)
        let playable = try await f.store.reserveVoiceSegment(value.id, id: last)
        try Data("third piece".utf8).write(to: playable)
        try await f.store.finalizeVoiceSegment(value.id, id: last, duration: 1)
        _ = try await f.store.reserveVoiceSegment(value.id, id: missing)
        let inventory = try await f.store.voiceSourceInventory(value.id)
        XCTAssertEqual(inventory.map(\.segmentID), [value.segments[0].id, middle, last, missing])
        XCTAssertEqual(inventory.filter(\.finalized).count, 2)
        XCTAssertNotNil(inventory[1].hash)
        XCTAssertNil(inventory[3].hash)
        let refs = try await f.store.voiceReferences(value.id)
        try await f.store.skipVoiceTranscript(value.id, media: refs[1])
        var proposal = VoiceTranscript(id: UUID(), media: refs, text: "First retained original")
        proposal.skipped = [refs[1]]; proposal.inventory = try await f.store.voiceSourceInventory(value.id)
        try await f.store.retainVoiceTranscript(value.id, proposal: proposal)
        try await f.store.retrySkippedVoiceTranscript(value.id, media: refs[1])
        let retained = try await f.store.voiceTranscript(value.id, version: proposal.id)
        XCTAssertEqual(retained.skipped, [refs[1]])
        let source = try await f.store.recording(value.id)
        XCTAssertEqual(source.skippedTranscripts, [])
        let candidate = try await f.store.prepareVoiceInsertion(value.id, draft: base)
        XCTAssertEqual(candidate.inventory, inventory)
        XCTAssertTrue(candidate.preview.contains("第 2、4 段尚不可用"))
        for original in inventory { XCTAssertTrue(candidate.preview.contains(original.relativeLink)) }
        try await f.store.finalizeVoiceSegment(value.id, id: middle, duration: 1)
        do { _ = try await f.store.insertVoice(candidate); XCTFail("Stale completeness accepted") }
        catch { XCTAssertEqual(error as? VoiceError, .changedMedia) }
        let originalDraft = try await f.store.open(f.day).draft
        XCTAssertEqual(originalDraft, base)
        let original = try await f.store.exportVoiceAudio(value.id, segmentID: middle)
        XCTAssertEqual(original, Data("damaged original bytes".utf8))
    }

    func testLoopThreeUnusedPreparationReuseAndCreationTimePagingStayStable() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, seeded) = try await f.seed()
        let first = try await f.store.prepareRecording(id: UUID(), target: seeded.target)
        for _ in 0..<8 {
            let retained = try await f.store.prepareRecording(id: UUID(), target: seeded.target)
            XCTAssertEqual(retained.id, first.id)
        }
        let reopened = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"))
        let retained = try await reopened.prepareRecording(id: UUID(), target: seeded.target)
        XCTAssertEqual(retained.id, first.id)
        try await reopened.retainVoiceCorrection(first.id, correction: VoiceCorrection(text: "Retained actual input"))
        let next = try await reopened.prepareRecording(id: UUID(), target: seeded.target)
        XCTAssertNotEqual(next.id, first.id)
        let folder = f.root.appendingPathComponent("State/voice")
        for index in 0..<25 {
            let id = UUID(uuidString: String(format: "00000000-0000-0000-0000-%012d", 100 - index))!
            let value = VoiceRecording(id: id, target: seeded.target, createdAt: Date(timeIntervalSince1970: 2100000000 + Double(index / 2)), segments: [], transcripts: [], corrections: [], ended: false, deletionRequested: false)
            try JSONEncoder().encode(value).write(to: folder.appendingPathComponent("\(id).json"))
        }
        let page = try await reopened.voiceLibraryPage()
        XCTAssertEqual(page.recordings.first?.id, UUID(uuidString: "00000000-0000-0000-0000-000000000076"))
        let second = try await reopened.voiceLibraryPage(after: page.nextCursor)
        XCTAssertTrue(Set(page.recordings.map(\.id)).isDisjoint(with: second.recordings.map(\.id)))
        let changed = VoiceRecording(id: next.id, target: next.target, createdAt: Date(timeIntervalSince1970: 2200000000), segments: [], transcripts: [], corrections: [], ended: false, deletionRequested: false)
        try JSONEncoder().encode(changed).write(to: folder.appendingPathComponent("\(next.id).json"))
        do { _ = try await reopened.voiceLibraryPage(after: page.nextCursor); XCTFail("Changed inventory cursor accepted") }
        catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        let refreshed = try await reopened.voiceLibraryPage()
        XCTAssertEqual(refreshed.recordings.first?.id, next.id)
    }

    func testLoopThreeCaptureLossEvidenceSurvivesManifestFaultsAndUnknownRecoveryIsExplicit() async throws {
        for stage in [FileStage.beforeWrite, .beforeReplace, .afterReplace] {
            let f = try VoiceFixture(); defer { f.cleanup() }
            let (_, value) = try await f.seed()
            let id = UUID(), url = try await f.store.reserveVoiceSegment(value.id, id: id, captureExpected: true)
            try Data("received original".utf8).write(to: url)
            var evidence = VoiceCaptureEvidence(firstFrame: 123, frameCount: 32000, sampleRate: 16000, streamID: UUID())
            evidence.rejectedFrames = 51; evidence.unassignedFrames = 17
            f.faults.arm(.voice, stage)
            do { try await f.store.finalizeVoiceSegment(value.id, id: id, duration: 2, evidence: evidence) } catch {}
            let reopened = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"))
            try await reopened.finalizeVoiceSegment(value.id, id: id, duration: 2)
            let retained = try await reopened.recording(value.id).segments.last!
            XCTAssertEqual(retained.captureEvidence, evidence)
            XCTAssertNil(retained.captureEvidenceUnknown)
        }
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, value) = try await f.seed()
        let id = UUID(), url = try await f.store.reserveVoiceSegment(value.id, id: id, captureExpected: true)
        try Data("received original".utf8).write(to: url)
        let evidence = VoiceCaptureEvidence(firstFrame: 0, frameCount: 16000, sampleRate: 16000, streamID: UUID())
        f.faults.arm(.voiceCapture, .beforeWrite)
        await expectFailure { try await f.store.finalizeVoiceSegment(value.id, id: id, duration: 1, evidence: evidence) }
        try await f.store.finalizeVoiceSegment(value.id, id: id, duration: 1)
        let sameProcess = try await f.store.recording(value.id).segments.last!
        XCTAssertEqual(sameProcess.captureEvidence, evidence)
        let unknownID = UUID(), unknownURL = try await f.store.reserveVoiceSegment(value.id, id: unknownID, captureExpected: true)
        try Data("unknown original".utf8).write(to: unknownURL)
        let reopened = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"))
        try await reopened.finalizeVoiceSegment(value.id, id: unknownID, duration: 1)
        let unknown = try await reopened.recording(value.id).segments.last!
        XCTAssertTrue(unknown.captureEvidenceUnknown == true)
        XCTAssertNil(unknown.captureEvidence)
        let initial = try await reopened.recording(value.id).segments.first!
        XCTAssertNil(initial.captureEvidenceUnknown)
        XCTAssertNil(initial.captureEvidence)
    }
}

extension VoiceStoreTests {
    func testLoopThreeLegacyUnreferencedDraftsAndDamagedProposalHistoryRemainRecoverable() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, value) = try await f.seed()
        let orphan = VoiceCorrection(text: "Legacy unreachable version e\u{301}")
        let folder = f.root.appendingPathComponent("State/voice-corrections/\(value.id)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let url = folder.appendingPathComponent("\(orphan.id).json")
        let bytes = try JSONEncoder().encode(VoiceCorrectionRecord(recordingID: value.id, target: value.target, correction: orphan))
        try bytes.write(to: url)
        let history = try await f.store.voiceCorrectionHistory(value.id)
        XCTAssertTrue(history.references.contains { $0.id == orphan.id })
        let recovered = try await f.store.voiceCorrection(value.id, version: orphan.id)
        XCTAssertEqual(recovered, orphan)
        let media = try await f.store.voiceReferences(value.id)
        let old = VoiceTranscript(id: UUID(), media: media, text: "Legacy proposal e\u{301}")
        var inline = try await f.store.voiceManifest(value.id)
        inline.transcripts = [old]
        try JSONEncoder().encode(inline).write(to: f.root.appendingPathComponent("State/voice/\(value.id).json"))
        let new = VoiceTranscript(id: UUID(), media: media, text: "New proposal")
        try await f.store.retainVoiceTranscript(value.id, proposal: new)
        let original = try await f.store.voiceTranscript(value.id, version: old.id)
        XCTAssertEqual(original, old)
        let sidecar = f.root.appendingPathComponent("State/voice-transcripts/\(value.id)/proposals/\(old.id).json")
        try Data("corrupt historical proposal".utf8).write(to: sidecar)
        let selected = try await f.store.recording(value.id)
        XCTAssertEqual(selected.transcripts.last, new)
        XCTAssertEqual(selected.correctedText, value.correctedText)
        XCTAssertTrue(selected.unavailableHistory?.contains(old.id) == true)
        let page = try await f.store.voiceTranscriptHistory(value.id)
        XCTAssertTrue(page.references.contains { $0.id == old.id })
        await expectFailure { _ = try await f.store.voiceTranscript(value.id, version: old.id) }
        let audio = try await f.store.exportVoiceAudio(value.id, segmentID: value.segments[0].id)
        XCTAssertEqual(audio, Data("synthetic controlled media".utf8))
        XCTAssertEqual(try Data(contentsOf: url), bytes)
    }

    func testLoopThreeCaptureEvidenceRejectsChangedOriginalAndCorruptEvidenceOnRecovery() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, value) = try await f.seed()
        let id = UUID(), url = try await f.store.reserveVoiceSegment(value.id, id: id, captureExpected: true)
        let original = Data("captured source".utf8)
        try original.write(to: url)
        var evidence = VoiceCaptureEvidence(firstFrame: 0, frameCount: 16000, sampleRate: 16000, streamID: UUID())
        evidence.rejectedFrames = 19
        f.faults.arm(.voice, .beforeReplace)
        await expectFailure { try await f.store.finalizeVoiceSegment(value.id, id: id, duration: 1, evidence: evidence) }
        try Data("different captured source".utf8).write(to: url)
        do { try await f.store.finalizeVoiceSegment(value.id, id: id, duration: 1); XCTFail("Changed evidence source accepted") }
        catch { XCTAssertEqual(error as? VoiceError, .changedMedia) }
        try original.write(to: url)
        let evidenceURL = f.root.appendingPathComponent("State/voice-capture/\(value.id)/\(id).json")
        try Data("corrupt capture evidence".utf8).write(to: evidenceURL)
        let reopened = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"))
        await expectFailure { try await reopened.finalizeVoiceSegment(value.id, id: id, duration: 1) }
        let raw = try await reopened.exportVoiceAudio(value.id, segmentID: id)
        XCTAssertEqual(raw, original)
        let retained = try await reopened.voiceManifest(value.id).segments.last!
        XCTAssertFalse(retained.finalized)
        XCTAssertNil(retained.captureEvidence)
    }
}

extension VoiceStoreTests {
    func testLoopThreeSeparateSampleAndNormalStoresRejectCrossNamespaceMediaAndInsertion() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("Voice-Isolation-\(UUID())")
        defer { try? FileManager.default.removeItem(at: root) }
        let sampleRoot = root.appendingPathComponent("Sample"), normalRoot = root.appendingPathComponent("Normal")
        let sample = try LocalJournalStore(journalDirectory: sampleRoot.appendingPathComponent("Journals"), stateDirectory: sampleRoot.appendingPathComponent("State"))
        let normal = try LocalJournalStore(journalDirectory: normalRoot.appendingPathComponent("Journals"), stateDirectory: normalRoot.appendingPathComponent("State"))
        let day = try JournalDay("2026-09-12")
        let sampleSnapshot = try await sample.open(day), normalSnapshot = try await normal.open(day)
        let sampleBase = try await sample.makeDraft(for: sampleSnapshot, section: "🧠 Notes").updating(text: "Sample original input", section: "🧠 Notes")
        let normalBase = try await normal.makeDraft(for: normalSnapshot, section: "🧠 Notes").updating(text: "Normal untouched input", section: "🧠 Notes")
        try await sample.saveDraft(sampleBase); try await normal.saveDraft(normalBase)
        let target = try await sample.voiceTarget(sampleBase)
        let value = try await sample.createRecording(id: UUID(), target: target)
        let segment = UUID(), original = Data("Synthetic namespace-owned original".utf8)
        let url = try await sample.reserveVoiceSegment(value.id, id: segment)
        try original.write(to: url)
        try await sample.finalizeVoiceSegment(value.id, id: segment, duration: 1)
        let correction = VoiceCorrection(text: "Sample correction e\u{301}")
        try await sample.retainVoiceCorrection(value.id, correction: correction)
        let candidate = try await sample.prepareVoiceInsertion(value.id, draft: sampleBase)
        await expectFailure { _ = try await normal.recording(value.id) }
        await expectFailure { _ = try await normal.exportVoiceAudio(value.id, segmentID: segment) }
        do { _ = try await normal.insertVoice(candidate); XCTFail("Foreign namespace insertion accepted") }
        catch { XCTAssertEqual(error as? VoiceError, .invalidTarget) }
        do { _ = try await normal.createRecording(id: value.id, target: target); XCTFail("Foreign capture target accepted") }
        catch { XCTAssertEqual(error as? VoiceError, .invalidTarget) }
        let normalAfter = try await normal.open(day).draft
        XCTAssertEqual(normalAfter, normalBase)
        let normalLibrary = try await normal.voiceLibraryPage()
        XCTAssertTrue(normalLibrary.recordings.isEmpty)
        let reopened = try LocalJournalStore(journalDirectory: sampleRoot.appendingPathComponent("Journals"), stateDirectory: sampleRoot.appendingPathComponent("State"))
        let recovered = try await reopened.recording(value.id)
        XCTAssertEqual(recovered.target, target)
        XCTAssertEqual(recovered.effectiveCorrection, correction)
        let recoveredAudio = try await reopened.exportVoiceAudio(value.id, segmentID: segment)
        XCTAssertEqual(recoveredAudio, original)
        let recoveredDraft = try await reopened.open(day).draft
        XCTAssertEqual(recoveredDraft, sampleBase)
        _ = try await reopened.insertVoice(candidate)
        let final = try await reopened.open(day).draft!
        XCTAssertEqual(final.text, sampleBase.text + "\n\n" + candidate.preview)
        let normalFinal = try await normal.open(day).draft
        XCTAssertEqual(normalFinal, normalBase)
    }
}

extension VoiceStoreTests {
    func testLoopFourBoundFinalizationRejectsReplacementAndRecoversOnlyExactValidatedBytes() async throws {
        for stage in [FileStage.beforeReplace, .afterReplace] {
            let f = try VoiceFixture(); defer { f.cleanup() }
            let (_, value) = try await f.seed()
            let id = UUID(), bytes = Data("first validated synthetic original".utf8)
            let url = try await f.store.reserveVoiceSegment(value.id, id: id, captureExpected: true)
            try bytes.write(to: url)
            let validation = VoiceMediaValidation(data: bytes, duration: 1.25)
            var evidence = VoiceCaptureEvidence(firstFrame: 100, frameCount: 60000, sampleRate: 48000)
            evidence.rejectedFrames = 17
            try Data("replacement original".utf8).write(to: url)
            do { try await f.store.finalizeVoiceSegment(value.id, id: id, duration: 1.25, evidence: evidence, validation: validation); XCTFail("Replacement finalized") }
            catch { XCTAssertEqual(error as? VoiceError, .changedMedia) }
            let rejected = try await f.store.voiceManifest(value.id).segments.last!
            XCTAssertFalse(rejected.finalized); XCTAssertNil(rejected.hash); XCTAssertNil(rejected.captureEvidence)
            try bytes.write(to: url)
            f.faults.arm(.voice, stage)
            do { try await f.store.finalizeVoiceSegment(value.id, id: id, duration: 1.25, evidence: evidence, validation: validation) } catch {}
            try await f.store.finalizeVoiceSegment(value.id, id: id, duration: 1.25, evidence: evidence, validation: validation)
            let final = try await f.store.voiceManifest(value.id).segments.last!
            XCTAssertEqual(final.hash, validation.hash); XCTAssertEqual(final.byteCount, validation.byteCount)
            XCTAssertEqual(final.captureEvidence, evidence); XCTAssertEqual(try Data(contentsOf: url), bytes)
        }
    }

    func testLoopFourMaximumAcceptedStampsRemainReadableAndOneOverNeverMutates() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (old, voice) = try await f.seed()
        var draft = old.updating(text: "Maximum accepted metadata", section: old.section)
        draft.voiceInsertions = (0..<9999).map { _ in VoiceInsertionStamp(recordingID: UUID(), insertionID: UUID(), payloadHash: String(repeating: "a", count: 64)) }
        try await f.store.saveDraft(draft)
        let absent = try await f.store.voiceInsertion(for: voice.id)
        XCTAssertNil(absent)
        let candidate = try await f.store.prepareVoiceInsertion(voice.id, draft: draft)
        let inserted = try await f.store.insertVoice(candidate)
        XCTAssertEqual(inserted, .inserted)
        let actual = try await f.store.open(f.day).draft!
        XCTAssertEqual(actual.voiceInsertions?.count, 10000)
        let url = f.root.appendingPathComponent("State/drafts/\(f.day.value).json"), bytes = try Data(contentsOf: url)
        XCTAssertGreaterThan(bytes.count, 1024 * 1024)
        var excess = actual.updating(text: actual.text + " new", section: actual.section)
        excess.voiceInsertions?.append(VoiceInsertionStamp(recordingID: UUID(), insertionID: UUID(), payloadHash: String(repeating: "b", count: 64)))
        do { try await f.store.saveDraft(excess); XCTFail("Unreadable metadata accepted") }
        catch { XCTAssertEqual(error as? VoiceError, .limit) }
        XCTAssertEqual(try Data(contentsOf: url), bytes)
        let retried = try await f.store.insertVoice(candidate)
        XCTAssertEqual(retried, .alreadyInserted)
        do { try await f.store.abandonVoiceInsertion(candidate); XCTFail("Applied insertion abandoned") }
        catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
        _ = try await f.store.commit(actual)
        let receiptRetry = try await f.store.insertVoice(candidate)
        XCTAssertEqual(receiptRetry, .alreadyInserted)
        let stamp = try await f.store.voiceInsertion(for: voice.id)
        XCTAssertEqual(stamp?.insertionID, candidate.id)
    }

    func testLoopFourHistoricalReferenceMetadataIsolatedAndConflictingCurrentFailsClosed() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, voice) = try await f.seed()
        try await f.store.checkpointVoiceCorrection(voice.id)
        let current = VoiceCorrection(text: "Current exact e\u{301}")
        try await f.store.retainVoiceCorrection(voice.id, correction: current)
        var manifest = try await f.store.voiceManifest(voice.id)
        let badID = manifest.correctionReferences![0].id
        manifest.correctionReferences = [VoiceCorrectionReference(id: badID, hash: "bad", byteCount: -1), VoiceCorrectionReference(id: badID, hash: "bad", byteCount: -1)]
        let badProposal = UUID()
        manifest.transcriptReferences = [VoiceTextReference(id: badProposal, hash: "broken", byteCount: -4), VoiceTextReference(id: badProposal, hash: "broken", byteCount: -4)]
        let path = f.root.appendingPathComponent("State/voice/\(voice.id).json")
        let original = try JSONEncoder().encode(manifest); try original.write(to: path)
        let read = try await f.store.recording(voice.id)
        XCTAssertEqual(read.correctedText.utf8.map { $0 }, current.text.utf8.map { $0 })
        XCTAssertTrue(read.unavailableHistory?.contains(badID) == true)
        XCTAssertTrue(read.unavailableHistory?.contains(badProposal) == true)
        XCTAssertNotEqual(read.currentCorrectionUnavailable, true)
        await expectFailure { _ = try await f.store.voiceCorrection(voice.id, version: badID) }
        await expectFailure { _ = try await f.store.voiceTranscript(voice.id, version: badProposal) }
        let exported = try await f.store.exportVoiceAudio(voice.id, segmentID: voice.segments[0].id)
        XCTAssertEqual(exported, Data("synthetic controlled media".utf8)); XCTAssertEqual(try Data(contentsOf: path), original)
        manifest.correctionReferences?.append(VoiceCorrectionReference(id: current.id, hash: String(repeating: "f", count: 64), byteCount: current.text.utf8.count))
        try JSONEncoder().encode(manifest).write(to: path)
        let damaged = try await f.store.recording(voice.id)
        XCTAssertTrue(damaged.currentCorrectionUnavailable == true)
        await expectFailure { try await f.store.retainVoiceCorrection(voice.id, correction: VoiceCorrection(text: "Must not replace current")) }
    }

    func testLoopFourOrphanPieceRecoversOriginalIdentityAcrossReferenceWriteFailures() async throws {
        for stage in [FileStage.beforeReplace, .afterReplace] {
            let f = try VoiceFixture(); defer { f.cleanup() }
            let (_, voice) = try await f.seed(), media = try await f.store.voiceReferences(voice.id)[0]
            let piece = VoiceSegmentTranscript(media: media, text: "Original immutable e\u{301} 中文")
            f.faults.arm(.voice, stage)
            do { try await f.store.retainVoiceSegmentTranscript(voice.id, piece: piece) } catch {}
            let path = f.root.appendingPathComponent("State/voice-transcripts/\(voice.id)/pieces/\(media.segmentID).json")
            let bytes = try Data(contentsOf: path)
            let reopened = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"))
            let retained = try await reopened.voiceSegmentTranscript(voice.id, segmentID: media.segmentID)
            XCTAssertEqual(retained, piece); XCTAssertEqual(try Data(contentsOf: path), bytes)
            let refs = try await reopened.voiceManifest(voice.id).segmentTranscriptReferences!
            XCTAssertEqual(refs.count, 1); XCTAssertEqual(refs[0].id, media.segmentID)
            await expectFailure { try await reopened.retainVoiceSegmentTranscript(voice.id, piece: VoiceSegmentTranscript(media: media, text: "Different second result")) }
            XCTAssertEqual(try Data(contentsOf: path), bytes)
        }
    }

    func testLoopFourManualOnlyAndUnavailableOriginalsRequireFreshCompleteInventory() async throws {
        for unavailable in [false, true] {
            let f = try VoiceFixture(); defer { f.cleanup() }
            let (draft, seed) = try await f.seed()
            let voice = try await f.store.createRecording(id: UUID(), target: seed.target)
            await expectFailure { _ = try await f.store.prepareVoiceInsertion(voice.id, draft: draft) }
            try await f.store.retainVoiceCorrection(voice.id, correction: VoiceCorrection(text: "手动原文 e\u{301}"))
            var candidate = try await f.store.prepareVoiceInsertion(voice.id, draft: draft)
            XCTAssertTrue(candidate.preview.contains("没有可用原音频"))
            if unavailable {
                let id = UUID(), url = try await f.store.reserveVoiceSegment(voice.id, id: id)
                let original = Data("formerly finalized original".utf8); try original.write(to: url)
                try await f.store.finalizeVoiceSegment(voice.id, id: id, duration: 2)
                try Data("changed original".utf8).write(to: url)
                await expectFailure { _ = try await f.store.insertVoice(candidate) }
                candidate = try await f.store.prepareVoiceInsertion(voice.id, draft: draft)
                XCTAssertEqual(candidate.inventory?.first?.hash, SourceHash.digest(original))
                XCTAssertTrue(candidate.inventory?.first?.unavailable == true)
                XCTAssertTrue(candidate.media.isEmpty); XCTAssertTrue(candidate.preview.contains("尚不可用"))
                try FileManager.default.removeItem(at: url)
                await expectFailure { _ = try await f.store.insertVoice(candidate) }
                candidate = try await f.store.prepareVoiceInsertion(voice.id, draft: draft)
                try original.write(to: url)
                await expectFailure { _ = try await f.store.insertVoice(candidate) }
                candidate = try await f.store.prepareVoiceInsertion(voice.id, draft: draft)
                XCTAssertEqual(try Data(contentsOf: url), original)
            }
            _ = try await f.store.insertVoice(candidate)
            let merged = try await f.store.open(f.day).draft!
            _ = try await f.store.commit(merged)
            let saved = try await f.store.exportMarkdown(f.day)
            XCTAssertTrue(String(decoding: saved, as: UTF8.self).contains(candidate.preview))
        }
    }

    func testLoopFourIntegrityWarningsSurviveProposalHistoryInsertionAndOrdinaryExport() async throws {
        for known in [true, false] {
            let f = try VoiceFixture(); defer { f.cleanup() }
            let (draft, voice) = try await f.seed()
            let id = UUID(), url = try await f.store.reserveVoiceSegment(voice.id, id: id, captureExpected: true)
            try Data("integrity synthetic original".utf8).write(to: url)
            var evidence = VoiceCaptureEvidence(firstFrame: 0, frameCount: 128, sampleRate: 48000)
            evidence.rejectedFrames = 19; evidence.unassignedFrames = 7
            try await f.store.finalizeVoiceSegment(voice.id, id: id, duration: 1, evidence: known ? evidence : nil)
            let candidate = try await f.store.prepareVoiceInsertion(voice.id, draft: draft)
            XCTAssertTrue(candidate.preview.contains(known ? "拒收 19 帧" : "采集证据不可用"))
            XCTAssertTrue(candidate.preview.contains(known ? "未写入 7 帧" : "可播放不代表全部输入"))
            let inventory = candidate.inventory!, media = candidate.media
            for index in 0..<5 {
                var proposal = VoiceTranscript(id: UUID(), media: media, text: String(repeating: "history \(index) ", count: 30000))
                proposal.inventory = inventory
                try await f.store.retainVoiceTranscript(voice.id, proposal: proposal)
            }
            let page = try await f.store.voiceTranscriptHistory(voice.id)
            let historical = try await f.store.voiceTranscript(voice.id, version: page.references[0].id)
            XCTAssertEqual(historical.inventory, inventory)
            XCTAssertTrue(VoiceSourceNotices.text(historical.inventory!).contains("录音完整性警告"))
            _ = try await f.store.insertVoice(candidate)
            _ = try await f.store.commit(f.store.open(f.day).draft!)
            let bytes = try await f.store.exportMarkdown(f.day)
            XCTAssertTrue(String(decoding: bytes, as: UTF8.self).contains(candidate.preview))
        }
    }

    func testLoopFourCorrectionCleanupFailureDoesNotUndoVerifiedCurrent() async throws {
        for stage in [FileStage.beforeRemove, .afterRemove] {
            let f = try VoiceFixture(); defer { f.cleanup() }
            let (draft, voice) = try await f.seed()
            let text = VoiceCorrection(text: "Durable newest correction")
            f.faults.arm(.voiceCorrection, stage)
            try await f.store.retainVoiceCorrection(voice.id, correction: text)
            let retained = try await f.store.recording(voice.id)
            XCTAssertEqual(retained.correctedText, text.text)
            f.faults.arm(.voiceCorrection, stage)
            try await f.store.checkpointVoiceCorrection(voice.id)
            let reopened = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"))
            let candidate = try await reopened.prepareVoiceInsertion(voice.id, draft: draft)
            XCTAssertEqual(candidate.correction, text)
            try await reopened.retryVoiceCorrectionCleanup(voice.id)
            let pending = try await reopened.voiceCorrectionCleanupPending(voice.id)
            XCTAssertFalse(pending)
            let checkpoint = try await reopened.voiceCorrection(voice.id, version: text.id)
            XCTAssertEqual(checkpoint, text)
        }
    }

    func testLoopFourColdIndexBatchesResumeWithBudgetsAndExactLaterOriginal() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, seed) = try await f.seed()
        for _ in 0..<24 {
            var value = try await f.store.createRecording(id: UUID(), target: seed.target)
            value.corrections = (0..<3).map { _ in VoiceCorrection(text: String(repeating: "a", count: 490000)) }
            try JSONEncoder().encode(value).write(to: f.root.appendingPathComponent("State/voice/\(value.id).json"))
        }
        let first = try await f.store.voiceLibraryBatch()
        XCTAssertGreaterThan(first.rebuildingCount, 0); XCTAssertLessThanOrEqual(first.scannedManifestBytes, 4 * 1024 * 1024)
        XCTAssertLessThanOrEqual(first.scannedManifestCount, 32); XCTAssertNil(first.nextCursor)
        let reopened = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"))
        var page = try await reopened.voiceLibraryBatch(), previous = first.rebuildingCount
        while page.rebuildingCount > 0 {
            XCTAssertLessThan(page.rebuildingCount, previous)
            XCTAssertLessThanOrEqual(page.scannedManifestBytes, 4 * 1024 * 1024)
            previous = page.rebuildingCount; page = try await reopened.voiceLibraryBatch()
        }
        let next = try await reopened.voiceLibraryBatch(after: page.nextCursor)
        XCTAssertTrue(next.recordings.contains { $0.id == seed.id })
        XCTAssertTrue(Set(page.recordings.map(\.id)).isDisjoint(with: next.recordings.map(\.id)))
        let audio = try await reopened.exportVoiceAudio(seed.id, segmentID: seed.segments[0].id)
        XCTAssertEqual(audio, Data("synthetic controlled media".utf8))
        let fresh = try await reopened.prepareRecording(id: UUID(), target: seed.target)
        let cold = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"))
        let reused = try await cold.prepareRecording(id: UUID(), target: seed.target)
        XCTAssertEqual(reused.id, fresh.id)
    }
}

extension VoiceStoreTests {
    func testLoopFourFullReceiptInventoryRejectsAnotherDaysNewInsertionBeforeAnyWrite() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, voice) = try await f.seed()
        let originalCandidate = try await f.store.prepareVoiceInsertion(voice.id, draft: base)
        _ = try await f.store.insertVoice(originalCandidate)
        let merged = try await f.store.open(f.day).draft!
        var receiptDraft = merged.updating(text: merged.text + "\nOrdinary later input", section: merged.section)
        receiptDraft.voiceInsertions! += (0..<9999).map { _ in
            VoiceInsertionStamp(recordingID: UUID(), insertionID: UUID(), payloadHash: String(repeating: "c", count: 64))
        }
        try await f.store.saveDraft(receiptDraft)
        let receipt = try await f.store.commit(receiptDraft)
        let receiptURL = f.root.appendingPathComponent("State/receipts/\(receipt.operationID).json")
        let receiptBytes = try Data(contentsOf: receiptURL)
        let savedBytes = try await f.store.exportMarkdown(f.day)
        let otherDay = try JournalDay("2026-09-13"), snapshot = try await f.store.open(otherDay)
        var draft = try await f.store.makeDraft(for: snapshot, section: base.section)
        draft = draft.updating(text: "Another day's untouched input", section: draft.section)
        try await f.store.saveDraft(draft)
        let target = try await f.store.voiceTarget(draft)
        let newVoice = try await f.store.createRecording(id: UUID(), target: target)
        try await f.store.retainVoiceCorrection(newVoice.id, correction: VoiceCorrection(text: "New manual correction"))
        let candidate = try await f.store.prepareVoiceInsertion(newVoice.id, draft: draft)
        let draftURL = f.root.appendingPathComponent("State/drafts/\(otherDay.value).json"), draftBytes = try Data(contentsOf: draftURL)
        do { _ = try await f.store.insertVoice(candidate); XCTFail("Cross-document stamp overflow wrote an intent") }
        catch { XCTAssertEqual(error as? VoiceError, .limit) }
        XCTAssertEqual(try Data(contentsOf: draftURL), draftBytes)
        XCTAssertEqual(try Data(contentsOf: receiptURL), receiptBytes)
        XCTAssertFalse(FileManager.default.fileExists(atPath: f.root.appendingPathComponent("State/voice-intents/\(otherDay.value).json").path))
        let pending = try await f.store.pendingVoiceInsertion(otherDay)
        XCTAssertNil(pending)
        let other = try await f.store.open(otherDay)
        XCTAssertEqual(other.draft, draft); XCTAssertNil(other.sourceHash)
        let previousRetry = try await f.store.insertVoice(originalCandidate)
        XCTAssertEqual(previousRetry, .alreadyInserted)
        let priorStamp = try await f.store.voiceInsertion(for: voice.id)
        XCTAssertEqual(priorStamp?.insertionID, originalCandidate.id)
        let after = try await f.store.exportMarkdown(f.day)
        XCTAssertEqual(after, savedBytes)
        do { try await f.store.abandonVoiceInsertion(originalCandidate); XCTFail("Applied old candidate abandoned") }
        catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
    }
}

extension VoiceStoreTests {
    func testLoopFiveAdoptedIncompleteOriginSurvivesRepairedAudioEditingSaveAndExport() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, value) = try await f.seed(), id = value.id
        let second = UUID(), url = try await f.store.reserveVoiceSegment(id, id: second)
        try Data("second synthetic original".utf8).write(to: url)
        try await f.store.finalizeVoiceSegment(id, id: second, duration: 1)
        let media = try await f.store.voiceReferences(id)
        try await f.store.skipVoiceTranscript(id, media: media[1])
        var proposal = VoiceTranscript(id: UUID(), media: media, text: "Original e\u{301}\n第一段")
        proposal.skipped = [media[1]]; proposal.inventory = try await f.store.voiceSourceInventory(id)
        try await f.store.retainVoiceTranscript(id, proposal: proposal)
        let origin = try VoiceCorrectionOrigin(recordingID: id, target: value.target, proposal: proposal)
        try await f.store.retrySkippedVoiceTranscript(id, media: media[1])
        try await f.store.retainVoiceSegmentTranscript(id, piece: VoiceSegmentTranscript(media: media[1], text: "Repaired second piece"))
        let exact = proposal.text + "\nManual edit"
        try await f.store.retainVoiceCorrection(id, correction: VoiceCorrection(text: proposal.text, origin: origin))
        try await f.store.retainVoiceCorrection(id, correction: VoiceCorrection(text: exact, origin: origin))
        let cold = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"))
        let retained = try await cold.recording(id)
        XCTAssertEqual(retained.effectiveCorrection.origin, origin)
        XCTAssertEqual(Data(retained.correctedText.utf8), Data(exact.utf8))
        let candidate = try await cold.prepareVoiceInsertion(id, draft: base)
        XCTAssertFalse(candidate.inventory!.contains { $0.skipped == true || !$0.usable })
        XCTAssertTrue(candidate.preview.contains("历史原稿说明：")); XCTAssertTrue(candidate.preview.contains("第 2 段已选择略过"))
        _ = try await cold.insertVoice(candidate)
        let merged = try await cold.open(f.day).draft!
        _ = try await cold.commit(merged)
        let saved = try await cold.exportMarkdown(f.day)
        XCTAssertTrue(String(decoding: saved, as: UTF8.self).contains(candidate.preview))
        let original = try await cold.exportVoiceAudio(id, segmentID: second)
        XCTAssertEqual(original, Data("second synthetic original".utf8))
        XCTAssertFalse(String(decoding: try JSONEncoder().encode(VoiceCorrection(text: "manual")), as: UTF8.self).contains("origin"))
        let other = try await cold.createRecording(id: UUID(), target: value.target)
        do { try await cold.retainVoiceCorrection(other.id, correction: VoiceCorrection(text: exact, origin: origin)); XCTFail("Foreign origin retained") }
        catch { XCTAssertEqual(error as? VoiceError, .invalidTarget) }
    }

    func testLoopFiveUnavailableUnfinishedTailsAreIsolatedAndFreshnessBound() async throws {
        for kind in ["fifo", "symlink", "oversize", "readFailure"] {
            let f = try VoiceFixture(); defer { f.cleanup() }
            let (base, value) = try await f.seed(), segment = UUID()
            let url = try await f.store.reserveVoiceSegment(value.id, id: segment, captureExpected: true)
            let intactURL = try await f.store.verifiedVoiceURL(value.id, segmentID: value.segments[0].id)
            let intact = try Data(contentsOf: intactURL)
            switch kind {
            case "fifo": XCTAssertEqual(mkfifo(url.path, S_IRUSR | S_IWUSR), 0)
            case "symlink": try FileManager.default.createSymbolicLink(at: url, withDestinationURL: intactURL)
            case "oversize":
                FileManager.default.createFile(atPath: url.path, contents: Data())
                let file = try FileHandle(forWritingTo: url); try file.truncate(atOffset: 33 * 1024 * 1024); try file.close()
            default: try Data("unreadable synthetic tail".utf8).write(to: url)
            }
            let started = Date()
            if kind == "readFailure" { f.faults.arm(.audio, .beforeRead) }
            // The injected failure is applied to the tail via a dedicated hook, below; unsafe kinds never block.
            let inventory = try await f.store.voiceSourceInventory(value.id)
            XCTAssertEqual(inventory.count, 2)
            if kind != "readFailure" { XCTAssertTrue(inventory[1].unavailable == true); XCTAssertNotNil(inventory[1].unavailableState) }
            XCTAssertLessThan(Date().timeIntervalSince(started), 2)
            let candidate = try await f.store.prepareVoiceInsertion(value.id, draft: base)
            XCTAssertTrue(candidate.preview.contains("第 2 段尚不可用"))
            XCTAssertEqual(try Data(contentsOf: intactURL), intact)
            let exported = try await f.store.exportVoiceAudio(value.id, segmentID: value.segments[0].id)
            XCTAssertEqual(exported, intact)
            try FileManager.default.removeItem(at: url)
            try Data("replacement regular tail".utf8).write(to: url)
            do { _ = try await f.store.insertVoice(candidate); XCTFail("Changed unavailable tail used an old preview") }
            catch { XCTAssertEqual(error as? VoiceError, .changedMedia) }
            let current = try await f.store.prepareVoiceInsertion(value.id, draft: base)
            _ = try await f.store.insertVoice(current)
            XCTAssertEqual(try Data(contentsOf: intactURL), intact)
        }
    }

    func testLoopFiveRecordingCapacityRejectsNewManifestButRetainsExactIDRetry() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, recording) = try await f.seed()
        let folder = f.root.appendingPathComponent("State/voice"), original = folder.appendingPathComponent("\(recording.id).json")
        let bytes = try Data(contentsOf: original)
        for _ in 0..<9999 { try Data("unreadable retained inventory".utf8).write(to: folder.appendingPathComponent("\(UUID()).json")) }
        let again = try await f.store.createRecording(id: recording.id, target: recording.target)
        XCTAssertEqual(again.id, recording.id)
        let newID = UUID()
        do { _ = try await f.store.createRecording(id: newID, target: recording.target); XCTFail("Created an unreachable 10001st record") }
        catch { XCTAssertEqual(error as? VoiceError, .limit) }
        XCTAssertFalse(FileManager.default.fileExists(atPath: folder.appendingPathComponent("\(newID).json").path))
        XCTAssertEqual(try Data(contentsOf: original), bytes)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil).count, 10000)
    }

    func testLoopFiveOrdinaryReceiptGrowthHasBoundedColdRepairAndHotAuthoritativeLookup() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, voice) = try await f.seed()
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let folder = f.root.appendingPathComponent("State/receipts")
        let receipt = JournalReceipt(operationID: UUID(), payloadHash: String(repeating: "a", count: 64), day: f.day, afterHash: String(repeating: "b", count: 64))
        for index in 0..<10030 {
            let id = UUID(uuidString: String(format: "00000000-0000-0000-0000-%012d", index))!
            var object = try JSONSerialization.jsonObject(with: encoder.encode(receipt)) as! [String: Any]
            object["operationID"] = id.uuidString
            try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]).write(to: folder.appendingPathComponent("\(id).json"))
        }
        let counter = LoopFiveReadCounter()
        let cold = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"), failure: { purpose, stage in if purpose == .voiceEvidence && stage == .beforeRead { counter.increment() } })
        var passes = 0
        while true {
            let before = counter.count
            do { let state = try await cold.voiceInsertion(for: voice.id); XCTAssertNil(state); break }
            catch { XCTAssertEqual(error as? VoiceError, .verifying) }
            XCTAssertLessThanOrEqual(counter.count - before, 64); passes += 1
            XCTAssertLessThan(passes, 200)
            await Task.yield()
        }
        XCTAssertGreaterThan(passes, 100); XCTAssertEqual(counter.count, 10031)
        let hot = counter.count
        try await cold.retainVoiceCorrection(voice.id, correction: VoiceCorrection(text: "New correction with unlimited ordinary saves"))
        let candidate = try await cold.prepareVoiceInsertion(voice.id, draft: base)
        XCTAssertEqual(counter.count, hot)
        _ = try await cold.insertVoice(candidate)
        let merged = try await cold.open(f.day).draft!
        _ = try await cold.commit(merged)
        let stamp = try await cold.voiceInsertion(for: voice.id)
        XCTAssertEqual(stamp?.insertionID, candidate.id)
        let changedURL = folder.appendingPathComponent("00000000-0000-0000-0000-000000010029.json")
        let original = try Data(contentsOf: changedURL)
        try Data("{malformed".utf8).write(to: changedURL)
        do { _ = try await cold.voiceInsertion(for: voice.id); XCTFail("Corrupt ordinary receipt was ignored") }
        catch { XCTAssertEqual(error as? JournalError, .storage) }
        try original.write(to: changedURL)
        let retry = try await cold.insertVoice(candidate)
        XCTAssertEqual(retry, .alreadyInserted)
        let relaunched = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"))
        for attempt in 0..<200 {
            do {
                let found = try await relaunched.voiceInsertion(for: voice.id)
                XCTAssertEqual(found?.insertionID, candidate.id); break
            } catch { XCTAssertEqual(error as? VoiceError, .verifying); XCTAssertLessThan(attempt, 199) }
        }
    }
}

private final class LoopFiveReadCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0
    var count: Int { lock.lock(); defer { lock.unlock() }; return value }
    func increment() { lock.lock(); value += 1; lock.unlock() }
}

extension VoiceStoreTests {
    func testLoopFiveLegacyCorrectionDiscoveryIsResumableBoundedAndReadsSelectedIdentityDirectly() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, recording) = try await f.seed(), id = recording.id
        let folder = f.root.appendingPathComponent("State/voice-corrections/\(id)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        var versions: [VoiceCorrection] = []
        for index in 0..<70 {
            let version = VoiceCorrection(id: UUID(uuidString: String(format: "00000000-0000-0000-0000-%012d", index))!, text: "Old exact \(index) e\u{301}\n" + String(repeating: "x", count: 330000))
            versions.append(version)
            let record = VoiceCorrectionRecord(recordingID: id, target: recording.target, correction: version)
            try encoder.encode(record).write(to: folder.appendingPathComponent("\(version.id).json"))
        }
        let first = try await f.store.voiceCorrectionHistoryBatch(id)
        XCTAssertGreaterThan(first.rebuildingCount, 0); XCTAssertLessThanOrEqual(first.scannedBytes, 4 * 1024 * 1024); XCTAssertLessThanOrEqual(first.scannedCount, 32)
        let later = try await f.store.voiceCorrection(id, version: versions[69].id)
        XCTAssertEqual(Data(later.text.utf8), Data(versions[69].text.utf8))
        let cold = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"))
        var page = try await cold.voiceCorrectionHistoryBatch(id)
        XCTAssertLessThan(page.rebuildingCount, first.rebuildingCount)
        var reads = first.scannedCount + page.scannedCount
        while page.rebuildingCount > 0 {
            page = try await cold.voiceCorrectionHistoryBatch(id)
            reads += page.scannedCount
            XCTAssertLessThanOrEqual(page.scannedBytes, 4 * 1024 * 1024); XCTAssertLessThanOrEqual(page.scannedCount, 32)
        }
        XCTAssertEqual(reads, 70)
        var all = page.references
        while let next = page.nextOffset { page = try await cold.voiceCorrectionHistoryBatch(id, offset: next); all += page.references }
        XCTAssertEqual(Set(all.map(\.id)), Set(versions.map(\.id)))
        let badURL = folder.appendingPathComponent("\(versions[65].id).json")
        try Data("corrupt retained historical entry".utf8).write(to: badURL)
        page = try await cold.voiceCorrectionHistoryBatch(id)
        XCTAssertEqual(page.unavailableCount, 1)
        let unchanged = try await cold.recording(id)
        XCTAssertEqual(unchanged.correctedText, recording.correctedText)
        let audio = try await cold.exportVoiceAudio(id, segmentID: recording.segments[0].id)
        XCTAssertEqual(audio, Data("synthetic controlled media".utf8))
        f.faults.arm(.voiceHistory, .beforeRead)
        let orphan = VoiceCorrection(text: "New old orphan")
        try encoder.encode(VoiceCorrectionRecord(recordingID: id, target: recording.target, correction: orphan)).write(to: folder.appendingPathComponent("\(orphan.id).json"))
        do { _ = try await f.store.voiceCorrectionHistoryBatch(id); XCTFail("Injected history read failure ignored") }
        catch { XCTAssertEqual(error as? JournalError, .storage) }
        let retry = try await f.store.voiceCorrectionHistory(id)
        XCTAssertNotNil(retry.references.first)
        let exactOrphan = try await f.store.voiceCorrection(id, version: orphan.id)
        XCTAssertEqual(exactOrphan, orphan)
    }

    func testLoopFiveDerivedAudioOwnershipRecoveryRevocationAndRemovalFailures() async throws {
        for stage in [FileStage.beforeRemove, .afterRemove] {
            let f = try VoiceFixture(); defer { f.cleanup() }
            let (_, value) = try await f.seed(), cache = f.store.derivedVoiceCache
            let bytes = Data("synthetic derived original bytes".utf8)
            let lease = try cache.snapshot(data: bytes, recordingID: value.id, segmentID: value.segments[0].id, fileExtension: "wav")
            let other = try cache.snapshot(data: bytes, recordingID: UUID(), segmentID: UUID(), fileExtension: "wav")
            try cache.recoverStale()
            XCTAssertEqual(try Data(contentsOf: lease.url), bytes); XCTAssertEqual(try Data(contentsOf: other.url), bytes)
            try await f.store.deleteVoiceAudio(value.id)
            let pending = try await f.store.voiceAudioRemovalComplete(value.id)
            XCTAssertFalse(pending)
            XCTAssertEqual(try Data(contentsOf: lease.url), bytes)
            do { _ = try cache.snapshot(data: bytes, recordingID: value.id, segmentID: UUID(), fileExtension: "wav"); XCTFail("Revoked source created a late copy") }
            catch { XCTAssertEqual(error as? VoiceError, .deleted) }
            f.faults.arm(.voiceDerived, stage)
            do { try lease.finish(); XCTFail("Removal failure was hidden") } catch { XCTAssertEqual(error as? JournalError, .storage) }
            if stage == .beforeRemove {
                let incomplete = try await f.store.voiceAudioRemovalComplete(value.id)
                XCTAssertFalse(incomplete); XCTAssertTrue(try cache.maintenancePending())
            }
            let reopened = VoiceDerivedAudioCache(directory: cache.directory)
            try reopened.recoverStale()
            XCTAssertTrue(try reopened.removalComplete(value.id))
            XCTAssertEqual(try Data(contentsOf: other.url), bytes)
            try other.finish()
        }
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, value) = try await f.seed(), cache = f.store.derivedVoiceCache
        let otherNamespace = VoiceDerivedAudioCache(directory: f.root.appendingPathComponent("Sample/derived"))
        let sample = try otherNamespace.snapshot(data: Data("sample".utf8), recordingID: value.id, segmentID: UUID(), fileExtension: "wav")
        let orphanFolder = cache.directory.appendingPathComponent(value.id.uuidString)
        try FileManager.default.createDirectory(at: orphanFolder, withIntermediateDirectories: true)
        let orphan = orphanFolder.appendingPathComponent("\(UUID())-\(SourceHash.digest(Data("orphan".utf8)))-\(UUID()).wav")
        try Data("orphan".utf8).write(to: orphan)
        let unrelated = orphanFolder.appendingPathComponent("user-export.wav")
        try Data("keep export".utf8).write(to: unrelated)
        // A simulated process boundary has no live lease for the exact owned orphan filename.
        let cold = VoiceDerivedAudioCache(directory: cache.directory)
        try cold.recoverStale()
        XCTAssertFalse(FileManager.default.fileExists(atPath: orphan.path))
        XCTAssertEqual(try Data(contentsOf: unrelated), Data("keep export".utf8))
        XCTAssertEqual(try Data(contentsOf: sample.url), Data("sample".utf8))
        let unsafe = orphanFolder.appendingPathComponent("\(UUID())-\(String(repeating: "a", count: 64))-\(UUID()).wav")
        try FileManager.default.createSymbolicLink(at: unsafe, withDestinationURL: sample.url)
        do { try cold.recoverStale(); XCTFail("Derived cleanup followed a symlink") } catch { XCTAssertEqual(error as? JournalError, .storage) }
        XCTAssertEqual(try Data(contentsOf: sample.url), Data("sample".utf8))
        try sample.finish()
    }
}

extension VoiceStoreTests {
    func testLoopFivePieceOriginRetainsImmutableIntegrityAndRejectsOriginOnlyTampering() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, value) = try await f.seed()
        let segment = value.segments[0]
        var evidence = VoiceCaptureEvidence(firstFrame: 0, frameCount: 10, sampleRate: 48000, streamID: UUID())
        evidence.rejectedFrames = 17
        try await f.store.finalizeVoiceSegment(value.id, id: segment.id, duration: 2.5, evidence: evidence)
        let media = try await f.store.voiceReferences(value.id)[0]
        let piece = VoiceSegmentTranscript(media: media, text: "Immutable piece e\u{301}")
        try await f.store.retainVoiceSegmentTranscript(value.id, piece: piece)
        let origin = try await f.store.voicePieceCorrectionOrigin(value.id, piece: piece)
        XCTAssertTrue(origin.notice.contains("拒收 17 帧"))
        let correction = VoiceCorrection(text: piece.text, origin: origin)
        try await f.store.retainVoiceCorrection(value.id, correction: correction)
        let candidate = try await f.store.prepareVoiceInsertion(value.id, draft: base)
        XCTAssertTrue(candidate.preview.contains("校正文字的历史来源")); XCTAssertTrue(candidate.preview.contains("拒收 17 帧"))
        let current = try await f.store.voiceManifest(value.id).currentCorrectionReference!
        let url = f.root.appendingPathComponent("State/voice-correction-drafts/\(value.id)/\(current.id).json")
        let bytes = try Data(contentsOf: url)
        var object = try JSONSerialization.jsonObject(with: bytes) as! [String: Any]
        var text = object["correction"] as! [String: Any]
        text.removeValue(forKey: "origin"); object["correction"] = text
        try JSONSerialization.data(withJSONObject: object).write(to: url)
        let damaged = try await f.store.recording(value.id)
        XCTAssertTrue(damaged.currentCorrectionUnavailable == true)
        do { _ = try await f.store.insertVoice(candidate); XCTFail("Origin-only mutation was ignored") }
        catch { XCTAssertEqual(error as? VoiceError, .missing) }
        try bytes.write(to: url)
        _ = try await f.store.insertVoice(candidate)
    }

    func testLoopFiveUnfinishedReadFailureKeepsIntactAudioAndInvalidatesOnRestoration() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (base, recording) = try await f.seed(), segment = UUID()
        let url = try await f.store.reserveVoiceSegment(recording.id, id: segment)
        let original = Data("retained tail original".utf8); try original.write(to: url)
        let fault = LoopFiveNthReadFault()
        let store = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"), failure: { if $0 == .audio && $1 == .beforeRead { try fault.check() } })
        fault.arm(skip: 1)
        let inventory = try await store.voiceSourceInventory(recording.id)
        XCTAssertTrue(inventory[0].usable); XCTAssertTrue(inventory[1].unavailable == true)
        XCTAssertEqual(inventory[1].segmentID, segment); XCTAssertNotNil(inventory[1].unavailableState)
        fault.arm(skip: 2)
        let candidate = try await store.prepareVoiceInsertion(recording.id, draft: base)
        XCTAssertTrue(candidate.inventory![1].unavailable == true)
        do { _ = try await store.insertVoice(candidate); XCTFail("Restored readability accepted an unavailable observation") }
        catch { XCTAssertEqual(error as? VoiceError, .changedMedia) }
        XCTAssertEqual(try Data(contentsOf: url), original)
        let draft = try await store.open(f.day).draft
        XCTAssertEqual(draft, base)
    }

    func testLoopFiveHistoryMembershipChangeInvalidatesPageBeforeSkippingAnyVersion() async throws {
        let f = try VoiceFixture(); defer { f.cleanup() }
        let (_, value) = try await f.seed()
        for index in 0..<25 {
            try await f.store.retainVoiceCorrection(value.id, correction: VoiceCorrection(text: "Checkpoint \(index)"))
            try await f.store.checkpointVoiceCorrection(value.id)
        }
        let first = try await f.store.voiceCorrectionHistoryBatch(value.id)
        XCTAssertNotNil(first.nextOffset)
        try await f.store.retainVoiceCorrection(value.id, correction: VoiceCorrection(text: "Another checkpoint"))
        try await f.store.checkpointVoiceCorrection(value.id)
        do { _ = try await f.store.voiceCorrectionHistoryBatch(value.id, offset: first.nextOffset!, snapshot: first.snapshot); XCTFail("Changed history silently shifted page") }
        catch { XCTAssertEqual(error as? JournalError, .changedSource) }
        let refreshed = try await f.store.voiceCorrectionHistoryBatch(value.id)
        XCTAssertNotEqual(first.snapshot, refreshed.snapshot)
        let second = try await f.store.voiceCorrectionHistoryBatch(value.id, offset: refreshed.nextOffset!, snapshot: refreshed.snapshot)
        XCTAssertEqual(first.references.count + second.references.count, 26)
    }

    func testLoopFiveDerivedSnapshotWriteFailuresLeaveRecoverableOwnedFilesOnly() async throws {
        for stage in [FileStage.beforeWrite, .beforeReplace, .afterReplace] {
            let f = try VoiceFixture(); defer { f.cleanup() }
            let (_, voice) = try await f.seed(), cache = f.store.derivedVoiceCache
            f.faults.arm(.voiceDerived, stage)
            do { _ = try cache.snapshot(data: Data("Synthetic snapshot".utf8), recordingID: voice.id, segmentID: UUID(), fileExtension: "wav"); XCTFail("Snapshot failure hidden") }
            catch { XCTAssertEqual(error as? JournalError, .storage) }
            XCTAssertEqual(try cache.maintenancePending(), stage == .afterReplace)
            try cache.recoverStale(); XCTAssertTrue(try cache.removalComplete(voice.id))
            let original = try await f.store.exportVoiceAudio(voice.id, segmentID: voice.segments[0].id)
            XCTAssertEqual(original, Data("synthetic controlled media".utf8))
        }
    }
}

private final class LoopFiveNthReadFault: @unchecked Sendable {
    private let lock = NSLock()
    private var skip: Int?
    func arm(skip: Int) { lock.lock(); self.skip = skip; lock.unlock() }
    func check() throws {
        lock.lock(); defer { lock.unlock() }
        guard let value = skip else { return }
        if value == 0 { skip = nil; throw JournalError.storage }
        skip = value - 1
    }
}


extension VoiceStoreTests {
    func testLoopFiveVerifiedCurrentOriginStaysIndependentOfMissingOrCorruptHistoricalSource() async throws {
        for pieceSource in [false, true] {
            for missing in [false, true] {
                let f = try VoiceFixture(); defer { f.cleanup() }
                let (base, recording) = try await f.seed(), id = recording.id
                let media = try await f.store.voiceReferences(id)
                let originalText = "Retained original e\u{301}\n原文"
                let origin: VoiceCorrectionOrigin
                if pieceSource {
                    let piece = VoiceSegmentTranscript(media: media[0], text: originalText)
                    try await f.store.retainVoiceSegmentTranscript(id, piece: piece)
                    origin = try await f.store.voicePieceCorrectionOrigin(id, piece: piece)
                } else {
                    var proposal = VoiceTranscript(id: UUID(), media: media, text: originalText)
                    proposal.inventory = try await f.store.voiceSourceInventory(id)
                    try await f.store.retainVoiceTranscript(id, proposal: proposal)
                    origin = try VoiceCorrectionOrigin(recordingID: id, target: recording.target, proposal: proposal)
                }
                let adopted = VoiceCorrection(text: originalText, origin: origin)
                try await f.store.retainVoiceCorrection(id, correction: adopted, adoptingSource: true)
                try await f.store.checkpointVoiceCorrection(id)
                let kind = pieceSource ? "pieces" : "proposals"
                let sourceURL = f.root.appendingPathComponent("State/voice-transcripts/\(id)/\(kind)/\(origin.versionID).json")
                let originalBytes = try Data(contentsOf: sourceURL)
                if missing { try FileManager.default.removeItem(at: sourceURL) }
                else { try Data("damaged historical sidecar".utf8).write(to: sourceURL) }
                let cold = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"))
                let retained = try await cold.recording(id)
                XCTAssertNotEqual(retained.currentCorrectionUnavailable, true)
                XCTAssertEqual(retained.effectiveCorrection, adopted)
                try await cold.retainVoiceCorrection(id, correction: adopted, adoptingSource: true)
                let edited = VoiceCorrection(text: originalText + "\nManual continuation", origin: origin)
                try await cold.retainVoiceCorrection(id, correction: edited)
                try await cold.checkpointVoiceCorrection(id)
                let notice = try await cold.voiceCorrectionOriginNotice(id, origin: origin)
                XCTAssertTrue(notice!.contains(VoiceCorrectionOrigin.unavailableNotice))
                do {
                    try await cold.retainVoiceCorrection(id, correction: VoiceCorrection(text: originalText, origin: origin), adoptingSource: true)
                    XCTFail("A new adoption accepted an unreadable original")
                } catch { XCTAssertTrue(error is VoiceError || error is JournalError) }
                var changed = VoiceTranscript(id: origin.versionID, media: media, text: "Invented different source")
                changed.inventory = origin.inventory
                let forged = try VoiceCorrectionOrigin(recordingID: id, target: recording.target, proposal: changed)
                do {
                    try await cold.retainVoiceCorrection(id, correction: VoiceCorrection(text: edited.text, origin: forged))
                    XCTFail("Changed origin was trusted as current provenance")
                } catch { XCTAssertTrue(error is VoiceError || error is JournalError) }
                let current = try await cold.recording(id)
                XCTAssertEqual(current.effectiveCorrection, edited)
                let preview = try await cold.prepareVoiceInsertion(id, draft: base)
                XCTAssertTrue(preview.preview.contains(VoiceCorrectionOrigin.unavailableNotice))
                try originalBytes.write(to: sourceURL)
                do { _ = try await cold.insertVoice(preview); XCTFail("Restored historical source left stale preview valid") }
                catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
                let restored = try await cold.prepareVoiceInsertion(id, draft: base)
                XCTAssertFalse(restored.preview.contains(VoiceCorrectionOrigin.unavailableNotice))
                if missing { try FileManager.default.removeItem(at: sourceURL) }
                else { try Data("damaged historical sidecar".utf8).write(to: sourceURL) }
                let final = try await cold.prepareVoiceInsertion(id, draft: base)
                _ = try await cold.insertVoice(final)
                let draft = try await cold.open(f.day).draft!
                _ = try await cold.commit(draft)
                let exported = try await cold.exportMarkdown(f.day)
                XCTAssertTrue(String(decoding: exported, as: UTF8.self).contains(final.preview))
                XCTAssertTrue(final.preview.hasPrefix(edited.text))
                let audio = try await cold.exportVoiceAudio(id, segmentID: media[0].segmentID)
                XCTAssertEqual(audio, Data("synthetic controlled media".utf8))
            }
        }
    }
}
