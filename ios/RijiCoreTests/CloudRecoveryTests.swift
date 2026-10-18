import XCTest
@testable import RijiCore

private final class TestCloudVersions: CloudVersionProvider, @unchecked Sendable {
    var values: [CloudVersion] = [CloudVersion(id: "other", content: Data("---\r\nproperty: other\r\n---\r\nOther 🧠 e\u{301}\r\n".utf8))]
    var complete = true
    var flagOnly = false
    var failAfterFirstMark = false
    var marked: [String] = []
    var contentReads = 0
    func displayInventory(at url: URL) throws -> CloudVersionInventory {
        CloudVersionInventory(versions: values.map { CloudVersion(id: $0.id, content: nil) }, hasConflict: flagOnly || !values.isEmpty, complete: complete && (!flagOnly || !values.isEmpty))
    }
    func inventory(at url: URL) throws -> CloudVersionInventory {
        contentReads += 1
        return CloudVersionInventory(versions: values, hasConflict: flagOnly || !values.isEmpty, complete: complete && (!flagOnly || !values.isEmpty))
    }
    func resolve(_ reviewed: [CloudVersion], at url: URL) throws {
        guard values == reviewed else { throw CloudRecoveryError.stale }
        for value in reviewed {
            marked.append(value.id); values.removeAll { $0.id == value.id }
            if failAfterFirstMark { failAfterFirstMark = false; throw CloudRecoveryError.pending }
        }
    }
}

final class CloudRecoveryTests: XCTestCase {
    private let day = try! JournalDay("2026-09-19")
    private let before = Data("---\r\nproperty: current\r\n---\r\nCurrent 🧠\r\n".utf8)
    private struct Fixture {
        let base: URL, root: URL, source: URL
        let access: OriginalVaultAccess
        let store: LocalJournalStore
    }
    private func fixture(_ versions: TestCloudVersions, failure: @escaping FileFailureHook = { _, _ in }) throws -> Fixture {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("CloudSynthetic-\(UUID())")
        let root = base.appendingPathComponent("riji"), source = root.appendingPathComponent("daily/\(day.value).md")
        for folder in ["daily", "templates"] { try FileManager.default.createDirectory(at: root.appendingPathComponent(folder), withIntermediateDirectories: true) }
        try Data("# Template\n".utf8).write(to: root.appendingPathComponent("templates/daily.md"))
        try before.write(to: source)
        let access = try OriginalVaultAccess(root: root, writePolicy: .syntheticFixture, failure: failure, versions: versions)
        let store = try LocalJournalStore(journalDirectory: base.appendingPathComponent("cache"), stateDirectory: base.appendingPathComponent("state"), failure: failure, original: access)
        return Fixture(base: base, root: root, source: source, access: access, store: store)
    }
    private func reopen(_ fixture: Fixture, _ versions: TestCloudVersions) throws -> LocalJournalStore {
        try LocalJournalStore(journalDirectory: fixture.base.appendingPathComponent("cache"), stateDirectory: fixture.base.appendingPathComponent("state"),
                              original: OriginalVaultAccess(root: fixture.root, writePolicy: .syntheticFixture, versions: versions))
    }
    private func rejected(_ operation: () async throws -> Void, file: StaticString = #filePath, line: UInt = #line) async {
        do { try await operation(); XCTFail("Expected rejection", file: file, line: line) } catch { }
    }

    func testUntouchedReviewRetiresAfterExternalResolutionWithoutRewritingCurrentOrVersions() async throws {
        let versions = TestCloudVersions(), f = try fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        let draft = try await f.store.reviewCloudVersions(day)
        versions.values = []
        let latest = before + Data("External arrived\r\n".utf8); try latest.write(to: f.source)
        let opened = try await f.store.open(day), pending = try await f.store.cloudWorkPending(day)
        XCTAssertEqual(Data(opened.markdown.utf8), latest); XCTAssertFalse(pending)
        XCTAssertEqual(try Data(contentsOf: f.source), latest); XCTAssertTrue(versions.marked.isEmpty)
        let backups = try await f.store.cloudVersionBackups(day)
        XCTAssertTrue(backups.contains { $0 == draft })
        let fresh = try await f.store.prepareCloudReview(day)
        XCTAssertEqual(fresh.draft.content, latest)
    }

