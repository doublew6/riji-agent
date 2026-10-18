import XCTest
import Foundation
import RijiCore
@testable import RijiState

let mobileCapabilities = """
{"api_version":1,"device":{"id":"device-one","name":"Synthetic"},"capabilities":{"mentors":true,"memory":true,"privacy":true,"roundtable":false,"workflows":false,"diary_upload":false},"mentors":[{"id":"gentle_reviewer","name":"温柔回顾者"},{"id":"blunt_coach","name":"直率教练"},{"id":"future_self","name":"未来的我"},{"id":"wang_yangming","name":"王阳明"}]}
"""
private let mobileConversation = """
{"conversation_id":null,"status":"empty","revision":0,"messages":[],"notice":null}
"""
private func mobileHistory(_ ids: [String], version: String = "history-one", cursor: String? = nil) throws -> String {
    let messages = ids.map { ["id": $0, "actor": "future_self", "text": "Synthetic " + $0, "created_at": 1, "source_ids": []] as [String: Any] }
    let value: [String: Any] = ["conversation_id": "conversation-one", "status": "completed", "revision": 2,
                               "messages": messages, "history_version": version, "next_cursor": cursor.map { $0 as Any } ?? NSNull(),
                               "history_has_more": cursor != nil]
    return String(decoding: try JSONSerialization.data(withJSONObject: value), as: UTF8.self)
}
func mobileMemory(_ version: String = "one") -> String {
    "{\"id\":\"m1\",\"content\":\"Synthetic fact\",\"version\":\"\(version)\",\"scope\":\"shared\",\"persona_id\":null,\"status\":\"active\",\"state\":\"current\",\"privacy\":\"local\",\"updated_at\":null,\"reviewed_at\":null,\"sources\":[]}"
}
private let mobilePrivacy = """
{"available":true,"valid":true,"binding":"binding-one","permissions":{"history":false,"incremental":false,"organization":false,"recall":false},"paused":true,"source_scope":{"sections":["Notes"],"date_from":null,"date_to":null,"mentors":["future_self"]},"targets":{"extraction":{"provider":"example-synthetic","model":"synthetic-model","host":"cloud.example"},"recall":{"provider":"example-synthetic","model":"synthetic-model","host":"cloud.example"}},"limits":{"daily_chars":100000,"fragment_chars":900,"source_chars":4000},"progress":{"discovered":2,"completed":1,"pending":1,"failed":0}}
"""
actor MobileStateWire: MobileTransport {
    struct Reply: Sendable { var status = 200; var json: String; var timeout = false }
    private(set) var requests: [URLRequest] = []
    private var replies: [String: [Reply]] = [:]
    private var heldPath: String?
    private var holdUnpagedOnly = false
    private var held: CheckedContinuation<Reply, Never>?
    var isHeld: Bool { held != nil }
    func queue(_ path: String, _ reply: Reply) { replies[path, default: []].append(reply) }
    func hold(_ path: String, unpagedOnly: Bool = false) { heldPath = path; holdUnpagedOnly = unpagedOnly }
    func release(_ reply: Reply, holdNext: String? = nil) {
        let continuation = held; held = nil; heldPath = holdNext; continuation?.resume(returning: reply)
    }
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        requests.append(request)
        let path = request.url!.path
        var reply: Reply
        if path == heldPath && (!holdUnpagedOnly || request.url?.query == nil) { heldPath = nil; reply = await withCheckedContinuation { held = $0 } }
        else if replies[path]?.isEmpty == false { reply = replies[path]!.removeFirst() }
        else if path.hasSuffix("/capabilities") { reply = Reply(json: mobileCapabilities) }
        else if path.hasSuffix("/conversation") { reply = Reply(json: mobileConversation) }
        else if path.hasSuffix("/privacy") { reply = Reply(json: mobilePrivacy) }
        else if path.hasSuffix("/memories/m1") { reply = Reply(json: mobileMemory()) }
        else { reply = Reply(status: 404, json: "{\"detail\":\"not_found\"}") }
        if reply.timeout { throw URLError(.timedOut) }
        let body = request.httpBody.flatMap { try? JSONDecoder().decode([String: MobileJSON].self, from: $0) }
        let operation = body?["operation_id"]?.string ?? request.url!.lastPathComponent
        let json = reply.json.replacingOccurrences(of: "$OP", with: operation)
        return (Data(json.utf8), HTTPURLResponse(url: request.url!, statusCode: reply.status, httpVersion: nil, headerFields: nil)!)
    }
}

@MainActor
final class MobileStateFixture {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("MobileState-\(UUID())")
    let wire = MobileStateWire()
    let credential = MobileCredential(endpoint: "https://air.example", deviceId: "device-one", token: "example-synthetic-device-only")
    let credentials: MobileVolatileCredentials
    let store: MobileLocalStore
    let model: MobileClientModel
    init() {
        credentials = MobileVolatileCredentials(credential); store = MobileLocalStore(directory: root)
        model = MobileClientModel(credentials: credentials, persistence: store, transport: wire)
    }
    func cleanup() { try? FileManager.default.removeItem(at: root) }
}

private final class MobileFailingCredentials: MobileCredentialStore, @unchecked Sendable {
    private let lock = NSLock()
    private var value: MobileCredential?
    private var failsRead = false
    private var failsWrite = false
    init(_ value: MobileCredential?) { self.value = value }
    func rejectReads(_ reject: Bool = true) { lock.lock(); defer { lock.unlock() }; failsRead = reject }
    func rejectWrites(_ reject: Bool) { lock.lock(); defer { lock.unlock() }; failsWrite = reject }
    func read() throws -> MobileCredential? {
        lock.lock(); defer { lock.unlock() }
        if failsRead { throw MobileError.storage }; return value
    }
    func write(_ value: MobileCredential?) throws {
        lock.lock(); defer { lock.unlock() }
        if failsWrite { throw MobileError.storage }; self.value = value
    }
}

@MainActor
private final class MobileManagedFixture {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("ManagedState-" + UUID().uuidString)
    let wire = MobileStateWire()
    let credentials: MobileFailingCredentials
    let store: MobileLocalStore
    let source: MobileInstallationFile
    let old = MobileCredential(endpoint: "https://air.example", deviceId: "device-one", token: String(repeating: "o", count: 64))
    var packageURL: URL { root.appendingPathComponent("Caches/MobileInstallation.json") }
    init(existing: Bool = false) throws {
        store = MobileLocalStore(directory: root.appendingPathComponent("State"))
        source = MobileInstallationFile(directory: root.appendingPathComponent("Caches"))
        credentials = MobileFailingCredentials(existing ? old : nil)
        try FileManager.default.createDirectory(at: root.appendingPathComponent("Caches"), withIntermediateDirectories: true)
    }
    func writePackage(endpoint: String = "https://air.example", previous: String? = nil) throws {
        let package = MobileInstallationPackage(endpoint: endpoint, previousEndpoint: previous,
            deviceId: "device-one", deviceToken: String(repeating: "n", count: 64))
        try JSONEncoder().encode(package).write(to: packageURL)
    }
    func model() -> MobileClientModel {
        MobileClientModel(credentials: credentials, persistence: store, transport: wire, installation: source)
    }
    func cleanup() { try? FileManager.default.removeItem(at: root) }
}

