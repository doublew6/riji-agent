import XCTest
@testable import RijiState
import RijiCore

private final class StateCloudVersions: CloudVersionProvider, @unchecked Sendable {
    var values = [CloudVersion(id: "alternate", content: Data("---\r\nproperty: alternate\r\n---\r\nAlternate 🧠\r\n".utf8))]
    func inventory(at url: URL) throws -> CloudVersionInventory { CloudVersionInventory(versions: values, hasConflict: !values.isEmpty) }
    func resolve(_ reviewed: [CloudVersion], at url: URL) throws {
        guard values == reviewed else { throw CloudRecoveryError.stale }; values = []
    }
}

@MainActor final class CloudRecoveryStateTests: XCTestCase {
    private let date = ISO8601DateFormatter().date(from: "2026-09-19T12:00:00Z")!
    private let before = "---\r\nproperty: current\r\n---\r\nCurrent source\r\n"
    private struct Fixture {
        let base: URL, root: URL, source: URL
        let browser: VaultBrowserStore
        let model: DiaryViewModel
    }
    private func fixture(_ versions: any CloudVersionProvider, failure: @escaping FileFailureHook = { _, _ in }) async throws -> Fixture {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("CloudStateSynthetic-\(UUID())")
        let root = base.appendingPathComponent("riji"), source = root.appendingPathComponent("daily/2026-09-19.md")
        for folder in ["daily", "templates"] { try FileManager.default.createDirectory(at: root.appendingPathComponent(folder), withIntermediateDirectories: true) }
        try Data("# Template\n".utf8).write(to: root.appendingPathComponent("templates/daily.md"))
        try Data(before.utf8).write(to: source)
        let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base, failure: failure, versions: versions)
        let model = DiaryViewModel(now: { self.date }, useOriginalCollection: true, vaultStore: browser)
        model.bodyQuietNanoseconds = 3_600_000_000_000; model.bodyCheckpointNanoseconds = 3_600_000_000_000
        await model.start(); await model.selectOriginalFolder(root)
        return Fixture(base: base, root: root, source: source, browser: browser, model: model)
    }
    private func reopen(_ fixture: Fixture, _ versions: any CloudVersionProvider) async -> DiaryViewModel {
        let model = DiaryViewModel(now: { self.date }, useOriginalCollection: true,
            vaultStore: VaultBrowserStore(stateDirectory: fixture.base.appendingPathComponent("state"), appRoot: fixture.base, versions: versions))
        await model.start(); return model
    }

    func testColdCloudDayOpensFromSelectionAndDirectoryWithReachableReview() async throws {
        let versions = StateCloudVersions(), f = try await fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        XCTAssertTrue(f.model.isReady); XCTAssertEqual(f.model.markdown, before); XCTAssertFalse(f.model.canEditBody); XCTAssertTrue(f.model.hasCloudRecovery)
        let day = await f.model.originalDay(for: VaultEntry(path: ["daily", "2026-09-19.md"], kind: .file))!
        let navigation = await f.model.navigate(to: day); XCTAssertTrue(navigation)
        await f.model.reviewCloudVersions()
        XCTAssertTrue(f.model.cloudReviewPresented); XCTAssertEqual(f.model.cloudDraft?.review.versions, versions.values)
        let entry = VaultEntry(path: ["daily", "2026-09-19.md"], kind: .file)
        let contents = try await f.browser.read(entry); XCTAssertTrue(contents.cloudConflict); XCTAssertEqual(contents.text, before)
        XCTAssertEqual(try Data(contentsOf: f.source), Data(before.utf8))
    }

    func testDiffChoicesPersistWithoutSourceWriteAndColdReopenConfirmsExactResult() async throws {
        let versions = StateCloudVersions(), f = try await fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        await f.model.reviewCloudVersions()
        let draft = try XCTUnwrap(f.model.cloudDraft)
        let diff = try MarkdownVersionDiff(current: draft.content, other: versions.values[0].content!)
        let result = try diff.applying([diff.hunks.last!.id: .edited(Data("Only this paragraph changed 🧠\r\n".utf8))], to: draft.content)
        XCTAssertTrue(f.model.applyCloudDifference(reviewID: draft.review.id, expected: draft.content, result: result))
        XCTAssertEqual(try Data(contentsOf: f.source), Data(before.utf8))
        let closed = await f.model.closeCloudReview(); XCTAssertTrue(closed)
        let reopened = await reopen(f, versions); await reopened.reviewCloudVersions()
        XCTAssertEqual(Data(reopened.cloudMergeText.utf8), result)
        let saved = await reopened.confirmCloudVersion(); XCTAssertTrue(saved)
        XCTAssertEqual(try Data(contentsOf: f.source), result)
    }

    func testStaleDiffCannotReplaceNewerManualDraftOrRefreshedReview() async throws {
        let versions = StateCloudVersions(), f = try await fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        await f.model.reviewCloudVersions()
        let original = try XCTUnwrap(f.model.cloudDraft)
        let newer = original.content + Data("Manual addition\r\n".utf8)
        f.model.updateCloudMerge(String(decoding: newer, as: UTF8.self))
        XCTAssertFalse(f.model.applyCloudDifference(reviewID: original.review.id, expected: original.content, result: Data("Stale".utf8)))
        XCTAssertFalse(f.model.applyCloudDifference(reviewID: UUID(), expected: newer, result: Data("Wrong review".utf8)))
        XCTAssertEqual(Data(f.model.cloudMergeText.utf8), newer)
        XCTAssertEqual(try Data(contentsOf: f.source), Data(before.utf8))
    }

    func testExternalResolutionClearsUntouchedReviewAndResumesAutomaticEditing() async throws {
        let versions = StateCloudVersions(), f = try await fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        await f.model.reviewCloudVersions()
        let closed = await f.model.closeCloudReview(); XCTAssertTrue(closed)
        versions.values = []
        let latest = before + "External update\r\n"; try Data(latest.utf8).write(to: f.source)
        await f.model.refreshOriginalBody()
        XCTAssertFalse(f.model.cloudBlocksEditing); XCTAssertTrue(f.model.canEditBody)
        XCTAssertEqual(f.model.markdown, latest)
        f.model.updateBody(latest + "Continued editing\r\n")
        let saved = await f.model.flushBody(); XCTAssertTrue(saved)
        let cold = await reopen(f, versions)
        XCTAssertTrue(cold.canEditBody); XCTAssertEqual(cold.markdown, latest + "Continued editing\r\n")
    }

    func testOpeningUntouchedReviewRefreshesStaleVersionSnapshotAndCurrentText() async throws {
        let versions = StateCloudVersions(), f = try await fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        await f.model.reviewCloudVersions()
        let closed = await f.model.closeCloudReview(); XCTAssertTrue(closed)
        let latest = before + "Arrived file update\r\n"; try Data(latest.utf8).write(to: f.source)
        versions.values = [CloudVersion(id: "new-alternate", content: Data("New alternate\n".utf8))]
        await f.model.reviewCloudVersions()
        XCTAssertEqual(f.model.cloudDraft?.review.current, Data(latest.utf8))
        XCTAssertEqual(f.model.cloudDraft?.review.versions, versions.values); XCTAssertEqual(f.model.cloudMergeText, latest)
        XCTAssertEqual(try Data(contentsOf: f.source), Data(latest.utf8))
    }

    func testClosingAndColdReopeningRetainsMergeWithoutWritingAndRefreshDoesNotReplaceReview() async throws {
        let versions = StateCloudVersions(), f = try await fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        await f.model.reviewCloudVersions()
        f.model.updateCloudMerge("Hand merged full source 🧠\r\n")
        let reviewID = f.model.cloudDraft?.review.id
        try Data("Later external\n".utf8).write(to: f.source)
        await f.model.refreshOriginalBody()
        XCTAssertEqual(f.model.cloudDraft?.review.id, reviewID); XCTAssertEqual(f.model.markdown, before)
        let closed = await f.model.closeCloudReview(); XCTAssertTrue(closed)
        let reopened = await reopen(f, versions)
        await reopened.reviewCloudVersions()
        XCTAssertEqual(reopened.cloudMergeText, "Hand merged full source 🧠\r\n")
        let resolved = await reopened.confirmCloudVersion(); XCTAssertFalse(resolved); XCTAssertNotNil(reopened.cloudError)
        await reopened.reviewCloudVersions(refresh: true)
        XCTAssertEqual(reopened.cloudMergeText, "Hand merged full source 🧠\r\n")
        XCTAssertEqual(reopened.cloudDraft?.review.current, Data("Later external\n".utf8))
        XCTAssertFalse(reopened.cloudBackups.isEmpty)
    }

    func testExplicitWholeFileSelectionReopensExactAndKeepsVersionBackupsReachable() async throws {
        let versions = StateCloudVersions(), f = try await fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        let chosen = versions.values[0].content!
        await f.model.reviewCloudVersions()
        let result = await f.model.confirmCloudVersion(chosen); XCTAssertTrue(result)
        XCTAssertEqual(Data(f.model.markdown.utf8), chosen); XCTAssertTrue(f.model.canEditBody); XCTAssertNil(f.model.bodyError)
        XCTAssertTrue(f.model.hasCloudRecovery); XCTAssertFalse(f.model.cloudBlocksEditing)
        let reopened = await reopen(f, versions)
        XCTAssertEqual(Data(reopened.markdown.utf8), chosen); XCTAssertFalse(reopened.cloudBackups.isEmpty)
        await reopened.reviewCloudVersions(); XCTAssertTrue(reopened.cloudReviewPresented)
        try Data("A later edit\n".utf8).write(to: f.source)
        let retry = await reopened.confirmCloudVersion(retry: true); XCTAssertFalse(retry)
        XCTAssertEqual(try Data(contentsOf: f.source), Data("A later edit\n".utf8))
    }

    func testIndependentUnsavedBodyDraftIsVisibleAfterResolutionAndNeverAutomaticallyReplayed() async throws {
        let versions = StateCloudVersions(); versions.values = []
        let f = try await fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        let local = before + "Unsaved local body\r\n"
        f.model.updateBody(local)
        versions.values = [CloudVersion(id: "arrived", content: Data(before.utf8))]
        await f.model.refreshOriginalBody(); XCTAssertFalse(f.model.canEditBody)
        await f.model.reviewCloudVersions()
        XCTAssertTrue(f.model.cloudLocalVersions.contains(Data(local.utf8)))
        let result = await f.model.confirmCloudVersion(Data(before.utf8)); XCTAssertTrue(result)
        XCTAssertEqual(f.model.markdown, local); XCTAssertTrue(f.model.bodyDirty)
        let flushed = await f.model.flushBody(); XCTAssertFalse(flushed)
        XCTAssertEqual(try Data(contentsOf: f.source), Data(before.utf8))
        let reopened = await reopen(f, versions)
        XCTAssertEqual(reopened.markdown, local); XCTAssertTrue(reopened.bodyDirty)
        XCTAssertEqual(try Data(contentsOf: f.source), Data(before.utf8))
        await reopened.reviewBodyConflict(); XCTAssertNotNil(reopened.bodyConflict)
    }

    func testHeldDraftRejectsFurtherTypingUntilExplicitBodyChoiceThenAllowsNewEdits() async throws {
        for appResolution in [true, false] {
            let versions = StateCloudVersions(); versions.values = []
            let f = try await fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
            let local = before + "Held input\r\n"
            f.model.updateBody(local)
            versions.values = [CloudVersion(id: "arrived", content: Data(before.utf8))]
            await f.model.refreshOriginalBody()
            if appResolution {
                await f.model.reviewCloudVersions()
                let resolved = await f.model.confirmCloudVersion(Data(before.utf8)); XCTAssertTrue(resolved)
            } else { versions.values = []; await f.model.refreshOriginalBody() }
            XCTAssertTrue(f.model.bodyHeldForCloud); XCTAssertFalse(f.model.canEditBody)
            f.model.updateBody(local + "Bypass attempt\n")
            XCTAssertEqual(f.model.markdown, local)
            let saved = await f.model.flushBody(); XCTAssertFalse(saved)
            XCTAssertEqual(try Data(contentsOf: f.source), Data(before.utf8))
            let reopened = await reopen(f, versions)
            XCTAssertFalse(reopened.canEditBody); reopened.updateBody(local + "Cold bypass\n")
            XCTAssertEqual(reopened.markdown, local)
            await reopened.reviewBodyConflict()
            let adopted = await reopened.keepLocalBodyVersion(); XCTAssertTrue(adopted)
            XCTAssertTrue(reopened.canEditBody)
            reopened.updateBody(local + "New deliberate edit\n")
            let followup = await reopened.flushBody(); XCTAssertTrue(followup)
            XCTAssertEqual(try Data(contentsOf: f.source), Data((local + "New deliberate edit\n").utf8))
        }
    }

    func testLateNativeCallbackDuringCloudPublicationRetainsOldBaseAndRejectsEchoAndFreshEditing() async throws {
        let versions = StateCloudVersions(); versions.values = []
        let f = try await fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        let origin = f.model.bodyInputOrigin, day = f.model.selectedDay
        let external = before + "Arrived external file\n", late = before + "Late native input\n"
        try Data(external.utf8).write(to: f.source)
        versions.values = [CloudVersion(id: "arrived", content: Data(before.utf8))]
        await f.model.refreshOriginalBody()
        f.model.updateBody(before, for: day, origin: origin); XCTAssertEqual(f.model.markdown, external)
        let fresh = f.model.bodyInputOrigin
        f.model.updateBody(late, for: day, origin: origin); XCTAssertEqual(f.model.markdown, late)
        f.model.updateBody(before, for: day, origin: origin); XCTAssertEqual(f.model.markdown, late)
        f.model.updateBody(external + "Fresh editing blocked\n", for: day, origin: fresh); XCTAssertEqual(f.model.markdown, late)
        let saved = await f.model.flushBody(); XCTAssertFalse(saved)
        let access = try await f.browser.originalAccess()!, store = try await f.browser.homeStore(access)
        let retained = try await store.retainedBodyEdit(day)
        XCTAssertEqual(retained?.before, Data(before.utf8)); XCTAssertEqual(retained?.content, Data(late.utf8))
        XCTAssertEqual(try Data(contentsOf: f.source), Data(external.utf8))
        let reopened = await reopen(f, versions)
        await reopened.reviewCloudVersions(); XCTAssertTrue(reopened.cloudLocalVersions.contains(Data(late.utf8)))
    }

    func testDraftWriteFailureKeepsReviewVisibleRefusesCloseThenCoalescesAndColdReopensLatest() async throws {
        let versions = StateCloudVersions(), gate = CloudDraftWriteGate()
        let f = try await fixture(versions, failure: gate.check); defer { try? FileManager.default.removeItem(at: f.base) }
        gate.failing = true
        await f.model.reviewCloudVersions()
        XCTAssertTrue(f.model.cloudReviewPresented); XCTAssertNotNil(f.model.cloudError)
        XCTAssertEqual(f.model.cloudDraft?.review.versions, versions.values)
        let exported = await f.model.exportRetainedBody(); XCTAssertEqual(exported?.data, Data(before.utf8))
        f.model.cloudDebounceNanoseconds = 3_600_000_000_000
        for index in 0..<30 { f.model.updateCloudMerge("Newest hand merge \(index) 🧠\r\n") }
        let expected = "Newest hand merge 29 🧠\r\n"
        let closed = await f.model.closeCloudReview(); XCTAssertFalse(closed)
        XCTAssertEqual(f.model.cloudMergeText, expected); XCTAssertNotNil(f.model.cloudError)
        XCTAssertLessThanOrEqual(gate.writes, 2, "Superseded keystrokes must not each encode/write the full evidence")
        gate.failing = false
        let retried = await f.model.closeCloudReview(); XCTAssertTrue(retried)
        let reopened = await reopen(f, versions)
        await reopened.reviewCloudVersions(); XCTAssertEqual(reopened.cloudMergeText, expected)
        XCTAssertEqual(try Data(contentsOf: f.source), Data(before.utf8))
    }

    func testPartialBackupFailureStillShowsCurrentAndAlternateWithWarning() async throws {
        let versions = StateCloudVersions(), f = try await fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        await f.model.reviewCloudVersions()
        let closed = await f.model.closeCloudReview(); XCTAssertTrue(closed)
        let access = try await f.browser.originalAccess()!
        let folder = f.base.appendingPathComponent("state/original-vaults/" + access.identity + "/cloud-evidence/2026-09-19")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try Data("broken evidence".utf8).write(to: folder.appendingPathComponent("broken.json"))
        let reopened = await reopen(f, versions)
        XCTAssertTrue(reopened.isReady); XCTAssertNotNil(reopened.cloudBackupWarning)
        await reopened.reviewCloudVersions(); XCTAssertTrue(reopened.cloudReviewPresented)
        XCTAssertEqual(reopened.cloudDraft?.review.current, Data(before.utf8))
        XCTAssertEqual(reopened.cloudDraft?.review.versions, versions.values); XCTAssertNotNil(reopened.cloudBackupWarning)
    }

    func testUnavailableSourceRestoresBodyAndCloudRecoveryTogether() async throws {
        let versions = StateCloudVersions(); versions.values = []
        let f = try await fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        let local = before + "Independent body recovery\n"
        f.model.updateBody(local)
        versions.values = [CloudVersion(id: "arrived", content: Data(before.utf8))]
        await f.model.refreshOriginalBody(); await f.model.reviewCloudVersions()
        f.model.updateCloudMerge("Independent cloud merge\n")
        let closed = await f.model.closeCloudReview(); XCTAssertTrue(closed)
        try FileManager.default.copyItem(at: f.root, to: f.base.appendingPathComponent("retained-copy"))
        try FileManager.default.removeItem(at: f.root)
        let reopened = await reopen(f, versions)
        XCTAssertEqual(reopened.markdown, local); XCTAssertEqual(reopened.cloudMergeText, "Independent cloud merge\n")
        XCTAssertTrue(reopened.sourceUnavailable); XCTAssertTrue(reopened.bodyHeldForCloud); XCTAssertFalse(reopened.canEditBody)
        await reopened.reviewCloudVersions(); XCTAssertTrue(reopened.cloudReviewPresented)
        XCTAssertTrue(reopened.cloudLocalVersions.contains(Data(local.utf8)))
        XCTAssertEqual(reopened.cloudDraft?.review.versions, versions.values)
    }

    func testMissingVersionBecomesReadableOnlyThroughBackgroundDownloadRequest() async throws {
        let provider = DownloadGatedCloudVersions()
        defer { provider.release() }
        let f = try await fixture(provider); defer { try? FileManager.default.removeItem(at: f.base) }
        await f.model.reviewCloudVersions()
        for _ in 0..<100 where !provider.requested { try await Task.sleep(nanoseconds: 10_000_000) }
        XCTAssertTrue(provider.requested); XCTAssertFalse(f.model.isBusy); XCTAssertTrue(f.model.cloudReviewPresented)
        XCTAssertNil(f.model.cloudDraft?.review.versions.first?.content)
        f.model.updateCloudMerge("Merge while download is pending\n")
        XCTAssertEqual(f.model.cloudMergeText, "Merge while download is pending\n")
        provider.release()
        for _ in 0..<100 where !provider.ready { try await Task.sleep(nanoseconds: 10_000_000) }
        await f.model.reviewCloudVersions(refresh: true)
        XCTAssertEqual(f.model.cloudDraft?.review.versions.first?.content, provider.bytes)
        XCTAssertEqual(f.model.cloudMergeText, "Merge while download is pending\n")
        XCTAssertTrue(f.model.cloudDraft!.review.canResolve)
    }

    func testUnavailableOriginalReopensLocalCloudEvidenceForReadAndExport() async throws {
        let versions = StateCloudVersions(), f = try await fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        await f.model.reviewCloudVersions()
        f.model.updateCloudMerge("Retained full merge\n")
        let closed = await f.model.closeCloudReview(); XCTAssertTrue(closed)
        try FileManager.default.copyItem(at: f.root, to: f.base.appendingPathComponent("retained-copy"))
        try FileManager.default.removeItem(at: f.root)
        let reopened = await reopen(f, versions)
        XCTAssertTrue(reopened.isReady); XCTAssertTrue(reopened.sourceUnavailable); XCTAssertFalse(reopened.canEditBody)
        XCTAssertEqual(reopened.markdown, before); XCTAssertTrue(reopened.hasCloudRecovery)
        await reopened.reviewCloudVersions(); XCTAssertTrue(reopened.cloudReviewPresented)
        XCTAssertEqual(reopened.cloudMergeText, "Retained full merge\n")
        let export = await reopened.exportRetainedBody(); XCTAssertEqual(export?.data, Data(before.utf8))
        let confirm = await reopened.confirmCloudVersion(); XCTAssertFalse(confirm)
        XCTAssertFalse(FileManager.default.fileExists(atPath: f.root.path))
    }

    func testReadableCloudSourceWithMalformedMarkdownStillOpensReadOnly() async throws {
        let versions = StateCloudVersions(), f = try await fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        let malformed = "# Readable synthetic\n```unclosed\nText remains readable\n"
        try Data(malformed.utf8).write(to: f.source)
        let reopened = await reopen(f, versions)
        XCTAssertTrue(reopened.isReady); XCTAssertEqual(reopened.markdown, malformed)
        XCTAssertFalse(reopened.canEditBody); XCTAssertTrue(reopened.hasCloudRecovery)
        await reopened.reviewCloudVersions(); XCTAssertTrue(reopened.cloudReviewPresented)
    }

    func testUnavailableVersionStillAllowsCurrentSourceAndRetryCompletesReview() async throws {
        let versions = StateCloudVersions(); versions.values = [CloudVersion(id: "unavailable", content: nil)]
        let f = try await fixture(versions); defer { try? FileManager.default.removeItem(at: f.base) }
        XCTAssertEqual(f.model.markdown, before)
        await f.model.reviewCloudVersions(); XCTAssertFalse(f.model.cloudDraft!.review.canResolve)
        let result = await f.model.confirmCloudVersion(); XCTAssertFalse(result)
        versions.values = [CloudVersion(id: "unavailable", content: Data("Downloaded\n".utf8))]
        await f.model.reviewCloudVersions(refresh: true); XCTAssertTrue(f.model.cloudDraft!.review.canResolve)
    }
}