    func testEditedProposalRemainsPendingWhenExternalConflictDisappears() async throws {
        let versions = TestCloudVersions(), f = try fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        let draft = try await f.store.reviewCloudVersions(day), manual = before + Data("Unconfirmed manual merge\n".utf8)
        try await f.store.retainCloudDraft(CloudRecoveryDraft(review: draft.review, content: manual))
        versions.values = []; try Data("External chosen\n".utf8).write(to: f.source)
        _ = try await f.store.open(day)
        let pending = try await f.store.cloudWorkPending(day), fresh = try await f.store.prepareCloudReview(day)
        XCTAssertTrue(pending); XCTAssertEqual(fresh.draft.content, manual)
        XCTAssertEqual(try Data(contentsOf: f.source), Data("External chosen\n".utf8)); XCTAssertTrue(versions.marked.isEmpty)
    }

    func testAlreadyAppliedConfirmedResultFinishesBookkeepingOnOpen() async throws {
        let versions = TestCloudVersions(), f = try fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        let draft = try await f.store.reviewCloudVersions(day), chosen = versions.values[0].content!
        versions.failAfterFirstMark = true
        await rejected { try await f.store.confirmCloudResolution(CloudRecoveryDraft(review: draft.review, content: chosen)) }
        let wasPending = try await f.store.cloudConfirmationPending(day); XCTAssertTrue(wasPending)
        let reopened = try reopen(f, versions), opened = try await reopened.open(day)
        let pending = try await reopened.cloudWorkPending(day), confirming = try await reopened.cloudConfirmationPending(day)
        XCTAssertFalse(pending); XCTAssertFalse(confirming); XCTAssertEqual(Data(opened.markdown.utf8), chosen)
        XCTAssertEqual(try Data(contentsOf: f.source), chosen); XCTAssertEqual(versions.marked, ["other"])
    }

    func testUncertainConfirmationNeverReplaysWhenCurrentChangedOrNewConflictAppears() async throws {
        for scenario in ["changed", "new-version", "unreadable-inventory", "missing-backup"] {
            let versions = TestCloudVersions(), f = try fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
            let draft = try await f.store.reviewCloudVersions(day), chosen = versions.values[0].content!
            versions.failAfterFirstMark = true
            await rejected { try await f.store.confirmCloudResolution(CloudRecoveryDraft(review: draft.review, content: chosen)) }
            if scenario == "changed" { try before.write(to: f.source) }
            if scenario == "new-version" { versions.values = [CloudVersion(id: "new", content: Data("New conflict\n".utf8))] }
            if scenario == "unreadable-inventory" { versions.complete = false }
            if scenario == "missing-backup" {
                let evidence = await f.store.state.appendingPathComponent("cloud-evidence")
                try FileManager.default.removeItem(at: evidence)
            }
            let source = try Data(contentsOf: f.source), remaining = versions.values, reopened = try reopen(f, versions)
            _ = try await reopened.open(day)
            let pending = try await reopened.cloudWorkPending(day); XCTAssertTrue(pending, scenario)
            XCTAssertEqual(try Data(contentsOf: f.source), source); XCTAssertEqual(versions.values, remaining)
        }
    }

    func testAutomaticReviewCleanupFailureKeepsRecoveryAndSourceReadable() async throws {
        for purpose in [FilePurpose.cloudBackup, .cloudConfirmation] {
            let versions = TestCloudVersions(), f = try fixture(versions, failure: { current, stage in
                if current == purpose && stage == .beforeWrite && versions.values.isEmpty { throw JournalError.storage }
            }); defer { try? FileManager.default.removeItem(at: f.base) }
            let draft = try await f.store.reviewCloudVersions(day); versions.values = []
            let opened = try await f.store.open(day), pending = try await f.store.cloudWorkPending(day)
            XCTAssertEqual(Data(opened.markdown.utf8), before); XCTAssertTrue(pending)
            let retained = try await f.store.retainedCloudDraft(day); XCTAssertEqual(retained, draft)
            XCTAssertEqual(try Data(contentsOf: f.source), before); XCTAssertTrue(versions.marked.isEmpty)
        }
    }

