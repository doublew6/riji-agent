import XCTest
import Darwin
@testable import RijiCore

final class LocalVaultReaderTests: XCTestCase {
    private func fixture() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("VaultReader-\(UUID())")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    func testDirectoryTreePreservesNamesAndLoadsOnlyRequestedLevel() throws {
        let root = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        for path in ["daily/nested", "north star", ".obsidian"] {
            try FileManager.default.createDirectory(at: root.appendingPathComponent(path), withIntermediateDirectories: true)
        }
        try Data("# Note\n".utf8).write(to: root.appendingPathComponent("daily/nested/中文.md"))
        for name in ["File10.md", "File2.md", ".hidden.md"] { try Data().write(to: root.appendingPathComponent(name)) }
        let entries = try LocalVaultReader.entries(root: root)
        XCTAssertEqual(entries.map(\.name), ["daily", "north star", "File2.md", "File10.md"])
        XCTAssertEqual(entries.map(\.kind), [.folder, .folder, .file, .file])
        XCTAssertEqual(try LocalVaultReader.entries(root: root, path: ["daily"]).map(\.id), ["daily/nested"])
        XCTAssertEqual(try LocalVaultReader.entries(root: root, path: ["daily", "nested"]).map(\.id), ["daily/nested/中文.md"])
    }

    func testReadKeepsExactBytesAndSeesExternalChangesWithoutWritingSource() throws {
        let root = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("Note.md"), bytes = Data("# 日记\r\n**e\u{301} 🧠**\r\n".utf8)
        try bytes.write(to: url)
        let before = try FileManager.default.attributesOfItem(atPath: url.path)[.modificationDate] as? Date
        XCTAssertEqual(try LocalVaultReader.read(root: root, path: ["Note.md"]), bytes)
        XCTAssertEqual(try Data(contentsOf: url), bytes)
        XCTAssertEqual(try FileManager.default.attributesOfItem(atPath: url.path)[.modificationDate] as? Date, before)
        try Data("Changed externally\n".utf8).write(to: url)
        XCTAssertEqual(try LocalVaultReader.read(root: root, path: ["Note.md"]), Data("Changed externally\n".utf8))
    }

    func testTraversalSymlinksAndFolderReplacementCannotEscapeRoot() throws {
        let base = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let root = base.appendingPathComponent("vault"), outside = base.appendingPathComponent("outside")
        for url in [root, outside, root.appendingPathComponent("daily")] { try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true) }
        try Data("Outside source\n".utf8).write(to: outside.appendingPathComponent("Note.md"))
        try FileManager.default.createSymbolicLink(at: root.appendingPathComponent("Link.md"), withDestinationURL: outside.appendingPathComponent("Note.md"))
        XCTAssertEqual(try LocalVaultReader.entries(root: root).first(where: { $0.name == "Link.md" })?.kind, .unavailable)
        XCTAssertThrowsError(try LocalVaultReader.read(root: root, path: ["Link.md"]))
        _ = try LocalVaultReader.entries(root: root)
        try FileManager.default.removeItem(at: root.appendingPathComponent("daily"))
        try FileManager.default.createSymbolicLink(at: root.appendingPathComponent("daily"), withDestinationURL: outside)
        XCTAssertThrowsError(try LocalVaultReader.read(root: root, path: ["daily", "Note.md"]))
        XCTAssertThrowsError(try LocalVaultReader.entries(root: root, path: ["daily"]))
        for path in [["..", "outside", "Note.md"], ["daily/Note.md"], [""], ["."], ["nul\0"]] {
            XCTAssertThrowsError(try LocalVaultReader.read(root: root, path: path))
        }
    }

    func testSpecialFilesAreListedUnavailableAndCannotBlockReading() throws {
        let root = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        XCTAssertEqual(mkfifo(root.appendingPathComponent("Pipe.md").path, 0o600), 0)
        XCTAssertEqual(try LocalVaultReader.entries(root: root).first?.kind, .unavailable)
        XCTAssertThrowsError(try LocalVaultReader.read(root: root, path: ["Pipe.md"]))
        XCTAssertThrowsError(try LocalVaultReader.read(root: root, path: []))
    }

    func testOversizedFileIsRejectedBeforeReadingItsContents() throws {
        let root = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("Large.bin")
        FileManager.default.createFile(atPath: url.path, contents: Data())
        let file = try FileHandle(forWritingTo: url); defer { try? file.close() }
        try file.truncate(atOffset: UInt64(LocalVaultReader.maximumPreviewBytes + 1))
        XCTAssertThrowsError(try LocalVaultReader.read(root: root, path: ["Large.bin"])) { XCTAssertEqual(($0 as? VaultReadError), .tooLarge) }
    }
}

extension LocalVaultReaderTests {
    func testCoordinatorRelativePathRejectsCallbacksOutsideAuthorization() throws {
        let root = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        XCTAssertEqual(try LocalVaultReader.relativePath(root: root, callback: root.appendingPathComponent("daily/nested/day.md")), ["daily", "nested", "day.md"])
        XCTAssertThrowsError(try LocalVaultReader.relativePath(root: root, callback: root.appendingPathComponent("../outside/day.md")))
        XCTAssertThrowsError(try LocalVaultReader.relativePath(root: root, callback: URL(fileURLWithPath: root.path + "-sibling/day.md")))
        XCTAssertThrowsError(try LocalVaultReader.relativePath(root: root, callback: URL(string: "https://example.invalid/day.md")!))
    }
}
