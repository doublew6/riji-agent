import XCTest
@testable import RijiState
import RijiCore

final class VaultBrowserTests: XCTestCase {
    private func fixture() throws -> (URL, URL, URL) {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("VaultBrowser-\(UUID())")
        let root = base.appendingPathComponent("riji"), state = base.appendingPathComponent("state")
        try FileManager.default.createDirectory(at: root.appendingPathComponent("daily/nested"), withIntermediateDirectories: true)
        try Data("# Synthetic\r\n**Original** e\u{301} 🧠\r\n".utf8).write(to: root.appendingPathComponent("daily/Note.md"))
        return (base, root, state)
    }

    func testBookmarkRestoresTreeAndReadOnlyUnicodeDocumentAcrossStoreInstances() async throws {
        let (base, root, state) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let source = root.appendingPathComponent("daily/Note.md"), before = try Data(contentsOf: source)
        let store = VaultBrowserStore(stateDirectory: state, appRoot: base)
        let chosen = try await store.connect(root); XCTAssertEqual(chosen.name, "riji")
        let reopened = VaultBrowserStore(stateDirectory: state, appRoot: base)
        let restored = try await reopened.restore(); XCTAssertEqual(restored?.entries.map(\.name), ["daily"])
        let entries = try await reopened.entries(path: ["daily"])
        let file = try XCTUnwrap(entries.first(where: { $0.name == "Note.md" }))
        let contents = try await reopened.read(file)
        XCTAssertEqual(contents.data, before); XCTAssertEqual(contents.text.map { Data($0.utf8) }, before)
        XCTAssertNil(contents.previewURL); XCTAssertEqual(try Data(contentsOf: source), before)
    }

    func testFailedReplacementKeepsPriorBookmarkAndMissingFolderCanBeReselected() async throws {
        let (base, root, state) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let store = VaultBrowserStore(stateDirectory: state, appRoot: base)
        _ = try await store.connect(root)
        let bookmark = state.appendingPathComponent("vault-folder.json"), before = try Data(contentsOf: bookmark)
        do { _ = try await store.connect(base.appendingPathComponent("missing")); XCTFail("Must reject missing folders") } catch {}
        XCTAssertEqual(try Data(contentsOf: bookmark), before)
        try FileManager.default.removeItem(at: root)
        let reopened = VaultBrowserStore(stateDirectory: state, appRoot: base)
        do { _ = try await reopened.restore(); XCTFail("Must report an unavailable folder") } catch {}
        XCTAssertEqual(try Data(contentsOf: bookmark), before)
        let next = base.appendingPathComponent("NewFolder")
        try FileManager.default.createDirectory(at: next, withIntermediateDirectories: true)
        let replacement = try await reopened.connect(next); XCTAssertEqual(replacement.name, "NewFolder")
    }

    func testCorruptBookmarkAndForgedAppLocalRootFailWithoutDeletingEvidence() async throws {
        let (base, root, state) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        try FileManager.default.createDirectory(at: state, withIntermediateDirectories: true)
        let url = state.appendingPathComponent("vault-folder.json"), damaged = Data("broken bookmark".utf8)
        try damaged.write(to: url)
        let store = VaultBrowserStore(stateDirectory: state, appRoot: root)
        do { _ = try await store.restore(); XCTFail("Must reject damage") } catch {}
        XCTAssertEqual(try Data(contentsOf: url), damaged)
        let forged = VaultFolderSelection(bookmark: try base.bookmarkData(options: [.minimalBookmark]), access: .appLocal)
        let bytes = try JSONEncoder().encode(forged); try bytes.write(to: url)
        do { _ = try await store.restore(); XCTFail("App-local references must remain within the app root") } catch {}
        XCTAssertEqual(try Data(contentsOf: url), bytes)
    }

