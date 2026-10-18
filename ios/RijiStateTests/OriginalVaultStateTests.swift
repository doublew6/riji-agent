import XCTest
@testable import RijiState
import RijiCore

@MainActor final class OriginalVaultStateTests: XCTestCase {
    private func fixture() throws -> (URL, URL, VaultBrowserStore) {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("OriginalState-\(UUID())")
        let root = base.appendingPathComponent("riji")
        for folder in ["daily/2026-09", "templates"] {
            try FileManager.default.createDirectory(at: root.appendingPathComponent(folder), withIntermediateDirectories: true)
        }
        try Data("# Original {{date}}\r\nUnknown {{keep}}\r\n".utf8).write(to: root.appendingPathComponent("templates/daily.md"))
        try Data("# Today's original\r\n| A | B |\r\n| --- | --- |\r\n| 🧠 | |\r\n".utf8).write(to: root.appendingPathComponent("daily/2026-09-16.md"))
        try Data("# Nested original\r\n".utf8).write(to: root.appendingPathComponent("daily/2026-09/2026-09-15.md"))
        return (base, root, VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base))
    }

    private var date: Date { ISO8601DateFormatter().date(from: "2026-09-16T12:00:00Z")! }

    func testFirstLaunchHasNoFallbackAndMainTabsWorkBeforeAuthorization() async throws {
        let (base, _, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = DiaryViewModel(now: { self.date }, useOriginalCollection: true, vaultStore: browser)
        await model.start()
        XCTAssertTrue(model.needsFolder); XCTAssertEqual(model.markdown, ""); XCTAssertFalse(model.isReady)
        let mayLeave = await model.prepareMainDestination(); XCTAssertTrue(mayLeave)
    }

    func testSelectedOriginalRestoresAndNestedTreeDateUsesOriginalBytes() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = DiaryViewModel(now: { self.date }, useOriginalCollection: true, vaultStore: browser)
        await model.start(); await model.selectOriginalFolder(root)
        XCTAssertTrue(model.isOriginalSource); XCTAssertTrue(model.isReady); XCTAssertTrue(model.canEditBody)
        XCTAssertTrue(model.markdown.contains("Today's original")); XCTAssertNil(model.voice); XCTAssertNil(model.workflows)
        let nested = try JournalDay("2026-09-15")
        let found = await model.originalDay(for: VaultEntry(path: ["daily", "2026-09", "2026-09-15.md"], kind: .file))
        XCTAssertEqual(found, nested)
        let navigated = await model.navigate(to: nested); XCTAssertTrue(navigated)
        XCTAssertEqual(Data(model.markdown.utf8), Data("# Nested original\r\n".utf8))
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("daily/2026-09-15.md").path))
        let restored = DiaryViewModel(now: { self.date }, useOriginalCollection: true,
            vaultStore: VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base))
        await restored.start()
        XCTAssertTrue(restored.markdown.contains("Today's original")); XCTAssertEqual(restored.sourceIdentity, model.sourceIdentity)
    }

    func testExternalRefreshFailureAndFailedSelectionKeepVisibleOriginal() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = DiaryViewModel(now: { self.date }, useOriginalCollection: true, vaultStore: browser)
        await model.start(); await model.selectOriginalFolder(root)
        let before = model.markdown, identity = model.sourceIdentity
        await model.selectOriginalFolder(base.appendingPathComponent("missing"))
        XCTAssertEqual(model.markdown, before); XCTAssertEqual(model.sourceIdentity, identity)
        try Data("External refreshed\n".utf8).write(to: root.appendingPathComponent("daily/2026-09-16.md"))
        await model.directoryClosed(); XCTAssertEqual(model.markdown, "External refreshed\n")
        try FileManager.default.removeItem(at: root)
        await model.directoryClosed()
        XCTAssertTrue(model.sourceUnavailable); XCTAssertEqual(model.markdown, "External refreshed\n")
        XCTAssertFalse(model.canEditBody)
    }

    func testFailedLocalSaveBlocksSourceSwitchAndPreservesInputAndBookmark() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let local = try LocalJournalStore(journalDirectory: base.appendingPathComponent("local"), stateDirectory: base.appendingPathComponent("local-state"), failure: { purpose, stage in
            if purpose == .bodyDiary && stage == .beforeReplace { throw JournalError.storage }
        })
        let model = DiaryViewModel(now: { self.date }, storageFactory: { local }, vaultStore: browser)
        model.bodyQuietNanoseconds = 3_600_000_000_000; model.bodyCheckpointNanoseconds = 3_600_000_000_000
        await model.start(); model.updateBody(model.markdown + "Pending local edit\n")
        let visible = model.markdown
        await model.selectOriginalFolder(root)
        XCTAssertFalse(model.isOriginalSource); XCTAssertEqual(model.markdown, visible); XCTAssertTrue(model.bodyDirty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: base.appendingPathComponent("state/vault-folder.json").path))
    }
}

private final class SourceReviewGate: @unchecked Sendable {
    private let condition = NSCondition()
    private var enabled = false
    private var reached = false
    private var released = false
    func arm() { condition.lock(); enabled = true; condition.unlock() }
    func hasReached() -> Bool { condition.lock(); defer { condition.unlock() }; return reached }
    func release() { condition.lock(); released = true; condition.broadcast(); condition.unlock() }
    func shouldFailBookmark() -> Bool { condition.lock(); defer { condition.unlock() }; return enabled }
    func holdBodyDraft(_ purpose: FilePurpose, _ stage: FileStage) {
        guard purpose == .bodyDraft, stage == .beforeWrite else { return }
        condition.lock(); defer { condition.unlock() }
        guard enabled, !reached else { return }
        reached = true
        while !released { condition.wait() }
    }
}