    func testAutomaticCompletionPreservesNewerManualProposalAndIndependentBodyHold() async throws {
        let versions = TestCloudVersions(), f = try fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        let body = BodyEdit(day: day, before: before, content: before + Data("Body draft\n".utf8), sourceID: try f.access.sourceID(day))
        try await f.store.retainBodyEdit(body)
        let draft = try await f.store.reviewCloudVersions(day), chosen = versions.values[0].content!
        versions.failAfterFirstMark = true
        await rejected { try await f.store.confirmCloudResolution(CloudRecoveryDraft(review: draft.review, content: chosen)) }
        let manual = before + Data("Newer unconfirmed proposal\n".utf8)
        try await f.store.retainCloudDraft(CloudRecoveryDraft(review: draft.review, content: manual))
        let reopened = try reopen(f, versions); _ = try await reopened.open(day)
        let confirming = try await reopened.cloudConfirmationPending(day), pending = try await reopened.cloudWorkPending(day)
        let retained = try await reopened.retainedCloudDraft(day), held = try await reopened.bodyHeldByCloud(day)
        XCTAssertFalse(confirming); XCTAssertTrue(pending); XCTAssertTrue(held); XCTAssertEqual(retained?.content, manual)
        let originalBody = try await reopened.retainedBodyEdit(day); XCTAssertEqual(originalBody, body)
        XCTAssertEqual(try Data(contentsOf: f.source), chosen)
    }

    func testColdDisplayAndExportRemainAvailableWhileOrdinaryReadAndWriteStayStrict() async throws {
        let versions = TestCloudVersions(), f = try fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        let snapshot = try await f.store.open(day), exported = try await f.store.exportMarkdown(day)
        XCTAssertEqual(Data(snapshot.markdown.utf8), before); XCTAssertNotNil(snapshot.cloudConflict); XCTAssertEqual(exported, before)
        XCTAssertThrowsError(try f.access.read(day))
        XCTAssertEqual(versions.contentReads, 0, "Display and strict conflict rejection never read alternate contents")
        let edit = BodyEdit(day: day, before: before, content: before + Data("Manual\n".utf8), sourceID: try f.access.sourceID(day))
        await rejected { try await f.store.commitBodyEdit(edit) }
        XCTAssertEqual(try Data(contentsOf: f.source), before); XCTAssertTrue(versions.marked.isEmpty)
    }

    func testWholeVersionChoicePreservesExactMetadataBytesAndDurableBackupsAcrossReopen() async throws {
        let versions = TestCloudVersions(), f = try fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        let review = try await f.store.reviewCloudVersions(day), selected = versions.values[0].content!
        XCTAssertThrowsError(try BodyMarkdown.validatePreservation(before: before, after: selected))
        try await f.store.confirmCloudResolution(CloudRecoveryDraft(review: review.review, content: selected))
        let reopened = try reopen(f, versions), snapshot = try await reopened.open(day), backups = try await reopened.cloudVersionBackups(day)
        XCTAssertEqual(Data(snapshot.markdown.utf8), selected); XCTAssertNil(snapshot.cloudConflict)
        XCTAssertTrue(backups.contains { $0.review.current == before && $0.content == selected && $0.review.versions[0].content == selected })
        XCTAssertEqual(versions.marked, ["other"])
        try await reopened.retryCloudResolution(day)
        XCTAssertEqual(versions.marked, ["other"])
    }

    func testUnavailableVariantAndFlagWithoutMetadataAllowReadingButNeverResolution() async throws {
        for flagOnly in [false, true] {
            let versions = TestCloudVersions()
            versions.flagOnly = flagOnly
            versions.values = flagOnly ? [] : [CloudVersion(id: "remote", content: nil)]
            let f = try fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
            let review = try await f.store.reviewCloudVersions(day)
            XCTAssertFalse(review.review.canResolve)
            let snapshot = try await f.store.open(day); XCTAssertEqual(Data(snapshot.markdown.utf8), before)
            await rejected { try await f.store.confirmCloudResolution(review) }
            XCTAssertEqual(try Data(contentsOf: f.source), before)
            XCTAssertTrue(versions.marked.isEmpty)
        }
    }

