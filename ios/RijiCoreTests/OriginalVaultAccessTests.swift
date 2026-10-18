import XCTest
import Darwin
@testable import RijiCore

final class OriginalVaultAccessTests: XCTestCase {
    private func fixture() throws -> (URL, URL) {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("OriginalVault-\(UUID())")
        let root = base.appendingPathComponent("riji")
        for folder in ["daily/2026-09", "templates"] {
            try FileManager.default.createDirectory(at: root.appendingPathComponent(folder), withIntermediateDirectories: true)
        }
        try Data("# {{date:YYYY-MM-DD}} {{date:dddd}}\r\n{{unknown}}\r\n| 名称 | 内容 |\r\n| --- | --- |\r\n| 空 | |\r\n".utf8)
            .write(to: root.appendingPathComponent("templates/daily.md"))
        return (base, root)
    }

    private func store(_ base: URL, _ root: URL, writable: Bool = false) throws -> LocalJournalStore {
        try LocalJournalStore(journalDirectory: base.appendingPathComponent("unused"), stateDirectory: base.appendingPathComponent("state"),
                              original: OriginalVaultAccess(root: root, writePolicy: writable ? .coordinatedUserEdit : .readOnly))
    }

    func testNestedOriginalAndTemplatePreviewPreserveBytesWithoutCreatingDays() async throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let day = try JournalDay("2026-09-16"), bytes = Data("---\r\nunknown: keep\r\n---\r\n# 中文 🧠 e\u{301}\r\n".utf8)
        let source = root.appendingPathComponent("daily/2026-09/2026-09-16.md")
        try bytes.write(to: source)
        let original = try store(base, root)
        let opened = try await original.open(day), directory = try await original.directory()
        XCTAssertEqual(Data(opened.markdown.utf8), bytes); XCTAssertEqual(directory.map(\.day), [day])
        let missing = try await original.open(JournalDay("2026-09-17"))
        XCTAssertFalse(missing.isSaved); XCTAssertTrue(missing.markdown.contains("2026-09-17 星期四\r\n{{unknown}}"))
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("daily/2026-09-17.md").path))
        XCTAssertEqual(try Data(contentsOf: source), bytes)
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("Weekly").path))
    }

    func testUnrelatedHistoricalPlaceholderDoesNotBlockTodayButTargetPlaceholderDoes() async throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        try Data().write(to: root.appendingPathComponent("daily/.2026-09-15.md.icloud"))
        try Data("Today\n".utf8).write(to: root.appendingPathComponent("daily/2026-09-16.md"))
        let access = try OriginalVaultAccess(root: root)
        XCTAssertEqual(try access.read(JournalDay("2026-09-16")), Data("Today\n".utf8))
        XCTAssertThrowsError(try access.read(JournalDay("2026-09-15")))
        XCTAssertEqual(try access.paths().count, 2)
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("daily/2026-09-15.md").path))
    }

    func testAmbiguityUnavailableDirectoryMissingTemplateAndUnsafeConfigurationFailClosed() throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let day = try JournalDay("2026-09-16"), access = try OriginalVaultAccess(root: root)
        for path in ["daily/2026-09-16.md", "daily/2026-09/2026-09-16.md"] { try Data("Synthetic\n".utf8).write(to: root.appendingPathComponent(path)) }
        XCTAssertThrowsError(try access.read(day))
        try FileManager.default.removeItem(at: root.appendingPathComponent("daily/2026-09-16.md"))
        try FileManager.default.removeItem(at: root.appendingPathComponent("templates/daily.md"))
        XCTAssertEqual(try access.read(day), Data("Synthetic\n".utf8))
        XCTAssertThrowsError(try access.templateVersion())
        try FileManager.default.createDirectory(at: root.appendingPathComponent(".obsidian"), withIntermediateDirectories: true)
        try Data(#"{"folder":"../outside","template":"templates/daily"}"#.utf8).write(to: root.appendingPathComponent(".obsidian/daily-notes.json"))
        XCTAssertThrowsError(try OriginalVaultAccess(root: root))
    }

    func testSafeConfigurationAndParentSelectionUseSameCollectionIdentity() throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        try FileManager.default.createDirectory(at: root.appendingPathComponent(".obsidian"), withIntermediateDirectories: true)
        try Data(#"{"folder":"daily/2026-09","template":"templates/daily","format":"YYYY-MM-DD"}"#.utf8).write(to: root.appendingPathComponent(".obsidian/daily-notes.json"))
        let direct = try OriginalVaultAccess(root: root), parent = try OriginalVaultAccess(root: base)
        XCTAssertEqual(direct.identity, parent.identity)
        XCTAssertEqual(try direct.path(for: JournalDay("2026-09-16")), ["daily", "2026-09", "2026-09-16.md"])
    }

    func testCoordinatedSyntheticBodyWriteHasReceiptAndRejectsChangeDeleteAndConcurrentCreate() async throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let day = try JournalDay("2026-09-16"), source = root.appendingPathComponent("daily/2026-09/2026-09-16.md")
        let before = Data("# Synthetic\r\nBefore\r\n".utf8), after = before + Data("本机编辑 🧠\r\n".utf8)
        try before.write(to: source)
        let original = try store(base, root, writable: true)
        let edit = try await BodyEdit(day: day, before: before, content: after, sourceID: original.bodySourceID(day))
        try await original.retainBodyEdit(edit); try await original.commitBodyEdit(edit)
        try await original.commitBodyEdit(edit)
        XCTAssertEqual(try Data(contentsOf: source), after)
        let retained = try await original.retainedBodyEdit(day); XCTAssertNil(retained)
        let next = BodyEdit(day: day, before: after, content: before, sourceID: edit.sourceID)
        try await original.retainBodyEdit(next)
        try Data("External\n".utf8).write(to: source)
        do { try await original.commitBodyEdit(next); XCTFail("External change must be retained") } catch {}
        XCTAssertEqual(try Data(contentsOf: source), Data("External\n".utf8))
        try FileManager.default.removeItem(at: source)
        do { try await original.commitBodyEdit(next); XCTFail("Deletion must not resurrect the original") } catch {}
        XCTAssertFalse(FileManager.default.fileExists(atPath: source.path))
        let absent = try JournalDay("2026-09-17")
        let new = try await BodyEdit(day: absent, before: nil, content: after, sourceID: original.bodySourceID(absent))
        try Data("Concurrent\n".utf8).write(to: root.appendingPathComponent("daily/2026-09-17.md"))
        do { try await original.commitBodyEdit(new); XCTFail("Creation must not overwrite") } catch {}
        XCTAssertEqual(try Data(contentsOf: root.appendingPathComponent("daily/2026-09-17.md")), Data("Concurrent\n".utf8))
    }

    func testReadOnlyGateAndRecoveryCannotCrossCollectionsOrChangedRelativePaths() async throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let day = try JournalDay("2026-09-16"), before = Data("Original\n".utf8)
        let source = root.appendingPathComponent("daily/2026-09/2026-09-16.md"); try before.write(to: source)
        let original = try store(base, root)
        let edit = try await BodyEdit(day: day, before: before, content: Data("Edit\n".utf8), sourceID: original.bodySourceID(day))
        try await original.retainBodyEdit(edit)
        do { try await original.commitBodyEdit(edit); XCTFail("Default construction is read only") } catch {}
        XCTAssertEqual(try Data(contentsOf: source), before)
        let (otherBase, otherRoot) = try fixture(); defer { try? FileManager.default.removeItem(at: otherBase) }
        let other = try store(base, otherRoot)
        let otherDraft = try await other.retainedBodyEdit(day); XCTAssertNil(otherDraft)
        do { try await other.retainBodyEdit(edit); XCTFail("Different vault must reject identity") } catch {}
        try FileManager.default.moveItem(at: source, to: root.appendingPathComponent("daily/2026-09-16.md"))
        let recovery = await original.bodyRecoveryState(day)
        XCTAssertTrue(recovery.isReadable)
        XCTAssertEqual(recovery.edit, edit)
        let writable = try store(base, root, writable: true)
        do { try await writable.commitBodyEdit(edit); XCTFail("Readable local recovery cannot authorize a moved source") }
        catch { guard case OriginalVaultError.changedIdentity = error else { return XCTFail("Expected changedIdentity") } }
        XCTAssertEqual(try Data(contentsOf: root.appendingPathComponent("daily/2026-09-16.md")), before)
    }
}