extension OriginalVaultStateTests {
    func testFailedCandidateTemplateTargetAndRecoveryKeepHomeAndRestoredBookmark() async throws {
        for failure in ["template", "target", "recovery"] {
            let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let model = DiaryViewModel(now: { self.date }, useOriginalCollection: true, vaultStore: browser)
            await model.start(); await model.selectOriginalFolder(root)
            let prior = model.markdown, identity = model.sourceIdentity
            let bookmarkURL = base.appendingPathComponent("state/vault-folder.json"), bookmark = try Data(contentsOf: bookmarkURL)
            let next = base.appendingPathComponent("Candidate"); try FileManager.default.copyItem(at: root, to: next)
            if failure == "template" {
                try FileManager.default.removeItem(at: next.appendingPathComponent("daily/2026-09-16.md"))
                try FileManager.default.removeItem(at: next.appendingPathComponent("templates/daily.md"))
            } else if failure == "target" {
                try FileManager.default.removeItem(at: next.appendingPathComponent("daily/2026-09-16.md"))
                try Data().write(to: next.appendingPathComponent("daily/.2026-09-16.md.icloud"))
            } else {
                let access = try OriginalVaultAccess(root: next)
                _ = try await browser.homeStore(access)
                try Data("broken recovery".utf8).write(to: base.appendingPathComponent("state/original-vaults/\(access.identity)/body-drafts/2026-09-16.json"))
            }
            await model.selectOriginalFolder(next)
            XCTAssertEqual(model.markdown, prior); XCTAssertEqual(model.sourceIdentity, identity); XCTAssertNotNil(model.errorMessage)
            XCTAssertEqual(try Data(contentsOf: bookmarkURL), bookmark)
            let restored = DiaryViewModel(now: { self.date }, useOriginalCollection: true,
                vaultStore: VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base))
            await restored.start(); XCTAssertEqual(restored.sourceIdentity, identity); XCTAssertEqual(restored.markdown, prior)
        }
    }

    func testBookmarkSaveFailureKeepsCurrentSourceAndPreviousConnection() async throws {
        let (base, root, _) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let gate = SourceReviewGate()
        let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base, failure: { purpose, stage in
            if purpose == .metadata, stage == .beforeWrite, gate.shouldFailBookmark() { throw JournalError.storage }
        })
        let model = DiaryViewModel(now: { self.date }, useOriginalCollection: true, vaultStore: browser)
        await model.start(); await model.selectOriginalFolder(root)
        let identity = model.sourceIdentity, before = model.markdown
        let bookmarkURL = base.appendingPathComponent("state/vault-folder.json"), bookmark = try Data(contentsOf: bookmarkURL)
        let next = base.appendingPathComponent("Candidate"); try FileManager.default.copyItem(at: root, to: next)
        gate.arm(); await model.selectOriginalFolder(next)
        XCTAssertEqual(model.sourceIdentity, identity); XCTAssertEqual(model.markdown, before); XCTAssertNotNil(model.errorMessage)
        XCTAssertEqual(try Data(contentsOf: bookmarkURL), bookmark)
        let restored = try await VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base).originalAccess()
        XCTAssertNil(restored)
        let connection = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base)
        _ = try await connection.restore()
        let access = try await connection.originalAccess(); XCTAssertEqual(access?.identity, identity)
    }

    func testSourceTransitionLocksBeforeAwaitingDraftRetentionAndRejectsOverlappingSwitch() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let gate = SourceReviewGate(); defer { gate.release() }
        let local = try LocalJournalStore(journalDirectory: base.appendingPathComponent("local"), stateDirectory: base.appendingPathComponent("local-state"), failure: { gate.holdBodyDraft($0, $1) })
        let model = DiaryViewModel(now: { self.date }, storageFactory: { local }, vaultStore: browser)
        model.bodyQuietNanoseconds = 3_600_000_000_000; model.bodyCheckpointNanoseconds = 3_600_000_000_000
        await model.start(); gate.arm(); model.updateBody(model.markdown + "Retain before switching\n")
        let first = Task { await model.selectOriginalFolder(root) }
        for _ in 0..<200 where !gate.hasReached() { try await Task.sleep(nanoseconds: 5_000_000) }
        XCTAssertTrue(gate.hasReached()); XCTAssertTrue(model.isBusy)
        await model.useLocalDiaries()
        XCTAssertTrue(model.isBusy)
        gate.release(); await first.value
        XCTAssertTrue(model.isOriginalSource); XCTAssertFalse(model.isBusy)
        let localSource = try await local.open(JournalDay(date: date, timeZone: local.timeZone))
        XCTAssertTrue(localSource.markdown.contains("Retain before switching"))
    }

    func testSuccessfulSourceSwitchRecomputesTodayUsingDestinationTimezone() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let instant = ISO8601DateFormatter().date(from: "2026-09-16T01:30:00Z")!
        let east = TimeZone(identifier: "Pacific/Kiritimati")!, west = TimeZone(identifier: "Pacific/Honolulu")!
        let local = try LocalJournalStore(journalDirectory: base.appendingPathComponent("local"), stateDirectory: base.appendingPathComponent("local-state"), timeZone: east)
        let access = try OriginalVaultAccess(root: root)
        _ = try LocalJournalStore(journalDirectory: base.appendingPathComponent("unused"), stateDirectory: base.appendingPathComponent("state"), timeZone: west, original: access)
        let model = DiaryViewModel(now: { instant }, storageFactory: { local }, vaultStore: browser)
        await model.start(); XCTAssertEqual(model.today.value, "2026-09-16")
        await model.selectOriginalFolder(root)
        XCTAssertEqual(model.today.value, "2026-09-15"); XCTAssertEqual(model.selectedDay.value, "2026-09-15")
        XCTAssertEqual(model.timeZone.identifier, west.identifier)
        await model.useLocalDiaries(); XCTAssertEqual(model.today.value, "2026-09-16")
    }

    func testPickerCancellationKeepsStateAndOtherFailureProvidesActionableMessage() async throws {
        let (base, _, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = DiaryViewModel(now: { self.date }, useOriginalCollection: true, vaultStore: browser)
        await model.start()
        model.sourcePickerFailed(NSError(domain: NSCocoaErrorDomain, code: NSUserCancelledError))
        XCTAssertNil(model.errorMessage)
        model.sourcePickerFailed(NSError(domain: NSCocoaErrorDomain, code: NSFileReadNoPermissionError))
        XCTAssertTrue(model.errorMessage?.contains("重新选择") == true)
        XCTAssertTrue(model.needsFolder); XCTAssertEqual(model.markdown, "")
    }
}


private final class OriginalEditingFailure: @unchecked Sendable {
    private let lock = NSLock()
    private var enabled = false
    let purpose: FilePurpose
    let stage: FileStage
    let error: Error
    init(_ purpose: FilePurpose, _ stage: FileStage, error: Error = JournalError.storage) {
        self.purpose = purpose; self.stage = stage; self.error = error
    }
    func arm() { lock.lock(); enabled = true; lock.unlock() }
    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        lock.lock(); defer { lock.unlock() }
        if enabled && purpose == self.purpose && stage == self.stage { throw error }
    }
}

extension OriginalVaultStateTests {
    private func editableModel(_ browser: VaultBrowserStore) -> DiaryViewModel {
        let model = DiaryViewModel(now: { self.date }, useOriginalCollection: true, vaultStore: browser)
        model.bodyQuietNanoseconds = 3_600_000_000_000
        model.bodyCheckpointNanoseconds = 3_600_000_000_000
        return model
    }

