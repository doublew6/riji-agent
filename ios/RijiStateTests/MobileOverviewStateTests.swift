import XCTest
import Foundation
import RijiCore
@testable import RijiState

private func overviewJSON(version: String = "a", id: String = "one", cursor: String? = nil, state: String = "ready") throws -> String {
    let visible = ["ready", "partial", "failed"].contains(state)
    let body: [String: Any] = ["version": String(repeating: version, count: 64), "updated_at": ISO8601DateFormatter().string(from: Date()),
        "cache_expires_at": Date().timeIntervalSince1970 + 899, "state": state, "job_status": state == "failed" ? "failed" : "ready",
        "coverage": ["eligible": 2, "organized": visible ? 2 : 0, "unorganized": visible ? 0 : 2, "stopped": 0, "invalid": state == "invalid" ? 1 : 0],
        "categories": ["preferences", "goals", "work", "relationships", "events", "other"].map { ["id": $0, "title": $0, "summary_count": $0 == "preferences" && visible ? 2 : 0] as [String: Any] },
        "summaries": visible ? [["id": id, "category": "preferences", "title": "Synthetic " + id, "summary": "Synthetic summary " + id, "scope": "shared", "states": ["current"], "evidence_ids": ["m1"]]] : [],
        "observations": [], "comparisons": [], "evidence": visible ? [["id": "m1", "content": "Synthetic evidence", "version": "one", "scope": "shared", "status": "active", "state": "current", "privacy": "cloud", "sources": []]] : [],
        "next_cursor": cursor as Any? ?? NSNull(), "can_organize": state == "unorganized", "organize_binding": String(repeating: "b", count: 64),
        "organize_reason": state == "unorganized" ? NSNull() : "no_eligible_work" as Any]
    return String(decoding: try JSONSerialization.data(withJSONObject: body), as: UTF8.self)
}