    func testStaleBytesIdentityOrVersionSetRejectWithoutLosingMerge() async throws {
        for change in ["bytes", "identity", "new-version", "changed-version", "missing-version"] {
            let versions = TestCloudVersions(), f = try fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
            let initial = try await f.store.reviewCloudVersions(day)
            let draft = CloudRecoveryDraft(review: initial.review, content: Data("Handwritten full source\n".utf8))
            try await f.store.retainCloudDraft(draft)
            if change == "bytes" { try Data("Later external\n".utf8).write(to: f.source) }
            if change == "identity" {
                try FileManager.default.moveItem(at: f.root, to: f.base.appendingPathComponent("moved"))
                try FileManager.default.copyItem(at: f.base.appendingPathComponent("moved"), to: f.root)
            }
            if change == "new-version" { versions.values.append(CloudVersion(id: "new", content: before)) }
            if change == "changed-version" { versions.values = [CloudVersion(id: "other", content: before)] }
            if change == "missing-version" { versions.values = [] }
            let unchanged = try Data(contentsOf: f.source)
            await rejected { try await f.store.confirmCloudResolution(draft) }
            let retained = try await f.store.retainedCloudDraft(day)
            XCTAssertEqual(retained, draft); XCTAssertEqual(try Data(contentsOf: f.source), unchanged); XCTAssertTrue(versions.marked.isEmpty)
        }
    }

    func testRefreshArchivesPriorEvidenceAndRetainsHandwrittenProposal() async throws {
        let versions = TestCloudVersions(), f = try fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        let first = try await f.store.reviewCloudVersions(day)
        let hand = Data("Manual merge 🧠\r\n".utf8)
        try await f.store.retainCloudDraft(CloudRecoveryDraft(review: first.review, content: hand))
        versions.values.append(CloudVersion(id: "late", content: before))
        let refreshed = try await f.store.reviewCloudVersions(day), backups = try await f.store.cloudVersionBackups(day)
        XCTAssertEqual(refreshed.content, hand); XCTAssertEqual(refreshed.review.versions.count, 2)
        XCTAssertTrue(backups.contains { $0.review == first.review && $0.content == hand })
        XCTAssertEqual(try Data(contentsOf: f.source), before)
    }

    func testBackupAndConfirmationFailuresBeforeMutationLeaveEverySourceVersionIntact() async throws {
        for purpose in [FilePurpose.cloudBackup, .cloudConfirmation] {
            for stage in [FileStage.beforeWrite, .afterReplace] {
                let versions = TestCloudVersions(), f = try fixture(versions, failure: { candidate, at in
                    if candidate == purpose && at == stage { throw JournalError.storage }
                }); defer { try? FileManager.default.removeItem(at: f.base) }
                let review = try await f.store.reviewCloudVersions(day)
                await rejected { try await f.store.confirmCloudResolution(CloudRecoveryDraft(review: review.review, content: versions.values[0].content!)) }
                XCTAssertEqual(try Data(contentsOf: f.source), before); XCTAssertTrue(versions.marked.isEmpty)
            }
        }
    }

    func testWriteInterruptionRetriesFromDurableEvidenceWithoutOverwritingLaterEdits() async throws {
        for laterEdit in [false, true] {
            let versions = TestCloudVersions(), f = try fixture(versions, failure: { purpose, stage in
                if purpose == .bodyDiary && stage == .afterReplace { throw JournalError.storage }
            }); defer { try? FileManager.default.removeItem(at: f.base) }
            let review = try await f.store.reviewCloudVersions(day), selected = versions.values[0].content!
            await rejected { try await f.store.confirmCloudResolution(CloudRecoveryDraft(review: review.review, content: selected)) }
            XCTAssertEqual(try Data(contentsOf: f.source), selected)
            let reopened = try reopen(f, versions), later = Data("A later external edit\n".utf8)
            if laterEdit {
                try later.write(to: f.source)
                await rejected { try await reopened.retryCloudResolution(day) }
                XCTAssertEqual(try Data(contentsOf: f.source), later); XCTAssertTrue(versions.marked.isEmpty)
            } else {
                try await reopened.retryCloudResolution(day)
                XCTAssertEqual(try Data(contentsOf: f.source), selected); XCTAssertEqual(versions.marked, ["other"])
            }
        }
    }