    func testNormalSelectionEditsNestedOriginalAndColdRestoresExactBytes() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        let day = try JournalDay("2026-09-15"), target = root.appendingPathComponent("daily/2026-09/2026-09-15.md")
        let before = Data("---\r\nunknown: keep\r\n---\r\n# 中文 e\u{301} 🧠\r\n| A | B |\r\n| --- | --- |\r\n| 空 | |\r\n".utf8)
        try before.write(to: target)
        let opened = await model.navigate(to: day); XCTAssertTrue(opened); XCTAssertTrue(model.canEditBody)
        let after = before + Data("Typed in place\r\n".utf8)
        model.updateBody(String(decoding: after, as: UTF8.self))
        let left = await model.prepareMainDestination(); XCTAssertTrue(left)
        XCTAssertEqual(try Data(contentsOf: target), after); XCTAssertEqual(model.bodyStatus, "已保存在此设备")
        XCTAssertFalse(model.canEditLegacy); XCTAssertNil(model.voice); XCTAssertNil(model.workflows)
        let restored = editableModel(VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base))
        await restored.start()
        let returned = await restored.navigate(to: day); XCTAssertTrue(returned)
        XCTAssertEqual(Data(restored.markdown.utf8), after)
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("daily/2026-09-15.md").path))
    }

    func testNormalTemplatePreviewCreatesOnlyOnFirstEditAndRejectsConcurrentCreation() async throws {
        for concurrent in [false, true] {
            let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let model = editableModel(browser)
            await model.start(); await model.selectOriginalFolder(root)
            let day = try JournalDay("2026-09-17"), target = root.appendingPathComponent("daily/2026-09-17.md")
            let navigated = await model.navigate(to: day); XCTAssertTrue(navigated)
            XCTAssertFalse(FileManager.default.fileExists(atPath: target.path))
            XCTAssertTrue(model.markdown.contains("Unknown {{keep}}\r\n"))
            let edited = model.markdown + "First edit\r\n"
            model.updateBody(edited)
            if concurrent { try Data("Concurrent source\r\n".utf8).write(to: target) }
            let saved = await model.flushBody(); XCTAssertEqual(saved, !concurrent)
            XCTAssertEqual(try Data(contentsOf: target), Data((concurrent ? "Concurrent source\r\n" : edited).utf8))
            XCTAssertEqual(model.markdown, edited)
            if concurrent { XCTAssertTrue(model.bodyDirty); XCTAssertNotNil(model.bodyError) }
        }
    }

    func testNormalOriginalWriteFailureBlocksDateTabAndSourceUntilColdRecovery() async throws {
        for (purpose, stage) in [(FilePurpose.bodyDiary, FileStage.beforeReplace), (.bodyDiary, .afterReplace), (.bodyReceipt, .beforeWrite), (.bodyReceipt, .afterReplace)] {
            let (base, root, _) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let failure = OriginalEditingFailure(purpose, stage)
            let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base, failure: { try failure.check($0, $1) })
            let model = editableModel(browser)
            await model.start(); await model.selectOriginalFolder(root)
            failure.arm()
            let day = model.selectedDay, identity = model.sourceIdentity
            let edited = model.markdown + "Retained through interruption 🧠\r\n"
            model.updateBody(edited)
            let left = await model.prepareMainDestination(); XCTAssertFalse(left)
            XCTAssertEqual(model.markdown, edited)
            XCTAssertTrue(model.bodyDirty); XCTAssertNotNil(model.bodyError)
            // Before replacement / receipt keeps failing. After replacement has a
            // durable receipt and can finish cleanup safely on the same operation.
            if stage != .afterReplace {
                let moved = await model.navigate(to: try JournalDay("2026-09-15")); XCTAssertFalse(moved)
                await model.useLocalDiaries()
                XCTAssertEqual(model.selectedDay, day); XCTAssertEqual(model.sourceIdentity, identity)
                XCTAssertEqual(model.markdown, edited)
            }
            let restoredBrowser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base)
            let restored = editableModel(restoredBrowser)
            await restored.start()
            XCTAssertEqual(restored.markdown, edited); XCTAssertFalse(restored.bodyDirty)
            XCTAssertEqual(restored.bodyStatus, "已保存在此设备")
            let restoredAccess = try await restoredBrowser.originalAccess()
            let access = try XCTUnwrap(restoredAccess)
            let store = try await restoredBrowser.homeStore(access)
            let history = try await store.bodyRevisions(day)
            XCTAssertEqual(history.count, 1)
        }
    }

    func testOriginalSourceFailuresKeepTypedDraftAndReportSafeActionableError() async throws {
        for issue in ["changed", "deleted", "placeholder", "denied", "moved", "versionConflict"] {
            let (base, root, _) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let failure = OriginalEditingFailure(.bodyDiary, .beforeRead, error: OriginalVaultError.conflict)
            let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base, failure: { try failure.check($0, $1) })
            let model = editableModel(browser)
            await model.start(); await model.selectOriginalFolder(root)
            let selectedAccess = try await browser.originalAccess()
            let access = try XCTUnwrap(selectedAccess), store = try await browser.homeStore(access)
            let day = model.selectedDay, target = root.appendingPathComponent("daily/2026-09-16.md")
            let before = try Data(contentsOf: target), edited = model.markdown + "Typed source-bound draft\r\n"
            model.updateBody(edited)
            for _ in 0..<100 {
                if try await store.retainedBodyEdit(day)?.content == Data(edited.utf8) { break }
                try await Task.sleep(nanoseconds: 1_000_000)
            }
            let state = base.appendingPathComponent("state/original-vaults/" + access.identity)
            let draftURL = state.appendingPathComponent("body-drafts/2026-09-16.json")
            let retained = try Data(contentsOf: draftURL)
            let moved = base.appendingPathComponent("moved-riji")
            switch issue {
            case "changed": try Data("External edit\r\n".utf8).write(to: target)
            case "deleted": try FileManager.default.removeItem(at: target)
            case "placeholder":
                try FileManager.default.removeItem(at: target)
                try Data().write(to: root.appendingPathComponent("daily/.2026-09-16.md.icloud"))
            case "denied": try FileManager.default.setAttributes([.posixPermissions: 0], ofItemAtPath: target.path)
            case "moved": try FileManager.default.moveItem(at: root, to: moved)
            default: failure.arm()
            }
            defer { if issue == "denied" { try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: target.path) } }
            let left = await model.prepareMainDestination(); XCTAssertFalse(left, issue)
            let switched = await model.navigate(to: try JournalDay("2026-09-15")); XCTAssertFalse(switched, issue)
            await model.useLocalDiaries()
            XCTAssertTrue(model.isOriginalSource); XCTAssertEqual(model.selectedDay, day); XCTAssertEqual(model.markdown, edited)
            XCTAssertNotNil(model.bodyError, issue)
            XCTAssertFalse(model.bodyError?.contains(base.path) == true)
            XCTAssertFalse(model.bodyStatus.contains("已保存在"))
            XCTAssertEqual(try JSONDecoder().decode(BodyEdit.self, from: Data(contentsOf: draftURL)), try JSONDecoder().decode(BodyEdit.self, from: retained))
            if issue == "versionConflict" {
                XCTAssertEqual(model.bodySourceIssue, .cloudConflict)
                XCTAssertTrue(model.bodyError?.contains("查看并处理版本") == true)
            }
            if issue == "placeholder" { XCTAssertTrue(model.bodyError?.contains("下载") == true) }
            if issue == "changed" { XCTAssertEqual(try Data(contentsOf: target), Data("External edit\r\n".utf8)) }
            if issue == "deleted" || issue == "placeholder" { XCTAssertFalse(FileManager.default.fileExists(atPath: target.path)) }
            if issue == "moved" { XCTAssertEqual(try Data(contentsOf: moved.appendingPathComponent("daily/2026-09-16.md")), before) }
        }
    }
}