extension OriginalVaultAccessTests {
    func testNestedDeletionReportsChangedIdentityAndPreservesDraftWithoutReceiptOrResurrection() async throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let day = try JournalDay("2026-09-16"), before = Data("# Synthetic\nBefore\n".utf8)
        let target = root.appendingPathComponent("daily/2026-09/2026-09-16.md")
        try before.write(to: target)
        let access = try OriginalVaultAccess(root: root, writePolicy: .syntheticFixture)
        let original = try LocalJournalStore(journalDirectory: base.appendingPathComponent("unused"),
                                            stateDirectory: base.appendingPathComponent("state"), original: access)
        let edit = BodyEdit(day: day, before: before, content: Data("# Synthetic\nDraft\n".utf8), sourceID: try access.sourceID(day))
        try await original.retainBodyEdit(edit)
        let state = base.appendingPathComponent("state/original-vaults/" + access.identity)
        let draft = state.appendingPathComponent("body-drafts/2026-09-16.json")
        let retained = try Data(contentsOf: draft)
        try FileManager.default.removeItem(at: target)
        do { try await original.commitBodyEdit(edit); XCTFail("Deleted nested source must be refused") }
        catch { guard case OriginalVaultError.changedIdentity = error else { return XCTFail("Expected changedIdentity") } }
        XCTAssertEqual(try Data(contentsOf: draft), retained)
        XCTAssertEqual(try JSONDecoder().decode(BodyEdit.self, from: retained), edit)
        XCTAssertFalse(FileManager.default.fileExists(atPath: state.appendingPathComponent("body-receipts/\(edit.id.uuidString).json").path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: target.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("daily/2026-09-16.md").path))
        XCTAssertNil(try access.paths()[day])
    }

    func testCoordinatedWriterInterruptedBeforeAndAfterReplaceRecoversSameOperation() async throws {
        for stage in [FileStage.beforeReplace, .afterReplace] {
            let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let day = try JournalDay("2026-09-16"), before = Data("# Before\r\n".utf8), after = Data("# After 🧠\r\n".utf8)
            let source = root.appendingPathComponent("daily/2026-09/2026-09-16.md")
            try before.write(to: source)
            let access = try OriginalVaultAccess(root: root, writePolicy: .syntheticFixture, failure: { purpose, current in
                if purpose == .bodyDiary && current == stage { throw JournalError.storage }
            })
            let original = try LocalJournalStore(journalDirectory: base.appendingPathComponent("unused"), stateDirectory: base.appendingPathComponent("state"), original: access)
            let edit = BodyEdit(day: day, before: before, content: after, sourceID: try access.sourceID(day))
            do { try await original.commitBodyEdit(edit); XCTFail("Injected write must remain uncertain") } catch {}
            let pending = try await original.retainedBodyEdit(day)
            XCTAssertEqual(pending, edit)
            XCTAssertEqual(try Data(contentsOf: source), stage == .beforeReplace ? before : after)
            let reopened = try store(base, root, writable: true)
            let recovered = try await reopened.recoverBodyEdit(day)
            XCTAssertEqual(recovered, edit)
            XCTAssertEqual(try Data(contentsOf: source), after)
            let clean = try await reopened.retainedBodyEdit(day), operation = try await reopened.hasBodyOperation(day)
            XCTAssertNil(clean); XCTAssertFalse(operation)
            let history = try await reopened.bodyRevisions(day)
            XCTAssertEqual(history.map(\.id), [edit.id])
            try await reopened.commitBodyEdit(edit)
            XCTAssertEqual(try Data(contentsOf: source), after)
        }
    }

    func testWriterRejectsParentSymlinkSwapWithoutTouchingOtherDirectory() async throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let day = try JournalDay("2026-09-16"), before = Data("# Same bytes\n".utf8)
        let parent = root.appendingPathComponent("daily/2026-09"), moved = root.appendingPathComponent("daily/archived")
        let outside = base.appendingPathComponent("outside")
        try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
        try before.write(to: parent.appendingPathComponent("2026-09-16.md"))
        try before.write(to: outside.appendingPathComponent("2026-09-16.md"))
        let access = try OriginalVaultAccess(root: root, writePolicy: .syntheticFixture, failure: { purpose, stage in
            if purpose == .bodyDiary && stage == .beforeReplace {
                try FileManager.default.moveItem(at: parent, to: moved)
                try FileManager.default.createSymbolicLink(at: parent, withDestinationURL: outside)
            }
        })
        let edit = BodyEdit(day: day, before: before, content: Data("Changed\n".utf8), sourceID: try access.sourceID(day))
        XCTAssertThrowsError(try access.write(edit))
        XCTAssertEqual(try Data(contentsOf: outside.appendingPathComponent("2026-09-16.md")), before)
        XCTAssertEqual(try Data(contentsOf: moved.appendingPathComponent("2026-09-16.md")), before)
    }

    func testParentAndDirectSelectionBindTheSameRelativeDocumentSource() throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let day = try JournalDay("2026-09-16")
        try Data("Nested\n".utf8).write(to: root.appendingPathComponent("daily/2026-09/2026-09-16.md"))
        XCTAssertEqual(try OriginalVaultAccess(root: base).sourceID(day), try OriginalVaultAccess(root: root).sourceID(day))
    }
}