@MainActor
final class MobileOverviewStateTests: XCTestCase {
    private let path = "/mobile/v1/memory-overview"
    private func start(_ fixture: MobileStateFixture) async {
        await fixture.wire.queue("/mobile/v1/capabilities", .init(json: mobileCapabilities.replacingOccurrences(of: "\"memory\":true", with: "\"memory\":true,\"memory_overview\":true")))
        await fixture.model.start()
    }
    private func queue(_ fixture: MobileStateFixture, version: String = "a", id: String = "one", cursor: String? = nil, state: String = "ready") async throws {
        await fixture.wire.queue(path, .init(json: try overviewJSON(version: version, id: id, cursor: cursor, state: state)))
    }
    func testSecondPageSurvivesSameVersionPollAndMixedVersionReloads() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; await start(f)
        try await queue(f, cursor: "next"); await f.model.refreshOverview()
        try await queue(f, id: "two"); await f.model.refreshOverview(more: true)
        XCTAssertEqual(f.model.overview?.summaries.map(\.id), ["one", "two"])
        try await queue(f, cursor: "next"); await f.model.refreshOverview()
        XCTAssertEqual(f.model.overview?.summaries.map(\.id), ["one", "two"])
        XCTAssertNil(f.model.overview?.nextCursor)
        try await queue(f, version: "b", id: "new", cursor: "changed"); await f.model.refreshOverview()
        XCTAssertEqual(f.model.overview?.summaries.map(\.id), ["new"])
        await f.wire.queue(path, .init(status: 409, json: "{\"detail\":\"memory_overview_changed\"}"))
        try await queue(f, version: "c", id: "reloaded"); await f.model.refreshOverview(more: true)
        XCTAssertEqual(f.model.overview?.summaries.map(\.id), ["reloaded"])
    }
    func testCorrectionInvalidatesProjectionImmediatelyAndFailedWriteKeepsDraft() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; await start(f)
        try await queue(f); await f.model.refreshOverview(); await f.model.refreshMemory("m1")
        // Existing detail has a different privacy, but same version; the write invalidates every derived entry.
        let memory = try XCTUnwrap(f.model.memoryDetails["m1"])
        f.model.setDraft("memory:m1", text: "Retained synthetic correction")
        await f.wire.hold("/mobile/v1/memories/m1/actions")
        let operation = Task { await f.model.memoryAction(memory, action: "update") }
        for _ in 0..<100 { if await f.wire.isHeld { break }; try await Task.sleep(nanoseconds: 10_000_000) }
        XCTAssertNil(f.model.overview)
        XCTAssertNil(try f.store.overviewCache.load(f.credential))
        try await queue(f, version: "b", state: "invalid")
        await f.wire.release(.init(json: "{\"operation_id\":\"$OP\",\"kind\":\"memory_action\",\"state\":\"failed\",\"error\":\"memory_version_changed\"}"))
        await operation.value
        XCTAssertEqual(f.model.draft("memory:m1"), "Retained synthetic correction")
        XCTAssertEqual(f.model.overview?.state, "invalid")
        XCTAssertTrue(f.model.overview?.summaries.isEmpty == true)
    }
    func testOfflineColdReadIsDatedAndCannotAuthorizeOrganization() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; await start(f)
        try await queue(f, state: "unorganized"); await f.model.refreshOverview()
        XCTAssertTrue(f.model.canOrganizeMemory)
        let restarted = MobileClientModel(credentials: f.credentials, persistence: f.store, transport: f.wire)
        restarted.retryCredentialRead()
        await f.wire.queue(path, .init(json: "", timeout: true)); await restarted.refreshOverview()
        XCTAssertTrue(restarted.overviewOffline); XCTAssertFalse(restarted.canOrganizeMemory)
        XCTAssertNotNil(restarted.overview?.updatedAt)
        await restarted.organizeMemory()
        let requests = await f.wire.requests
        XCTAssertFalse(requests.contains { $0.httpMethod == "POST" })
    }
    func testKnownRevocationAndAccountChangeNeverResurrectCachedText() async throws {
        for mode in ["source", "privacy", "device", "account"] {
            let f = MobileStateFixture(); defer { f.cleanup() }; await start(f)
            try await queue(f); await f.model.refreshOverview()
            XCTAssertNotNil(try f.store.overviewCache.load(f.credential))
            switch mode {
            case "source":
                await f.wire.queue("/mobile/v1/memories/m1", .init(status: 404, json: "{\"detail\":\"memory_not_found\"}")); await f.model.refreshMemory("m1")
            case "privacy": await f.model.refreshPrivacy()
            case "device":
                await f.wire.queue(path, .init(status: 401, json: "{}")); await f.model.refreshOverview()
                f.credentials.write(f.credential)
            default:
                f.credentials.write(MobileCredential(endpoint: "https://different.example", deviceId: "other-device", token: "example-other-token"))
                f.model.retryCredentialRead(); f.credentials.write(f.credential)
            }
            let restarted = MobileClientModel(credentials: f.credentials, persistence: f.store, transport: f.wire)
            restarted.retryCredentialRead()
            await f.wire.queue(path, .init(json: "", timeout: true)); await restarted.refreshOverview()
            XCTAssertNil(restarted.overview, mode)
        }
    }
    func testOutOfOrderReadCannotRestoreSummaryAfterEvidenceInvalidation() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; await start(f)
        try await queue(f); await f.model.refreshOverview()
        await f.wire.hold(path)
        let pending = Task { await f.model.refreshOverview() }
        for _ in 0..<100 { if await f.wire.isHeld { break }; try await Task.sleep(nanoseconds: 10_000_000) }
        await f.wire.queue("/mobile/v1/memories/m1", .init(json: mobileMemory("changed")))
        await f.model.refreshMemory("m1")
        await f.wire.release(.init(json: try overviewJSON())); await pending.value
        XCTAssertNil(f.model.overview)
        XCTAssertNil(try f.store.overviewCache.load(f.credential))
    }
    func testOrganizationTimeoutRelaunchReconcilesWithoutDuplicatePost() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; await start(f)
        try await queue(f, state: "unorganized"); await f.model.refreshOverview()
        await f.wire.queue(path + "/actions", .init(json: "", timeout: true))
        try await queue(f, state: "unorganized"); await f.model.organizeMemory()
        await f.model.organizeMemory()
        let operation = try XCTUnwrap(f.model.operation("memory-overview")); XCTAssertTrue(operation.unresolved)
        let restarted = MobileClientModel(credentials: f.credentials, persistence: f.store, transport: f.wire)
        restarted.retryCredentialRead()
        await f.wire.queue("/mobile/v1/operations/" + operation.id, .init(json: "{\"operation_id\":\"$OP\",\"kind\":\"memory_organize\",\"state\":\"completed\",\"result\":{\"accepted\":true,\"state\":\"pending\"}}"))
        try await queue(f, state: "unorganized"); await restarted.reconcile(operation)
        XCTAssertEqual(restarted.operation("memory-overview")?.state, "completed")
        let requests = await f.wire.requests
        XCTAssertEqual(requests.filter { $0.httpMethod == "POST" }.count, 1)
    }
    func testUnsupportedAndHonestStatesKeepRawRecordAccess() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; await f.model.start()
        await f.model.refreshOverview(); XCTAssertTrue(f.model.overviewUnsupported)
        await startFreshCapabilities(f)
        for state in ["partial", "unorganized", "empty", "permission_denied", "failed", "invalid"] {
            try await queue(f, state: state); await f.model.refreshOverview()
            XCTAssertEqual(f.model.overview?.state, state)
            XCTAssertFalse(f.model.overview?.stateText.isEmpty ?? true)
        }
        await f.model.refreshMemory("m1"); XCTAssertNotNil(f.model.memoryDetails["m1"])
    }
    func testRemotePurposeChangeAndUnavailablePrivacyInvalidateDurably() async throws {
        for changed in ["purposes", "engine"] {
            let f = MobileStateFixture(); defer { f.cleanup() }; await start(f)
            let current = "{\"available\":true,\"valid\":true,\"binding\":\"" + String(repeating: "b", count: 64) + "\",\"permissions\":{\"history\":false,\"incremental\":true,\"organization\":true,\"recall\":true}}"
            await f.wire.queue("/mobile/v1/privacy", .init(json: current)); await f.model.refreshPrivacy()
            try await queue(f); await f.model.refreshOverview()
            let replacement = changed == "engine" ? "{\"available\":false}" : current.replacingOccurrences(of: "\"recall\":true", with: "\"recall\":false")
            await f.wire.queue("/mobile/v1/privacy", .init(json: replacement)); await f.model.refreshPrivacy()
            XCTAssertNil(f.model.overview); XCTAssertNil(try f.store.overviewCache.load(f.credential))
        }
    }
    func testConfirmedCandidateCannotSubmitAfterRemoteVersionChanges() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; await start(f)
        await f.model.refreshMemory("m1")
        let candidate = try XCTUnwrap(f.model.memoryDetails["m1"])
        f.model.setDraft("memory:m1", text: "Retained candidate correction")
        await f.wire.queue("/mobile/v1/memories/m1", .init(json: mobileMemory("remote-version")))
        await f.model.refreshMemory("m1")
        await f.model.memoryAction(candidate, action: "update")
        XCTAssertEqual(f.model.draft("memory:m1"), "Retained candidate correction")
        let requests = await f.wire.requests
        XCTAssertFalse(requests.contains { $0.httpMethod == "POST" })
    }
    #if DEBUG
    func testConfirmationChangeFixtureWaitsForPresentedComparisonAndRealRefresh() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }
        let wire = MobileUIFixtureWire(mode: "overview-confirmation-change")
        let credential = MobileCredential(endpoint: "https://air.example", deviceId: "synthetic-ui-device", token: "example-synthetic-ui-only")
        let model = MobileClientModel(credentials: MobileVolatileCredentials(credential), persistence: f.store, transport: wire, isSyntheticEnvironment: true)
        XCTAssertNil(model.correctionComparisonDidAppear)
        model.correctionComparisonDidAppear = { await wire.confirmationDidAppear(memoryID: $0) }
        await model.start()
        XCTAssertTrue(model.serviceConnected)
        let id = "synthetic-memory", key = "memory:synthetic-memory"
        await model.refreshMemory(id)
        let candidate = try XCTUnwrap(model.memoryDetails[id])
        model.setDraft(key, text: "Synthetic confirmation candidate")
        await model.correctionComparisonDidAppear?("another-memory")
        for _ in 0..<10 {
            await model.refreshMemory(id)
            XCTAssertEqual(model.memoryDetails[id]?.version, "synthetic-v1")
            XCTAssertEqual(model.memoryDetails[id]?.content, "Synthetic memory for mobile acceptance")
            XCTAssertFalse(model.correctionNeedsRebase(candidate))
        }
        await model.correctionComparisonDidAppear?(id)
        XCTAssertEqual(model.memoryDetails[id]?.version, "synthetic-v1")
        await model.refreshMemory(id)
        let changed = try XCTUnwrap(model.memoryDetails[id])
        XCTAssertEqual(changed.version, "synthetic-v2")
        XCTAssertEqual(changed.content, "Synthetic changed during confirmation")
        XCTAssertTrue(model.correctionNeedsRebase(changed))
        await model.memoryAction(candidate, action: "update")
        XCTAssertNil(model.operation(key))
        XCTAssertEqual(model.draft(key), "Synthetic confirmation candidate")
        XCTAssertEqual(try f.store.load(credential).drafts[key], "Synthetic confirmation candidate")
        await model.refreshMemory(id)
        XCTAssertEqual(model.memoryDetails[id]?.content, "Synthetic changed during confirmation")
        let independentWire = MobileUIFixtureWire(mode: "overview-confirmation-change")
        let api = MobileAPI(endpoint: try MobileEndpoint("https://air.example"), token: nil, transport: independentWire)
        let independent: MobileMemory = try await api.request(["memories", id])
        XCTAssertEqual(independent.version, "synthetic-v1")
    }
    #endif
    private func startFreshCapabilities(_ f: MobileStateFixture) async {
        await f.wire.queue("/mobile/v1/capabilities", .init(json: mobileCapabilities.replacingOccurrences(of: "\"memory\":true", with: "\"memory\":true,\"memory_overview\":true")))
        await f.model.refreshCapabilities()
    }
}