extension OriginalVaultStateTests {
    func testLatestInputSurvivesSourceLossColdStartAndCanExportWithoutSource() async throws {
        for missingRoot in [false, true] {
            let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let model = editableModel(browser)
            await model.start(); await model.selectOriginalFolder(root)
            let before = model.markdown, target = root.appendingPathComponent("daily/2026-09-16.md")
            if missingRoot { try FileManager.default.removeItem(at: root) }
            else {
                try FileManager.default.removeItem(at: target)
                try Data().write(to: root.appendingPathComponent("daily/.2026-09-16.md.icloud"))
            }
            model.updateBody(before + "First unavailable edit\r\n")
            let first = await model.flushBody(); XCTAssertFalse(first)
            XCTAssertTrue(model.canEditBody); XCTAssertFalse(model.bodyRecoveryUnavailable)
            let latest = before + "Latest unavailable edit e\u{301} 🧠\r\n"
            model.updateBody(latest)
            let second = await model.flushBody(); XCTAssertFalse(second)
            let exported = await model.exportRetainedBody()
            XCTAssertEqual(exported?.data, Data(latest.utf8)); XCTAssertTrue(exported?.isRecoveryDraft == true)
            let restored = editableModel(VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base))
            await restored.start()
            XCTAssertTrue(restored.isOriginalSource); XCTAssertTrue(restored.isReady); XCTAssertTrue(restored.sourceUnavailable)
            XCTAssertFalse(restored.canEditBody); XCTAssertFalse(restored.bodyRecoveryUnavailable)
            XCTAssertEqual(restored.markdown, latest)
            let coldExport = await restored.exportRetainedBody()
            XCTAssertEqual(coldExport?.data, Data(latest.utf8))
            XCTAssertFalse(restored.bodyStatus.contains("已保存在"))
            XCTAssertFalse(FileManager.default.fileExists(atPath: target.path))
        }
    }

    func testSameCollectionReselectionReplacesUnavailableAdapterBeforeFlushButForeignCannot() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        let identity = model.sourceIdentity, latest = model.markdown + "Latest reauthorized input\r\n"
        let moved = base.appendingPathComponent("moved-original")
        try FileManager.default.moveItem(at: root, to: moved)
        model.updateBody(latest)
        let saved = await model.flushBody(); XCTAssertFalse(saved)
        let foreign = base.appendingPathComponent("foreign")
        try FileManager.default.copyItem(at: moved, to: foreign)
        await model.selectOriginalFolder(foreign)
        XCTAssertEqual(model.sourceIdentity, identity); XCTAssertEqual(model.markdown, latest); XCTAssertTrue(model.bodyDirty)
        XCTAssertFalse(try String(contentsOf: foreign.appendingPathComponent("daily/2026-09-16.md")).contains("reauthorized"))
        await model.selectOriginalFolder(moved)
        XCTAssertEqual(model.sourceIdentity, identity); XCTAssertEqual(model.markdown, latest)
        XCTAssertFalse(model.bodyDirty); XCTAssertNil(model.bodyError)
        XCTAssertEqual(try Data(contentsOf: moved.appendingPathComponent("daily/2026-09-16.md")), Data(latest.utf8))
        let restored = editableModel(VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base))
        await restored.start(); XCTAssertEqual(restored.markdown, latest); XCTAssertFalse(restored.sourceUnavailable)
    }

    func testColdUnavailableRootCanReauthorizeSameCollectionAndNeverRecreateDeletedFile() async throws {
        for deleted in [false, true] {
            let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let model = editableModel(browser)
            await model.start(); await model.selectOriginalFolder(root)
            let latest = model.markdown + "Draft before revoked access\r\n"
            let target = root.appendingPathComponent("daily/2026-09-16.md")
            try FileManager.default.setAttributes([.posixPermissions: 0], ofItemAtPath: root.path)
            defer { try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: root.path) }
            model.updateBody(latest)
            let saved = await model.flushBody(); XCTAssertFalse(saved)
            let restored = editableModel(VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base))
            await restored.start(); XCTAssertEqual(restored.markdown, latest); XCTAssertTrue(restored.sourceUnavailable)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: root.path)
            if deleted { try FileManager.default.removeItem(at: target) }
            await restored.selectOriginalFolder(root)
            XCTAssertEqual(restored.markdown, latest)
            XCTAssertEqual(restored.bodyDirty, deleted)
            if deleted { XCTAssertFalse(FileManager.default.fileExists(atPath: target.path)); XCTAssertNotNil(restored.bodyError) }
            else { XCTAssertEqual(try Data(contentsOf: target), Data(latest.utf8)); XCTAssertNil(restored.bodyError) }
        }
    }

    func testNestedOriginalConflictMergePersistsSourceIdentityAndColdReopensBothVersions() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        let day = try JournalDay("2026-09-15"), target = root.appendingPathComponent("daily/2026-09/2026-09-15.md")
        let navigated = await model.navigate(to: day); XCTAssertTrue(navigated)
        let original = model.markdown, typed = original + "Local typed 🧠\r\n", external = original + "External edit\r\n"
        model.updateBody(typed); try Data(external.utf8).write(to: target)
        let saved = await model.flushBody(); XCTAssertFalse(saved)
        await model.reviewBodyConflict()
        XCTAssertEqual(model.bodyConflict?.source, Data(external.utf8))
        XCTAssertEqual(model.bodyConflict?.edit.content, Data(typed.utf8))
        let merged = external + "Reviewed local typed 🧠\r\n"
        model.updateBodyMerge(merged)
        let retained = await model.flushBodyMerge(); XCTAssertTrue(retained)
        let restoredBrowser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base)
        let restored = editableModel(restoredBrowser)
        await restored.start(); await restored.reviewBodyConflict()
        XCTAssertEqual(restored.selectedDay, day); XCTAssertEqual(restored.bodyMergeText, merged)
        let resolved = await restored.resolveBodyConflict(with: restored.bodyMergeText); XCTAssertTrue(resolved)
        XCTAssertEqual(try Data(contentsOf: target), Data(merged.utf8))
        let accessValue = try await restoredBrowser.originalAccess(), access = try XCTUnwrap(accessValue)
        let store = try await restoredBrowser.homeStore(access), history = try await store.bodyRevisions(day)
        XCTAssertTrue(history.allSatisfy { $0.sourceID == model.bodyConflict?.edit.sourceID })
        XCTAssertTrue(history.contains { $0.before == Data(original.utf8) && $0.content == Data(typed.utf8) })
        XCTAssertTrue(history.contains { $0.before == Data(external.utf8) })
        let cold = editableModel(VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base))
        await cold.start(); _ = await cold.navigate(to: day)
        XCTAssertEqual(cold.markdown, merged); XCTAssertFalse(cold.bodyDirty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("daily/2026-09-15.md").path))
    }
}


extension OriginalVaultStateTests {
    func testColdMovedRelativePathNeverRebindsLaterTypingToNewLocation() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        let day = try JournalDay("2026-09-15")
        _ = await model.navigate(to: day)
        let original = model.markdown, draft = original + "First retained edit\r\n"
        let from = root.appendingPathComponent("daily/2026-09/2026-09-15.md"), to = root.appendingPathComponent("daily/2026-09-15.md")
        try FileManager.default.moveItem(at: from, to: to)
        model.updateBody(draft)
        let first = await model.flushBody(); XCTAssertFalse(first)
        let restoredBrowser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base)
        let restored = editableModel(restoredBrowser)
        await restored.start()
        XCTAssertEqual(restored.markdown, draft)
        XCTAssertTrue(restored.bodyError?.contains("位置") == true)
        restored.updateBody(draft + "Newer after cold restore\r\n")
        let saved = await restored.flushBody(); XCTAssertFalse(saved)
        XCTAssertEqual(try Data(contentsOf: to), Data(original.utf8))
        let accessValue = try await restoredBrowser.originalAccess(), access = try XCTUnwrap(accessValue)
        let store = try await restoredBrowser.homeStore(access), retained = try await store.retainedBodyEdit(day)
        XCTAssertTrue(retained?.sourceID?.hasSuffix(":daily/2026-09/2026-09-15.md") == true)
        XCTAssertEqual(retained?.content, Data((draft + "Newer after cold restore\r\n").utf8))
        XCTAssertFalse(FileManager.default.fileExists(atPath: from.path))
    }
}

private final class ExternalRefreshGate: @unchecked Sendable {
    private let condition = NSCondition()
    private var armed = false
    private var released = false
    private(set) var count = 0
    var failure: Error?
    func arm() { condition.lock(); armed = true; released = false; count = 0; condition.unlock() }
    func reads() -> Int { condition.lock(); defer { condition.unlock() }; return count }
    func release() { condition.lock(); released = true; condition.broadcast(); condition.unlock() }
    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        guard purpose == .bodyDiary, stage == .beforeRead else { return }
        condition.lock(); defer { condition.unlock() }
        guard armed else { return }
        count += 1
        while !released { condition.wait() }
        if let failure { throw failure }
    }
}