extension OriginalVaultAccessTests {
    func testSyntheticNewDayIsCreatedOnlyByExplicitSourceBoundCommit() async throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let day = try JournalDay("2026-09-18")
        let original = try store(base, root, writable: true)
        let preview = try await original.open(day)
        let target = root.appendingPathComponent("daily/2026-09-18.md")
        XCTAssertFalse(FileManager.default.fileExists(atPath: target.path))
        let bytes = Data((preview.markdown + "Explicit synthetic edit\r\n").utf8)
        let edit = try await BodyEdit(day: day, before: nil, content: bytes, sourceID: original.bodySourceID(day))
        try await original.retainBodyEdit(edit)
        XCTAssertFalse(FileManager.default.fileExists(atPath: target.path))
        try await original.commitBodyEdit(edit)
        XCTAssertEqual(try Data(contentsOf: target), bytes)
        let reopened = try store(base, root, writable: true)
        try await reopened.commitBodyEdit(edit)
        XCTAssertEqual(try Data(contentsOf: target), bytes)
    }
}

extension OriginalVaultAccessTests {
    func testNonDiaryFilePlaceholderDoesNotBlockTodayButUnknownFolderDoes() throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let day = try JournalDay("2026-09-16"), bytes = Data("Available today\n".utf8)
        try bytes.write(to: root.appendingPathComponent("daily/2026-09-16.md"))
        try Data().write(to: root.appendingPathComponent("daily/.Readme.txt.icloud"))
        let access = try OriginalVaultAccess(root: root)
        XCTAssertEqual(try access.read(day), bytes)
        try Data().write(to: root.appendingPathComponent("daily/.UndownloadedFolder.icloud"))
        XCTAssertThrowsError(try access.read(day))
        XCTAssertThrowsError(try access.read(JournalDay("2026-09-17")))
    }

    func testParentConfigurationCannotAliasSiblingCollectionsOrTemplates() throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        for name in ["Other", "Else", ".obsidian"] { try FileManager.default.createDirectory(at: base.appendingPathComponent(name), withIntermediateDirectories: true) }
        let config = base.appendingPathComponent(".obsidian/daily-notes.json")
        for value in [#"{"folder":"Other","template":"riji/templates/daily"}"#, #"{"folder":"Else","template":"riji/templates/daily"}"#, #"{"folder":"riji/daily","template":"Other/daily"}"#] {
            try Data(value.utf8).write(to: config)
            XCTAssertThrowsError(try OriginalVaultAccess(root: base))
        }
        try Data(#"{"folder":"riji/daily","template":"riji/templates/daily"}"#.utf8).write(to: config)
        let day = try JournalDay("2026-09-16")
        XCTAssertEqual(try OriginalVaultAccess(root: base).sourceID(day), try OriginalVaultAccess(root: root).sourceID(day))
    }

    func testCoordinatedReadAndWriteRejectIntermediateSymlinkSwappedInsideCallback() throws {
        for writing in [false, true] {
            let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let day = try JournalDay("2026-09-16"), bytes = Data("Original bytes\n".utf8)
            let outside = base.appendingPathComponent("outside"), directory = root.appendingPathComponent("daily")
            try FileManager.default.createDirectory(at: outside.appendingPathComponent("2026-09"), withIntermediateDirectories: true)
            try bytes.write(to: directory.appendingPathComponent("2026-09/2026-09-16.md"))
            let outsideFile = outside.appendingPathComponent("2026-09/2026-09-16.md")
            try bytes.write(to: outsideFile)
            let access = try OriginalVaultAccess(root: root, writePolicy: .syntheticFixture, failure: { purpose, stage in
                if purpose == .bodyDiary && stage == .beforeRead {
                    try FileManager.default.moveItem(at: directory, to: root.appendingPathComponent("retained-daily"))
                    try FileManager.default.createSymbolicLink(at: directory, withDestinationURL: outside)
                }
            })
            if writing {
                let edit = BodyEdit(day: day, before: bytes, content: Data("Changed\n".utf8), sourceID: try access.sourceID(day))
                XCTAssertThrowsError(try access.write(edit))
            } else { XCTAssertThrowsError(try access.read(day)) }
            XCTAssertEqual(try Data(contentsOf: outsideFile), bytes)
            XCTAssertEqual(try Data(contentsOf: root.appendingPathComponent("retained-daily/2026-09/2026-09-16.md")), bytes)
        }
    }

    func testTemplateCallbackKeepsAuthorizedRootWhenIntermediateDirectoryBecomesSymlink() throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let outside = base.appendingPathComponent("outside"), templates = root.appendingPathComponent("templates")
        for url in [templates.appendingPathComponent("nested"), outside.appendingPathComponent("nested"), root.appendingPathComponent(".obsidian")] {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        }
        try Data("Original template\n".utf8).write(to: templates.appendingPathComponent("nested/daily.md"))
        try Data("Outside template\n".utf8).write(to: outside.appendingPathComponent("nested/daily.md"))
        try Data(#"{"template":"templates/nested/daily"}"#.utf8).write(to: root.appendingPathComponent(".obsidian/daily-notes.json"))
        let access = try OriginalVaultAccess(root: root, failure: { purpose, stage in
            if purpose == .bodyDiary && stage == .beforeRead {
                try FileManager.default.moveItem(at: templates, to: root.appendingPathComponent("retained-templates"))
                try FileManager.default.createSymbolicLink(at: templates, withDestinationURL: outside)
            }
        })
        XCTAssertThrowsError(try access.templateVersion())
    }

    func testOversizedSyntheticEditLeavesOriginalUntouched() async throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let day = try JournalDay("2026-09-16"), bytes = Data("Original\n".utf8)
        let target = root.appendingPathComponent("daily/2026-09-16.md"); try bytes.write(to: target)
        let access = try OriginalVaultAccess(root: root, writePolicy: .syntheticFixture)
        let edit = BodyEdit(day: day, before: bytes, content: Data(repeating: 65, count: LocalVaultReader.maximumPreviewBytes + 1), sourceID: try access.sourceID(day))
        XCTAssertThrowsError(try access.write(edit)) { XCTAssertEqual($0 as? VaultReadError, .tooLarge) }
        XCTAssertEqual(try Data(contentsOf: target), bytes)
        XCTAssertEqual(try LocalVaultReader.entries(root: root, path: ["daily"]).map(\.name), ["2026-09", "2026-09-16.md"])
    }
}


