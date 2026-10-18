import Foundation
import XCTest
@testable import RijiICloudProbeSupport

final class ProbeFixtureTests: XCTestCase {
    private func fixture() throws -> (ProbeFixture, URL, URL) {
        let base = FileManager.default.temporaryDirectory.resolvingSymlinksInPath().appendingPathComponent("ProbeGuards-\(UUID())")
        try FileManager.default.createDirectory(at: base, withIntermediateDirectories: false)
        let fixture = ProbeFixture(run: UUID()), root = base.appendingPathComponent(fixture.folderName)
        try fixture.create(at: root, storage: .localTest)
        return (fixture, root, base)
    }

    func testExactFixtureAndEveryWrongRunRefuseBeforeWriterAdmission() throws {
        let (fixture, root, base) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        try fixture.validate(root, phase: .pristine, storage: .localTest)
        XCTAssertThrowsError(try ProbeFixture(run: UUID()).validate(root, phase: .pristine, storage: .localTest))
        XCTAssertThrowsError(try fixture.validate(root, phase: .pristine))
        XCTAssertThrowsError(try fixture.create(at: root, storage: .localTest))
        try fixture.validate(root, phase: .pristine, storage: .localTest)
    }

    func testUnknownFilesFoldersPlaceholdersAndChangedBytesAreRejected() throws {
        for mutation in ["extra", "folder", "placeholder", "bytes", "marker", "missing"] {
            let (fixture, root, base) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            switch mutation {
            case "extra": try Data("unknown".utf8).write(to: root.appendingPathComponent(".DS_Store"))
            case "folder": try FileManager.default.createDirectory(at: root.appendingPathComponent("extra"), withIntermediateDirectories: false)
            case "placeholder": try Data().write(to: root.appendingPathComponent("daily/.missing.md.icloud"))
            case "bytes": try Data("private or unknown".utf8).write(to: root.appendingPathComponent(ProbeFixture.savedPath))
            case "marker": try Data("{}".utf8).write(to: root.appendingPathComponent("probe.json"))
            default: try FileManager.default.removeItem(at: root.appendingPathComponent(ProbeFixture.deletedPath))
            }
            XCTAssertThrowsError(try fixture.validate(root, phase: .pristine, storage: .localTest), mutation)
        }
    }

    func testFileDirectoryRootSymlinksAndHardLinksAreRejectedWithoutFollowingThem() throws {
        for mutation in ["file", "directory", "root", "hardlink"] {
            let (fixture, root, base) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
            let target = mutation == "root" ? root : root.appendingPathComponent(mutation == "directory" ? "daily/nested" : ProbeFixture.savedPath)
            let outside = base.appendingPathComponent("outside")
            try FileManager.default.moveItem(at: target, to: outside)
            if mutation == "hardlink" { try FileManager.default.linkItem(at: outside, to: target) }
            else { try FileManager.default.createSymbolicLink(at: target, withDestinationURL: outside) }
            XCTAssertThrowsError(try fixture.validate(root, phase: .pristine, storage: .localTest), mutation)
            XCTAssertTrue(FileManager.default.fileExists(atPath: outside.path))
        }
    }

    func testMacStepRequiresExactPhoneReceiptAndPreservesDeletionEvidence() throws {
        let (fixture, root, base) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        let evidence = base.appendingPathComponent("evidence")
        XCTAssertThrowsError(try fixture.applyMacChanges(root, evidence: evidence, storage: .localTest))
        try fixture.validate(root, phase: .pristine, storage: .localTest)
        try fixture.files(.phoneSaved)[ProbeFixture.savedPath]!.write(to: root.appendingPathComponent(ProbeFixture.savedPath))
        XCTAssertThrowsError(try fixture.applyMacChanges(root, evidence: evidence, storage: .localTest))
        try fixture.writePhoneReceipt(root, storage: .localTest)
        XCTAssertThrowsError(try fixture.applyMacChanges(root, evidence: root.appendingPathComponent("evidence"), storage: .localTest))
        try fixture.validate(root, phase: .phoneReady, storage: .localTest)
        try fixture.applyMacChanges(root, evidence: evidence, storage: .localTest)
        try fixture.validate(root, phase: .macReady, storage: .localTest)
        let recordedHashes = try JSONDecoder().decode([String: String].self, from: Data(contentsOf: evidence.appendingPathComponent("mac-hashes.json")))
        XCTAssertEqual(recordedHashes, fixture.hashes(.macReady))
        XCTAssertEqual(try Data(contentsOf: evidence.appendingPathComponent("deleted-synthetic.md")), fixture.files(.pristine)[ProbeFixture.deletedPath])
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent(ProbeFixture.deletedPath).path))
        XCTAssertThrowsError(try fixture.applyMacChanges(root, evidence: base.appendingPathComponent("again"), storage: .localTest))
    }

    func testForgedReadyReceiptWithWrongDocumentBytesCannotAdmitMacWrites() throws {
        let (fixture, root, base) = try fixture(); defer { try? FileManager.default.removeItem(at: base) }
        try fixture.files(.phoneReady)["phone-ready.json"]!.write(to: root.appendingPathComponent("phone-ready.json"))
        XCTAssertThrowsError(try fixture.applyMacChanges(root, evidence: base.appendingPathComponent("evidence"), storage: .localTest))
        XCTAssertEqual(try Data(contentsOf: root.appendingPathComponent(ProbeFixture.savedPath)), fixture.files(.pristine)[ProbeFixture.savedPath])
    }
}