extension OriginalVaultStateTests {
    private func waitForRefresh(_ condition: () -> Bool) async throws {
        for _ in 0..<300 {
            if condition() { return }
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTFail("External refresh did not reach its expected state")
        throw JournalError.storage
    }

    func testForegroundRefreshAdoptsExactBytesAndOwnSaveDoesNotReplaceEditor() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        let target = root.appendingPathComponent("daily/2026-09-16.md")
        let initialRevision = model.bodyReplacementRevision
        await model.refreshOriginalBody(); await model.refreshOriginalBody()
        XCTAssertEqual(model.bodyReplacementRevision, initialRevision)
        let external = Data("# External e\u{301} 中文 🧠\r\n| A | B |\r\n| --- | --- |\r\n| Changed | |\r\n".utf8)
        try external.write(to: target)
        model.foregroundTick(isActive: true)
        try await waitForRefresh { Data(model.markdown.utf8) == external }
        XCTAssertEqual(model.bodyReplacementRevision, initialRevision + 1)
        let revised = external + Data("Subsequent Riji edit\r\n".utf8)
        model.updateBody(String(decoding: revised, as: UTF8.self))
        let saved = await model.flushBody(); XCTAssertTrue(saved)
        await model.refreshOriginalBody()
        XCTAssertEqual(try Data(contentsOf: target), revised)
        XCTAssertEqual(model.bodyReplacementRevision, initialRevision + 1)
        let accessValue = try await browser.originalAccess()
        let access = try XCTUnwrap(accessValue)
        let store = try await browser.homeStore(access)
        let history = try await store.bodyRevisions(model.selectedDay)
        XCTAssertEqual(history.count, 1)
        let cold = editableModel(VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base))
        await cold.start(); XCTAssertEqual(Data(cold.markdown.utf8), revised)
    }

    func testProductionLifecycleCommitsOriginalDayBeforeBackgroundSaveAndRefreshesOnResume() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        let day = model.selectedDay, content = model.markdown + "Committed composition\r\n"
        var committed = false
        model.scenePhaseChanged(.inactive) {
            committed = true; model.updateBody(content, for: day)
        }
        XCTAssertTrue(committed); XCTAssertEqual(model.markdown, content)
        let target = root.appendingPathComponent("daily/2026-09-16.md")
        try await waitForRefresh { !model.bodyDirty }
        XCTAssertEqual(try Data(contentsOf: target), Data(content.utf8))
        let external = content + "Arrived while backgrounded\r\n"
        try Data(external.utf8).write(to: target)
        model.foregroundTick(isActive: false); await model.refreshOriginalBody()
        XCTAssertEqual(model.markdown, content)
        model.scenePhaseChanged(.active) { XCTFail("Active lifecycle must not interrupt input") }
        try await waitForRefresh { model.markdown == external }
        XCTAssertEqual(model.selectedDay, day)
    }

    func testRefreshSingleFlightRejectsInputAndNavigationArrivingDuringRead() async throws {
        for action in ["typing", "native", "navigation", "background"] {
            let (base, root, _) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let gate = ExternalRefreshGate(); defer { gate.release() }
            let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base,
                failure: { try gate.check($0, $1) })
            let model = editableModel(browser)
            await model.start(); await model.selectOriginalFolder(root)
            let before = model.markdown, revision = model.bodyReplacementRevision
            let target = root.appendingPathComponent("daily/2026-09-16.md")
            try Data((before + "External\r\n").utf8).write(to: target)
            gate.arm()
            let first = Task { await model.refreshOriginalBody() }
            try await waitForRefresh { gate.reads() > 0 }
            let second = Task { await model.refreshOriginalBody() }
            var navigation: Task<Bool, Never>?
            switch action {
            case "typing": model.updateBody(before + "Local typing\r\n")
            case "native": model.bodyRefreshAdmission = { false }
            case "navigation": navigation = Task { await model.navigate(to: try! JournalDay("2026-09-15")) }
            default: model.scenePhaseChanged(.inactive) { model.updateBody(before + "Leaving input\r\n") }
            }
            await Task.yield(); gate.release()
            await first.value; await second.value
            if let navigation { let moved = await navigation.value; XCTAssertTrue(moved); XCTAssertEqual(model.markdown, "# Nested original\r\n") }
            else {
                XCTAssertEqual(model.bodyReplacementRevision, revision, action)
                XCTAssertEqual(model.markdown, action == "typing" ? before + "Local typing\r\n" : action == "background" ? before + "Leaving input\r\n" : before)
                if action != "background" { XCTAssertEqual(gate.reads(), 1) }
            }
        }
    }

    func testNetZeroUndoRetiresOnlyBufferAndAdoptsExternalOrKeepsAbsentPreview() async throws {
        for missing in [false, true] {
            let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let model = editableModel(browser)
            await model.start(); await model.selectOriginalFolder(root)
            if missing { _ = await model.navigate(to: try JournalDay("2026-09-17")) }
            let original = model.markdown, day = model.selectedDay
            model.updateBody(original + "Temporary\r\n"); model.updateBody(original)
            let target = root.appendingPathComponent("daily/\(day.value).md")
            if !missing { try Data("External after undo\r\n".utf8).write(to: target) }
            await model.refreshOriginalBody()
            XCTAssertFalse(model.bodyDirty); XCTAssertNil(model.bodyError)
            XCTAssertEqual(model.markdown, missing ? original : "External after undo\r\n")
            XCTAssertEqual(FileManager.default.fileExists(atPath: target.path), !missing)
            let accessValue = try await browser.originalAccess()
        let access = try XCTUnwrap(accessValue), store = try await browser.homeStore(access)
            let retained = try await store.retainedBodyEdit(day), history = try await store.bodyRevisions(day)
            XCTAssertNil(retained); XCTAssertEqual(history.count, 1)
            await model.refreshOriginalBody()
            let repeatedHistory = try await store.bodyRevisions(day); XCTAssertEqual(repeatedHistory.count, 1)
        }
    }

    func testExplicitLatestAndLocalChoicesArchiveBothAndRejectStaleReview() async throws {
        for choice in ["latest", "local"] {
            let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let model = editableModel(browser)
            await model.start(); await model.selectOriginalFolder(root)
            let before = model.markdown, local = before + "Local\r\n", external = before + "External\r\n"
            let target = root.appendingPathComponent("daily/2026-09-16.md")
            model.updateBody(local); try Data(external.utf8).write(to: target)
            await model.refreshOriginalBody()
            XCTAssertEqual(model.markdown, local); XCTAssertEqual(model.bodySourceIssue, .contentConflict)
            XCTAssertFalse(model.needsSourceReauthorization)
            await model.reviewBodyConflict()
            let later = external + "Newer external\r\n"; try Data(later.utf8).write(to: target)
            let stale = await model.useLatestBodyVersion(); XCTAssertFalse(stale)
            XCTAssertEqual(model.markdown, local); XCTAssertEqual(try Data(contentsOf: target), Data(later.utf8))
            await model.reviewBodyConflict(refreshingSource: true)
            let resolved = await (choice == "latest" ? model.useLatestBodyVersion() : model.keepLocalBodyVersion())
            XCTAssertTrue(resolved)
            XCTAssertEqual(model.markdown, choice == "latest" ? later : local)
            XCTAssertEqual(try Data(contentsOf: target), Data(model.markdown.utf8))
            let accessValue = try await browser.originalAccess()
        let access = try XCTUnwrap(accessValue), store = try await browser.homeStore(access)
            let revisions = try await store.bodyRevisions(model.selectedDay)
            XCTAssertTrue(revisions.contains { $0.before == Data(before.utf8) && $0.content == Data(local.utf8) })
            XCTAssertTrue(revisions.contains { $0.before == Data(later.utf8) })
            let cold = editableModel(VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base))
            await cold.start(); XCTAssertEqual(cold.markdown, model.markdown); XCTAssertFalse(cold.hasRetainedBodyMerge)
        }
    }

    func testUnavailableDeletedAndSystemConflictRefreshPreserveVisibleSourceAndRetry() async throws {
        for kind in ["deleted", "placeholder", "denied", "cloud"] {
            let (base, root, _) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let gate = ExternalRefreshGate()
            let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base,
                failure: { try gate.check($0, $1) })
            let model = editableModel(browser)
            await model.start(); await model.selectOriginalFolder(root)
            let before = model.markdown, revision = model.bodyReplacementRevision
            let target = root.appendingPathComponent("daily/2026-09-16.md")
            let placeholder = root.appendingPathComponent("daily/.2026-09-16.md.icloud")
            if kind == "cloud" { gate.failure = OriginalVaultError.conflict; gate.arm(); gate.release() }
            else if kind == "denied" { try FileManager.default.setAttributes([.posixPermissions: 0], ofItemAtPath: target.path) }
            else {
                try FileManager.default.removeItem(at: target)
                if kind == "placeholder" { try Data().write(to: placeholder) }
            }
            await model.refreshOriginalBody()
            XCTAssertEqual(model.markdown, before); XCTAssertEqual(model.bodyReplacementRevision, revision)
            XCTAssertNotNil(model.bodyError); XCTAssertFalse(model.canReviewBodyVersions)
            XCTAssertEqual(model.bodySourceIssue == .cloudConflict, kind == "cloud")
            XCTAssertEqual(model.needsSourceReauthorization, kind != "cloud")
            let exported = await model.exportRetainedBody(); XCTAssertEqual(exported?.data, Data(before.utf8))
            if kind == "deleted" || kind == "placeholder" { XCTAssertFalse(FileManager.default.fileExists(atPath: target.path)) }
            gate.failure = nil
            if kind == "placeholder" { try FileManager.default.removeItem(at: placeholder) }
            if kind == "denied" { try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: target.path) }
            try Data((before + "Available again\r\n").utf8).write(to: target)
            await model.retryOriginalBodySource()
            XCTAssertEqual(model.markdown, before + "Available again\r\n"); XCTAssertNil(model.bodyError)
            XCTAssertFalse(model.sourceUnavailable)
        }
    }
}