private final class CloudDraftWriteGate: @unchecked Sendable {
    private let lock = NSLock()
    private var fail = false, count = 0
    var failing: Bool { get { lock.lock(); defer { lock.unlock() }; return fail } set { lock.lock(); fail = newValue; lock.unlock() } }
    var writes: Int { lock.lock(); defer { lock.unlock() }; return count }
    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        guard purpose == .cloudDraft && stage == .beforeWrite else { return }
        lock.lock(); defer { lock.unlock() }; count += 1
        if fail { throw JournalError.storage }
    }
}

private final class DownloadGatedCloudVersions: CloudVersionProvider, @unchecked Sendable {
    let bytes = Data("Downloaded only after explicit request\n".utf8)
    private let condition = NSCondition()
    private var started = false, released = false, downloaded = false
    var requested: Bool { condition.lock(); defer { condition.unlock() }; return started }
    var ready: Bool { condition.lock(); defer { condition.unlock() }; return downloaded }
    func inventory(at url: URL) throws -> CloudVersionInventory {
        CloudVersionInventory(versions: [CloudVersion(id: "remote", content: ready ? bytes : nil)], hasConflict: true)
    }
    func requestDownloads(at url: URL) {
        condition.lock(); defer { condition.unlock() }
        started = true; condition.broadcast()
        while !released { condition.wait() }
        downloaded = true
    }
    func release() { condition.lock(); released = true; condition.broadcast(); condition.unlock() }
    func resolve(_ reviewed: [CloudVersion], at url: URL) throws { throw CloudRecoveryError.pending }
}