@MainActor
final class MobileManagedLifecycleTests: XCTestCase {
    func testManagedLaunchRelaunchAndForegroundRetainIdentityDraftAndPendingOperationWithoutReplay() async throws {
        let fixture = try MobileManagedFixture(); defer { fixture.cleanup() }
        try fixture.writePackage()
        let first = fixture.model(); await first.start()
        XCTAssertNotNil(first.capabilities); XCTAssertNil(try fixture.source.read())
        first.setDraft("mentor:future_self", text: "Synthetic retained draft")
        var state = try fixture.store.load(try XCTUnwrap(first.credential))
        let pending = MobilePendingOperation(kind: "mentor_message", target: "mentor:future_self",
            path: ["mentors", "future_self", "messages"], body: ["text": .string("Synthetic pending request")])
        state.operations.append(pending); try fixture.store.save(state)
        let relaunched = fixture.model(); await relaunched.start()
        let identity = relaunched.accountIdentity, device = relaunched.credential?.deviceId
        relaunched.suspend(); await relaunched.resume(); await relaunched.retryConnection()
        XCTAssertEqual(relaunched.accountIdentity, identity); XCTAssertEqual(relaunched.credential?.deviceId, device)
        XCTAssertEqual(relaunched.draft("mentor:future_self"), "Synthetic retained draft")
        XCTAssertEqual(relaunched.local?.operations.first?.id, pending.id)
        let requests = await fixture.wire.requests
        XCTAssertTrue(requests.allSatisfy { $0.httpMethod == "GET" && $0.url?.path == "/mobile/v1/capabilities" })
        first.disconnect(); let disconnected = fixture.model(); await disconnected.start()
        XCTAssertFalse(disconnected.connected); XCTAssertNil(try fixture.source.read())
    }
    func testLockedCredentialReadDoesNotConsumeOrReplacePackageAndUnlockRetriesAutomatically() async throws {
        let fixture = try MobileManagedFixture(); defer { fixture.cleanup() }
        try fixture.writePackage(); let original = try fixture.source.read()
        fixture.credentials.rejectReads()
        let model = fixture.model(); await model.start()
        XCTAssertFalse(model.connected); XCTAssertTrue(model.credentialRestorationPending)
        XCTAssertEqual(try fixture.source.read(), original)
        let requests = await fixture.wire.requests; XCTAssertTrue(requests.isEmpty)
        fixture.credentials.rejectReads(false); await model.resume()
        XCTAssertNotNil(model.capabilities); XCTAssertNil(try fixture.source.read())
    }
    func testOriginTransitionUsesOriginalTokenAndOperationsOnForeground() async throws {
        let fixture = try MobileManagedFixture(existing: true); defer { fixture.cleanup() }
        var state = MobileLocalState(credential: fixture.old)
        state.drafts["mentor:future_self"] = "Synthetic migration draft"
        state.operations.append(MobilePendingOperation(kind: "mentor_message", target: "mentor:future_self",
            path: ["mentors", "future_self", "messages"], body: ["text": .string("Synthetic request")]))
        try fixture.store.save(state)
        let model = fixture.model(); await model.start(); let identity = model.accountIdentity
        try fixture.writePackage(endpoint: "https://public.example:10000", previous: fixture.old.endpoint)
        model.suspend(); await model.resume()
        XCTAssertEqual(model.credential?.endpoint, "https://public.example:10000")
        XCTAssertEqual(model.credential?.token, fixture.old.token); XCTAssertEqual(model.credential?.deviceId, fixture.old.deviceId)
        XCTAssertNotEqual(model.accountIdentity, identity)
        XCTAssertEqual(model.local?.operations.first?.id, state.operations.first?.id)
        XCTAssertEqual(model.local?.drafts, state.drafts); XCTAssertEqual(try fixture.store.load(fixture.old).drafts, state.drafts)
        let requests = await fixture.wire.requests; XCTAssertTrue(requests.allSatisfy { $0.httpMethod == "GET" })
    }
    func testUnreadableOriginalStatePreventsInstallAndKeepsPackageRetryable() async throws {
        let fixture = try MobileManagedFixture(existing: true); defer { fixture.cleanup() }
        try fixture.store.save(MobileLocalState(credential: fixture.old))
        try Data("corrupt".utf8).write(to: fixture.root.appendingPathComponent("State/" + fixture.old.scope + ".json"))
        try fixture.writePackage(endpoint: "https://public.example", previous: fixture.old.endpoint)
        let model = fixture.model(); await model.start()
        XCTAssertFalse(model.connected); XCTAssertTrue(model.credentialRestorationPending)
        XCTAssertNotNil(try fixture.source.read()); XCTAssertEqual(try fixture.credentials.read()?.endpoint, fixture.old.endpoint)
        let requests = await fixture.wire.requests; XCTAssertTrue(requests.isEmpty)
    }
    func testKeychainUpdateFailureConsumesPackageButKeepsOriginalDevice() async throws {
        let fixture = try MobileManagedFixture(existing: true); defer { fixture.cleanup() }
        try fixture.writePackage(endpoint: "https://public.example", previous: fixture.old.endpoint)
        fixture.credentials.rejectWrites(true)
        let model = fixture.model(); await model.start()
        XCTAssertEqual(model.credential?.endpoint, fixture.old.endpoint)
        XCTAssertEqual(try fixture.credentials.read()?.endpoint, fixture.old.endpoint)
        XCTAssertNotNil(model.installationError); XCTAssertNil(try fixture.source.read())
        fixture.credentials.rejectWrites(false); await model.resume()
        XCTAssertEqual(model.credential?.endpoint, fixture.old.endpoint)
    }
    func testRejectedPackageCannotBecomeFreshEnrollmentAfterDisconnectOrRevocation() async throws {
        for revoked in [false, true] {
            let fixture = try MobileManagedFixture(existing: true); defer { fixture.cleanup() }
            try fixture.writePackage(endpoint: "https://public.example", previous: "https://wrong.example")
            let model = fixture.model()
            if revoked { await fixture.wire.queue("/mobile/v1/capabilities", .init(status: 401, json: "{\"detail\":\"revoked\"}")) }
            await model.start()
            if !revoked { XCTAssertNotNil(try fixture.source.read()); model.disconnect() }
            XCTAssertNil(try fixture.source.read()); XCTAssertNil(try fixture.credentials.read())
            let relaunched = fixture.model(); await relaunched.start()
            XCTAssertFalse(relaunched.connected)
            let requests = await fixture.wire.requests; XCTAssertEqual(requests.count, 1)
        }
    }
    func testMissingInstallationAndOutageHaveServiceStatusWhileDiaryRemainsIndependent() async throws {
        let fixture = try MobileManagedFixture(); defer { fixture.cleanup() }
        let model = fixture.model(); await model.start()
        XCTAssertEqual(model.connectionState, .notConnected)
        try fixture.writePackage()
        await fixture.wire.queue("/mobile/v1/capabilities", .init(json: "{}", timeout: true))
        await model.retryConnection()
        XCTAssertTrue(model.connected); XCTAssertNil(model.capabilities)
        XCTAssertEqual(model.connectionState, .offline)
        XCTAssertFalse(model.serviceConnected)
        await model.retryConnection(); XCTAssertNotNil(model.capabilities)
    }
}