extension OriginalVaultAccessTests {
    func testManualWritePolicyDoesNotEnableLegacyAppend() async throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        XCTAssertFalse(try OriginalVaultAccess(root: root).canWrite)
        let original = try store(base, root, writable: true)
        XCTAssertTrue(original.permitsBodyWriting)
        let day = try JournalDay("2026-09-16"), snapshot = try await original.open(day)
        let draft = try await original.makeDraft(for: snapshot, section: "Notes")
        do { try await original.saveDraft(draft); XCTFail("Legacy draft cannot target original files") }
        catch { guard case OriginalVaultError.readOnly = error else { return XCTFail("Expected readOnly") } }
        do { _ = try await original.commit(draft); XCTFail("Legacy commit cannot target original files") }
        catch { guard case OriginalVaultError.readOnly = error else { return XCTFail("Expected readOnly") } }
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("daily/2026-09-16.md").path))
    }

    func testOriginalReceiptInterruptionRecoversOneRevisionAndSameOperationAcrossStores() async throws {
        for stage in [FileStage.beforeWrite, .afterReplace] {
            let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let day = try JournalDay("2026-09-16"), before = Data("# Original 🧠\r\n".utf8)
            let target = root.appendingPathComponent("daily/2026-09/2026-09-16.md")
            try before.write(to: target)
            let access = try OriginalVaultAccess(root: root, writePolicy: .coordinatedUserEdit)
            let original = try LocalJournalStore(journalDirectory: base.appendingPathComponent("unused"),
                stateDirectory: base.appendingPathComponent("state"), failure: { purpose, current in
                    if purpose == .bodyReceipt && current == stage { throw JournalError.storage }
                }, original: access)
            let edit = BodyEdit(day: day, before: before, content: before + Data("Typed e\u{301}\r\n".utf8), sourceID: try access.sourceID(day))
            do { try await original.commitBodyEdit(edit); XCTFail("Receipt interruption must remain pending") } catch {}
            XCTAssertEqual(try Data(contentsOf: target), edit.content)
            let reopened = try store(base, root, writable: true)
            let recovered = try await reopened.recoverBodyEdit(day)
            XCTAssertEqual(recovered, edit)
            try await reopened.commitBodyEdit(edit)
            let revisions = try await reopened.bodyRevisions(day), pending = try await reopened.hasBodyOperation(day)
            XCTAssertEqual(revisions.map(\.id), [edit.id]); XCTAssertFalse(pending)
            XCTAssertEqual(try Data(contentsOf: target), edit.content)
        }
    }

    func testMovedRootReplacementCannotReceivePreviouslyBoundEdit() async throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let day = try JournalDay("2026-09-16"), before = Data("Original\r\n".utf8)
        let relative = "daily/2026-09/2026-09-16.md"
        try before.write(to: root.appendingPathComponent(relative))
        let original = try store(base, root, writable: true)
        let edit = try await BodyEdit(day: day, before: before, content: before + Data("Typed\r\n".utf8), sourceID: original.bodySourceID(day))
        try await original.retainBodyEdit(edit)
        let moved = base.appendingPathComponent("moved-riji")
        try FileManager.default.moveItem(at: root, to: moved)
        try FileManager.default.copyItem(at: moved, to: root)
        do { try await original.commitBodyEdit(edit); XCTFail("A new directory is not the authorized collection") }
        catch { guard case OriginalVaultError.changedIdentity = error else { return XCTFail("Expected changedIdentity") } }
        XCTAssertEqual(try Data(contentsOf: moved.appendingPathComponent(relative)), before)
        XCTAssertEqual(try Data(contentsOf: root.appendingPathComponent(relative)), before)
    }
}


