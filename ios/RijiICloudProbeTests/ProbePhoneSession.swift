import Foundation
import RijiCore

private let probeProcessID = UUID()

struct ProbePhoneState: Codable, Equatable {
    let run: UUID
    let process: UUID
    let identity: String
    let saved: BodyEdit
    let drafts: [BodyEdit]

    func validate(fixture: ProbeFixture) throws {
        let paths = [ProbeFixture.savedPath, ProbeFixture.modifiedPath, ProbeFixture.deletedPath, ProbeFixture.createdPath]
        let edits = [saved] + drafts
        guard run == fixture.run, drafts.count == 3, Set(edits.map(\.id)).count == 4,
              identity.count == 64, identity.allSatisfy({ "0123456789abcdef".contains($0) }) else { throw ProbeFailure.state }
        for (index, edit) in edits.enumerated() {
            let path = paths[index]
            let day = URL(fileURLWithPath: path).deletingPathExtension().lastPathComponent
            let content = fixture.document(index == 0 ? "Phone saved day" : "Retained phone draft \(index)")
            guard edit.day.value == day, edit.before == fixture.files(.pristine)[path], edit.content == content,
                  edit.sourceID == identity + ":" + path else { throw ProbeFailure.state }
        }
    }
}

enum ProbeRunRequest {
    static func parse(_ value: String?) throws -> UUID? {
        guard let value else { return nil }
        guard let run = UUID(uuidString: value) else { throw ProbeFailure.invalidArguments }
        return run
    }
}

private struct ProbeBodyReceipt: Decodable {
    let edit: BodyEdit
    let supersededBy: UUID?
}