@MainActor
final class MobileClientModelTests: XCTestCase {
    private let persona = "future_self"
    private var messagePath: String { "/mobile/v1/mentors/\(persona)/messages" }
    private func receipt(_ kind: String, state: String = "completed") -> String {
        "{\"operation_id\":\"$OP\",\"kind\":\"\(kind)\",\"state\":\"\(state)\",\"result\":{},\"error\":null}"
    }
    private func waitForHold(_ wire: MobileStateWire) async throws {
        for _ in 0..<100 { if await wire.isHeld { return }; try await Task.sleep(nanoseconds: 10_000_000) }
        XCTFail("Synthetic request did not reach its barrier"); throw MobileError.transport
    }
    func testEmptyUnsavedDraftDeletionStaysVisibleForDiscardAndSaveRetry() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        let key = "mentor:" + persona
        fixture.model.setDraft(key, text: "Saved original")
        try FileManager.default.removeItem(at: fixture.root)
        try Data("storage obstacle".utf8).write(to: fixture.root)
        fixture.model.setDraft(key, text: "")
        XCTAssertEqual(fixture.model.visibleArchives([]).map(\.scope), [fixture.credential.scope])
        XCTAssertEqual(fixture.model.unsavedRecovery[fixture.credential.scope]?.drafts[key], "")
        XCTAssertEqual(fixture.model.pendingDraftClears(scope: fixture.credential.scope), [key])
        fixture.model.discardRecovery(scope: fixture.credential.scope)
        XCTAssertEqual(fixture.model.draft(key), "Saved original")
        XCTAssertTrue(fixture.model.pendingDraftClears(scope: fixture.credential.scope).isEmpty)
        fixture.model.setDraft(key, text: "")
        try FileManager.default.removeItem(at: fixture.root)
        XCTAssertTrue(fixture.model.retryRecoverySave(scope: fixture.credential.scope))
        XCTAssertEqual(try fixture.store.load(fixture.credential).drafts[key], "")
        XCTAssertTrue(fixture.model.visibleArchives(try fixture.store.archives().states).isEmpty)
    }
    func testDisconnectedFirstArchiveReadShowsHealthyDraftDespiteDuplicateDecodedScope() throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }
        var state = MobileLocalState(credential: fixture.credential); state.drafts["mentor:" + persona] = "Healthy old draft"
        try fixture.store.save(state)
        let badScope = MobileCredential(endpoint: "https://other.example", deviceId: "other", token: "example-synthetic").scope
        let file = fixture.root.appendingPathComponent(badScope + ".json")
        let mismatched = try JSONEncoder().encode(state); try mismatched.write(to: file)
        let result = try fixture.model.archives()
        XCTAssertFalse(fixture.model.connected)
        XCTAssertEqual(result.unreadableCount, 1); XCTAssertNotNil(result.warning)
        XCTAssertEqual(fixture.model.visibleArchives(result.states).first?.drafts["mentor:" + persona], "Healthy old draft")
        XCTAssertEqual(fixture.model.visibleArchives(result.states + result.states).count, 1)
        XCTAssertEqual(try Data(contentsOf: file), mismatched)
    }
    func testCredentialReadRetryRestoresExistingDraftWithoutWritingOrSending() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }
        var state = MobileLocalState(credential: fixture.credential); state.drafts["mentor:" + persona] = "Existing draft"
        try fixture.store.save(state)
        let credentials = MobileFailingCredentials(fixture.credential); credentials.rejectReads(); credentials.rejectWrites(true)
        let model = MobileClientModel(credentials: credentials, persistence: fixture.store, transport: fixture.wire)
        await model.start(); model.retryCredentialRead()
        XCTAssertFalse(model.connected); XCTAssertNotNil(model.credentialError)
        credentials.rejectReads(false); model.retryCredentialRead()
        XCTAssertEqual(model.credential?.deviceId, fixture.credential.deviceId); XCTAssertNil(model.credentialError)
        XCTAssertEqual(model.draft("mentor:" + persona), "Existing draft")
        XCTAssertNil(model.capabilities)
        let requests = await fixture.wire.requests; XCTAssertTrue(requests.isEmpty)
    }
    func testSuccessfulCredentialReadKeepsRetryVisibleUntilLocalRecordsRestore() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }
        var state = MobileLocalState(credential: fixture.credential); state.drafts["mentor:" + persona] = "Saved draft"
        try fixture.store.save(state)
        let file = fixture.root.appendingPathComponent(fixture.credential.scope + ".json")
        let backup = fixture.root.appendingPathComponent("saved-record.backup")
        try FileManager.default.moveItem(at: file, to: backup)
        try FileManager.default.createDirectory(at: file, withIntermediateDirectories: false)
        let credentials = MobileFailingCredentials(fixture.credential); credentials.rejectReads(); credentials.rejectWrites(true)
        let model = MobileClientModel(credentials: credentials, persistence: fixture.store, transport: fixture.wire)
        await model.start(); XCTAssertNotNil(model.credentialError)
        credentials.rejectReads(false); model.retryCredentialRead()
        XCTAssertNil(model.credentialError); XCTAssertTrue(model.credentialRestorationPending)
        XCTAssertNil(model.credential); XCTAssertNil(model.local)
        XCTAssertTrue(model.connectionStatus.contains("恢复未完成")); XCTAssertTrue(model.error?.contains("无法读取") == true)
        await model.refreshCapabilities(); await model.refreshConversation(persona)
        try FileManager.default.removeItem(at: file); try FileManager.default.moveItem(at: backup, to: file)
        model.retryCredentialRead()
        XCTAssertFalse(model.credentialRestorationPending); XCTAssertNil(model.error)
        XCTAssertEqual(model.draft("mentor:" + persona), "Saved draft")
        let requests = await fixture.wire.requests; XCTAssertTrue(requests.isEmpty)
    }
    func testNewCredentialRestoreFailurePausesOldIdentityAndPreservesBothDraftVersions() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        let key = "mentor:" + persona
        fixture.model.setDraft(key, text: "Old durable draft")
        let oldFile = fixture.root.appendingPathComponent(fixture.credential.scope + ".json")
        let backup = fixture.root.appendingPathComponent("old-record.backup")
        try FileManager.default.moveItem(at: oldFile, to: backup)
        try FileManager.default.createDirectory(at: oldFile, withIntermediateDirectories: false)
        fixture.model.setDraft(key, text: "Old unsaved draft")
        try FileManager.default.removeItem(at: oldFile); try FileManager.default.moveItem(at: backup, to: oldFile)
        let next = MobileCredential(endpoint: "https://other.example", deviceId: "device-two", token: "example-synthetic")
        var state = MobileLocalState(credential: next); state.drafts[key] = "New account draft"
        let file = fixture.root.appendingPathComponent(next.scope + ".json")
        try FileManager.default.createDirectory(at: file, withIntermediateDirectories: false)
        await fixture.wire.hold("/mobile/v1/memories/m1")
        let task = Task { await fixture.model.refreshMemory("m1") }; try await waitForHold(fixture.wire)
        let before = await fixture.wire.requests.count
        fixture.credentials.write(next); fixture.model.retryCredentialRead()
        XCTAssertTrue(fixture.model.credentialRestorationPending); XCTAssertFalse(fixture.model.connected)
        XCTAssertNil(fixture.model.credential); XCTAssertNil(fixture.model.local)
        await fixture.model.refreshCapabilities(); await fixture.model.refreshConversation(persona)
        await fixture.model.refreshMemory("m1"); await fixture.model.refreshPrivacy()
        await fixture.model.send(persona); await fixture.model.control(persona, action: "resume", revision: 1)
        await fixture.wire.release(.init(json: mobileMemory())); await task.value
        XCTAssertNil(fixture.model.memoryDetails["m1"])
        XCTAssertEqual(fixture.model.unsavedRecovery[fixture.credential.scope]?.drafts[key], "Old unsaved draft")
        XCTAssertEqual(try fixture.store.load(fixture.credential).drafts[key], "Old durable draft")
        try FileManager.default.removeItem(at: file); try fixture.store.save(state)
        fixture.model.retryCredentialRead()
        XCTAssertFalse(fixture.model.credentialRestorationPending); XCTAssertEqual(fixture.model.credential?.scope, next.scope)
        XCTAssertEqual(fixture.model.draft(key), "New account draft")
        XCTAssertEqual(fixture.model.unsavedRecovery[fixture.credential.scope]?.drafts[key], "Old unsaved draft")
        let after = await fixture.wire.requests.count; XCTAssertEqual(after, before)
    }
    func testCredentialRetryRetainsRAMDraftAndInvalidatesLateRead() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        let key = "mentor:" + persona
        try Data("storage obstacle".utf8).write(to: fixture.root)
        fixture.model.setDraft(key, text: "RAM draft")
        await fixture.wire.hold("/mobile/v1/memories/m1")
        let task = Task { await fixture.model.refreshMemory("m1") }; try await waitForHold(fixture.wire)
        let before = await fixture.wire.requests.count
        fixture.model.retryCredentialRead()
        XCTAssertEqual(fixture.model.draft(key), "RAM draft")
        XCTAssertEqual(fixture.model.unsavedRecovery[fixture.credential.scope]?.drafts[key], "RAM draft")
        await fixture.wire.release(.init(json: mobileMemory())); await task.value
        let after = await fixture.wire.requests.count; XCTAssertEqual(after, before)
        XCTAssertNil(fixture.model.memoryDetails["m1"])
    }
    func testCredentialRetryRejectsPreviousAccountResponseAndDoesNotSendOldDraft() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        let key = "mentor:" + persona
        fixture.model.setDraft(key, text: "Old account draft")
        let next = MobileCredential(endpoint: "https://other.example", deviceId: "device-two", token: "example-synthetic")
        var state = MobileLocalState(credential: next); state.drafts[key] = "New account draft"; try fixture.store.save(state)
        await fixture.wire.hold("/mobile/v1/memories/m1")
        let task = Task { await fixture.model.refreshMemory("m1") }; try await waitForHold(fixture.wire)
        fixture.credentials.write(next); fixture.model.retryCredentialRead()
        await fixture.wire.release(.init(json: mobileMemory())); await task.value
        XCTAssertEqual(fixture.model.draft(key), "New account draft"); XCTAssertEqual(fixture.model.credential?.scope, next.scope)
        XCTAssertNil(fixture.model.memoryDetails["m1"])
        XCTAssertEqual(try fixture.store.load(fixture.credential).drafts[key], "Old account draft")
        let posts = (await fixture.wire.requests).filter { $0.httpMethod == "POST" }; XCTAssertTrue(posts.isEmpty)
    }
    func testDisconnectedHealthyArchiveCanDiscardFailedInputDespiteUnrelatedCorruption() throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }
        var state = MobileLocalState(credential: fixture.credential)
        var failed = MobilePendingOperation(kind: "memory_action", target: "memory:m1", path: ["memories", "m1", "actions"], body: ["content": .string("Rejected correction")])
        failed.state = "failed"; state.operations = [failed]
        try fixture.store.save(state)
        let badScope = MobileCredential(endpoint: "https://other.example", deviceId: "broken", token: "example-synthetic").scope
        let badFile = fixture.root.appendingPathComponent(badScope + ".json")
        let corrupt = Data("corrupt unrelated archive".utf8); try corrupt.write(to: badFile)
        let initial = try fixture.model.archives()
        XCTAssertEqual(initial.unreadableCount, 1); XCTAssertNotNil(initial.warning)
        XCTAssertEqual(fixture.model.visibleArchives(initial.states).map(\.scope), [fixture.credential.scope])
        XCTAssertNil(fixture.model.credential)
        XCTAssertTrue(fixture.model.discardFailedInputs(scope: fixture.credential.scope, operationID: failed.id))
        XCTAssertFalse(try fixture.store.load(fixture.credential).operations.contains(where: \.hasFailedInput))
        XCTAssertFalse(fixture.model.discardFailedInputs(scope: badScope))
        XCTAssertEqual(try Data(contentsOf: badFile), corrupt)
        XCTAssertEqual(try fixture.model.archives().unreadableCount, 1)
    }
    func testCredentialWriteFailureSurvivesOldRecoverySettlementAndSuccessfulRefresh() async throws {
        for settlement in ["retry", "discard"] {
            let fixture = MobileStateFixture(); defer { fixture.cleanup() }
            let credentials = MobileFailingCredentials(fixture.credential)
            let model = MobileClientModel(credentials: credentials, persistence: fixture.store, transport: fixture.wire)
            await model.start()
            try Data("storage obstacle".utf8).write(to: fixture.root)
            model.setDraft("mentor:" + persona, text: "Old recovery")
            model.disconnect()
            await fixture.wire.queue("/mobile/v1/pair", .init(json: "{\"api_version\":1,\"device_id\":\"device-one\",\"device_token\":\"new-synthetic\"}"))
            await model.pair(endpoint: "https://other.example", code: "once", deviceName: "Synthetic")
            credentials.rejectWrites(true); model.disconnect()
            XCTAssertEqual(model.credentialError, MobileError.credentialStorage.localizedDescription)
            await model.refreshCapabilities()
            if settlement == "retry" {
                try FileManager.default.removeItem(at: fixture.root)
                XCTAssertTrue(model.retryRecoverySave(scope: fixture.credential.scope))
            } else { model.discardRecovery(scope: fixture.credential.scope) }
            XCTAssertEqual(model.credentialError, MobileError.credentialStorage.localizedDescription)
            XCTAssertTrue(model.connected)
            credentials.rejectWrites(false); model.disconnect()
            XCTAssertNil(model.credentialError); XCTAssertFalse(model.connected)
        }
    }
    func testCredentialReadFailureSurvivesUnrelatedDisconnectedArchiveCleanup() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }
        var state = MobileLocalState(credential: fixture.credential)
        var failed = MobilePendingOperation(kind: "memory_action", target: "memory:m1", path: ["memories", "m1", "actions"], body: ["content": .string("Retained correction")])
        failed.state = "failed"; state.operations = [failed]; try fixture.store.save(state)
        let credentials = MobileFailingCredentials(fixture.credential); credentials.rejectReads()
        let model = MobileClientModel(credentials: credentials, persistence: fixture.store, transport: fixture.wire)
        await model.start()
        XCTAssertTrue(model.discardFailedInputs(scope: fixture.credential.scope))
        await model.refreshCapabilities()
        XCTAssertEqual(model.credentialError, MobileError.credentialStorage.localizedDescription)
        XCTAssertNil(model.credential)
    }
    func testOldRecoveryDiscardCannotClearCurrentScopesStorageFailure() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        try Data("storage obstacle".utf8).write(to: fixture.root)
        fixture.model.setDraft("mentor:" + persona, text: "Old recovery"); fixture.model.disconnect()
        await fixture.wire.queue("/mobile/v1/pair", .init(json: "{\"api_version\":1,\"device_id\":\"device-one\",\"device_token\":\"new-synthetic\"}"))
        await fixture.model.pair(endpoint: "https://other.example", code: "once", deviceName: "Synthetic")
        fixture.model.setDraft("mentor:" + persona, text: "Current unsaved draft")
        fixture.model.discardRecovery(scope: fixture.credential.scope)
        XCTAssertEqual(fixture.model.error, MobileError.storage.localizedDescription)
        XCTAssertNotNil(fixture.model.recoveryError)
        XCTAssertEqual(fixture.model.draft("mentor:" + persona), "Current unsaved draft")
    }
    func testFailedMemoryCorrectionKeepsSubmittedContentAndNewDraftAcrossReload() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        await fixture.model.refreshMemory("m1")
        let memory = try XCTUnwrap(fixture.model.memoryDetails["m1"])
        fixture.model.setDraft("memory:m1", text: "Correction A")
        await fixture.wire.hold("/mobile/v1/memories/m1/actions")
        let send = Task { await fixture.model.memoryAction(memory, action: "update") }; try await waitForHold(fixture.wire)
        fixture.model.setDraft("memory:m1", text: "Correction B")
        await fixture.wire.release(.init(json: receipt("memory_action", state: "failed"))); await send.value
        let restarted = MobileClientModel(credentials: fixture.credentials, persistence: fixture.store, transport: fixture.wire)
        await restarted.start()
        XCTAssertEqual(restarted.operation("memory:m1")?.body["content"], .string("Correction A"))
        XCTAssertTrue(restarted.operation("memory:m1")?.hasFailedInput == true)
        XCTAssertEqual(restarted.draft("memory:m1"), "Correction B")
        XCTAssertEqual(restarted.local?.memoryDraftVersions?["memory:m1"], "one")
        XCTAssertFalse(restarted.hasUnresolved("memory:m1"))
    }
    func testFailedRequestRetainsOriginalInputAfterNewTypingAndSuccessfulNextSend() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start(); await fixture.model.refreshConversation(persona)
        let key = "mentor:" + persona
        fixture.model.setDraft(key, text: "Request A")
        await fixture.wire.hold(messagePath)
        let send = Task { await fixture.model.send(persona) }; try await waitForHold(fixture.wire)
        fixture.model.setDraft(key, text: "Draft B")
        await fixture.wire.release(.init(json: receipt("mentor_message", state: "failed"))); await send.value
        let failed = try XCTUnwrap(fixture.model.operation(key))
        XCTAssertEqual(failed.inputText, "Request A"); XCTAssertTrue(failed.hasFailedInput)
        XCTAssertEqual(fixture.model.draft(key), "Draft B"); XCTAssertFalse(fixture.model.hasUnresolved(key))
        await fixture.wire.queue(messagePath, .init(json: receipt("mentor_message")))
        await fixture.model.send(persona)
        let saved = try fixture.store.load(fixture.credential)
        XCTAssertEqual(saved.operations.first { $0.id == failed.id }?.inputText, "Request A")
        XCTAssertTrue(saved.operations.last?.body.isEmpty == true)
        fixture.model.setDraft(key, text: "Draft C")
        XCTAssertTrue(fixture.model.discardFailedInputs(scope: fixture.credential.scope, operationID: failed.id))
        XCTAssertEqual(fixture.model.draft(key), "Draft C")
        XCTAssertFalse(try fixture.store.load(fixture.credential).operations.contains(where: \.hasFailedInput))
        let posts = (await fixture.wire.requests).filter { $0.httpMethod == "POST" }; XCTAssertEqual(posts.count, 2)
    }
    func testOldRecoveryRetryRequiresNoCredentialAndKeepsTextOnSaveFailure() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        try Data("storage obstacle".utf8).write(to: fixture.root)
        fixture.model.setDraft("mentor:" + persona, text: "Old RAM-only draft")
        fixture.model.disconnect()
        let before = await fixture.wire.requests
        XCTAssertNil(fixture.model.recoveryError); XCTAssertTrue(fixture.model.hasOtherRecovery)
        XCTAssertFalse(fixture.model.retryRecoverySave(scope: fixture.credential.scope))
        XCTAssertEqual(fixture.model.unsavedRecovery[fixture.credential.scope]?.drafts["mentor:" + persona], "Old RAM-only draft")
        try FileManager.default.removeItem(at: fixture.root)
        XCTAssertTrue(fixture.model.retryRecoverySave(scope: fixture.credential.scope))
        XCTAssertNil(fixture.model.credential); XCTAssertTrue(fixture.model.unsavedRecovery.isEmpty)
        XCTAssertEqual(try fixture.store.load(fixture.credential).drafts["mentor:" + persona], "Old RAM-only draft")
        let after = await fixture.wire.requests; XCTAssertEqual(after.count, before.count)
    }
    func testExplicitOldRAMDraftDiscardWorksWhileDiskIsStillUnavailable() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        try Data("storage obstacle".utf8).write(to: fixture.root)
        fixture.model.setDraft("mentor:" + persona, text: "User has copied this unsaved text")
        fixture.model.disconnect()
        let before = await fixture.wire.requests
        fixture.model.discardRecovery(scope: fixture.credential.scope)
        XCTAssertTrue(fixture.model.unsavedRecovery.isEmpty); XCTAssertFalse(fixture.model.hasOtherRecovery)
        XCTAssertNil(fixture.model.local); XCTAssertNil(fixture.model.credential)
        XCTAssertEqual(try String(contentsOf: fixture.root), "storage obstacle")
        let after = await fixture.wire.requests; XCTAssertEqual(after.count, before.count)
    }
    func testExplicitDiscardRestoresSavedDraftAndPreservesUnresolvedRequestWithoutIO() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }
        let key = "mentor:" + persona
        var state = MobileLocalState(credential: fixture.credential); state.drafts[key] = "Previously saved"
        var operation = MobilePendingOperation(kind: "mentor_message", target: key, path: ["mentors", persona, "messages"], body: ["text": .string("Uncertain original")])
        operation.state = "unknown"; state.operations = [operation]
        try fixture.store.save(state, credential: fixture.credential); await fixture.model.start()
        fixture.model.setDraft(key, text: String(repeating: "x", count: 8_388_609))
        XCTAssertNotNil(fixture.model.recoveryError)
        fixture.model.discardRecovery(scope: fixture.credential.scope)
        XCTAssertEqual(fixture.model.draft(key), "Previously saved")
        XCTAssertEqual(fixture.model.operation(key)?.body, operation.body)
        XCTAssertEqual(fixture.model.operation(key)?.id, operation.id)
        XCTAssertTrue(fixture.model.unsavedRecovery.isEmpty)
        XCTAssertEqual(try fixture.store.load(fixture.credential).drafts[key], "Previously saved")
        let requests = await fixture.wire.requests; XCTAssertFalse(requests.contains { $0.httpMethod == "POST" })
    }
    func testBulkFailedInputDiscardFreesCapacityWithoutDroppingDraftOrPendingBody() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }
        let key = "mentor:" + persona
        var state = MobileLocalState(credential: fixture.credential)
        for _ in 0..<120 {
            var failed = MobilePendingOperation(kind: "mentor_message", target: key, path: ["mentors", persona, "messages"], body: ["text": .string(String(repeating: "a", count: 60_000))])
            failed.state = "failed"; state.operations.append(failed)
        }
        let first = try XCTUnwrap(state.operations.first)
        let pending = MobilePendingOperation(kind: "mentor_message", target: key, path: ["mentors", persona, "messages"], body: ["text": .string("Still pending")])
        state.operations.append(pending)
        try fixture.store.save(state, credential: fixture.credential); await fixture.model.start()
        let draft = String(repeating: "b", count: 2_000_000)
        fixture.model.setDraft(key, text: draft)
        XCTAssertNotNil(fixture.model.recoveryError)
        XCTAssertFalse(fixture.model.discardFailedInputs(scope: fixture.credential.scope, operationID: first.id))
        XCTAssertEqual(fixture.model.local?.operations.filter(\.hasFailedInput).count, 120)
        XCTAssertTrue(fixture.model.discardFailedInputs(scope: fixture.credential.scope))
        let saved = try fixture.store.load(fixture.credential)
        XCTAssertFalse(saved.operations.contains(where: \.hasFailedInput))
        XCTAssertEqual(saved.drafts[key], draft); XCTAssertEqual(saved.operations.last?.body, pending.body)
        XCTAssertEqual(saved.operations.last?.id, pending.id); XCTAssertNil(fixture.model.recoveryError)
    }
    func testPagingConflictRefreshCannotOverwriteNewAccountsErrorState() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        let path = "/mobile/v1/mentors/" + persona + "/conversation"
        await fixture.wire.queue(path, .init(json: try mobileHistory(["new"], cursor: "older")))
        await fixture.model.refreshConversation(persona)
        await fixture.wire.queue(path, .init(status: 409, json: "{\"detail\":\"conversation_page_changed\"}"))
        await fixture.wire.hold(path, unpagedOnly: true)
        let task = Task { await fixture.model.loadOlderMessages(persona) }; try await waitForHold(fixture.wire)
        fixture.model.disconnect()
        await fixture.wire.queue("/mobile/v1/pair", .init(json: "{\"api_version\":1,\"device_id\":\"device-one\",\"device_token\":\"new-synthetic\"}"))
        await fixture.model.pair(endpoint: "https://other.example", code: "synthetic-once", deviceName: "Synthetic")
        await fixture.wire.release(.init(json: try mobileHistory(["old-account"]))); await task.value
        XCTAssertNil(fixture.model.error); XCTAssertEqual(fixture.model.endpointLabel, "https://other.example")
        XCTAssertTrue(fixture.model.conversations.isEmpty); XCTAssertEqual(fixture.model.connectionStatus, "服务已连接")
    }
    func testLostSendPersistsOriginalIDAndRestartOnlyQueriesThatOperation() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start(); await fixture.model.refreshConversation(persona)
        fixture.model.setDraft("mentor:" + persona, text: "中文原稿 e\u{301}")
        await fixture.wire.queue(messagePath, .init(json: "", timeout: true))
        await fixture.model.send(persona)
        let operation = try XCTUnwrap(fixture.model.operation("mentor:" + persona))
        XCTAssertEqual(operation.state, "unknown"); XCTAssertEqual(fixture.model.draft("mentor:" + persona), "中文原稿 e\u{301}")
        let restarted = MobileClientModel(credentials: fixture.credentials, persistence: fixture.store, transport: fixture.wire)
        await restarted.start(); await restarted.send(persona)
        XCTAssertEqual(restarted.operation("mentor:" + persona)?.id, operation.id)
        await fixture.wire.queue("/mobile/v1/operations/" + operation.id, .init(json: receipt("mentor_message")))
        await restarted.reconcile(operation)
        XCTAssertEqual(restarted.draft("mentor:" + persona), "")
        let requests = await fixture.wire.requests
        XCTAssertEqual(requests.filter { $0.httpMethod == "POST" }.count, 1)
        XCTAssertEqual(requests.filter { $0.url?.path.hasPrefix("/mobile/v1/operations/") == true }.last?.url?.lastPathComponent, operation.id)
    }
    func testMissingOperationAllowsOnlyExplicitReplayOfExactOriginalIDAndBody() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start(); await fixture.model.refreshConversation(persona)
        fixture.model.setDraft("mentor:" + persona, text: "Preserved")
        await fixture.wire.queue(messagePath, .init(json: "", timeout: true)); await fixture.model.send(persona)
        let operation = try XCTUnwrap(fixture.model.operation("mentor:" + persona))
        await fixture.model.reconcile(operation); await fixture.model.send(persona)
        XCTAssertEqual(fixture.model.operation("mentor:" + persona)?.state, "not_recorded")
        XCTAssertEqual(fixture.model.draft("mentor:" + persona), "Preserved")
        let requests = await fixture.wire.requests; XCTAssertEqual(requests.filter { $0.httpMethod == "POST" }.count, 1)
        await fixture.wire.queue(messagePath, .init(json: receipt("mentor_message")))
        await fixture.model.retryRecordedRequest(operation)
        let posts = (await fixture.wire.requests).filter { $0.httpMethod == "POST" }
        XCTAssertEqual(posts.count, 2); XCTAssertEqual(posts[0].httpBody, posts[1].httpBody)
        XCTAssertEqual(fixture.model.operation("mentor:" + persona)?.id, operation.id)
        XCTAssertEqual(fixture.model.draft("mentor:" + persona), "")
    }
    func testAcceptedOldDraftDoesNotClearNewTypingOrAnotherPersona() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start(); await fixture.model.refreshConversation(persona)
        fixture.model.setDraft("mentor:" + persona, text: "Submitted")
        fixture.model.setDraft("mentor:blunt_coach", text: "Other persona")
        await fixture.wire.hold(messagePath)
        let task = Task { await fixture.model.send(persona) }; try await waitForHold(fixture.wire)
        let durable = try fixture.store.load(fixture.credential)
        XCTAssertEqual(durable.operations.last?.body["text"], .string("Submitted"))
        fixture.model.setDraft("mentor:" + persona, text: "New typing")
        await fixture.wire.release(.init(json: receipt("mentor_message"))); await task.value
        XCTAssertEqual(fixture.model.draft("mentor:" + persona), "New typing")
        XCTAssertEqual(fixture.model.draft("mentor:blunt_coach"), "Other persona")
        XCTAssertEqual(fixture.model.operation("mentor:" + persona)?.state, "completed")
    }
    func testConnectionChangeRejectsLateAccountDataAndKeepsIsolatedDraftArchive() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        fixture.model.setDraft("mentor:" + persona, text: "Old endpoint draft")
        await fixture.wire.hold("/mobile/v1/memories/m1")
        let task = Task { await fixture.model.refreshMemory("m1") }; try await waitForHold(fixture.wire)
        fixture.model.disconnect()
        await fixture.wire.queue("/mobile/v1/pair", .init(json: "{\"api_version\":1,\"device_id\":\"device-one\",\"device_token\":\"new-synthetic\"}"))
        await fixture.model.pair(endpoint: "https://other.example", code: "synthetic-once", deviceName: "Synthetic")
        await fixture.wire.release(.init(json: mobileMemory())); await task.value
        XCTAssertNil(fixture.model.memoryDetails["m1"])
        XCTAssertEqual(fixture.model.endpointLabel, "https://other.example")
        XCTAssertEqual(fixture.model.draft("mentor:" + persona), "")
        XCTAssertEqual(try fixture.store.load(fixture.credential).drafts["mentor:" + persona], "Old endpoint draft")
        XCTAssertEqual(try fixture.model.archives().states.first?.endpoint, "https://air.example")
    }
    func testRevokedCredentialClearsServerDataButRetainsInputOnDisk() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        fixture.model.setDraft("mentor:" + persona, text: "Retain on revoke")
        await fixture.model.refreshMemory("m1"); XCTAssertNotNil(fixture.model.memoryDetails["m1"])
        await fixture.wire.queue("/mobile/v1/privacy", .init(status: 401, json: "{\"detail\":\"revoked\"}"))
        await fixture.model.refreshPrivacy()
        XCTAssertFalse(fixture.model.connected); XCTAssertNil(fixture.credentials.read())
        XCTAssertTrue(fixture.model.memoryDetails.isEmpty); XCTAssertNil(fixture.model.capabilities)
        XCTAssertEqual(try fixture.store.load(fixture.credential).drafts["mentor:" + persona], "Retain on revoke")
    }
    func testMemoryVersionConflictRefreshesVersionWithoutLosingCorrection() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        await fixture.model.refreshMemory("m1")
        let memory = try XCTUnwrap(fixture.model.memoryDetails["m1"])
        fixture.model.setDraft("memory:m1", text: "本人纠正")
        await fixture.wire.queue("/mobile/v1/memories/m1/actions", .init(status: 409, json: "{\"detail\":\"memory_version_changed\"}"))
        await fixture.wire.queue("/mobile/v1/memories/m1", .init(json: mobileMemory("two")))
        await fixture.model.memoryAction(memory, action: "update")
        XCTAssertEqual(fixture.model.memoryDetails["m1"]?.version, "two")
        XCTAssertEqual(fixture.model.draft("memory:m1"), "本人纠正")
        XCTAssertEqual(fixture.model.error, MobileError.server(409, "memory_version_changed").localizedDescription)
        let requests = await fixture.wire.requests
        let request = try XCTUnwrap(requests.first { $0.httpMethod == "POST" })
        let body = try JSONDecoder().decode([String: MobileJSON].self, from: request.httpBody!)
        XCTAssertEqual(body["expected_version"], .string("one")); XCTAssertEqual(body["content"], .string("本人纠正"))
    }
    func testPolledVersionNeverSilentlyRebasesCorrectionAndRestartRetainsBase() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        await fixture.model.refreshMemory("m1")
        fixture.model.setDraft("memory:m1", text: "My correction")
        await fixture.wire.queue("/mobile/v1/memories/m1", .init(json: mobileMemory("two")))
        await fixture.model.refreshMemory("m1")
        fixture.model.setDraft("memory:m1", text: "My revised correction")
        let newer = try XCTUnwrap(fixture.model.memoryDetails["m1"])
        XCTAssertTrue(fixture.model.correctionNeedsRebase(newer))
        await fixture.model.memoryAction(newer, action: "update")
        let before = await fixture.wire.requests
        XCTAssertFalse(before.contains { $0.httpMethod == "POST" })
        let restored = try fixture.store.load(fixture.credential)
        XCTAssertEqual(restored.memoryDraftVersions?["memory:m1"], "one")
        let restarted = MobileClientModel(credentials: fixture.credentials, persistence: fixture.store, transport: fixture.wire)
        await restarted.start()
        await fixture.wire.queue("/mobile/v1/memories/m1", .init(json: mobileMemory("two")))
        await restarted.refreshMemory("m1")
        restarted.rebaseCorrection(newer)
        XCTAssertEqual(restarted.draft("memory:m1"), "My revised correction")
        XCTAssertEqual(try fixture.store.load(fixture.credential).memoryDraftVersions?["memory:m1"], "two")
        await fixture.wire.queue("/mobile/v1/memories/m1/actions", .init(json: receipt("memory_action")))
        await restarted.memoryAction(newer, action: "update")
        let posts = (await fixture.wire.requests).filter { $0.httpMethod == "POST" }
        let request = try XCTUnwrap(posts.last)
        let body = try JSONDecoder().decode([String: MobileJSON].self, from: request.httpBody!)
        XCTAssertEqual(body["expected_version"], .string("two")); XCTAssertEqual(posts.count, 1)
    }
    func testHTTP200FailedMemoryVersionReceiptPersistsCorrectionAndRequiresComparison() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        await fixture.model.refreshMemory("m1")
        let original = try XCTUnwrap(fixture.model.memoryDetails["m1"])
        fixture.model.setDraft("memory:m1", text: "Correction must survive")
        let failed = receipt("memory_action", state: "failed").replacingOccurrences(of: "\"error\":null", with: "\"error\":\"memory_version_changed\"")
        await fixture.wire.queue("/mobile/v1/memories/m1/actions", .init(json: failed))
        await fixture.wire.queue("/mobile/v1/memories/m1", .init(json: mobileMemory("two")))
        await fixture.model.memoryAction(original, action: "update")
        XCTAssertEqual(fixture.model.operation("memory:m1")?.state, "failed")
        XCTAssertEqual(fixture.model.operation("memory:m1")?.error, "memory_version_changed")
        XCTAssertEqual(fixture.model.memoryDetails["m1"]?.version, "two")
        let saved = try fixture.store.load(fixture.credential)
        XCTAssertEqual(saved.drafts["memory:m1"], "Correction must survive")
        XCTAssertEqual(saved.memoryDraftVersions?["memory:m1"], "one")
        XCTAssertFalse(fixture.model.hasUnresolved("memory:m1"))
        XCTAssertTrue(fixture.model.correctionNeedsRebase(try XCTUnwrap(fixture.model.memoryDetails["m1"])))
    }
    func testUnsavedDraftRecoverySurvivesSuccessfulPollRevokeAndNewAccount() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start(); await fixture.model.refreshConversation(persona)
        try Data("storage obstacle".utf8).write(to: fixture.root)
        fixture.model.setDraft("mentor:" + persona, text: "RAM recovery text")
        XCTAssertNotNil(fixture.model.recoveryError)
        await fixture.model.refreshCapabilities(); await fixture.model.refreshConversation(persona)
        XCTAssertNotNil(fixture.model.recoveryError)
        await fixture.wire.queue("/mobile/v1/privacy", .init(status: 401, json: "{}"))
        await fixture.model.refreshPrivacy()
        XCTAssertNil(fixture.model.local); XCTAssertFalse(fixture.model.connected)
        XCTAssertEqual(fixture.model.unsavedRecovery[fixture.credential.scope]?.drafts["mentor:" + persona], "RAM recovery text")
        fixture.model.disconnect()
        await fixture.wire.queue("/mobile/v1/pair", .init(json: "{\"api_version\":1,\"device_id\":\"device-one\",\"device_token\":\"new-synthetic\"}"))
        await fixture.model.pair(endpoint: "https://other.example", code: "synthetic-once", deviceName: "Synthetic")
        XCTAssertEqual(fixture.model.draft("mentor:" + persona), "")
        XCTAssertNil(fixture.model.recoveryError); XCTAssertTrue(fixture.model.hasOtherRecovery)
        XCTAssertEqual(fixture.model.unsavedRecovery[fixture.credential.scope]?.drafts["mentor:" + persona], "RAM recovery text")
        await fixture.model.send(persona)
        let requests = await fixture.wire.requests
        XCTAssertFalse(requests.contains { $0.url?.path == messagePath })
        try FileManager.default.removeItem(at: fixture.root)
        fixture.model.setDraft("mentor:" + persona, text: "New account draft")
        XCTAssertEqual(fixture.model.unsavedRecovery[fixture.credential.scope]?.drafts["mentor:" + persona], "RAM recovery text")
        XCTAssertEqual(fixture.model.unsavedRecovery.count, 1)
        XCTAssertEqual(fixture.model.draft("mentor:" + persona), "New account draft")
        XCTAssertNil(fixture.model.recoveryError); XCTAssertTrue(fixture.model.hasOtherRecovery)
    }
    func testSuccessfulDraftPersistenceClearsOnlyThatAccountsRecovery() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        try Data("storage obstacle".utf8).write(to: fixture.root)
        fixture.model.setDraft("mentor:" + persona, text: "Failed write")
        try FileManager.default.removeItem(at: fixture.root)
        fixture.model.setDraft("mentor:" + persona, text: "Saved revision")
        XCTAssertNil(fixture.model.recoveryError)
        XCTAssertEqual(try fixture.store.load(fixture.credential).drafts["mentor:" + persona], "Saved revision")
    }
    func testOversizedEmojiDraftIsDefinitelyUnsentAndCanBeShortened() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start(); await fixture.model.refreshConversation(persona)
        let key = "mentor:" + persona, text = String(repeating: "👨‍👩‍👧‍👦", count: 3000)
        fixture.model.setDraft(key, text: text); await fixture.model.send(persona)
        XCTAssertEqual(fixture.model.draft(key), text); XCTAssertNil(fixture.model.operation(key))
        XCTAssertFalse(fixture.model.hasUnresolved(key))
        let before = await fixture.wire.requests
        XCTAssertFalse(before.contains { $0.httpMethod == "POST" })
        fixture.model.setDraft(key, text: "Shortened message")
        await fixture.wire.queue(messagePath, .init(json: receipt("mentor_message")))
        await fixture.model.send(persona)
        XCTAssertEqual(fixture.model.operation(key)?.state, "completed")
        let after = await fixture.wire.requests
        XCTAssertEqual(after.filter { $0.httpMethod == "POST" }.count, 1)
    }
    func testLegacyOversizedNotRecordedOperationBecomesUnsentFailureAndAllowsNewInput() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }
        var state = MobileLocalState(credential: fixture.credential)
        var old = MobilePendingOperation(kind: "mentor_message", target: "mentor:" + persona, path: ["mentors", persona, "messages"], body: ["text": .string(String(repeating: "a", count: 70_000))])
        old.state = "not_recorded"; state.operations = [old]; state.drafts[old.target] = "Short replacement"
        try fixture.store.save(state, credential: fixture.credential); await fixture.model.start(); await fixture.model.refreshConversation(persona)
        await fixture.model.retryRecordedRequest(old)
        XCTAssertEqual(fixture.model.operation(old.target)?.state, "failed")
        XCTAssertEqual(fixture.model.operation(old.target)?.error, "invalid_input_unsent")
        XCTAssertFalse(fixture.model.hasUnresolved(old.target))
        await fixture.wire.queue(messagePath, .init(json: receipt("mentor_message")))
        await fixture.model.send(persona)
        let posts = (await fixture.wire.requests).filter { $0.httpMethod == "POST" }
        XCTAssertEqual(posts.count, 1)
        XCTAssertNotEqual(fixture.model.operation(old.target)?.id, old.id)
    }
    func testOlderHistoryPrependsWithoutReplacingCurrentStatusOrDraftAndSurvivesUnchangedPoll() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        let path = "/mobile/v1/mentors/" + persona + "/conversation"
        fixture.model.setDraft("mentor:" + persona, text: "Unsent while paging")
        await fixture.wire.queue(path, .init(json: try mobileHistory(["new"], cursor: "older-one")))
        await fixture.model.refreshConversation(persona)
        await fixture.wire.queue(path, .init(json: try mobileHistory(["old"])))
        await fixture.model.loadOlderMessages(persona)
        XCTAssertEqual(fixture.model.messages(persona).map(\.id), ["old", "new"])
        XCTAssertEqual(fixture.model.conversations[persona]?.revision, 2)
        XCTAssertNil(fixture.model.historyCursor(persona))
        await fixture.wire.queue(path, .init(json: try mobileHistory(["new"], cursor: "older-one")))
        await fixture.model.refreshConversation(persona)
        XCTAssertEqual(fixture.model.messages(persona).map(\.id), ["old", "new"])
        XCTAssertEqual(fixture.model.draft("mentor:" + persona), "Unsent while paging")
        let requests = await fixture.wire.requests
        XCTAssertEqual(requests.first { $0.url?.query != nil }?.url?.query, "cursor=older-one")
    }
    func testChangedHistoryClearsStalePagesAndKeepsDraftOnPagingConflict() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        let path = "/mobile/v1/mentors/" + persona + "/conversation"
        fixture.model.setDraft("mentor:" + persona, text: "Keep my input")
        await fixture.wire.queue(path, .init(json: try mobileHistory(["new"], cursor: "older-one")))
        await fixture.model.refreshConversation(persona)
        await fixture.wire.queue(path, .init(json: try mobileHistory(["old"], cursor: "older-two")))
        await fixture.model.loadOlderMessages(persona)
        await fixture.wire.queue(path, .init(status: 409, json: "{\"detail\":\"conversation_page_changed\"}"))
        await fixture.wire.queue(path, .init(json: try mobileHistory(["revalidated"], version: "history-two")))
        await fixture.model.loadOlderMessages(persona)
        XCTAssertEqual(fixture.model.messages(persona).map(\.id), ["revalidated"])
        XCTAssertTrue(fixture.model.olderMessages[persona]?.isEmpty ?? true)
        XCTAssertEqual(fixture.model.draft("mentor:" + persona), "Keep my input")
        XCTAssertNotNil(fixture.model.error)
    }
    func testLateHistoryPageCannotRestoreRevokedMessagesAfterNewerPoll() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        let path = "/mobile/v1/mentors/" + persona + "/conversation"
        await fixture.wire.queue(path, .init(json: try mobileHistory(["new"], cursor: "older-one")))
        await fixture.model.refreshConversation(persona)
        await fixture.wire.hold(path)
        let task = Task { await fixture.model.loadOlderMessages(persona) }; try await waitForHold(fixture.wire)
        await fixture.wire.queue(path, .init(json: try mobileHistory(["revalidated"], version: "history-two")))
        await fixture.model.refreshConversation(persona)
        await fixture.wire.release(.init(json: try mobileHistory(["stale"]))); await task.value
        XCTAssertEqual(fixture.model.messages(persona).map(\.id), ["revalidated"])
        XCTAssertTrue(fixture.model.olderMessages.isEmpty)
    }
    func testReceiptSaveFailureRetainsUnresolvedExactBodyAndRecoveryText() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start(); await fixture.model.refreshConversation(persona)
        let key = "mentor:" + persona
        fixture.model.setDraft(key, text: "Receipt recovery text")
        await fixture.wire.hold(messagePath)
        let task = Task { await fixture.model.send(persona) }; try await waitForHold(fixture.wire)
        let original = try XCTUnwrap(fixture.model.operation(key))
        try FileManager.default.removeItem(at: fixture.root)
        try Data("storage obstacle".utf8).write(to: fixture.root)
        await fixture.wire.release(.init(json: receipt("mentor_message"))); await task.value
        XCTAssertEqual(fixture.model.operation(key)?.state, "unknown")
        XCTAssertEqual(fixture.model.operation(key)?.id, original.id)
        XCTAssertEqual(fixture.model.operation(key)?.body, original.body)
        XCTAssertEqual(fixture.model.unsavedRecovery[fixture.credential.scope]?.drafts[key], "Receipt recovery text")
        XCTAssertNotNil(fixture.model.recoveryError)
    }
    func testReceiptStorageWarningKeepsItsOwnerAcrossRefreshUntilLocalSettlement() async throws {
        for (settlement, duringRefresh) in [("retry", false), ("discard", false), ("retry", true), ("discard", true)] {
            let fixture = MobileStateFixture(); defer { fixture.cleanup() }
            await fixture.model.start(); await fixture.model.refreshConversation(persona)
            let key = "mentor:" + persona
            fixture.model.setDraft(key, text: "Receipt recovery")
            await fixture.wire.hold(messagePath)
            let task = Task { await fixture.model.send(persona) }; try await waitForHold(fixture.wire)
            let original = try XCTUnwrap(fixture.model.operation(key))
            try FileManager.default.removeItem(at: fixture.root)
            try Data("storage obstacle".utf8).write(to: fixture.root)
            await fixture.wire.release(.init(json: receipt("mentor_message")), holdNext: duringRefresh ? "/mobile/v1/mentors/" + persona + "/conversation" : nil)
            if duringRefresh { try await waitForHold(fixture.wire) } else { await task.value }
            XCTAssertEqual(fixture.model.error, MobileError.storage.localizedDescription)
            if settlement == "retry" {
                try FileManager.default.removeItem(at: fixture.root)
                XCTAssertTrue(fixture.model.retryRecoverySave(scope: fixture.credential.scope))
            } else { fixture.model.discardRecovery(scope: fixture.credential.scope) }
            if duringRefresh { await fixture.wire.release(.init(json: mobileConversation)); await task.value }
            XCTAssertNotNil(fixture.model.conversations[persona])
            XCTAssertNil(fixture.model.error)
            XCTAssertEqual(fixture.model.operation(key)?.id, original.id)
            XCTAssertEqual(fixture.model.operation(key)?.body, original.body)
            XCTAssertTrue(fixture.model.operation(key)?.unresolved == true)
        }
    }
    func testStopInFlightAndUnknownBlockSendingUntilReceiptAndFreshConversation() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        let path = "/mobile/v1/mentors/" + persona + "/conversation"
        let control = "/mobile/v1/mentors/" + persona + "/control"
        let refused = "{\"conversation_id\":\"one\",\"status\":\"interrupted\",\"revision\":4,\"messages\":[],\"notice\":\"model_refused\"}"
        let stopped = refused.replacingOccurrences(of: "interrupted", with: "stopped")
        await fixture.wire.queue(path, .init(json: refused)); await fixture.model.refreshConversation(persona)
        fixture.model.setDraft("mentor:" + persona, text: "Adjusted question")
        XCTAssertFalse(fixture.model.canSend(persona)); await fixture.model.send(persona)
        await fixture.wire.hold(control)
        let task = Task { await fixture.model.control(persona, action: "stop", revision: 4) }; try await waitForHold(fixture.wire)
        XCTAssertNil(fixture.model.conversations[persona]); XCTAssertFalse(fixture.model.canSend(persona))
        await fixture.wire.queue(path, .init(json: stopped)); await fixture.model.refreshConversation(persona)
        XCTAssertFalse(fixture.model.canSend(persona)); await fixture.model.send(persona)
        await fixture.wire.release(.init(json: receipt("mentor_control", state: "unknown"))); await task.value
        let operation = try XCTUnwrap(fixture.model.operation("stop:" + persona))
        XCTAssertFalse(fixture.model.canSend(persona)); await fixture.model.send(persona)
        await fixture.wire.queue("/mobile/v1/operations/" + operation.id, .init(json: receipt("mentor_control")))
        await fixture.wire.queue(path, .init(json: "", timeout: true))
        await fixture.model.reconcile(operation)
        XCTAssertFalse(fixture.model.hasUnresolved("stop:" + persona))
        XCTAssertNil(fixture.model.conversations[persona]); XCTAssertFalse(fixture.model.canSend(persona))
        await fixture.model.send(persona)
        let before = (await fixture.wire.requests).filter { $0.httpMethod == "POST" }; XCTAssertEqual(before.count, 1)
        XCTAssertEqual(before.first?.url?.path, control)
        await fixture.wire.queue(path, .init(json: stopped)); await fixture.model.refreshConversation(persona)
        XCTAssertTrue(fixture.model.canSend(persona))
        await fixture.wire.queue(messagePath, .init(json: receipt("mentor_message"))); await fixture.model.send(persona)
        XCTAssertEqual(fixture.model.operation("mentor:" + persona)?.state, "completed")
    }
    func testSendRequiresReadButAllowsValidatedEmptyConversation() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        fixture.model.setDraft("mentor:" + persona, text: "First question")
        XCTAssertFalse(fixture.model.canSend(persona)); await fixture.model.send(persona)
        let before = await fixture.wire.requests; XCTAssertFalse(before.contains { $0.httpMethod == "POST" })
        await fixture.model.refreshConversation(persona)
        XCTAssertEqual(fixture.model.conversations[persona]?.status, "empty"); XCTAssertTrue(fixture.model.canSend(persona))
        await fixture.wire.queue(messagePath, .init(json: receipt("mentor_message"))); await fixture.model.send(persona)
        XCTAssertEqual(fixture.model.operation("mentor:" + persona)?.state, "completed")
    }
    func testResumeInFlightAndUnknownBlockSendingAfterStoppedConversationRefresh() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        let path = "/mobile/v1/mentors/" + persona + "/conversation", control = "/mobile/v1/mentors/" + persona + "/control"
        let stopped = "{\"conversation_id\":\"one\",\"status\":\"stopped\",\"revision\":4,\"messages\":[],\"notice\":null}"
        await fixture.wire.queue(path, .init(json: stopped)); await fixture.model.refreshConversation(persona)
        let key = "mentor:" + persona; fixture.model.setDraft(key, text: "Keep while resuming")
        XCTAssertTrue(fixture.model.canSend(persona))
        await fixture.wire.hold(control)
        let task = Task { await fixture.model.control(persona, action: "resume", revision: 4) }; try await waitForHold(fixture.wire)
        await fixture.wire.queue(path, .init(json: stopped)); await fixture.model.refreshConversation(persona)
        XCTAssertTrue(fixture.model.busy.contains("control:" + persona)); XCTAssertFalse(fixture.model.canSend(persona))
        await fixture.model.send(persona)
        await fixture.wire.queue(path, .init(json: stopped))
        await fixture.wire.release(.init(json: receipt("mentor_control", state: "unknown"))); await task.value
        XCTAssertEqual(fixture.model.operation("control:" + persona)?.state, "unknown")
        XCTAssertEqual(fixture.model.conversations[persona]?.status, "stopped")
        XCTAssertFalse(fixture.model.busy.contains("control:" + persona)); XCTAssertFalse(fixture.model.canSend(persona))
        await fixture.model.send(persona)
        XCTAssertNil(fixture.model.operation(key)); XCTAssertEqual(fixture.model.draft(key), "Keep while resuming")
        let posts = (await fixture.wire.requests).filter { $0.httpMethod == "POST" }
        XCTAssertEqual(posts.count, 1); XCTAssertEqual(posts.first?.url?.path, control)
    }
    func testEachSourceChangeNoticeAllowsExactDraftThroughCompletedSend() async throws {
        for notice in ["source_revoked", "source_changed_or_revoked", "source_changed"] {
            let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
            let conversation = "{\"conversation_id\":\"one\",\"status\":\"interrupted\",\"revision\":4,\"messages\":[],\"notice\":\"\(notice)\"}"
            await fixture.wire.queue("/mobile/v1/mentors/" + persona + "/conversation", .init(json: conversation))
            await fixture.model.refreshConversation(persona)
            let key = "mentor:" + persona, draft = "按当前来源继续：\(notice)\n保留中文 e\u{301} 🧠"
            fixture.model.setDraft(key, text: draft)
            XCTAssertTrue(fixture.model.canSend(persona), notice)
            await fixture.wire.queue(messagePath, .init(json: receipt("mentor_message"))); await fixture.model.send(persona)
            XCTAssertEqual(fixture.model.operation(key)?.state, "completed", notice); XCTAssertEqual(fixture.model.draft(key), "")
            let posts = (await fixture.wire.requests).filter { $0.httpMethod == "POST" }
            XCTAssertEqual(posts.count, 1); XCTAssertEqual(posts.first?.url?.path, messagePath)
            let body = try JSONDecoder().decode([String: MobileJSON].self, from: XCTUnwrap(posts.first?.httpBody))
            XCTAssertEqual(body["text"], .string(draft)); XCTAssertEqual(body["operation_id"]?.string, fixture.model.operation(key)?.id)
        }
    }
    func testPrivacyReadNeverGrantsAndExplicitGrantCarriesExactFourChoicesAndBinding() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start(); await fixture.model.refreshPrivacy()
        let before = await fixture.wire.requests; XCTAssertFalse(before.contains { $0.httpMethod == "POST" })
        let privacy = try XCTUnwrap(fixture.model.privacy)
        var selected = MobilePermissions(); selected.incremental = true
        await fixture.wire.queue("/mobile/v1/privacy/actions", .init(json: receipt("privacy_action")))
        await fixture.model.privacyAction("grant", value: privacy, permissions: selected)
        let requests = await fixture.wire.requests
        let request = try XCTUnwrap(requests.first { $0.httpMethod == "POST" })
        let body = try JSONDecoder().decode([String: MobileJSON].self, from: request.httpBody!)
        XCTAssertEqual(body["binding"], .string("binding-one")); XCTAssertEqual(body["permissions"], selected.json)
        XCTAssertEqual(body["acknowledged"], .bool(true))
        XCTAssertEqual(requests.filter { $0.httpMethod == "POST" }.count, 1)
    }
    func testFailedDurableWritePreventsNetworkSideEffect() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start(); await fixture.model.refreshConversation(persona)
        try Data("storage obstacle".utf8).write(to: fixture.root)
        fixture.model.setDraft("mentor:" + persona, text: "Do not lose")
        await fixture.model.send(persona)
        let requests = await fixture.wire.requests; XCTAssertFalse(requests.contains { $0.httpMethod == "POST" })
        XCTAssertEqual(fixture.model.draft("mentor:" + persona), "Do not lose")
        XCTAssertNotNil(fixture.model.error)
    }
    func testBackgroundDiscardsDisplayedAnswersAndRejectsLateRead() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        await fixture.model.refreshConversation(persona); XCTAssertNotNil(fixture.model.conversations[persona])
        await fixture.wire.hold("/mobile/v1/memories/m1")
        let task = Task { await fixture.model.refreshMemory("m1") }; try await waitForHold(fixture.wire)
        fixture.model.suspend(); await fixture.wire.release(.init(json: mobileMemory())); await task.value
        XCTAssertTrue(fixture.model.conversations.isEmpty); XCTAssertTrue(fixture.model.memoryDetails.isEmpty)
        XCTAssertTrue(fixture.model.connected)
    }
    func testServerUnknownReceiptNeverOffersReplay() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start(); await fixture.model.refreshConversation(persona)
        fixture.model.setDraft("mentor:" + persona, text: "Uncertain original")
        await fixture.wire.queue(messagePath, .init(json: receipt("mentor_message", state: "unknown")))
        await fixture.model.send(persona)
        let operation = try XCTUnwrap(fixture.model.operation("mentor:" + persona))
        await fixture.model.retryRecordedRequest(operation)
        let posts = (await fixture.wire.requests).filter { $0.httpMethod == "POST" }
        XCTAssertEqual(posts.count, 1); XCTAssertEqual(operation.state, "unknown")
        XCTAssertEqual(fixture.model.draft("mentor:" + persona), "Uncertain original")
    }
    func testUnknownGrantCannotBlockIndependentExplicitRevoke() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start(); await fixture.model.refreshPrivacy()
        let privacy = try XCTUnwrap(fixture.model.privacy)
        await fixture.wire.queue("/mobile/v1/privacy/actions", .init(json: receipt("privacy_action", state: "unknown")))
        await fixture.model.privacyAction("grant", value: privacy, permissions: MobilePermissions())
        let original = try XCTUnwrap(fixture.model.operation("privacy"))
        await fixture.wire.queue("/mobile/v1/privacy/actions", .init(json: receipt("privacy_action")))
        await fixture.model.privacyAction("revoke", value: privacy)
        XCTAssertEqual(fixture.model.operation("privacy")?.id, original.id)
        XCTAssertEqual(fixture.model.operation("privacy")?.state, "unknown")
        XCTAssertEqual(fixture.model.operation("privacy-revoke")?.state, "completed")
        let posts = (await fixture.wire.requests).filter { $0.httpMethod == "POST" }
        XCTAssertEqual(posts.count, 2)
        XCTAssertNotEqual(posts[0].httpBody, posts[1].httpBody)
    }
    func testUnknownResumeCannotBlockIndependentExplicitStop() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        let path = "/mobile/v1/mentors/" + persona + "/control"
        await fixture.wire.queue(path, .init(json: receipt("mentor_control", state: "unknown")))
        await fixture.model.control(persona, action: "resume", revision: 4)
        let original = try XCTUnwrap(fixture.model.operation("control:" + persona))
        await fixture.wire.queue(path, .init(json: receipt("mentor_control")))
        await fixture.model.control(persona, action: "stop", revision: 4)
        XCTAssertEqual(fixture.model.operation("control:" + persona)?.id, original.id)
        XCTAssertEqual(fixture.model.operation("control:" + persona)?.state, "unknown")
        XCTAssertEqual(fixture.model.operation("stop:" + persona)?.state, "completed")
        let posts = (await fixture.wire.requests).filter { $0.httpMethod == "POST" }; XCTAssertEqual(posts.count, 2)
    }
    func testMissingSourceScopeCannotGrantPurposes() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        let data = try JSONSerialization.jsonObject(with: Data(mobilePrivacy.utf8)) as! [String: Any]
        var legacy = data; legacy.removeValue(forKey: "source_scope")
        let json = String(decoding: try JSONSerialization.data(withJSONObject: legacy), as: UTF8.self)
        await fixture.wire.queue("/mobile/v1/privacy", .init(json: json)); await fixture.model.refreshPrivacy()
        let privacy = try XCTUnwrap(fixture.model.privacy); XCTAssertNil(privacy.sourceScope); XCTAssertFalse(privacy.canGrant)
        await fixture.model.privacyAction("grant", value: privacy, permissions: MobilePermissions())
        let requests = await fixture.wire.requests; XCTAssertFalse(requests.contains { $0.httpMethod == "POST" })
    }

    func testPrivacyMutationRejectsReadStartedBeforeRevocationReceipt() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start(); await fixture.model.refreshPrivacy()
        let privacy = try XCTUnwrap(fixture.model.privacy)
        await fixture.wire.hold("/mobile/v1/memories/m1")
        let task = Task { await fixture.model.refreshMemory("m1") }; try await waitForHold(fixture.wire)
        await fixture.wire.queue("/mobile/v1/privacy/actions", .init(json: receipt("privacy_action")))
        await fixture.model.privacyAction("revoke", value: privacy)
        await fixture.wire.release(.init(json: mobileMemory())); await task.value
        XCTAssertNil(fixture.model.memoryDetails["m1"])
        XCTAssertEqual(fixture.model.operation("privacy-revoke")?.state, "completed")
    }

}