extension OriginalVaultStateTests {
    func testLateNativeInputAfterRefreshPublicationKeepsOriginalBaseAndIgnoresUnchangedEcho() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        let day = model.selectedDay, before = model.markdown, origin = model.bodyInputOrigin
        let target = root.appendingPathComponent("daily/2026-09-16.md"), external = before + "External published\r\n"
        try Data(external.utf8).write(to: target)
        await model.refreshOriginalBody()
        model.updateBody(before, for: day, origin: origin)
        XCTAssertEqual(model.markdown, external); XCTAssertFalse(model.bodyDirty); XCTAssertNil(model.bodyError)
        let local = before + "Late native input\r\n"
        model.updateBody(local, for: day, origin: origin)
        XCTAssertEqual(model.markdown, local); XCTAssertTrue(model.bodyDirty)
        let saved = await model.flushBody(); XCTAssertFalse(saved)
        XCTAssertEqual(try Data(contentsOf: target), Data(external.utf8))
        let accessValue = try await browser.originalAccess(), access = try XCTUnwrap(accessValue)
        let store = try await browser.homeStore(access), retained = try await store.retainedBodyEdit(day)
        XCTAssertEqual(retained?.before, Data(before.utf8)); XCTAssertEqual(retained?.content, Data(local.utf8))
        await model.reviewBodyConflict()
        let selected = await model.useLatestBodyVersion(); XCTAssertTrue(selected)
        model.updateBody(local + "Stale after explicit choice\r\n", for: day, origin: origin)
        XCTAssertEqual(model.markdown, external); XCTAssertFalse(model.bodyDirty)
        let currentOrigin = model.bodyInputOrigin
        _ = await model.navigate(to: day)
        model.updateBody(external + "Stale after reopen\r\n", for: day, origin: currentOrigin)
        XCTAssertEqual(model.markdown, external); XCTAssertFalse(model.bodyDirty)
    }

    func testLateNativeInputAfterReadFailureRetainsOriginalSourceAndCanExport() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        let day = model.selectedDay, before = model.markdown, origin = model.bodyInputOrigin
        let target = root.appendingPathComponent("daily/2026-09-16.md")
        try FileManager.default.removeItem(at: target)
        await model.refreshOriginalBody()
        XCTAssertTrue(model.sourceUnavailable); XCTAssertFalse(model.canEditBody)
        let local = before + "Input from still-visible native editor\r\n"
        model.updateBody(local, for: day, origin: origin)
        XCTAssertEqual(model.markdown, local); XCTAssertTrue(model.bodyDirty)
        let exported = await model.exportRetainedBody(); XCTAssertEqual(exported?.data, Data(local.utf8))
        let saved = await model.flushBody(); XCTAssertFalse(saved)
        XCTAssertFalse(FileManager.default.fileExists(atPath: target.path))
        let accessValue = try await browser.originalAccess(), access = try XCTUnwrap(accessValue)
        let store = try await browser.homeStore(access), retained = try await store.retainedBodyEdit(day)
        XCTAssertEqual(retained?.before, Data(before.utf8)); XCTAssertEqual(retained?.content, Data(local.utf8))
    }
}

extension OriginalVaultStateTests {
    func testRapidLateNativeCallbacksAndNetZeroUndoRemainBoundToOriginalBase() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        let day = model.selectedDay, before = model.markdown, origin = model.bodyInputOrigin
        let target = root.appendingPathComponent("daily/2026-09-16.md"), external = before + "External refresh\r\n"
        try Data(external.utf8).write(to: target)
        await model.refreshOriginalBody()
        model.updateBody(before + "First", for: day, origin: origin)
        XCTAssertEqual(model.markdown, before + "First")
        model.updateBody(before + "First second", for: day, origin: origin)
        XCTAssertEqual(model.markdown, before + "First second")
        model.updateBody(before, for: day, origin: origin)
        XCTAssertEqual(model.markdown, before); XCTAssertTrue(model.bodyDirty)
        await model.refreshOriginalBody()
        XCTAssertEqual(model.markdown, external); XCTAssertFalse(model.bodyDirty); XCTAssertNil(model.bodyError)
        XCTAssertEqual(try Data(contentsOf: target), Data(external.utf8))
    }

    func testMatchingLocalAndExternalChangesAdoptBaseWithoutCompetingEdit() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        let target = root.appendingPathComponent("daily/2026-09-16.md"), same = model.markdown + "Same edit\r\n"
        model.updateBody(same); try Data(same.utf8).write(to: target)
        await model.refreshOriginalBody()
        XCTAssertEqual(model.markdown, same); XCTAssertFalse(model.bodyDirty); XCTAssertNil(model.bodyError)
        let accessValue = try await browser.originalAccess(), access = try XCTUnwrap(accessValue)
        let store = try await browser.homeStore(access), retained = try await store.retainedBodyEdit(model.selectedDay)
        XCTAssertNil(retained)
        model.updateBody(same + "Next Riji edit\r\n")
        let saved = await model.flushBody(); XCTAssertTrue(saved)
        XCTAssertEqual(try Data(contentsOf: target), Data((same + "Next Riji edit\r\n").utf8))
    }
}