    func testPartialExactMarkingCanRetryButAnUnreviewedVersionRemainsUntouched() async throws {
        for newVersion in [false, true] {
            let versions = TestCloudVersions(); versions.values.append(CloudVersion(id: "second", content: before)); versions.failAfterFirstMark = true
            let f = try fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
            let review = try await f.store.reviewCloudVersions(day)
            await rejected { try await f.store.confirmCloudResolution(review) }
            XCTAssertEqual(versions.marked, ["other"])
            let reopened = try reopen(f, versions)
            if newVersion {
                versions.values.append(CloudVersion(id: "unreviewed", content: before))
                await rejected { try await reopened.retryCloudResolution(day) }
                XCTAssertEqual(versions.marked, ["other"]); XCTAssertTrue(versions.values.contains { $0.id == "unreviewed" })
            } else {
                try await reopened.retryCloudResolution(day)
                XCTAssertEqual(versions.marked, ["other", "second"])
            }
        }
    }

    func testChangedBytesAfterReplacementNeverMarkVersionsOrOverwriteLaterSource() async throws {
        let versions = TestCloudVersions()
        let later = Data("Uncoordinated later writer\n".utf8)
        let gate = CloudSourceMutation()
        let f = try fixture(versions, failure: { purpose, stage in
            if purpose == .bodyDiary && stage == .afterReplace, let url = gate.url { try later.write(to: url) }
        }); defer { try? FileManager.default.removeItem(at: f.base) }
        gate.url = f.source
        let review = try await f.store.reviewCloudVersions(day)
        await rejected { try await f.store.confirmCloudResolution(CloudRecoveryDraft(review: review.review, content: versions.values[0].content!)) }
        XCTAssertEqual(try Data(contentsOf: f.source), later); XCTAssertTrue(versions.marked.isEmpty)
        let reopened = try reopen(f, versions)
        await rejected { try await reopened.retryCloudResolution(day) }
        XCTAssertEqual(try Data(contentsOf: f.source), later); XCTAssertTrue(versions.marked.isEmpty)
    }

    func testInvalidOrOversizedAlternateCannotAuthorizeWholeFileResolution() async throws {
        for bytes in [Data([0xff, 0xfe]), Data(repeating: 65, count: CloudReview.maximumBytes + 1)] {
            let versions = TestCloudVersions(); versions.values = [CloudVersion(id: "unreadable", content: bytes)]
            let f = try fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
            let snapshot = try await f.store.open(day)
            XCTAssertEqual(Data(snapshot.markdown.utf8), before)
            let display = try f.access.displaySnapshot(day, reviewVersions: true)
            XCTAssertFalse(display.cloud!.canResolve)
            await rejected { try await f.store.confirmCloudResolution(CloudRecoveryDraft(review: display.cloud!, content: before)) }
            XCTAssertEqual(try Data(contentsOf: f.source), before); XCTAssertTrue(versions.marked.isEmpty)
        }
    }

    func testInterruptedCompletionAndLaterEditCanExplicitlyKeepReviewedCurrentWithoutWriting() async throws {
        let versions = TestCloudVersions(), f = try fixture(versions, failure: { purpose, stage in
            if purpose == .cloudConfirmation && stage == .beforeWrite && versions.values.isEmpty { throw JournalError.storage }
        }); defer { try? FileManager.default.removeItem(at: f.base) }
        let local = BodyEdit(day: day, before: before, content: before + Data("Local held\n".utf8), sourceID: try f.access.sourceID(day))
        try await f.store.retainBodyEdit(local)
        let initial = try await f.store.reviewCloudVersions(day), chosen = versions.values[0].content!
        await rejected { try await f.store.confirmCloudResolution(CloudRecoveryDraft(review: initial.review, content: chosen)) }
        XCTAssertTrue(versions.values.isEmpty); XCTAssertEqual(try Data(contentsOf: f.source), chosen)
        let reopened = try reopen(f, versions), later = Data("Later external current\n".utf8)
        try later.write(to: f.source)
        let current = try await reopened.reviewCloudVersions(day)
        XCTAssertTrue(current.review.canKeepCurrent)
        try Data("Unseen later change\n".utf8).write(to: f.source)
        await rejected { try await reopened.keepReviewedCloudCurrent(current) }
        let refreshed = try await reopened.reviewCloudVersions(day), bytes = try Data(contentsOf: f.source)
        try await reopened.keepReviewedCloudCurrent(refreshed)
        let pending = try await reopened.cloudWorkPending(day), held = try await reopened.bodyHeldByCloud(day)
        XCTAssertFalse(pending); XCTAssertTrue(held)
        XCTAssertEqual(try Data(contentsOf: f.source), bytes); XCTAssertEqual(versions.marked, ["other"])
        let backups = try await reopened.cloudVersionBackups(day)
        XCTAssertTrue(backups.contains { $0.content == chosen }); XCTAssertTrue(backups.contains { $0.review.current == bytes })
        let retained = try await reopened.retainedBodyEdit(day); XCTAssertEqual(retained, local)
        let newIdentity = BodyEdit(day: day, before: bytes, content: local.content, sourceID: local.sourceID)
        await rejected { try await reopened.commitBodyEdit(newIdentity) }
        XCTAssertEqual(try Data(contentsOf: f.source), bytes)
    }