@MainActor
final class MobileConnectionPresentationTests: XCTestCase {
    private func waitForHold(_ wire: MobileStateWire) async throws {
        for _ in 0..<100 {
            if await wire.isHeld { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("Synthetic connection check did not reach its barrier")
        throw MobileError.transport
    }
    func testCredentialReadDoesNotClaimOnlineBeforeValidatedCapabilities() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }
        await fixture.wire.hold("/mobile/v1/capabilities")
        let task = Task { await fixture.model.start() }; try await waitForHold(fixture.wire)
        XCTAssertTrue(fixture.model.connected)
        XCTAssertEqual(fixture.model.connectionState, .connecting)
        XCTAssertFalse(fixture.model.serviceConnected)
        XCTAssertNil(fixture.model.capabilities)
        await fixture.wire.release(.init(json: mobileCapabilities)); await task.value
        XCTAssertEqual(fixture.model.connectionState, .connected)
        XCTAssertTrue(fixture.model.serviceConnected)
        let requests = await fixture.wire.requests
        XCTAssertEqual(requests.count, 1); XCTAssertEqual(requests.first?.httpMethod, "GET")
    }
    func testOfflineRetryRetainsAuthorizationDraftAndOriginalOperationWithoutReplay() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }
        let operation = MobilePendingOperation(kind: "mentor_message", target: "mentor:future_self",
            path: ["mentors", "future_self", "messages"], body: ["text": .string("Synthetic original")])
        var state = MobileLocalState(credential: fixture.credential)
        state.drafts["mentor:future_self"] = "Synthetic newer draft"; state.operations = [operation]
        try fixture.store.save(state)
        await fixture.wire.queue("/mobile/v1/capabilities", .init(json: "", timeout: true))
        await fixture.model.start()
        XCTAssertEqual(fixture.model.connectionState, .offline)
        XCTAssertTrue(fixture.model.connected); XCTAssertFalse(fixture.model.serviceConnected)
        XCTAssertEqual(fixture.credentials.read()?.token, fixture.credential.token)
        XCTAssertEqual(fixture.model.local?.operations.first?.id, operation.id)
        XCTAssertEqual(fixture.model.draft("mentor:future_self"), "Synthetic newer draft")
        let identity = fixture.model.accountIdentity
        await fixture.model.retryConnection()
        XCTAssertEqual(fixture.model.connectionState, .connected)
        XCTAssertEqual(fixture.model.accountIdentity, identity)
        XCTAssertEqual(fixture.model.local?.operations.first?.body, operation.body)
        let requests = await fixture.wire.requests
        XCTAssertEqual(requests.count, 2)
        XCTAssertTrue(requests.allSatisfy { $0.httpMethod == "GET" && $0.url?.path == "/mobile/v1/capabilities" })
    }
    func testInboxFailureAndRecoveryUpdateReachabilityWithoutInventingUnsupportedCapabilities() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        await fixture.wire.queue("/mobile/v1/capabilities", .init(json: "", timeout: true))
        await fixture.model.refreshChatInbox()
        XCTAssertEqual(fixture.model.connectionState, .offline)
        XCTAssertNotNil(fixture.model.capabilities)
        XCTAssertFalse(fixture.model.serviceConnected)
        await fixture.model.refreshChatInbox()
        XCTAssertEqual(fixture.model.connectionState, .connected)
        XCTAssertTrue(fixture.model.serviceConnected)
        let requests = await fixture.wire.requests
        XCTAssertTrue(requests.allSatisfy { $0.httpMethod == "GET" })
    }
    func testInboxCapabilityHTTPFailuresPreserveAuthorizationAndKnownCapabilitiesWithoutReplay() async throws {
        for status in [403, 404, 429] {
            let fixture = MobileStateFixture(); defer { fixture.cleanup() }
            let operation = MobilePendingOperation(kind: "mentor_message", target: "mentor:future_self",
                path: ["mentors", "future_self", "messages"], body: ["text": .string("Synthetic original")])
            var state = MobileLocalState(credential: fixture.credential)
            state.drafts[operation.target] = "Synthetic newer draft"; state.operations = [operation]
            try fixture.store.save(state)
            await fixture.model.start()
            let capabilities = try XCTUnwrap(fixture.model.capabilities)
            let identity = fixture.model.accountIdentity
            XCTAssertTrue(fixture.model.serviceConnected)
            await fixture.wire.queue("/mobile/v1/capabilities", .init(status: status, json: "{\"detail\":\"unavailable\"}"))
            await fixture.model.refreshChatInbox()
            XCTAssertEqual(fixture.model.connectionState, .unavailable, "HTTP \(status)")
            XCTAssertFalse(fixture.model.serviceConnected); XCTAssertTrue(fixture.model.connected)
            XCTAssertEqual(fixture.credentials.read()?.token, fixture.credential.token)
            XCTAssertEqual(fixture.model.accountIdentity, identity)
            XCTAssertEqual(fixture.model.capabilities?.device.id, capabilities.device.id)
            XCTAssertEqual(fixture.model.capabilities?.capabilities, capabilities.capabilities)
            XCTAssertEqual(fixture.model.capabilities?.mentors.map(\.id), capabilities.mentors.map(\.id))
            XCTAssertEqual(fixture.model.draft(operation.target), state.drafts[operation.target])
            XCTAssertEqual(fixture.model.local?.operations.first?.id, operation.id)
            XCTAssertEqual(fixture.model.local?.operations.first?.body, operation.body)
            await fixture.model.refreshChatInbox()
            XCTAssertTrue(fixture.model.serviceConnected)
            XCTAssertEqual(fixture.model.accountIdentity, identity)
            let requests = await fixture.wire.requests
            XCTAssertEqual(requests.count, 3)
            XCTAssertTrue(requests.allSatisfy { $0.httpMethod == "GET" && $0.url?.path == "/mobile/v1/capabilities" })
        }
    }
    func testUnverifiableResponseAndOldServiceHaveDifferentStateFromUnavailableNetwork() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }
        await fixture.wire.queue("/mobile/v1/capabilities", .init(json: "{}"))
        await fixture.model.start()
        XCTAssertEqual(fixture.model.connectionState, .unavailable)
        XCTAssertNil(fixture.model.capabilities)
        await fixture.model.retryConnection()
        XCTAssertEqual(fixture.model.connectionState, .connected)
        XCTAssertEqual(fixture.model.capabilities?.capabilities["mentors"], true)
        XCTAssertNotEqual(fixture.model.capabilities?.capabilities["group_chat"], true)
        XCTAssertFalse(fixture.model.canCreateGroup)
    }
    func testInboxRejectsMismatchedDeviceWithoutAdvertisingItsCapabilities() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        let invalid = mobileCapabilities.replacingOccurrences(of: "device-one", with: "another-device")
        await fixture.wire.queue("/mobile/v1/capabilities", .init(json: invalid))
        await fixture.model.refreshChatInbox()
        XCTAssertEqual(fixture.model.connectionState, .unavailable)
        XCTAssertFalse(fixture.model.serviceConnected)
        XCTAssertEqual(fixture.model.capabilities?.device.id, fixture.credential.deviceId)
        let requests = await fixture.wire.requests
        XCTAssertTrue(requests.allSatisfy { $0.httpMethod == "GET" })
    }
    func testCapabilityHTTPFailuresEndConnectionAttemptAndAllowExplicitRetry() async throws {
        for status in [403, 404, 429] {
            let fixture = MobileStateFixture(); defer { fixture.cleanup() }
            await fixture.wire.queue("/mobile/v1/capabilities", .init(status: status, json: "{\"detail\":\"unavailable\"}"))
            await fixture.model.start()
            XCTAssertEqual(fixture.model.connectionState, .unavailable, "HTTP \(status)")
            XCTAssertFalse(fixture.model.serviceConnected)
            XCTAssertFalse(fixture.model.busy.contains("connection"))
            XCTAssertNotNil(fixture.model.error)
            XCTAssertTrue(fixture.model.connected); XCTAssertNil(fixture.model.capabilities)
            await fixture.model.retryConnection()
            XCTAssertEqual(fixture.model.connectionState, .connected)
            XCTAssertFalse(fixture.model.busy.contains("connection"))
            let requests = await fixture.wire.requests
            XCTAssertEqual(requests.count, 2)
            XCTAssertTrue(requests.allSatisfy { $0.httpMethod == "GET" })
        }
    }
    func testRevokedAuthorizationRequiresReconnectAndNeverReplaysRetainedContent() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }
        var state = MobileLocalState(credential: fixture.credential)
        state.drafts["mentor:future_self"] = "Synthetic retained draft"
        let operation = MobilePendingOperation(kind: "mentor_message", target: "mentor:future_self",
            path: ["mentors", "future_self", "messages"], body: ["text": .string("Synthetic original")])
        state.operations = [operation]; try fixture.store.save(state)
        await fixture.wire.queue("/mobile/v1/capabilities", .init(status: 401, json: "{\"detail\":\"revoked\"}"))
        await fixture.model.start()
        XCTAssertEqual(fixture.model.connectionState, .authorizationRequired)
        XCTAssertFalse(fixture.model.connected); XCTAssertFalse(fixture.model.serviceConnected)
        XCTAssertNil(fixture.credentials.read())
        XCTAssertEqual(try fixture.store.load(fixture.credential).drafts, state.drafts)
        XCTAssertEqual(try fixture.store.load(fixture.credential).operations.first?.id, operation.id)
        let requests = await fixture.wire.requests
        XCTAssertEqual(requests.count, 1); XCTAssertEqual(requests.first?.httpMethod, "GET")
    }
    func testRepeatedRetryDuringConnectionCheckDoesNotStartAnotherRequest() async throws {
        let fixture = MobileStateFixture(); defer { fixture.cleanup() }; await fixture.model.start()
        await fixture.wire.hold("/mobile/v1/capabilities")
        let task = Task { await fixture.model.retryConnection() }; try await waitForHold(fixture.wire)
        await fixture.model.retryConnection()
        let requests = await fixture.wire.requests
        XCTAssertEqual(requests.count, 2)
        XCTAssertTrue(fixture.model.busy.contains("connection"))
        await fixture.wire.release(.init(json: mobileCapabilities)); await task.value
        XCTAssertFalse(fixture.model.busy.contains("connection"))
        XCTAssertEqual(fixture.model.connectionState, .connected)
    }
}
