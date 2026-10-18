import XCTest
@testable import RijiCore

final class NorthStarAnchorTests: XCTestCase {
    private let scope = NorthStarAnchor.scope(source: "synthetic-vault", account: "synthetic-account")

    func testParagraphRangesPreserveUnicodeCRLFAndRejectChangedFileOrPartialParagraph() throws {
        let data = Data("# Direction\r\n\r\n练习 e\u{301} 🧠\r\n第二行\r\n\r\nNext\n".utf8)
        let values = try NorthStarParagraph.read(data)
        XCTAssertEqual(values.map(\.text), ["# Direction", "练习 e\u{301} 🧠\r\n第二行", "Next"])
        let paragraph = values[1]
        let anchor = try NorthStarAnchor(scope: scope, source: .paragraph(
            path: ["Goals", "Direction.md"], start: paragraph.start, end: paragraph.end, hash: SourceHash.digest(data)))
        XCTAssertEqual(try NorthStarParagraph.resolve(anchor, in: data), paragraph.text)
        XCTAssertEqual(Data(paragraph.text.utf8), data[paragraph.start..<paragraph.end])
        XCTAssertThrowsError(try NorthStarParagraph.resolve(anchor, in: data + Data("\n".utf8)))
        let partial = try NorthStarAnchor(scope: scope, source: .paragraph(
            path: ["Direction.md"], start: paragraph.start + 1, end: paragraph.end, hash: SourceHash.digest(data)))
        XCTAssertThrowsError(try NorthStarParagraph.resolve(partial, in: data))
    }

    func testDecodedReferenceCannotEscapeDirectoryOrBypassByteAndHashBounds() throws {
        let anchor = try NorthStarAnchor(scope: scope, source: .paragraph(path: ["Direction.md"], start: 0, end: 4, hash: scope))
        let valid = String(decoding: try JSONEncoder().encode(anchor), as: UTF8.self)
        for invalid in [valid.replacingOccurrences(of: "Direction.md", with: ".."),
                        valid.replacingOccurrences(of: "Direction.md", with: "/tmp/secret.md"),
                        valid.replacingOccurrences(of: "\"end\":4", with: "\"end\":2147483647"),
                        valid.replacingOccurrences(of: scope, with: "invalid")] {
            let decoded = try JSONDecoder().decode(NorthStarAnchor.self, from: Data(invalid.utf8))
            XCTAssertThrowsError(try decoded.validate())
        }
        XCTAssertThrowsError(try NorthStarParagraph.read(Data([0xFF])))
        XCTAssertThrowsError(try NorthStarParagraph.read(Data(repeating: 65, count: NorthStarParagraph.maximumFileBytes + 1)))
        XCTAssertTrue(try NorthStarParagraph.read(Data(repeating: 65, count: NorthStarParagraph.maximumParagraphBytes + 1)).isEmpty)
    }

    func testReferencePersistenceIsSourceAndAccountScopedAndContainsNoGoalText() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = NorthStarAnchorStore(directory: directory)
        let anchor = try NorthStarAnchor(scope: scope, source: .definition(UUID(), hash: scope))
        try await store.save(anchor)
        let reopened = NorthStarAnchorStore(directory: directory)
        let loaded = try await reopened.read(scope: scope)
        XCTAssertEqual(loaded, anchor)
        for other in [NorthStarAnchor.scope(source: "another-vault", account: "synthetic-account"),
                      NorthStarAnchor.scope(source: "synthetic-vault", account: "another-account")] {
            let missing = try await reopened.read(scope: other); XCTAssertNil(missing)
        }
        let bytes = try Data(contentsOf: directory.appendingPathComponent("north-star-anchors/" + scope + ".json"))
        XCTAssertFalse(String(decoding: bytes, as: UTF8.self).contains("synthetic-vault"))
        try await reopened.remove(scope: scope)
        let removed = try await store.read(scope: scope); XCTAssertNil(removed)
    }

    func testPersistenceFailurePreservesPriorAssociationAndCorruptEvidence() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = NorthStarAnchorStore(directory: directory)
        let anchor = try NorthStarAnchor(scope: scope, source: .definition(UUID(), hash: scope))
        try await store.save(anchor)
        let failing = NorthStarAnchorStore(directory: directory, failure: { throw NorthStarAnchorError.storage })
        do { try await failing.save(NorthStarAnchor(scope: scope, source: .definition(UUID(), hash: scope))); XCTFail() } catch {}
        let loaded = try await store.read(scope: scope); XCTAssertEqual(loaded, anchor)
        let url = directory.appendingPathComponent("north-star-anchors/" + scope + ".json")
        try Data("broken".utf8).write(to: url)
        do { _ = try await store.read(scope: scope); XCTFail() } catch {}
        XCTAssertEqual(try Data(contentsOf: url), Data("broken".utf8))
    }

    func testReferenceDirectoryIsExcludedFromBackupOnSaveAndExistingDirectoryRead() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = NorthStarAnchorStore(directory: directory)
        let anchor = try NorthStarAnchor(scope: scope, source: .definition(UUID(), hash: scope))
        try await store.save(anchor)
        var folder = directory.appendingPathComponent("north-star-anchors")
        XCTAssertEqual(try folder.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup, true)
        var included = URLResourceValues(); included.isExcludedFromBackup = false
        try folder.setResourceValues(included)
        let reopened = NorthStarAnchorStore(directory: directory)
        let loaded = try await reopened.read(scope: scope); XCTAssertEqual(loaded, anchor)
        let reread = URL(fileURLWithPath: folder.path)
        XCTAssertEqual(try reread.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup, true)
    }
}
