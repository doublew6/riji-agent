import Foundation
import Security
import RijiCore

/// A single device credential. It never syncs or migrates to another device.
final class MobileKeychain: MobileCredentialStore, @unchecked Sendable {
    private let service = "com.riji.local.mobile-device.v1"
    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
         kSecAttrAccount as String: "active-device", kSecAttrSynchronizable as String: false]
    }
    func read() throws -> MobileCredential? {
        var lookup = query
        lookup[kSecReturnData as String] = true; lookup[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(lookup as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data,
              let value = try? JSONDecoder().decode(MobileCredential.self, from: data) else { throw MobileError.storage }
        return value
    }
    func write(_ value: MobileCredential?) throws {
        guard let value else {
            let status = SecItemDelete(query as CFDictionary)
            guard status == errSecSuccess || status == errSecItemNotFound else { throw MobileError.storage }; return
        }
        let attributes: [String: Any] = [kSecValueData as String: try JSONEncoder().encode(value),
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
            kSecAttrSynchronizable as String: false]
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            let added = SecItemAdd(query.merging(attributes) { _, rhs in rhs } as CFDictionary, nil)
            guard added == errSecSuccess else { throw MobileError.storage }
        } else if status != errSecSuccess { throw MobileError.storage }
    }
}

extension MobileClientModel {
    static func live() -> MobileClientModel {
        let environment = ProcessInfo.processInfo.environment
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        #if DEBUG
        if let value = environment["RIJI_TEST_CONTAINER"], let id = UUID(uuidString: value) {
            let fixture = environment["RIJI_TEST_MOBILE"]
            let connected = ["roundtable-offline", "roundtable-long", "roundtable", "overview-confirmation-change", "overview-multiple", "overview", "connected", "history", "rebase", "rebase-updated", "recovery", "refused"].contains(fixture)
            let credential = connected ? MobileCredential(endpoint: "https://air.example", deviceId: "synthetic-ui-device", token: "example-synthetic-ui-only") : nil
            let folder = support.appendingPathComponent("MobileTests/" + id.uuidString)
            if fixture == "managed" { return managedFixture(folder: folder) }
            if fixture == "archive-mixed" || fixture == "archive-corrupt" {
                do {
                    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                    if fixture == "archive-mixed" {
                        let archived = MobileCredential(endpoint: "https://air.example", deviceId: "synthetic-archive-device", token: "example-synthetic-ui-only")
                        var state = MobileLocalState(credential: archived)
                        state.drafts["mentor:future_self"] = "Synthetic readable archive"
                        try MobileLocalStore(directory: folder).save(state)
                    }
                    try Data("{synthetic invalid archive".utf8).write(to: folder.appendingPathComponent(String(repeating: "a", count: 64) + ".json"))
                } catch { /* Missing fixture evidence fails the native UI assertions. */ }
            }
            let model = MobileClientModel(credentials: MobileVolatileCredentials(credential),
                persistence: MobileLocalStore(directory: folder),
                transport: connected || fixture == "pair" ? MobileUIFixtureWire(mode: fixture ?? "", roundtableFile: folder.appendingPathComponent("roundtable-fixture.json")) : MobileURLTransport(),
                isSyntheticEnvironment: connected || fixture == "pair")
            if fixture == "recovery" {
                Task {
                    await model.start()
                    do {
                        try FileManager.default.createDirectory(at: folder.deletingLastPathComponent(), withIntermediateDirectories: true)
                        try Data("Synthetic storage obstacle".utf8).write(to: folder)
                        model.setDraft("mentor:future_self", text: "Synthetic recovery text")
                    } catch { /* A fixture failure remains visible to the UI assertion. */ }
                }
            }
            return model
        }
        #endif
        return MobileClientModel(credentials: MobileKeychain(),
            persistence: MobileLocalStore(directory: support.appendingPathComponent("MobileClient")), transport: MobileURLTransport(),
            installation: MobileInstallationFile(directory: FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]))
    }
}

#if DEBUG
private extension MobileClientModel {
    static func managedFixture(folder: URL) -> MobileClientModel {
        let cache = folder.appendingPathComponent("Caches")
        let credentials = MobileFixtureCredentials(file: folder.appendingPathComponent("fixture-credential.json"))
        do {
            try FileManager.default.createDirectory(at: cache, withIntermediateDirectories: true)
            let package = cache.appendingPathComponent("MobileInstallation.json")
            if !FileManager.default.fileExists(atPath: package.path),
               !FileManager.default.fileExists(atPath: cache.appendingPathComponent("MobileInstallationReceipt.json").path) {
                let value = MobileInstallationPackage(endpoint: "https://air.example", deviceId: "synthetic-ui-device",
                    deviceToken: "synthetic-ui-installation-token-only-0000")
                try JSONEncoder().encode(value).write(to: package, options: .withoutOverwriting)
            }
        } catch { /* Missing fixture evidence fails the native UI assertions. */ }
        return MobileClientModel(credentials: credentials, persistence: MobileLocalStore(directory: folder.appendingPathComponent("State")),
            transport: MobileUIFixtureWire(mode: "managed"), installation: MobileInstallationFile(directory: cache), isSyntheticEnvironment: true)
    }
}

