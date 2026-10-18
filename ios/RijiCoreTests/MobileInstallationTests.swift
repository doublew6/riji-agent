import XCTest
@testable import RijiCore

private final class InstallationSource: MobileInstallationSource, @unchecked Sendable {
    var data: Data?
    var failConsume = false
    var failReceipt = false
    var receipts: [MobileInstallationReceipt] = []
    var onConsume: (() throws -> Void)?
    init(_ package: MobileInstallationPackage) throws { data = try JSONEncoder().encode(package) }
    func read() -> Data? { data }
    func invalidateReceipt() { receipts = [] }
    func consume(_ expected: Data) throws {
        guard !failConsume, data == expected else { throw MobileInstallationError.consumption }
        try onConsume?()
        data = nil
    }
    func receipt(_ value: MobileInstallationReceipt) throws {
        if failReceipt { throw MobileError.storage }
        receipts.append(value)
    }
}
private final class InstallationCredentials: MobileCredentialStore, @unchecked Sendable {
    var value: MobileCredential?
    var fail = false
    var writes = 0
    func read() -> MobileCredential? { value }
    func write(_ value: MobileCredential?) throws {
        writes += 1
        if fail { throw MobileError.storage }
        self.value = value
    }
}

final class MobileInstallationTests: XCTestCase {
    private var root: URL!
    private var store: MobileLocalStore!
    private let token = String(repeating: "s", count: 64)
    private var old: MobileCredential { MobileCredential(endpoint: "https://old.example:8443", deviceId: "old-device", token: token) }
    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("MobileInstallation-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        store = MobileLocalStore(directory: root.appendingPathComponent("State"))
    }
    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }
    private func package(previous: String? = nil) -> MobileInstallationPackage {
        MobileInstallationPackage(endpoint: "https://new.example:10000", previousEndpoint: previous,
                                  deviceId: "new-device", deviceToken: token)
    }
    private func apply(_ source: InstallationSource, _ credentials: InstallationCredentials) throws -> MobileCredential? {
        try MobileInstallation.apply(source: source, existing: credentials.read(), credentials: credentials, persistence: store)
    }
    func testFreshPackageConsumedAndReceiptContainsNoTokenHostOrContent() throws {
        let source = try InstallationSource(package()), credentials = InstallationCredentials()
        let result = try XCTUnwrap(apply(source, credentials))
        XCTAssertEqual(result.deviceId, "new-device"); XCTAssertEqual(credentials.value?.token, token)
        XCTAssertNil(source.data); XCTAssertEqual(source.receipts.last?.status, "success")
        let receipt = String(decoding: try JSONEncoder().encode(source.receipts.last), as: UTF8.self)
        XCTAssertFalse(receipt.contains(token)); XCTAssertFalse(receipt.contains("example"))
        XCTAssertNil(try apply(source, credentials)); XCTAssertEqual(credentials.writes, 1)
    }
    func testSameOriginKeepsExistingIdentityAndReportsUnusedProvisionedCredential() throws {
        let source = try InstallationSource(MobileInstallationPackage(endpoint: old.endpoint, deviceId: "unused-device", deviceToken: token))
        let credentials = InstallationCredentials(); credentials.value = old
        let result = try apply(source, credentials)
        XCTAssertEqual(result?.deviceId, old.deviceId); XCTAssertEqual(result?.token, old.token)
        XCTAssertEqual(credentials.writes, 0)
        XCTAssertEqual(source.receipts.last?.usedDeviceId, old.deviceId)
        XCTAssertEqual(source.receipts.last?.unusedProvisionedDeviceId, "unused-device")
    }
    func testBoundMigrationRetainsExactOriginalArchiveDraftsVersionsAndOperationIDs() throws {
        var state = MobileLocalState(credential: old)
        state.drafts["mentor:future_self"] = "Synthetic unsent text"
        state.memoryDraftVersions = ["memory:one": "version-one"]
        state.operations = [MobilePendingOperation(kind: "mentor_message", target: "mentor:future_self", path: ["mentors", "future_self", "messages"], body: ["text": .string("Synthetic request")])]
        try store.save(state)
        let file = root.appendingPathComponent("State/" + old.scope + ".json")
        let original = try Data(contentsOf: file)
        let source = try InstallationSource(package(previous: old.endpoint)), credentials = InstallationCredentials()
        credentials.value = old
        let result = try XCTUnwrap(apply(source, credentials)), migrated = try store.load(result)
        XCTAssertEqual(result.deviceId, old.deviceId); XCTAssertEqual(result.token, old.token)
        XCTAssertEqual(migrated.drafts, state.drafts); XCTAssertEqual(migrated.memoryDraftVersions, state.memoryDraftVersions)
        XCTAssertEqual(migrated.operations[0].id, state.operations[0].id)
        XCTAssertEqual(migrated.operations[0].body, state.operations[0].body)
        XCTAssertEqual(try Data(contentsOf: file), original)
        XCTAssertEqual(try store.archives().states.count, 2)
    }
    func testWrongBindingAndDestinationCollisionPreserveCredentialsAndPackage() throws {
        let credentials = InstallationCredentials(); credentials.value = old
        let wrong = try InstallationSource(package(previous: "https://wrong.example"))
        XCTAssertThrowsError(try apply(wrong, credentials)); XCTAssertNotNil(wrong.data)
        let destination = MobileCredential(endpoint: package().endpoint, deviceId: old.deviceId, token: token)
        var state = MobileLocalState(credential: destination); state.drafts["mentor:future_self"] = "Keep destination"
        try store.save(state)
        let source = try InstallationSource(package(previous: old.endpoint))
        XCTAssertThrowsError(try apply(source, credentials)); XCTAssertNotNil(source.data)
        XCTAssertEqual(credentials.writes, 0); XCTAssertEqual(credentials.value?.endpoint, old.endpoint)
        XCTAssertEqual(try store.load(destination).drafts, state.drafts)
    }
    func testIdenticalDestinationMigrationIsAccepted() throws {
        let credentials = InstallationCredentials(); credentials.value = old
        let destination = MobileCredential(endpoint: package().endpoint, deviceId: old.deviceId, token: token)
        try store.save(MobileLocalState(credential: destination))
        let source = try InstallationSource(package(previous: old.endpoint))
        XCTAssertEqual(try apply(source, credentials)?.endpoint, destination.endpoint)
    }
    func testCorruptOrSymlinkSourceArchiveCannotActivateOrConsumePackage() throws {
        let directory = root.appendingPathComponent("State")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let archive = directory.appendingPathComponent(old.scope + ".json")
        let credentials = InstallationCredentials(); credentials.value = old
        for mode in ["corrupt", "symlink"] {
            if mode == "corrupt" { try Data("broken".utf8).write(to: archive) }
            else { try FileManager.default.createSymbolicLink(at: archive, withDestinationURL: root.appendingPathComponent("missing")) }
            let source = try InstallationSource(package(previous: old.endpoint))
            XCTAssertThrowsError(try apply(source, credentials)); XCTAssertNotNil(source.data)
            XCTAssertEqual(credentials.writes, 0)
            try FileManager.default.removeItem(at: archive)
        }
    }
    func testConsumptionFailurePreservesOldCredentialAndRetryablePackage() throws {
        let source = try InstallationSource(package(previous: old.endpoint)), credentials = InstallationCredentials()
        credentials.value = old; source.failConsume = true
        XCTAssertThrowsError(try apply(source, credentials))
        XCTAssertNotNil(source.data); XCTAssertEqual(credentials.writes, 0)
        source.failConsume = false
        XCTAssertEqual(try apply(source, credentials)?.deviceId, old.deviceId)
    }
    func testKeychainFailureAfterConsumptionPreservesOldCredentialAndCannotReplay() throws {
        let source = try InstallationSource(package(previous: old.endpoint)), credentials = InstallationCredentials()
        credentials.value = old; credentials.fail = true
        XCTAssertThrowsError(try apply(source, credentials))
        XCTAssertNil(source.data); XCTAssertEqual(credentials.value?.endpoint, old.endpoint)
        XCTAssertEqual(source.receipts.last?.status, "error")
        credentials.fail = false; XCTAssertNil(try apply(source, credentials))
        XCTAssertEqual(credentials.value?.endpoint, old.endpoint)
    }
    func testReceiptFailureDoesNotRollbackOrAllowReplay() throws {
        let source = try InstallationSource(package()), credentials = InstallationCredentials()
        source.failReceipt = true
        XCTAssertNotNil(try apply(source, credentials)); XCTAssertNil(source.data)
        XCTAssertNil(try apply(source, credentials)); XCTAssertEqual(credentials.writes, 1)
    }
    func testOldSuccessReceiptIsInvalidatedWhenCurrentReceiptWriteFails() throws {
        let source = try InstallationSource(package()), credentials = InstallationCredentials()
        source.receipts = [MobileInstallationReceipt(status: "success", usedDeviceId: "previous-device", unusedProvisionedDeviceId: nil)]
        source.failReceipt = true
        XCTAssertNotNil(try apply(source, credentials)); XCTAssertNil(source.data)
        XCTAssertTrue(source.receipts.isEmpty); XCTAssertEqual(credentials.value?.deviceId, "new-device")
        let file = MobileInstallationFile(directory: root)
        try file.receipt(MobileInstallationReceipt(status: "success", usedDeviceId: "previous-device", unusedProvisionedDeviceId: nil))
        try file.invalidateReceipt()
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("MobileInstallationReceipt.json").path))
    }
    func testFinalMigrationEncodingAcceptsExactlyEightMiBAndRejectsNextByteBeforeConsumption() throws {
        let limit = 8_388_608, endpoint = "https://longer-managed-service.example:10000"
        let target = MobileCredential(endpoint: endpoint, deviceId: old.deviceId, token: old.token)
        for overflow in [0, 1] {
            let directory = root.appendingPathComponent("size-\(overflow)")
            store = MobileLocalStore(directory: directory)
            var final = MobileLocalState(credential: target); final.drafts["mentor:future_self"] = ""
            let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
            let overhead = try encoder.encode(final).count
            final.drafts["mentor:future_self"] = String(repeating: "a", count: limit + overflow - overhead)
            XCTAssertEqual(try encoder.encode(final).count, limit + overflow)
            var original = final; original.endpoint = old.endpoint
            try store.save(original)
            let originalURL = directory.appendingPathComponent(old.scope + ".json")
            let originalData = try Data(contentsOf: originalURL)
            XCTAssertLessThan(originalData.count, limit)
            let source = try InstallationSource(MobileInstallationPackage(endpoint: endpoint, previousEndpoint: old.endpoint))
            let credentials = InstallationCredentials(); credentials.value = old
            if overflow == 0 {
                XCTAssertEqual(try apply(source, credentials)?.endpoint, endpoint)
                XCTAssertEqual(try Data(contentsOf: directory.appendingPathComponent(target.scope + ".json")).count, limit)
                XCTAssertEqual(try store.load(target).drafts, final.drafts)
            } else {
                XCTAssertThrowsError(try apply(source, credentials)); XCTAssertNotNil(source.data)
                XCTAssertEqual(credentials.writes, 0); XCTAssertEqual(credentials.value?.endpoint, old.endpoint)
                XCTAssertNil(try store.load(scope: target.scope))
            }
            XCTAssertEqual(try Data(contentsOf: originalURL), originalData)
        }
    }
    func testDestinationAppearingAfterPlanningMustMatchBeforeExclusivePublication() throws {
        let target = MobileCredential(endpoint: package().endpoint, deviceId: old.deviceId, token: old.token)
        let planned = MobileLocalState(credential: target)
        try store.validateMigration(from: old, to: target)
        var collision = planned; collision.drafts["mentor:future_self"] = "Newer destination draft"
        try store.save(collision)
        XCTAssertThrowsError(try store.publishMigration(planned))
        XCTAssertEqual(try store.load(target).drafts, collision.drafts)
        XCTAssertNoThrow(try store.publishMigration(collision))
    }
    func testUSBReplacementBeforeAtomicClaimIsPreservedAndNeverActivatesOlderBytes() throws {
        let original = try JSONEncoder().encode(package())
        let replacement = try JSONEncoder().encode(MobileInstallationPackage(endpoint: "https://replacement.example",
            deviceId: "replacement-device", deviceToken: token))
        let file = root.appendingPathComponent("MobileInstallation.json")
        try original.write(to: file)
        let source = MobileInstallationFile(directory: root, claim: { source, claimed in
            try replacement.write(to: source, options: .atomic)
            try MobileInstallationFile.claimPath(source, claimed)
        })
        let credentials = InstallationCredentials()
        XCTAssertThrowsError(try MobileInstallation.apply(source: source, existing: nil, credentials: credentials, persistence: store))
        XCTAssertEqual(try Data(contentsOf: root.appendingPathComponent(".MobileInstallationRecovery/package.json")), replacement)
        XCTAssertFalse(FileManager.default.fileExists(atPath: file.path))
        XCTAssertThrowsError(try source.read()); XCTAssertNil(credentials.value)
        XCTAssertThrowsError(try MobileInstallation.apply(source: source, existing: nil, credentials: credentials, persistence: store))
        XCTAssertNil(credentials.value)
    }
    func testUSBReplacementAfterClaimPreservesBothPackagesForControlledRecovery() throws {
        let original = try JSONEncoder().encode(package()), replacement = Data("Synthetic newer package".utf8)
        let file = root.appendingPathComponent("MobileInstallation.json")
        try original.write(to: file)
        let source = MobileInstallationFile(directory: root, claim: { source, claimed in
            try MobileInstallationFile.claimPath(source, claimed)
            try replacement.write(to: source, options: .atomic)
        })
        let credentials = InstallationCredentials()
        XCTAssertThrowsError(try MobileInstallation.apply(source: source, existing: nil, credentials: credentials, persistence: store))
        XCTAssertEqual(try Data(contentsOf: file), replacement)
        XCTAssertThrowsError(try source.read()); XCTAssertNil(credentials.value)
        let folders = try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { $0.lastPathComponent == ".MobileInstallationRecovery" }
        let recovery = try XCTUnwrap(folders.first)
        XCTAssertEqual(folders.count, 1)
        XCTAssertEqual(try Data(contentsOf: recovery.appendingPathComponent("package.json")), original)
        XCTAssertEqual((try FileManager.default.attributesOfItem(atPath: recovery.path)[.posixPermissions] as? NSNumber)?.intValue, 0o700)
        XCTAssertEqual((try FileManager.default.attributesOfItem(atPath: recovery.appendingPathComponent("package.json").path)[.posixPermissions] as? NSNumber)?.intValue, 0o600)
    }
    func testPackageFailureMatrixNeverWritesCredential() throws {
        let credentials = InstallationCredentials()
        let values = ["{", String(repeating: "x", count: 16_385),
            "{\"api_version\":2,\"endpoint\":\"https://new.example\"}",
            "{\"api_version\":1,\"endpoint\":\"http://new.example\"}",
            "{\"api_version\":1,\"endpoint\":\"https://new.example/\"}",
            "{\"api_version\":1,\"endpoint\":\"https://new.example\",\"device_id\":\"only-id\"}",
            "{\"api_version\":1,\"endpoint\":\"https://new.example\",\"device_token\":\"only-token\"}",
            "{\"api_version\":1,\"endpoint\":\"https://new.example\"}"]
        for value in values {
            let source = try InstallationSource(package()); source.data = Data(value.utf8)
            XCTAssertThrowsError(try apply(source, credentials)); XCTAssertNotNil(source.data)
            XCTAssertEqual(credentials.writes, 0)
        }
    }
    func testRealFileReaderRejectsSymlinkOversizeAndChangedPackageThenConsumesOnce() throws {
        let file = root.appendingPathComponent("MobileInstallation.json"), source = MobileInstallationFile(directory: root)
        XCTAssertNil(try source.read())
        try FileManager.default.createSymbolicLink(at: file, withDestinationURL: root.appendingPathComponent("missing"))
        XCTAssertThrowsError(try source.read()); try FileManager.default.removeItem(at: file)
        try Data(repeating: 120, count: 16_385).write(to: file)
        XCTAssertThrowsError(try source.read())
        let original = try JSONEncoder().encode(package()); try original.write(to: file)
        XCTAssertEqual(try source.read(), original)
        try Data("changed".utf8).write(to: file)
        XCTAssertThrowsError(try source.consume(original))
        XCTAssertEqual(try Data(contentsOf: root.appendingPathComponent(".MobileInstallationRecovery/package.json")), Data("changed".utf8))
        XCTAssertThrowsError(try source.read())
        try FileManager.default.removeItem(at: root.appendingPathComponent(".MobileInstallationRecovery"))
        try original.write(to: file); try source.consume(original)
        XCTAssertNil(try source.read()); XCTAssertThrowsError(try source.consume(original))
    }
}