extension OriginalVaultStateTests {
    func testActiveLifecycleFencesNewInputUntilHeldRefreshCompletesOrFails() async throws {
        for failing in [false, true] {
            let (base, root, _) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let gate = ExternalRefreshGate(); defer { gate.release() }
            let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base,
                failure: { try gate.check($0, $1) })
            let model = editableModel(browser)
            await model.start(); await model.selectOriginalFolder(root)
            let before = model.markdown, day = model.selectedDay
            await model.scenePhaseChanged(.inactive, commitEditing: {})?.value
            let external = before + "Changed before resume\r\n"
            try Data(external.utf8).write(to: root.appendingPathComponent("daily/2026-09-16.md"))
            if failing { gate.failure = JournalError.storage }
            gate.arm()
            var nativeAdmission: [Bool] = []
            model.bodyResumeAdmission = { nativeAdmission.append($0) }
            model.scenePhaseChanged(.active) { XCTFail() }
            XCTAssertTrue(model.isCheckingResumedSource); XCTAssertFalse(model.canEditBody)
            XCTAssertEqual(nativeAdmission, [false])
            let fencedOrigin = model.bodyInputOrigin
            model.updateBody(before + "Premature direct typing", for: day)
            model.updateBody(before + "Premature native typing", for: day, origin: fencedOrigin)
            XCTAssertEqual(model.markdown, before)
            try await waitForRefresh { gate.reads() > 0 }
            gate.release()
            try await waitForRefresh { !model.isCheckingResumedSource }
            XCTAssertEqual(nativeAdmission, [false, true]); XCTAssertTrue(model.canEditBody)
            XCTAssertEqual(model.markdown, failing ? before : external)
            XCTAssertEqual(model.bodyError != nil, failing)
            if failing {
                gate.failure = nil
                await model.retryOriginalBodySource()
                XCTAssertEqual(model.markdown, external)
            }
            model.updateBody(external + "After resume\r\n")
            let saved = await model.flushBody(); XCTAssertTrue(saved)
        }
    }
}

extension OriginalVaultStateTests {
    func testReadableDivergentSourceClearsUnavailableAndRecoveredNavigationErrorOnly() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        let before = model.markdown, target = root.appendingPathComponent("daily/2026-09-16.md")
        let placeholder = root.appendingPathComponent("daily/.2026-09-16.md.icloud")
        try FileManager.default.removeItem(at: target); try Data().write(to: placeholder)
        _ = await model.navigate(to: model.selectedDay)
        XCTAssertNotNil(model.errorMessage)
        try FileManager.default.removeItem(at: placeholder); try Data(before.utf8).write(to: target)
        await model.retryOriginalBodySource()
        XCTAssertNil(model.errorMessage); XCTAssertFalse(model.sourceUnavailable)
        model.updateBody(before + "Local\r\n")
        try FileManager.default.removeItem(at: target)
        await model.refreshOriginalBody(); XCTAssertTrue(model.sourceUnavailable)
        try Data((before + "External\r\n").utf8).write(to: target)
        await model.retryOriginalBodySource()
        XCTAssertFalse(model.sourceUnavailable); XCTAssertEqual(model.bodySourceIssue, .contentConflict)
        XCTAssertTrue(model.canReviewBodyVersions); XCTAssertEqual(model.markdown, before + "Local\r\n")
        model.sourcePickerFailed(NSError(domain: NSCocoaErrorDomain, code: NSFileReadNoPermissionError))
        let unrelated = model.errorMessage
        await model.refreshOriginalBody()
        XCTAssertEqual(model.errorMessage, unrelated, "Successful refresh must not erase a failed folder-selection action")
    }

    func testStaleLatestAndLocalChoicesPreserveHandMergeAndSuccessfulChoiceArchivesIt() async throws {
        for latestChoice in [true, false] {
            let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let model = editableModel(browser)
            await model.start(); await model.selectOriginalFolder(root)
            let before = model.markdown, target = root.appendingPathComponent("daily/2026-09-16.md")
            model.updateBody(before + "Local\r\n"); try Data((before + "External\r\n").utf8).write(to: target)
            await model.refreshOriginalBody(); await model.reviewBodyConflict()
            let manual = before + "Unique handwritten merge\r\n"
            model.updateBodyMerge(manual); _ = await model.flushBodyMerge()
            let newer = before + "Newer external\r\n"; try Data(newer.utf8).write(to: target)
            let stale = await (latestChoice ? model.useLatestBodyVersion() : model.keepLocalBodyVersion())
            XCTAssertFalse(stale); XCTAssertEqual(model.bodyMergeText, manual)
            let accessValue = try await browser.originalAccess(), access = try XCTUnwrap(accessValue)
            let store = try await browser.homeStore(access), retained = try await store.retainedBodyMerge(model.selectedDay)
            XCTAssertEqual(retained?.content, Data(manual.utf8)); XCTAssertEqual(try Data(contentsOf: target), Data(newer.utf8))
            await model.reviewBodyConflict(refreshingSource: true)
            let selected = await (latestChoice ? model.useLatestBodyVersion() : model.keepLocalBodyVersion())
            XCTAssertTrue(selected)
            let merge = try await store.retainedBodyMerge(model.selectedDay), history = try await store.bodyRevisions(model.selectedDay)
            XCTAssertNil(merge); XCTAssertTrue(history.contains { $0.content == Data(manual.utf8) })
        }
    }

    func testFailedVersionSelectionKeepsHandMergeAndUncertainOperationOwner() async throws {
        for purpose in [FilePurpose.bodyResolution, .bodyDiary, .bodyReceipt] {
            let (base, root, _) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let failure = OriginalEditingFailure(purpose, purpose == .bodyResolution ? .afterReplace : .beforeReplace)
            let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base,
                failure: { try failure.check($0, $1) })
            let model = editableModel(browser)
            await model.start(); await model.selectOriginalFolder(root)
            let before = model.markdown, target = root.appendingPathComponent("daily/2026-09-16.md")
            model.updateBody(before + "Local\r\n"); try Data((before + "External\r\n").utf8).write(to: target)
            await model.refreshOriginalBody(); await model.reviewBodyConflict()
            let manual = before + "Hand merge survives selection failure\r\n"
            model.updateBodyMerge(manual); _ = await model.flushBodyMerge()
            failure.arm()
            let selected = await model.keepLocalBodyVersion(); XCTAssertFalse(selected)
            XCTAssertEqual(model.bodyMergeText, manual)
            let accessValue = try await browser.originalAccess(), access = try XCTUnwrap(accessValue)
            let store = try await browser.homeStore(access), retained = try await store.retainedBodyMerge(model.selectedDay)
            XCTAssertEqual(retained?.content, Data(manual.utf8))
            let recovery = await store.bodyRecoveryState(model.selectedDay)
            XCTAssertTrue(recovery.hasOperation); XCTAssertTrue(recovery.isReadable)
            let coldBrowser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base)
            let cold = editableModel(coldBrowser); await cold.start()
            XCTAssertTrue(cold.hasRetainedBodyMerge); await cold.reviewBodyConflict()
            XCTAssertEqual(cold.bodyMergeText, manual)
        }
    }

    func testKeepLocalPreflightExplainsChangedMetadataAndKeepsManualAndLatestChoices() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let target = root.appendingPathComponent("daily/2026-09-16.md")
        let before = "---\r\ntags: original\r\n---\r\nBody\r\n", external = "---\r\ntags: external\r\n---\r\nExternal body\r\n"
        try Data(before.utf8).write(to: target)
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        model.updateBody(before + "Local input\r\n"); try Data(external.utf8).write(to: target)
        await model.refreshOriginalBody(); await model.reviewBodyConflict()
        let manual = external + "Hand merged local input\r\n"
        model.updateBodyMerge(manual); _ = await model.flushBodyMerge()
        XCTAssertNotNil(model.localBodyChoiceExplanation); XCTAssertTrue(model.canEditBodyMerge)
        let local = await model.keepLocalBodyVersion(); XCTAssertFalse(local)
        XCTAssertEqual(model.bodyMergeText, manual); XCTAssertEqual(try Data(contentsOf: target), Data(external.utf8))
        let merged = await model.resolveBodyConflict(with: manual); XCTAssertTrue(merged)
        XCTAssertEqual(try Data(contentsOf: target), Data(manual.utf8))
    }

    func testColdMovedDraftImmediatelyClassifiesSourceIdentity() async throws {
        let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        model.updateBody(model.markdown + "Retained\r\n")
        let accessValue = try await browser.originalAccess(), access = try XCTUnwrap(accessValue)
        let store = try await browser.homeStore(access)
        for _ in 0..<100 {
            if try await store.retainedBodyEdit(model.selectedDay) != nil { break }
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        try FileManager.default.moveItem(at: root.appendingPathComponent("daily/2026-09-16.md"),
            to: root.appendingPathComponent("daily/2026-09/2026-09-16.md"))
        let cold = editableModel(VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base))
        await cold.start()
        XCTAssertEqual(cold.bodySourceIssue, .identity); XCTAssertTrue(cold.needsSourceReauthorization)
        XCTAssertFalse(cold.canReviewBodyVersions)
    }

    func testBackgroundNetZeroUndoNeverWritesExternalFileOrCreatesAbsentDay() async throws {
        for missing in [false, true] {
            let (base, root, browser) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let model = editableModel(browser)
            await model.start(); await model.selectOriginalFolder(root)
            if missing { _ = await model.navigate(to: try JournalDay("2026-09-17")) }
            let before = model.markdown, target = root.appendingPathComponent("daily/\(model.selectedDay.value).md")
            model.updateBody(before + "Temporary typing\r\n")
            if !missing { try Data("External file remains\r\n".utf8).write(to: target) }
            let flush = model.scenePhaseChanged(.inactive) { model.updateBody(before) }
            await flush?.value
            XCTAssertFalse(model.bodyDirty); XCTAssertNil(model.bodyError)
            if missing { XCTAssertFalse(FileManager.default.fileExists(atPath: target.path)) }
            else { XCTAssertEqual(try Data(contentsOf: target), Data("External file remains\r\n".utf8)) }
        }
    }

    func testRecoveryExportStillReturnsExactVisibleBytesWhenDraftRetentionFails() async throws {
        let (base, root, _) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let failure = OriginalEditingFailure(.bodyDraft, .beforeWrite)
        let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base,
            failure: { try failure.check($0, $1) })
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        let before = model.markdown, latest = before + "Exact export e\u{301} 中文 🧠\r\n"
        failure.arm(); model.updateBody(latest)
        let document = await model.exportRetainedBody()
        XCTAssertEqual(document?.data, Data(latest.utf8)); XCTAssertTrue(document?.isRecoveryDraft == true)
        XCTAssertTrue(model.bodyDirty); XCTAssertNotNil(model.bodyError); XCTAssertEqual(model.markdown, latest)
        XCTAssertEqual(try Data(contentsOf: root.appendingPathComponent("daily/2026-09-16.md")), Data(before.utf8))
    }
}