extension OriginalVaultAccessTests {
    func testCoordinatorRootSwapCannotWriteReplacementCollection() throws {
        for stage in [FileStage.beforeRead, .beforeReplace] {
            let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let day = try JournalDay("2026-09-16"), before = Data("Bound collection\r\n".utf8)
            let relative = "daily/2026-09/2026-09-16.md", moved = base.appendingPathComponent("moved")
            try before.write(to: root.appendingPathComponent(relative))
            let access = try OriginalVaultAccess(root: root, writePolicy: .coordinatedUserEdit, failure: { purpose, current in
                if purpose == .bodyDiary && current == stage {
                    try FileManager.default.moveItem(at: root, to: moved)
                    try FileManager.default.copyItem(at: moved, to: root)
                }
            })
            let edit = BodyEdit(day: day, before: before, content: before + Data("Typed\r\n".utf8), sourceID: try access.sourceID(day))
            XCTAssertThrowsError(try access.write(edit))
            XCTAssertEqual(try Data(contentsOf: root.appendingPathComponent(relative)), before)
            XCTAssertEqual(try Data(contentsOf: moved.appendingPathComponent(relative)), before)
        }
    }

    func testLocalRecoveryRetainsNewerInputWithoutRootButRejectsForeignAndMalformedIdentities() async throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let day = try JournalDay("2026-09-16"), before = Data("Source\r\n".utf8)
        try before.write(to: root.appendingPathComponent("daily/2026-09/2026-09-16.md"))
        let access = try OriginalVaultAccess(root: root, writePolicy: .coordinatedUserEdit)
        let original = try store(base, root, writable: true), sourceID = try access.sourceID(day)
        try FileManager.default.moveItem(at: root, to: base.appendingPathComponent("moved"))
        for suffix in ["First", "Newer"] {
            let edit = BodyEdit(day: day, before: before, content: before + Data(suffix.utf8), sourceID: sourceID)
            try await original.retainBodyEdit(edit)
            let retained = try await original.retainedBodyEdit(day); XCTAssertEqual(retained, edit)
        }
        let detached = try LocalJournalStore(journalDirectory: base.appendingPathComponent("unused"), stateDirectory: base.appendingPathComponent("state"), original: OriginalVaultAccess(recovery: access.recoveryReference))
        let restored = try await detached.retainedBodyEdit(day)
        XCTAssertEqual(restored?.content, before + Data("Newer".utf8))
        for identity in ["foreign:daily/2026-09-16.md", access.identity + ":daily/../daily/2026-09-16.md", access.identity + ":daily/2026-09-15.md", access.identity + ":templates/2026-09-16.md"] {
            let foreign = BodyEdit(day: day, before: before, content: before, sourceID: identity)
            do { try await detached.retainBodyEdit(foreign); XCTFail("Invalid local identity must be refused") } catch {}
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.path))
    }

    func testCoordinatedReplacementPreservesFileModeExtendedAttributesAndACL() async throws {
        let (base, root) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let day = try JournalDay("2026-09-16"), before = Data("Metadata source\r\n".utf8)
        let source = root.appendingPathComponent("daily/2026-09/2026-09-16.md")
        try before.write(to: source)
        try FileManager.default.setAttributes([.posixPermissions: 0o640], ofItemAtPath: source.path)
        let attribute = "com.riji.synthetic-metadata", value = Data("preserved-xattr".utf8)
        XCTAssertEqual(value.withUnsafeBytes { setxattr(source.path, attribute, $0.baseAddress, value.count, 0, 0) }, 0)
        #if os(macOS)
        let command = Process(); command.executableURL = URL(fileURLWithPath: "/bin/chmod")
        command.arguments = ["+a", "everyone allow read", source.path]
        try command.run(); command.waitUntilExit(); XCTAssertEqual(command.terminationStatus, 0)
        let aclBefore = try aclText(source)
        #endif
        let original = try store(base, root, writable: true)
        let edit = try await BodyEdit(day: day, before: before, content: before + Data("Edited\r\n".utf8), sourceID: original.bodySourceID(day))
        try await original.commitBodyEdit(edit)
        let permissions = try FileManager.default.attributesOfItem(atPath: source.path)[.posixPermissions] as? NSNumber
        XCTAssertEqual(permissions?.intValue, 0o640)
        var after = Data(count: value.count)
        let count = after.withUnsafeMutableBytes { getxattr(source.path, attribute, $0.baseAddress, value.count, 0, 0) }
        XCTAssertEqual(count, value.count); XCTAssertEqual(after, value)
        #if os(macOS)
        XCTAssertEqual(try aclText(source), aclBefore)
        #endif
        XCTAssertEqual(try Data(contentsOf: source), edit.content)
    }

    #if os(macOS)
    private func aclText(_ url: URL) throws -> String {
        guard let acl = acl_get_file(url.path, ACL_TYPE_EXTENDED) else { throw OriginalVaultError.unavailable }
        defer { acl_free(UnsafeMutableRawPointer(acl)) }
        guard let text = acl_to_text(acl, nil) else { throw OriginalVaultError.unavailable }
        defer { acl_free(text) }
        return String(cString: text)
    }
    #endif
}
