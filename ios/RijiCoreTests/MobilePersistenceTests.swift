import XCTest
import Foundation
import RijiCore

final class MobilePersistenceTests: XCTestCase {
    func testArchiveEnumerationSkipsMismatchedAndInvalidFilenamesWithoutOverwritingBytes() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("MobilePartial-\(UUID())")
        defer { try? FileManager.default.removeItem(at: root) }
        let store = MobileLocalStore(directory: root)
        let credential = MobileCredential(endpoint: "https://air.example", deviceId: "one", token: "example-synthetic")
        var good = MobileLocalState(credential: credential); good.drafts["mentor:future_self"] = "Healthy draft"
        try store.save(good)
        let duplicate = try JSONEncoder().encode(good)
        let other = MobileCredential(endpoint: "https://air.example", deviceId: "two", token: "example-synthetic")
        let badNames = [other.scope + ".json", "not-a-scope.json"]
        for name in badNames { try duplicate.write(to: root.appendingPathComponent(name)) }
        let result = try store.archives()
        XCTAssertEqual(result.states.map(\.scope), [credential.scope])
        XCTAssertEqual(result.states.first?.drafts, good.drafts)
        XCTAssertEqual(result.unreadableCount, 2); XCTAssertTrue(result.warning?.contains("2") == true)
        for name in badNames { XCTAssertEqual(try Data(contentsOf: root.appendingPathComponent(name)), duplicate) }
    }
    func testTextlessRecoveryNamesControlMemoryAndPrivacyActionsSafely() {
        let cases = [("mentor_control", "stop:future_self", "stop", "停止回复"),
                     ("memory_action", "memory:m1", "delete", "删除"),
                     ("privacy_action", "privacy", "grant", "确认用途"),
                     ("privacy_action", "privacy", "scan", "检查新增与修改"),
                     ("privacy_action", "privacy-pause", "pause", "暂停提取"),
                     ("privacy_action", "privacy-revoke", "revoke", "撤销用途")]
        for (kind, target, action, label) in cases {
            let operation = MobilePendingOperation(kind: kind, target: target, path: [], body: ["action": .string(action)])
            XCTAssertNil(operation.inputText)
            XCTAssertTrue(operation.recoveryLabel.contains(label)); XCTAssertTrue(operation.recoveryLabel.contains(operation.targetLabel))
            XCTAssertTrue(operation.recoveryLabel.contains(String(operation.id.prefix(8))))
        }
        let unknown = MobilePendingOperation(kind: "privacy_action", target: "privacy", path: [], body: ["action": .string("private-untrusted-action")])
        XCTAssertFalse(unknown.recoveryLabel.contains("private-untrusted-action"))
    }
    func testScopedReadIgnoresUnrelatedCorruptionButValidatesItsOwnIdentity() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("MobileScoped-\(UUID())")
        defer { try? FileManager.default.removeItem(at: root) }
        let store = MobileLocalStore(directory: root)
        let credential = MobileCredential(endpoint: "https://one.example", deviceId: "one", token: "example-synthetic")
        let other = MobileCredential(endpoint: "https://one.example", deviceId: "two", token: "example-synthetic")
        let original = MobileLocalState(credential: credential)
        try store.save(original)
        let unrelated = root.appendingPathComponent(other.scope + ".json")
        try Data("corrupt".utf8).write(to: unrelated)
        let partial = try store.archives()
        XCTAssertEqual(partial.states.map(\.deviceId), ["one"]); XCTAssertEqual(partial.unreadableCount, 1)
        XCTAssertNotNil(partial.warning)
        XCTAssertEqual(try store.load(scope: credential.scope)?.deviceId, "one")
        XCTAssertThrowsError(try store.load(scope: other.scope))
        for scope in ["../escape", "", String(repeating: "z", count: 64)] { XCTAssertThrowsError(try store.load(scope: scope)) }
        let file = root.appendingPathComponent(credential.scope + ".json")
        let swapped = try JSONEncoder().encode(MobileLocalState(credential: other)); try swapped.write(to: file)
        XCTAssertThrowsError(try store.load(scope: credential.scope)); XCTAssertEqual(try Data(contentsOf: file), swapped)
        for bad in [MobileCredential(endpoint: "http://one.example", deviceId: "one", token: "x"), MobileCredential(endpoint: "https://one.example", deviceId: "../bad", token: "x")] {
            let state = MobileLocalState(credential: bad)
            try JSONEncoder().encode(state).write(to: root.appendingPathComponent(state.scope + ".json"))
            XCTAssertThrowsError(try store.load(scope: state.scope))
        }
    }
    func testArchiveLabelsDistinguishDeviceRecordsAndNameTargetsWithoutSecrets() {
        let one = MobileLocalState(credential: MobileCredential(endpoint: "https://air.example", deviceId: "one", token: "example-private-token"))
        let two = MobileLocalState(credential: MobileCredential(endpoint: "https://air.example", deviceId: "two", token: "example-private-token"))
        XCTAssertNotEqual(one.archiveLabel, two.archiveLabel)
        XCTAssertTrue(one.archiveLabel.contains("https://air.example")); XCTAssertFalse(one.archiveLabel.contains("example-private-token"))
        let mentor = MobilePendingOperation(kind: "mentor_message", target: "mentor:future_self", path: ["mentors", "future_self", "messages"], body: [:])
        let memory = MobilePendingOperation(kind: "memory_action", target: "memory:m1", path: ["memories", "m1", "actions"], body: [:])
        XCTAssertEqual(mentor.targetLabel, "未来的我"); XCTAssertEqual(memory.targetLabel, "记忆 m1")
        XCTAssertTrue(mentor.recoveryLabel.contains(String(mentor.id.prefix(8))))
    }
    func testFailedReceiptLabelStopsClaimingTextRetentionAfterDiscard() {
        var operation = MobilePendingOperation(kind: "memory_action", target: "memory:m1", path: ["memories", "m1", "actions"], body: ["content": .string("Correction")])
        operation.state = "failed"
        XCTAssertTrue(operation.stateLabel.contains("仍保留"))
        operation.body = [:]
        XCTAssertFalse(operation.stateLabel.contains("仍保留")); XCTAssertTrue(operation.stateLabel.contains("没有保留"))
    }
    func testMoreThanFortyFailedInputsRemainUntilExplicitlyDiscarded() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("MobileFailedInput-\(UUID())")
        defer { try? FileManager.default.removeItem(at: root) }
        let credential = MobileCredential(endpoint: "https://one.example", deviceId: "one", token: "example-synthetic")
        let store = MobileLocalStore(directory: root)
        var state = MobileLocalState(credential: credential)
        for index in 0..<85 {
            var operation = MobilePendingOperation(kind: "mentor_message", target: "mentor:future_self", path: ["mentors", "future_self", "messages"], body: ["text": .string("Rejected input \(index)")])
            operation.state = "failed"; state.operations.append(operation)
        }
        let original = state.operations.map(\.body)
        try store.save(state)
        let restored = try store.load(credential)
        XCTAssertEqual(restored.operations.count, 85)
        XCTAssertEqual(restored.operations.map(\.body), original)
        XCTAssertTrue(restored.operations.allSatisfy(\.hasFailedInput))
        XCTAssertFalse(restored.operations.contains(where: \.unresolved))
    }
    func testSettledBodiesAreBoundedWhileEveryUnresolvedRequestAndDraftSurvives() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("MobileCompact-\(UUID())")
        defer { try? FileManager.default.removeItem(at: root) }
        let store = MobileLocalStore(directory: root)
        let credential = MobileCredential(endpoint: "https://one.example", deviceId: "one", token: "example-synthetic")
        var state = MobileLocalState(credential: credential)
        state.drafts["memory:m1"] = "Untouched correction"; state.memoryDraftVersions = ["memory:m1": "version-one"]
        for index in 0..<200 {
            var operation = MobilePendingOperation(kind: "mentor_message", target: "mentor:future_self", path: ["mentors", "future_self", "messages"], body: ["text": .string(String(repeating: "a", count: 60_000))])
            operation.state = index % 40 == 0 ? "unknown" : "completed"
            state.operations.append(operation)
        }
        let unresolved = state.operations.filter(\.unresolved)
        XCTAssertGreaterThan(try JSONEncoder().encode(state).count, 8_388_608)
        try store.save(state, credential: credential)
        let restored = try store.load(credential)
        XCTAssertEqual(restored.operations.filter { !$0.unresolved }.count, 40)
        XCTAssertTrue(restored.operations.filter { !$0.unresolved }.allSatisfy { $0.body.isEmpty })
        XCTAssertEqual(restored.operations.filter(\.unresolved).map(\.id), unresolved.map(\.id))
        XCTAssertEqual(restored.operations.filter(\.unresolved).map(\.body), unresolved.map(\.body))
        XCTAssertEqual(restored.drafts, state.drafts); XCTAssertEqual(restored.memoryDraftVersions, state.memoryDraftVersions)
    }
    func testLegacyDraftWithoutBaseVersionRequiresExplicitRebase() throws {
        let credential = MobileCredential(endpoint: "https://one.example", deviceId: "one", token: "example-synthetic")
        var state = MobileLocalState(credential: credential); state.drafts["memory:m1"] = "Legacy draft"
        var object = try JSONSerialization.jsonObject(with: JSONEncoder().encode(state)) as! [String: Any]
        object.removeValue(forKey: "memoryDraftVersions")
        let restored = try JSONDecoder().decode(MobileLocalState.self, from: JSONSerialization.data(withJSONObject: object))
        XCTAssertEqual(restored.drafts["memory:m1"], "Legacy draft")
        XCTAssertNil(restored.memoryDraftVersions)
    }
    func testEndpointDeviceAndPersonaNamespacesDoNotShareDraftsOrRequests() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("MobileLocal-\(UUID())")
        defer { try? FileManager.default.removeItem(at: root) }
        let store = MobileLocalStore(directory: root)
        let original = MobileCredential(endpoint: "https://one.example", deviceId: "one", token: "example-secret-not-in-json")
        var state = MobileLocalState(credential: original)
        state.drafts["mentor:future_self"] = "中文 e\u{301} 🧠"
        state.drafts["mentor:blunt_coach"] = "Different draft"
        let operation = MobilePendingOperation(kind: "mentor_message", target: "mentor:future_self", path: ["mentors", "future_self", "messages"], body: ["text": .string("中文 e\u{301} 🧠")])
        state.operations.append(operation); try store.save(state, credential: original)
        let restored = try store.load(original)
        XCTAssertEqual(restored.operations.first?.id, operation.id)
        XCTAssertEqual(restored.operations.first?.body, operation.body)
        XCTAssertEqual(Data(restored.drafts["mentor:future_self"]!.utf8), Data("中文 e\u{301} 🧠".utf8))
        for credential in [MobileCredential(endpoint: "https://two.example", deviceId: "one", token: "x"), MobileCredential(endpoint: "https://one.example", deviceId: "two", token: "x")] {
            XCTAssertTrue(try store.load(credential).drafts.isEmpty)
            XCTAssertTrue(try store.load(credential).operations.isEmpty)
        }
        let file = try XCTUnwrap(FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil).first)
        let raw = try String(contentsOf: file)
        XCTAssertFalse(raw.contains("example-secret-not-in-json")); XCTAssertFalse(raw.contains("device_token"))
        XCTAssertEqual(try store.archives().states.count, 1)
        XCTAssertNil(try store.archives().warning)
    }
    func testCorruptRecoveryDataIsNotTreatedAsAnEmptyDraftOrOverwritten() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("MobileCorrupt-\(UUID())")
        defer { try? FileManager.default.removeItem(at: root) }
        let store = MobileLocalStore(directory: root)
        let credential = MobileCredential(endpoint: "https://one.example", deviceId: "one", token: "example-synthetic")
        try store.save(MobileLocalState(credential: credential), credential: credential)
        let file = root.appendingPathComponent(credential.scope + ".json")
        let corrupt = Data("incomplete synthetic record".utf8); try corrupt.write(to: file)
        XCTAssertThrowsError(try store.load(credential))
        XCTAssertEqual(try Data(contentsOf: file), corrupt)
        XCTAssertTrue(try store.archives().states.isEmpty)
        XCTAssertEqual(try store.archives().unreadableCount, 1)
    }
    func testMismatchedAccountStateCannotOverwriteAnotherNamespace() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("MobileMismatch-\(UUID())")
        defer { try? FileManager.default.removeItem(at: root) }
        let store = MobileLocalStore(directory: root)
        let original = MobileCredential(endpoint: "https://one.example", deviceId: "one", token: "example-synthetic")
        let other = MobileCredential(endpoint: "https://two.example", deviceId: "two", token: "example-synthetic")
        let state = MobileLocalState(credential: original)
        XCTAssertThrowsError(try store.save(state, credential: other))
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.path))
    }
}