/// File operations execute on this actor, never on the UI actor.
actor ProbePhoneSession {
    let fixture: ProbeFixture
    let directory: URL
    private var checks = ["physicalDevice": "passed", "iCloudMetadata": "pending", "read": "pending",
                          "localSave": "pending", "idempotency": "pending", "bookmarkNewProcess": "pending",
                          "retainedDrafts": "pending", "durableState": "pending",
                          "phoneToMacPropagation": "pending", "macToPhonePropagation": "pending",
                          "staleModify": "pending", "staleDelete": "pending", "staleCreate": "pending",
                          "downloadEviction": "pending", "NSFileVersionConflict": "pending", "interruption": "pending"]

    init(fixture: ProbeFixture, localTestDirectory: URL? = nil) throws {
        self.fixture = fixture
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        directory = localTestDirectory ?? support.appendingPathComponent("UITests-\(ProbeFixture.namespace)/ICloudProbe/\(fixture.run.uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    }

    func prepareInitial() throws {
        guard !FileManager.default.fileExists(atPath: directory.appendingPathComponent("phase.json").path),
              !FileManager.default.fileExists(atPath: directory.appendingPathComponent("folder.bookmark").path),
              !FileManager.default.fileExists(atPath: directory.appendingPathComponent("summary-initial.json").path) else { throw ProbeFailure.state }
    }

    func initial(root: URL) async throws {
        guard root.startAccessingSecurityScopedResource() else { throw ProbeFailure.unavailable }
        defer { root.stopAccessingSecurityScopedResource() }
        try fixture.validate(root, phase: .pristine)
        checks["iCloudMetadata"] = "passed"
        let original = try OriginalVaultAccess(root: root, writePolicy: .syntheticFixture)
        let store = try makeStore(original)
        let state = try await prepareState(store, identity: original.identity)
        try state.validate(fixture: fixture)
        let bookmark = try root.bookmarkData(options: .minimalBookmark, includingResourceValuesForKeys: nil, relativeTo: nil)
        try bookmark.write(to: directory.appendingPathComponent("folder.bookmark"), options: .withoutOverwriting)
        try save(state, name: "phase.json")
        for edit in [state.saved] + state.drafts {
            try require(try original.read(edit.day) == edit.before)
        }
        checks["read"] = "passed"
        try await store.retainBodyEdit(state.saved)
        try await store.commitBodyEdit(state.saved)
        try await store.commitBodyEdit(state.saved)
        try require(try original.read(state.saved.day) == state.saved.content)
        try require(try await store.retainedBodyEdit(state.saved.day) == nil)
        try require(try await store.bodyRevisions(state.saved.day).map(\.id) == [state.saved.id])
        try verifyReceipt(state.saved, identity: state.identity)
        checks["localSave"] = "passed"; checks["idempotency"] = "passed"
        for edit in state.drafts { try await store.retainBodyEdit(edit) }
        try verifyDrafts(state)
        checks["retainedDrafts"] = "passed"
        let restored: ProbePhoneState = try load("phase.json")
        try restored.validate(fixture: fixture)
        try require(restored == state)
        checks["durableState"] = "passed"
        try fixture.validate(root, phase: .phoneSaved, storage: .localTest)
        try fixture.writePhoneReceipt(root)
        try record(phase: "initial", outcome: "passed")
    }

    private func prepareState(_ store: LocalJournalStore, identity: String) async throws -> ProbePhoneState {
        var edits: [BodyEdit] = []
        for (index, path) in [ProbeFixture.savedPath, ProbeFixture.modifiedPath, ProbeFixture.deletedPath, ProbeFixture.createdPath].enumerated() {
            let day = try JournalDay(URL(fileURLWithPath: path).deletingPathExtension().lastPathComponent)
            let content = index == 0 ? fixture.document("Phone saved day") : fixture.document("Retained phone draft \(index)")
            edits.append(try await BodyEdit(day: day, before: fixture.files(.pristine)[path], content: content, sourceID: store.bodySourceID(day)))
        }
        return ProbePhoneState(run: fixture.run, process: probeProcessID, identity: identity, saved: edits[0], drafts: Array(edits.dropFirst()))
    }

    func verify() async throws {
        let state: ProbePhoneState = try load("phase.json")
        try state.validate(fixture: fixture)
        guard state.process != probeProcessID else { throw ProbeFailure.sameProcess }
        let initial: [String: String] = try load("summary-initial.json")
        try require(initial["outcome"] == "passed" && initial["phase"] == "initial" && initial["run"] == fixture.run.uuidString)
        try Self.requireCompletedChecks(initial, phase: "initial")
        for key in checks.keys where initial[key] == "passed" { checks[key] = "passed" }
        let bookmark = try Data(contentsOf: directory.appendingPathComponent("folder.bookmark"))
        var stale = false
        let root = try URL(resolvingBookmarkData: bookmark, options: .withoutUI, relativeTo: nil, bookmarkDataIsStale: &stale)
        guard !stale else { throw ProbeFailure.staleBookmark }
        guard root.startAccessingSecurityScopedResource() else { throw ProbeFailure.unavailable }
        defer { root.stopAccessingSecurityScopedResource() }
        try fixture.validate(root, phase: .macReady)
        let original = try OriginalVaultAccess(root: root, writePolicy: .syntheticFixture)
        try require(original.identity == state.identity)
        let store = try makeStore(original)
        try require(try original.read(state.saved.day) == fixture.files(.macReady)[ProbeFixture.savedPath])
        checks["bookmarkNewProcess"] = "passed"; checks["macToPhonePropagation"] = "passed"
        checks["phoneToMacPropagation"] = "passed-via-mac-ready-receipt"
        for (index, edit) in state.drafts.enumerated() {
            let name = ["staleModify", "staleDelete", "staleCreate"][index]
            do { try await store.commitBodyEdit(edit); throw ProbeFailure.rejectedWriteSucceeded }
            catch ProbeFailure.rejectedWriteSucceeded { throw ProbeFailure.rejectedWriteSucceeded }
            catch {
                guard let code = refusalCode(error) else { throw error }
                checks[name] = "refused-\(code)"
            }
            try verifyDrafts(state)
            try fixture.validate(root, phase: .macReady)
        }
        try verifyReceipt(state.saved, identity: state.identity)
        try record(phase: "verify", outcome: "passed")
    }

    private func refusalCode(_ error: Error) -> String? {
        if case OriginalVaultError.changedIdentity = error { return "changedIdentity" }
        if case JournalError.changedSource = error { return "changedSource" }
        return nil
    }

    private func makeStore(_ original: OriginalVaultAccess) throws -> LocalJournalStore {
        try LocalJournalStore(journalDirectory: directory.appendingPathComponent("unused"),
                              stateDirectory: directory.appendingPathComponent("state"), original: original)
    }

    private func bodyFile(_ folder: String, key: String, identity: String) -> URL {
        directory.appendingPathComponent("state/original-vaults/\(identity)/body-\(folder)/\(key).json")
    }

    private func verifyDrafts(_ state: ProbePhoneState) throws {
        for edit in state.drafts {
            // Nested deletion changes the derived source ID. Inspect the durable
            // draft bytes without asking recovery to rebind them to another path.
            let bytes = try Data(contentsOf: bodyFile("drafts", key: edit.day.value, identity: state.identity))
            try require(try JSONDecoder().decode(BodyEdit.self, from: bytes) == edit)
            let receipt = bodyFile("receipts", key: edit.id.uuidString, identity: state.identity)
            guard !FileManager.default.fileExists(atPath: receipt.path) else { throw ProbeFailure.receipt }
        }
    }

    private func verifyReceipt(_ edit: BodyEdit, identity: String) throws {
        let bytes = try Data(contentsOf: bodyFile("receipts", key: edit.id.uuidString, identity: identity))
        let receipt = try JSONDecoder().decode(ProbeBodyReceipt.self, from: bytes)
        guard receipt.edit == edit, receipt.supersededBy == nil else { throw ProbeFailure.receipt }
    }

    private func save<T: Encodable>(_ value: T, name: String) throws {
        try JSONEncoder().encode(value).write(to: directory.appendingPathComponent(name), options: .atomic)
    }

    private func load<T: Decodable>(_ name: String) throws -> T {
        try JSONDecoder().decode(T.self, from: Data(contentsOf: directory.appendingPathComponent(name)))
    }

    private func require(_ condition: Bool) throws { guard condition else { throw ProbeFailure.assertion } }

    nonisolated static func requireCompletedChecks(_ checks: [String: String], phase: String) throws {
        let initial = ["physicalDevice", "iCloudMetadata", "read", "localSave", "idempotency", "retainedDrafts", "durableState"]
        guard ["initial", "verify"].contains(phase), initial.allSatisfy({ checks[$0] == "passed" }) else { throw ProbeFailure.state }
        if phase == "verify" {
            guard checks["bookmarkNewProcess"] == "passed", checks["macToPhonePropagation"] == "passed",
                  checks["phoneToMacPropagation"] == "passed-via-mac-ready-receipt",
                  ["staleModify", "staleDelete", "staleCreate"].allSatisfy({
                      ["refused-changedSource", "refused-changedIdentity"].contains(checks[$0] ?? "")
                  }) else { throw ProbeFailure.state }
        }
    }

    @discardableResult func record(phase: String, outcome: String, failure: String? = nil) throws -> Data {
        guard ["initial", "verify"].contains(phase), ["passed", "failed"].contains(outcome) else { throw ProbeFailure.state }
        if outcome == "passed" { try Self.requireCompletedChecks(checks, phase: phase) }
        var summary = checks
        summary["protocol"] = "1"; summary["run"] = fixture.run.uuidString
        summary["phase"] = phase; summary["outcome"] = outcome; summary["failure"] = failure
        summary["fullCloudGate"] = "pending"; summary["privateOriginalWriting"] = "readOnly"
        summary["conflictMeaning"] = "stale-source-refusal; not NSFileVersion conflict"
        summary["recordedAt"] = ISO8601DateFormatter().string(from: Date())
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(summary)
        let name = outcome == "passed" ? "summary-\(phase).json" : "attempt-\(phase)-\(UUID().uuidString).json"
        let destination = directory.appendingPathComponent(name)
        if outcome == "passed", FileManager.default.fileExists(atPath: destination.path) {
            let previous = try Data(contentsOf: destination)
            let summary = try JSONDecoder().decode([String: String].self, from: previous)
            guard summary["outcome"] == "passed", summary["run"] == fixture.run.uuidString, summary["phase"] == phase else { throw ProbeFailure.state }
            try Self.requireCompletedChecks(summary, phase: phase)
            return previous
        }
        try data.write(to: destination, options: .withoutOverwriting)
        return data
    }
}