    func testAttachmentPreviewUsesDisposableCopyAndNeverChangesOriginal() async throws {
        let (base, root, state) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let bytes = Data(base64Encoded: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")!
        let source = root.appendingPathComponent("Synthetic.png"); try bytes.write(to: source)
        let store = VaultBrowserStore(stateDirectory: state, appRoot: base); _ = try await store.connect(root)
        let contents = try await store.read(VaultEntry(path: ["Synthetic.png"], kind: .file))
        let preview = try XCTUnwrap(contents.previewURL)
        XCTAssertNotEqual(preview, source); XCTAssertEqual(try Data(contentsOf: preview), bytes)
        await store.discardPreview(contents)
        XCTAssertFalse(FileManager.default.fileExists(atPath: preview.path)); XCTAssertEqual(try Data(contentsOf: source), bytes)
    }

    func testUnsupportedEncodingAndLargeTextAreVisibleFailuresWithoutModifyingFiles() async throws {
        let (base, root, state) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let store = VaultBrowserStore(stateDirectory: state, appRoot: base); _ = try await store.connect(root)
        for (name, data) in [("Invalid.md", Data([0xFF, 0xFE])), ("Large.md", Data(repeating: 0x61, count: 2 * 1024 * 1024 + 1)), ("Program.exe", Data([1, 2, 3]))] {
            let source = root.appendingPathComponent(name); try data.write(to: source)
            do { _ = try await store.read(VaultEntry(path: [name], kind: .file)); XCTFail("Must reject unsupported previews") } catch {}
            XCTAssertEqual(try Data(contentsOf: source), data)
        }
    }

    @MainActor func testTreeExpansionPreviewCloseRefreshAndFailedSelectionKeepTheCorrectRoot() async throws {
        let (base, root, state) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let model = VaultBrowserModel(store: VaultBrowserStore(stateDirectory: state, appRoot: base))
        await model.start(); XCTAssertNil(model.rootName)
        await model.selectFolder(root); XCTAssertEqual(model.rows.map(\.id), ["daily"])
        let folder = try XCTUnwrap(model.rows.first?.entry)
        await model.toggle(folder); XCTAssertEqual(model.rows.map(\.id), ["daily", "daily/nested", "daily/Note.md"])
        let file = try XCTUnwrap(model.rows.first(where: { $0.id == "daily/Note.md" })?.entry)
        await model.open(file); XCTAssertEqual(model.preview?.entry, file)
        model.closePreview(); XCTAssertNil(model.preview)
        await model.selectFolder(base.appendingPathComponent("missing")); XCTAssertEqual(model.rootName, "riji"); XCTAssertNotNil(model.error)
        model.toggleRoot(); XCTAssertTrue(model.rows.isEmpty)
        await model.reload(); XCTAssertEqual(model.rows.map(\.id), ["daily"]); XCTAssertNil(model.error)
        await model.toggle(folder); await model.toggle(folder)
        XCTAssertEqual(model.rows.map(\.id), ["daily"])
    }
}

private final class BrowserBoundaryArm: @unchecked Sendable {
    private let lock = NSLock()
    private var enabled = false
    func arm() { lock.lock(); enabled = true; lock.unlock() }
    func take() -> Bool { lock.lock(); defer { lock.unlock() }; let result = enabled; enabled = false; return result }
}

extension VaultBrowserTests {
    func testBrowserCallbacksCannotReadOrListThroughSwappedIntermediateSymlink() async throws {
        for reading in [false, true] {
            let (base, root, state) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let daily = root.appendingPathComponent("daily"), outside = base.appendingPathComponent("outside")
            try FileManager.default.createDirectory(at: outside.appendingPathComponent("nested"), withIntermediateDirectories: true)
            try Data("Inside\n".utf8).write(to: daily.appendingPathComponent("nested/Readme.txt"))
            let outsideFile = outside.appendingPathComponent("nested/Readme.txt")
            try Data("Outside\n".utf8).write(to: outsideFile)
            let arm = BrowserBoundaryArm()
            let store = VaultBrowserStore(stateDirectory: state, appRoot: base, failure: { purpose, stage in
                if stage == .beforeRead, purpose == (reading ? .bodyDiary : .metadata), arm.take() {
                    try FileManager.default.moveItem(at: daily, to: root.appendingPathComponent("retained-daily"))
                    try FileManager.default.createSymbolicLink(at: daily, withDestinationURL: outside)
                }
            })
            _ = try await store.connect(root); arm.arm()
            do {
                if reading { _ = try await store.read(VaultEntry(path: ["daily", "nested", "Readme.txt"], kind: .file)) }
                else { _ = try await store.entries(path: ["daily", "nested"]) }
                XCTFail("The callback must keep traversal rooted in the original authorization")
            } catch {}
            XCTAssertEqual(try Data(contentsOf: outsideFile), Data("Outside\n".utf8))
        }
    }

    func testPreparingOriginalDoesNotChangeGenericBookmarkAndGenericBrowsingNeedsNoDiaryLayout() async throws {
        let (base, root, state) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let store = VaultBrowserStore(stateDirectory: state, appRoot: base)
        _ = try await store.connect(root)
        let bookmark = try Data(contentsOf: state.appendingPathComponent("vault-folder.json"))
        let next = base.appendingPathComponent("Candidate")
        for name in ["daily", "templates"] { try FileManager.default.createDirectory(at: next.appendingPathComponent(name), withIntermediateDirectories: true) }
        _ = try await store.connectOriginal(next)
        XCTAssertEqual(try Data(contentsOf: state.appendingPathComponent("vault-folder.json")), bookmark)
        let entries = try await store.entries(path: ["daily"])
        XCTAssertTrue(entries.contains { $0.name == "Note.md" })
        let restored = try await VaultBrowserStore(stateDirectory: state, appRoot: base).restore()
        XCTAssertEqual(restored?.name, root.lastPathComponent)
    }
}