private final class ResumeWriteGate: @unchecked Sendable {
    private let condition = NSCondition()
    private var armed = false
    private var reached = false
    private var released = false
    func arm() { condition.lock(); armed = true; condition.unlock() }
    func hasReached() -> Bool { condition.lock(); defer { condition.unlock() }; return reached }
    func release() { condition.lock(); released = true; condition.broadcast(); condition.unlock() }
    func check(_ purpose: FilePurpose, _ stage: FileStage) {
        guard purpose == .bodyDiary, stage == .beforeReplace else { return }
        condition.lock(); defer { condition.unlock() }
        guard armed else { return }
        reached = true
        while !released { condition.wait() }
    }
}

extension OriginalVaultStateTests {
    func testResumeWaitsForBusyNavigationBeforePerformingItsOwnRead() async throws {
        let (base, root, _) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let gate = ExternalRefreshGate(); defer { gate.release() }
        let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base,
            failure: { try gate.check($0, $1) })
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        model.resumeRefreshTimeout = 1
        gate.arm()
        let navigation = Task { await model.navigate(to: model.selectedDay) }
        try await waitForRefresh { gate.reads() > 0 }
        XCTAssertTrue(model.isBusy)
        model.scenePhaseChanged(.active) { XCTFail() }
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertTrue(model.isCheckingResumedSource); XCTAssertFalse(model.canEditBody)
        gate.release(); let moved = await navigation.value; XCTAssertTrue(moved)
        try await waitForRefresh { !model.isCheckingResumedSource }
        XCTAssertGreaterThanOrEqual(gate.reads(), 2, "Resume must perform a read after busy navigation settles")
        XCTAssertTrue(model.canEditBody); XCTAssertNil(model.bodyError)
    }

    func testResumeWaitsForOwnedBodyWriteAndRetainsItsReceipt() async throws {
        let (base, root, _) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let gate = ResumeWriteGate(); defer { gate.release() }
        let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base,
            failure: { gate.check($0, $1) })
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        model.resumeRefreshTimeout = 1
        let changed = model.markdown + "Owned body write\r\n"
        gate.arm(); model.updateBody(changed)
        let flush = Task { await model.flushBody() }
        try await waitForRefresh { gate.hasReached() }
        model.scenePhaseChanged(.active) { XCTFail() }
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertTrue(model.isCheckingResumedSource); XCTAssertEqual(model.markdown, changed)
        gate.release(); let saved = await flush.value; XCTAssertTrue(saved)
        try await waitForRefresh { !model.isCheckingResumedSource }
        XCTAssertEqual(try Data(contentsOf: root.appendingPathComponent("daily/2026-09-16.md")), Data(changed.utf8))
        XCTAssertFalse(model.bodyDirty); XCTAssertNil(model.bodyError)
        let accessValue = try await browser.originalAccess(), access = try XCTUnwrap(accessValue)
        let store = try await browser.homeStore(access), history = try await store.bodyRevisions(model.selectedDay)
        XCTAssertEqual(history.filter { $0.content == Data(changed.utf8) }.count, 1)
    }

    func testResumeDeadlineReleasesFenceWithoutCancellingHeldReadAndRetryRecovers() async throws {
        let (base, root, _) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let gate = ExternalRefreshGate(); defer { gate.release() }
        let browser = VaultBrowserStore(stateDirectory: base.appendingPathComponent("state"), appRoot: base,
            failure: { try gate.check($0, $1) })
        let model = editableModel(browser)
        await model.start(); await model.selectOriginalFolder(root)
        let before = model.markdown, target = root.appendingPathComponent("daily/2026-09-16.md")
        let external = before + "External after slow coordination\r\n"; try Data(external.utf8).write(to: target)
        model.resumeRefreshTimeout = 0.05
        gate.arm(); model.scenePhaseChanged(.active) { XCTFail() }
        try await waitForRefresh { gate.reads() > 0 }
        try await waitForRefresh { !model.isCheckingResumedSource }
        XCTAssertEqual(model.markdown, before); XCTAssertTrue(model.canEditBody)
        XCTAssertTrue(model.bodyError?.contains("暂时没有完成") == true)
        gate.release()
        await model.refreshOriginalBody()
        XCTAssertEqual(model.markdown, before, "The old timed-out read must not publish a late replacement")
        model.resumeRefreshTimeout = 1
        await model.retryOriginalBodySource()
        XCTAssertEqual(model.markdown, external); XCTAssertNil(model.bodyError); XCTAssertTrue(model.canEditBody)
        XCTAssertEqual(try Data(contentsOf: target), Data(external.utf8))
    }
}