private struct MobileFixtureCredentials: MobileCredentialStore {
    let file: URL
    func read() throws -> MobileCredential? {
        guard FileManager.default.fileExists(atPath: file.path) else { return nil }
        return try JSONDecoder().decode(MobileCredential.self, from: Data(contentsOf: file))
    }
    func write(_ value: MobileCredential?) throws {
        if let value { try JSONEncoder().encode(value).write(to: file, options: .atomic) }
        else if FileManager.default.fileExists(atPath: file.path) { try FileManager.default.removeItem(at: file) }
    }
}
/// UI acceptance only: factory access requires an explicit UUID test namespace.
/// This transport never opens a socket or reads a production credential.
private actor MobileUIFixtureWire: MobileTransport {
    private let mode: String
    private let roundtable: MobileRoundtableFixture?
    init(mode: String, roundtableFile: URL? = nil) {
        self.roundtable = ["roundtable", "roundtable-long"].contains(mode) ? roundtableFile.map { MobileRoundtableFixture(file: $0, seedLegacyHistory: true) } : nil
        self.mode = mode
        if mode == "rebase-updated" {
            memoryText = "Synthetic remotely updated memory"; memoryVersion = "synthetic-v2"
        }
    }
    private var sent: [String: String] = [:]
    private var stopped: Set<String> = []
    private var receipts: [String: Data] = [:]
    private var memoryText = "Synthetic memory for mobile acceptance"
    private var memoryVersion = "synthetic-v1"
    private var organizing = false
    private var memoryReads = 0
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        guard let url = request.url, url.host == "air.example" else { throw URLError(.unsupportedURL) }
        if mode == "roundtable-offline" { throw URLError(.notConnectedToInternet) }
        if let roundtable, let response = try await roundtable.response(request, memories: fixtureMemories) { return response }
        let path = url.path
        let body = request.httpBody.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        var status = 200
        let value: [String: Any]
        if path == "/mobile/v1/pair", request.httpMethod == "POST" {
            value = ["api_version": 1, "device_id": "synthetic-ui-device", "device_token": "example-synthetic-ui-only"]
        } else if path == "/mobile/v1/capabilities" {
            value = ["api_version": 1, "device": ["id": "synthetic-ui-device", "name": "Synthetic"],
                     "capabilities": ["mentors": true, "memory": true, "memory_overview": mode.hasPrefix("overview"), "privacy": true, "roundtable": ["roundtable", "roundtable-long"].contains(mode), "group_chat": ["roundtable", "roundtable-long"].contains(mode), "workflows": false],
                     "mentors": [["id": "gentle_reviewer", "name": "温柔回顾者"], ["id": "blunt_coach", "name": "直率教练"],
                                 ["id": "future_self", "name": "未来的我"], ["id": "wang_yangming", "name": "王阳明"]]]
        } else if path.hasPrefix("/mobile/v1/mentors/"), path.hasSuffix("/conversation") {
            let persona = url.pathComponents[4]
            var messages: [[String: Any]] = []
            if let text = sent[persona] {
                messages = [["id": "synthetic-user", "actor": "user", "text": text, "created_at": 1, "unavailable": NSNull(), "source_ids": []],
                            ["id": "synthetic-reply", "actor": persona, "text": "Synthetic mentor reply", "created_at": 2, "unavailable": NSNull(), "source_ids": []]]
            }
            let older = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.contains { $0.name == "cursor" && $0.value == "synthetic-older" } == true
            if mode == "history" {
                messages = [["id": older ? "synthetic-old" : "synthetic-latest", "actor": "user",
                             "text": older ? "Synthetic older message" : "Synthetic newest message",
                             "created_at": older ? 1 : 2, "unavailable": NSNull(), "source_ids": []]]
            }
            let refused = mode == "refused" && !stopped.contains(persona) && sent[persona] == nil
            value = ["conversation_id": "synthetic-conversation", "status": refused ? "interrupted" : stopped.contains(persona) && sent[persona] == nil ? "stopped" : "completed", "revision": 1,
                     "messages": messages, "notice": refused ? "model_refused" : NSNull(), "next_cursor": mode == "history" && !older ? "synthetic-older" : NSNull(),
                     "history_has_more": mode == "history" && !older, "history_version": "synthetic-history"]
        } else if path == "/mobile/v1/memory-overview" {
            value = overview()
        } else if path == "/mobile/v1/memories" {
            let items = fixtureMemories
            value = ["items": items, "next_cursor": NSNull(), "updated_at": "2026-01-01T00:00:00Z"]
        } else if path == "/mobile/v1/memories/synthetic-memory" {
            memoryReads += 1
            if mode == "overview-confirmation-change", memoryReads > 2 {
                memoryText = "Synthetic changed during confirmation"; memoryVersion = "synthetic-v2"
            }
            value = memory()
        } else if path == "/mobile/v1/privacy" {
            value = ["available": true, "valid": true, "binding": "synthetic-binding", "paused": true,
                     "permissions": ["history": false, "incremental": true, "organization": true, "recall": true],
                     "targets": ["extraction": ["provider": "example-synthetic", "model": "example-synthetic", "host": "example.com"],
                                 "recall": ["provider": "example-synthetic", "model": "example-synthetic", "host": "example.com"]],
                     "source_scope": ["sections": ["Notes"], "date_from": NSNull(), "date_to": NSNull(), "mentors": ["future_self"]],
                     "limits": ["daily_chars": 100000, "fragment_chars": 900, "source_chars": 4000],
                     "progress": ["discovered": 1, "completed": 1, "pending": 0, "failed": 0]]
        } else if path.hasPrefix("/mobile/v1/operations/"), let data = receipts[url.lastPathComponent] {
            return (data, HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: nil)!)
        } else if request.httpMethod == "POST", let id = body["operation_id"] as? String {
            let kind: String
            if path.hasPrefix("/mobile/v1/mentors/"), path.hasSuffix("/messages") {
                sent[url.pathComponents[4]] = body["text"] as? String; kind = "mentor_message"
            } else if mode == "refused", path.hasPrefix("/mobile/v1/mentors/"), path.hasSuffix("/control"), body["action"] as? String == "stop" {
                stopped.insert(url.pathComponents[4]); kind = "mentor_control"
            } else if path == "/mobile/v1/memory-overview/actions" {
                organizing = true; kind = "memory_organize"
            } else if path == "/mobile/v1/memories/synthetic-memory/actions" {
                if let content = body["content"] as? String { memoryText = content; memoryVersion = "synthetic-v2" }
                kind = "memory_action"
            } else { throw URLError(.unsupportedURL) }
            value = ["operation_id": id, "kind": kind, "state": "completed", "result": [:], "error": NSNull()]
            receipts[id] = try JSONSerialization.data(withJSONObject: value)
        } else { status = 404; value = ["detail": "synthetic_not_found"] }
        return (try JSONSerialization.data(withJSONObject: value),
                HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: nil)!)
    }
    private func overview() -> [String: Any] {
        let current = memoryVersion == "synthetic-v1"
        let themes = [("preferences", "偏好与习惯"), ("goals", "目标与计划"), ("work", "工作与学习"), ("relationships", "关系与生活"), ("events", "经历与变化"), ("other", "其他认识")]
        let summary: [String: Any] = ["id": "synthetic-summary", "category": "preferences", "title": "Synthetic reading preference", "summary": "Synthetic summary supported by a saved memory", "scope": "shared", "persona_id": NSNull(), "states": ["current"], "evidence_ids": ["synthetic-memory"], "updated_at": "2026-01-01T00:00:00Z"]
        var summaries = [summary]
        if mode == "overview-multiple" {
            for (id, title) in [("synthetic-goal", "Synthetic practice plan"), ("synthetic-goal-next", "Synthetic reflection plan")] {
                var goal = summary
                goal["id"] = id; goal["category"] = "goals"; goal["title"] = title
                goal["summary"] = "A synthetic plan supported by the selected evidence."
                goal["states"] = ["current", "plan"]
                summaries.append(goal)
            }
        }
        return ["version": String(repeating: current ? "a" : "b", count: 64), "updated_at": ISO8601DateFormatter().string(from: Date()),
            "organized_at": "2026-01-01T00:00:00Z", "cache_expires_at": Date().timeIntervalSince1970 + 899,
            "state": current ? "ready" : "invalid", "job_status": organizing ? "pending" : "ready",
            "coverage": ["eligible": 1, "organized": current ? 1 : 0, "unorganized": current ? 0 : 1, "stopped": 0, "invalid": current ? 0 : 1],
            "categories": themes.map { theme in ["id": theme.0, "title": theme.1,
                "summary_count": current ? summaries.filter { $0["category"] as? String == theme.0 }.count : 0] as [String: Any] },
            "summaries": current ? summaries : [], "observations": [], "comparisons": [], "evidence": current ? [memory()] : [],
            "next_cursor": NSNull(), "can_organize": !current && !organizing, "organize_binding": String(repeating: "c", count: 64),
            "organize_reason": organizing ? "already_pending" as Any : current ? "no_eligible_work" as Any : NSNull()]
    }
    private var fixtureMemories: [[String: Any]] {
        mode == "roundtable-long" ? (1...40).map { index in
            var item = memory(); item["id"] = "synthetic-long-\(index)"
            item["content"] = "Synthetic optional context \(index): a long selected-memory list must never hide the invitation action."
            return item
        } : [memory()]
    }
    private func memory() -> [String: Any] {
        ["id": "synthetic-memory", "content": memoryText, "version": memoryVersion, "scope": "shared",
         "persona_id": NSNull(), "status": "active", "state": "current", "privacy": "cloud",
         "updated_at": NSNull(), "reviewed_at": NSNull(), "sources": []]
    }
}
#endif
