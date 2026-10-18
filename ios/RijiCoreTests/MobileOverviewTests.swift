import XCTest
@testable import RijiCore

final class MobileOverviewTests: XCTestCase {
    private func payload() -> [String: Any] {
        ["version": String(repeating: "a", count: 64), "updated_at": ISO8601DateFormatter().string(from: Date()),
         "cache_expires_at": Date().timeIntervalSince1970 + 899, "state": "ready", "job_status": "ready",
         "coverage": ["eligible": 1, "organized": 1, "unorganized": 0, "stopped": 0, "invalid": 0],
         "categories": ["preferences", "goals", "work", "relationships", "events", "other"].map { ["id": $0, "title": $0, "summary_count": $0 == "preferences" ? 1 : 0] as [String: Any] },
         "summaries": [["id": "summary-one", "category": "preferences", "title": "Synthetic preference", "summary": "Supported synthetic summary", "scope": "shared", "states": ["current"], "evidence_ids": ["m1"]]],
         "observations": [], "comparisons": [], "evidence": [["id": "m1", "content": "Synthetic evidence", "version": "one", "scope": "shared", "status": "active", "state": "current", "privacy": "cloud", "sources": []]],
         "can_organize": false, "organize_binding": String(repeating: "b", count: 64), "organize_reason": "no_eligible_work"]
    }
    private func page(_ body: [String: Any]) throws -> MobileMemoryOverview {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(MobileMemoryOverview.self, from: JSONSerialization.data(withJSONObject: body))
    }
    func testTypedPageValidationRejectsCrossScopeEvidenceAndExtraRecords() throws {
        let valid = try page(payload()).validated()
        XCTAssertEqual(valid.themeSummaries.count, 1)
        for invalidity in ["owner", "unreferenced", "expired", "coverage", "category", "permission"] {
            var body = payload()
            if invalidity == "expired" { body["cache_expires_at"] = Date().timeIntervalSince1970 - 1 }
            if invalidity == "coverage" { body["coverage"] = ["eligible": 2, "organized": 1, "unorganized": 0, "stopped": 0, "invalid": 0] }
            if invalidity == "category" { body["categories"] = [] }
            if ["owner", "unreferenced", "permission"].contains(invalidity) {
                var evidence = body["evidence"] as! [[String: Any]]
                if invalidity == "owner" { evidence[0]["scope"] = "persona"; evidence[0]["persona_id"] = "future_self" }
                if invalidity == "permission" { evidence[0]["privacy"] = "none" }
                if invalidity == "unreferenced" { var extra = evidence[0]; extra["id"] = "m2"; evidence.append(extra) }
                body["evidence"] = evidence
            }
            XCTAssertThrowsError(try page(body).validated(), invalidity)
        }
    }
    func testVersionMismatchNeverMixesPages() throws {
        let first = try page(payload())
        var changed = payload(); changed["version"] = String(repeating: "c", count: 64)
        XCTAssertThrowsError(try first.appending(page(changed)))
    }
    func testCacheIsDatedBoundedCredentialScopedAndSeparateFromDraftArchive() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("OverviewCore-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let credential = MobileCredential(endpoint: "https://synthetic.example", deviceId: "device", token: "example-synthetic-token")
        let store = MobileLocalStore(directory: root), cache = store.overviewCache
        defer { try? cache.invalidate(credential) }
        var local = MobileLocalState(credential: credential); local.drafts["memory:m1"] = "Retained edit"
        try store.save(local); try cache.save(page(payload()), credential: credential)
        XCTAssertEqual(try cache.load(credential)?.summaries.count, 1)
        XCTAssertNil(try cache.load(credential, now: Date().addingTimeInterval(901)))
        XCTAssertNil(try cache.load(MobileCredential(endpoint: credential.endpoint, deviceId: credential.deviceId, token: "example-replacement-token")))
        XCTAssertEqual(try store.archives().states.count, 1)
        try cache.invalidate(credential)
        XCTAssertNil(try cache.load(credential)); XCTAssertEqual(try store.load(credential).drafts["memory:m1"], "Retained edit")
    }
    func testFailedDeletionRevokesPersistentAdmissionBeforeColdRead() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("OverviewFailure-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let credential = MobileCredential(endpoint: "https://synthetic.example", deviceId: "device", token: "example-synthetic-token")
        let cache = MobileOverviewCache(directory: root, removeFile: { _ in throw MobileError.storage })
        try cache.save(page(payload()), credential: credential)
        XCTAssertNotNil(try cache.load(credential))
        XCTAssertThrowsError(try cache.invalidate(credential))
        XCTAssertTrue(FileManager.default.fileExists(atPath: root.appendingPathComponent(credential.scope + ".json").path))
        let restarted = MobileOverviewCache(directory: root)
        XCTAssertNil(try restarted.load(credential), "Independent durable admission must reject an undeletable old snapshot")
    }
    func testRestoringOldFileCannotRestoreRevokedAdmission() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("OverviewRestore-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let credential = MobileCredential(endpoint: "https://synthetic.example", deviceId: "device", token: "example-synthetic-token")
        let cache = MobileOverviewCache(directory: root), file = root.appendingPathComponent(credential.scope + ".json")
        try cache.save(page(payload()), credential: credential)
        let old = try Data(contentsOf: file)
        try cache.invalidate(credential); try old.write(to: file)
        XCTAssertNil(try MobileOverviewCache(directory: root).load(credential))
    }
}