    func testObservedCloudConflictHoldsExistingTransactionAfterExternalResolution() async throws {
        let versions = TestCloudVersions(); versions.values = []
        let f = try fixture(versions, failure: { purpose, stage in
            if purpose == .bodyDiary && stage == .beforeReplace { throw JournalError.storage }
        }); defer { try? FileManager.default.removeItem(at: f.base) }
        let edit = BodyEdit(day: day, before: before, content: before + Data("Local intent\n".utf8), sourceID: try f.access.sourceID(day))
        await rejected { try await f.store.commitBodyEdit(edit) }
        versions.values = [CloudVersion(id: "observed", content: before)]
        _ = try await f.store.open(day)
        versions.values = []
        let reopened = try reopen(f, versions)
        await rejected { _ = try await reopened.recoverBodyEdit(day) }
        await rejected { try await reopened.commitBodyEdit(BodyEdit(day: self.day, before: self.before, content: edit.content, sourceID: edit.sourceID)) }
        XCTAssertEqual(try Data(contentsOf: f.source), before)
        let conflict = try await reopened.bodyConflict(day)!, adopted = try await reopened.resolveBodyConflict(conflict, content: edit.content)
        try await reopened.commitBodyEdit(adopted)
        let followup = BodyEdit(day: day, before: edit.content, content: edit.content + Data("New intentional input\n".utf8), sourceID: edit.sourceID)
        try await reopened.commitBodyEdit(followup)
        XCTAssertEqual(try Data(contentsOf: f.source), followup.content)
    }

    func testEvidenceIsIsolatedByDayDeduplicatedAndReadableRecordsSurviveCorruption() async throws {
        let versions = TestCloudVersions(), f = try fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        let first = try await f.store.reviewCloudVersions(day)
        try await f.store.retainCloudDraft(CloudRecoveryDraft(review: first.review, content: Data("Hand merge\n".utf8)))
        versions.values.append(CloudVersion(id: "late", content: before))
        _ = try await f.store.reviewCloudVersions(day)
        let root = await f.store.state.appendingPathComponent("cloud-evidence")
        let count = try FileManager.default.contentsOfDirectory(at: root.appendingPathComponent(day.value), includingPropertiesForKeys: nil).count
        for _ in 0..<12 { _ = try await f.store.reviewCloudVersions(day) }
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(at: root.appendingPathComponent(day.value), includingPropertiesForKeys: nil).count, count)
        let foreign = root.appendingPathComponent("2026-09-01")
        try FileManager.default.createDirectory(at: foreign, withIntermediateDirectories: true)
        try Data("damaged foreign record".utf8).write(to: foreign.appendingPathComponent("foreign.json"))
        var backups = try await f.store.cloudBackupState(day)
        XCTAssertEqual(backups.unreadableCount, 0); XCTAssertFalse(backups.drafts.isEmpty)
        try Data("damaged own record".utf8).write(to: root.appendingPathComponent(day.value + "/damaged.json"))
        backups = try await f.store.cloudBackupState(day)
        XCTAssertEqual(backups.unreadableCount, 1); XCTAssertNotNil(backups.warning)
        XCTAssertTrue(backups.drafts.contains { $0.content == Data("Hand merge\n".utf8) })
        let healthy = try await f.store.open(day); XCTAssertEqual(Data(healthy.markdown.utf8), before)
        let repeated = try await f.store.cloudBackupState(day); XCTAssertEqual(backups.drafts, repeated.drafts)
    }

    func testCloudRecordBoundsRejectBeforeReplacingValidDraftAndExposeCountLimit() async throws {
        let versions = TestCloudVersions(), f = try fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        let draft = try await f.store.reviewCloudVersions(day)
        let oversized = CloudRecoveryDraft(review: draft.review, content: Data(repeating: 65, count: CloudReview.maximumBytes + 1))
        await rejected { try await f.store.retainCloudDraft(oversized) }
        let retained = try await f.store.retainedCloudDraft(day); XCTAssertEqual(retained, draft)
        let countReview = CloudReview(day: day, sourceID: draft.review.sourceID, current: before,
                                      inventory: CloudVersionInventory(versions: versions.values, hasConflict: true, complete: false, totalVersions: 23))
        XCTAssertFalse(countReview.canResolve); XCTAssertTrue(countReview.resolutionIssue!.contains("23")); XCTAssertTrue(countReview.resolutionIssue!.contains("16"))
        let large = Data(repeating: 65, count: CloudReview.maximumBytes)
        let fullReview = CloudReview(day: day, sourceID: draft.review.sourceID, current: large,
            inventory: CloudVersionInventory(versions: (0..<16).map { CloudVersion(id: "version-\($0)", content: large) }, hasConflict: true))
        let bounded = CloudRecoveryDraft(review: fullReview, content: large)
        try await f.store.retainCloudDraft(bounded)
        let reopened = try reopen(f, versions), roundtrip = try await reopened.retainedCloudDraft(day)
        XCTAssertEqual(roundtrip, bounded)
        let encodedURL = await f.store.state.appendingPathComponent("cloud-drafts/" + day.value + ".json")
        XCTAssertLessThan(try Data(contentsOf: encodedURL).count, CloudReview.maximumRecordBytes)
    }

    func testHistoricalCloudWorkRestoresAfterMidnightButCompletedBackupsDoNotHijackToday() async throws {
        let versions = TestCloudVersions(), f = try fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        let review = try await f.store.reviewCloudVersions(day)
        let tomorrow = day.date(in: f.store.timeZone).addingTimeInterval(86400), nextDay = JournalDay(date: tomorrow, timeZone: f.store.timeZone)
        let selected = try await f.store.initialDay(now: tomorrow), directory = try await f.store.directory()
        XCTAssertEqual(selected, day); XCTAssertTrue(directory.first { $0.day == day }!.hasDraft)
        try await f.store.confirmCloudResolution(review)
        let reopened = try reopen(f, versions), cleanDay = try await reopened.initialDay(now: tomorrow), cleanDirectory = try await reopened.directory()
        XCTAssertEqual(cleanDay, nextDay); XCTAssertFalse(cleanDirectory.first { $0.day == day }!.hasDraft)
    }

    func testIndependentBodyDraftAndTransactionRemainRecoverableWithoutReplay() async throws {
        let versions = TestCloudVersions(); versions.values = []
        let f = try fixture(versions, failure: { purpose, stage in
            if purpose == .bodyDiary && stage == .beforeReplace { throw JournalError.storage }
        }); defer { try? FileManager.default.removeItem(at: f.base) }
        let edit = BodyEdit(day: day, before: before, content: before + Data("Local draft\n".utf8), sourceID: try f.access.sourceID(day))
        await rejected { try await f.store.commitBodyEdit(edit) }
        versions.values = [CloudVersion(id: "arrived", content: before)]
        let reopened = try reopen(f, versions), review = try await reopened.reviewCloudVersions(day)
        try await reopened.confirmCloudResolution(review)
        let after = try reopen(f, versions)
        await rejected { _ = try await after.recoverBodyEdit(day) }
        await rejected { try await after.commitBodyEdit(edit) }
        let recovery = await after.bodyRecoveryState(day)
        XCTAssertEqual(recovery.edit, edit); XCTAssertEqual(recovery.operation, edit)
        XCTAssertEqual(try Data(contentsOf: f.source), before)
        let bodyConflict = try await after.bodyConflict(day)!
        let newEdit = try await after.resolveBodyConflict(bodyConflict, content: edit.content)
        try await after.commitBodyEdit(newEdit)
        XCTAssertEqual(try Data(contentsOf: f.source), edit.content)
    }
}

private final class CloudSourceMutation: @unchecked Sendable { var url: URL? }
