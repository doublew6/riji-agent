import Foundation
import Darwin

public struct MobileCredential: Codable, Sendable {
    public let endpoint: String
    public let deviceId: String
    public let token: String
    public init(endpoint: String, deviceId: String, token: String) {
        self.endpoint = endpoint; self.deviceId = deviceId; self.token = token
    }
    public var scope: String { SourceHash.digest(Data((endpoint + "\n" + deviceId).utf8)) }
}
public protocol MobileCredentialStore: Sendable {
    func read() throws -> MobileCredential?
    func write(_ value: MobileCredential?) throws
}
public final class MobileVolatileCredentials: MobileCredentialStore, @unchecked Sendable {
    private let lock = NSLock()
    private var value: MobileCredential?
    public init(_ value: MobileCredential? = nil) { self.value = value }
    public func read() -> MobileCredential? { lock.lock(); defer { lock.unlock() }; return value }
    public func write(_ value: MobileCredential?) { lock.lock(); defer { lock.unlock() }; self.value = value }
}

public struct MobilePendingOperation: Codable, Identifiable, Sendable {
    public let id: String
    public let kind: String
    public let target: String
    public let path: [String]
    public var body: [String: MobileJSON]
    public var state: String
    public var error: String?
    public var result: [String: MobileJSON]?
    public init(kind: String, target: String, path: [String], body: [String: MobileJSON]) {
        id = UUID().uuidString; self.kind = kind; self.target = target; self.path = path
        var payload = body; payload["operation_id"] = .string(id); self.body = payload
        state = "pending"
    }
    public var unresolved: Bool { ["pending", "unknown", "not_recorded"].contains(state) }
    public var inputText: String? { body["text"]?.string ?? body["content"]?.string }
    public var hasFailedInput: Bool { state == "failed" && inputText?.isEmpty == false }
    public var targetLabel: String { MobileLocalState.targetLabel(target) }
    public var actionLabel: String {
        switch kind {
        case "mentor_message": return "发送消息"
        case "roundtable_create": return target == "roundtable-create" ? "创建群聊" : "开始讨论"
        case "roundtable_message": return "发送消息"
        case "roundtable_result": return "准备日记内容"
        case "roundtable_command": return ["rename": "修改群名", "stop": "停止回复", "resume": "继续回复", "archive": "收起群聊", "restore": "恢复群聊"][body["action"]?.string ?? ""] ?? "群聊操作"
        case "mentor_control": return ["stop": "停止回复", "resume": "继续回复"][body["action"]?.string ?? ""] ?? "对话控制"
        case "memory_action": return ["update": "纠正", "reconfirm": "复核", "archive": "归档", "restore": "恢复", "permission": "变更使用范围", "delete": "删除"][body["action"]?.string ?? ""] ?? "记忆操作"
        case "memory_organize": return "请求整理记忆"
        case "privacy_action": return ["grant": "确认用途", "revoke": "撤销用途", "pause": "暂停提取", "resume": "继续提取", "scan": "检查新增与修改"][body["action"]?.string ?? ""] ?? "资料使用操作"
        case "device_revoke": return "撤销设备"
        default: return "待核对操作"
        }
    }
    public var recoveryLabel: String { targetLabel + " · " + actionLabel + " · 请求 " + String(id.prefix(8)) }
    public var stateLabel: String {
        switch state {
        case "completed": return "已完成"
        case "pending": return isMessage ? "正在确认是否送达，文字仍保留" : "正在确认这次操作的结果"
        case "failed": return hasFailedInput ? "操作未完成；原请求文字仍保留" : "操作未完成；没有保留原请求文字"
        case "not_recorded": return isMessage ? "暂未查到这条消息，可以重试发送" : "暂未查到这次操作，可以重试"
        default: return isMessage ? "暂时无法确认是否送达，文字仍保留" : "这次操作的结果暂未确认"
        }
    }
    private var isMessage: Bool { ["mentor_message", "roundtable_message"].contains(kind) }
}
public struct MobileLocalState: Codable, Sendable {
    public var endpoint: String
    public var deviceId: String
    public var roundtableSelection: String?
    public var roundtableReading: [String: MobileRoundtableReadingState]?
    public var mentorReading: [String: String]?
    public var groupCreationPersonas: [String]?
    public var lastKnownReplies: [String: String]?
    public var lastReadReplies: [String: String]?
    public var drafts: [String: String] = [:]
    public var memoryDraftVersions: [String: String]? = [:]
    public var operations: [MobilePendingOperation] = []
    public init(credential: MobileCredential) { endpoint = credential.endpoint; deviceId = credential.deviceId }
    public var scope: String { SourceHash.digest(Data((endpoint + "\n" + deviceId).utf8)) }
    public var archiveLabel: String { endpoint + " · 设备记录 " + String(scope.prefix(12)) }
    public var recoveryTargetsLabel: String {
        Set(drafts.keys.map(Self.targetLabel) + operations.filter { $0.unresolved || $0.hasFailedInput }.map(\.targetLabel)).sorted().joined(separator: "、")
    }
    public var failedTargetsLabel: String { Set(operations.filter(\.hasFailedInput).map(\.targetLabel)).sorted().joined(separator: "、") }
    public static func targetLabel(_ target: String) -> String {
        if target.hasPrefix("roundtable") { return "圆桌" }
        if target == "memory-overview" { return "记忆概览" }
        let parts = target.split(separator: ":", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { return target.hasPrefix("privacy") ? "资料使用设置" : "设备连接" }
        if parts[0] == "memory" { return MobileEndpoint.validIdentifier(parts[1]) ? "记忆 " + parts[1] : "记忆更正" }
        let mentors = ["gentle_reviewer": "温柔回顾者", "blunt_coach": "直率教练", "future_self": "未来的我", "wang_yangming": "王阳明"]
        return mentors[parts[1]] ?? "导师对话"
    }
    /// Failed input remains recoverable until explicitly discarded; successful bodies can be removed.
    public mutating func compact() {
        let retained = Set(operations.filter { !$0.unresolved && !$0.hasFailedInput }.suffix(40).map(\.id))
        operations = operations.filter { $0.unresolved || $0.hasFailedInput || retained.contains($0.id) }.map { operation in
            guard !operation.unresolved && !operation.hasFailedInput else { return operation }
            var receipt = operation; receipt.body = [:]
            receipt.result = operation.result?["deleted"].map { ["deleted": $0] }
            if operation.state == "completed", ["roundtable_create", "roundtable_message", "roundtable_command", "roundtable_result"].contains(operation.kind) {
                let field = operation.kind == "roundtable_result" ? "result_id" : "conversation_id"
                if let identifier = operation.result?[field]?.string, MobileEndpoint.validIdentifier(identifier) {
                    receipt.result = [field: .string(identifier)]
                }
            }
            return receipt
        }
    }
}

public struct MobileArchiveRead: Sendable {
    public let states: [MobileLocalState]
    public let unreadableCount: Int
    public var warning: String? {
        unreadableCount == 0 ? nil : "有 \(unreadableCount) 份本机记录无法读取，恢复内容不完整。完好内容显示在下方，异常记录原件仍保留。"
    }
}

/// User input and operation recovery; the bounded read cache lives in a separate directory.
public final class MobileLocalStore: @unchecked Sendable {
    private let directory: URL
    private let lock = NSLock()
    public init(directory: URL) { self.directory = directory }
    public var roundtableCache: MobileRoundtableCache { MobileRoundtableCache(directory: directory.appendingPathComponent("roundtable-cache")) }
    public var overviewCache: MobileOverviewCache { MobileOverviewCache(directory: directory.appendingPathComponent("overview-cache")) }
    private func migrationState(from old: MobileCredential?, to new: MobileCredential) throws -> MobileLocalState {
        var info = stat()
        if lstat(directory.path, &info) != 0 {
            guard errno == ENOENT else { throw MobileError.storage }
        } else if info.st_mode & S_IFMT != S_IFDIR { throw MobileError.storage }
        guard let old, old.endpoint != new.endpoint else {
            let state = try load(new); _ = try migrationData(state); return state
        }
        guard old.deviceId == new.deviceId, old.token == new.token else { throw MobileError.storage }
        var state = try load(old); state.endpoint = new.endpoint
        if let destination = try load(scope: new.scope) {
            guard try migrationData(destination) == migrationData(state) else { throw MobileError.storage }
        }
        _ = try migrationData(state)
        return state
    }
    private func migrationData(_ state: MobileLocalState) throws -> Data {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(state)
        guard data.count <= 8_388_608 else { throw MobileError.storage }
        return data
    }
    public func validateMigration(from old: MobileCredential?, to new: MobileCredential) throws {
        _ = try migrationState(from: old, to: new)
    }
    public func migrate(from old: MobileCredential?, to new: MobileCredential) throws {
        let state = try migrationState(from: old, to: new)
        guard let old, old.scope != new.scope else { return }
        try publishMigration(state)
    }
    /// Revalidate a destination created after planning, then publish exclusively.
    func publishMigration(_ state: MobileLocalState) throws {
        let data = try migrationData(state)
        if let destination = try load(scope: state.scope) {
            guard try migrationData(destination) == data else { throw MobileError.storage }
            return
        }
        // Keep the original archive. An existing destination is never replaced.
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        var excluded = URLResourceValues(); excluded.isExcludedFromBackup = true
        var folder = directory; try folder.setResourceValues(excluded)
        let file = directory.appendingPathComponent(state.scope + ".json")
        let staging = directory.appendingPathComponent(".migration-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: staging) }
        #if os(iOS)
        try data.write(to: staging, options: [.withoutOverwriting, .completeFileProtection])
        #else
        try data.write(to: staging, options: .withoutOverwriting)
        #endif
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: staging.path)
        // Publish complete bytes atomically without replacing a concurrently created destination.
        try FileManager.default.linkItem(at: staging, to: file)
    }
    public func load(_ credential: MobileCredential) throws -> MobileLocalState {
        guard let state = try load(scope: credential.scope) else { return MobileLocalState(credential: credential) }
        guard state.endpoint == credential.endpoint, state.deviceId == credential.deviceId else { throw MobileError.storage }
        return state
    }
    public func load(scope: String) throws -> MobileLocalState? {
        lock.lock(); defer { lock.unlock() }
        guard Self.validScope(scope) else { throw MobileError.storage }
        let file = directory.appendingPathComponent(scope + ".json")
        var info = stat()
        if lstat(file.path, &info) != 0 {
            guard errno == ENOENT || errno == ENOTDIR else { throw MobileError.storage }
            return nil
        }
        guard info.st_mode & S_IFMT == S_IFREG else { throw MobileError.storage }
        return try readState(file, scope: scope)
    }
    private static func validScope(_ scope: String) -> Bool {
        scope.utf8.count == 64 && scope.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
    private func readState(_ file: URL, scope: String) throws -> MobileLocalState {
        do {
            guard Self.validScope(scope) else { throw MobileError.storage }
            let attributes = try FileManager.default.attributesOfItem(atPath: file.path)
            guard attributes[.type] as? FileAttributeType == .typeRegular,
                  (attributes[.size] as? NSNumber)?.intValue ?? Int.max <= 8_388_608 else { throw MobileError.storage }
            let data = try Data(contentsOf: file)
            guard data.count <= 8_388_608 else { throw MobileError.storage }
            var state = try JSONDecoder().decode(MobileLocalState.self, from: data)
            guard state.scope == scope, (try? MobileEndpoint(state.endpoint).origin) == state.endpoint,
                  MobileEndpoint.validIdentifier(state.deviceId) else { throw MobileError.storage }
            state.compact()
            return state
        } catch { throw MobileError.storage }
    }
    public func save(_ state: MobileLocalState, credential: MobileCredential) throws {
        guard state.endpoint == credential.endpoint, state.deviceId == credential.deviceId else { throw MobileError.storage }
        try save(state)
    }
    /// Account-scoped local recovery never needs an access token or network request.
    public func save(_ state: MobileLocalState) throws {
        lock.lock(); defer { lock.unlock() }
        guard (try? MobileEndpoint(state.endpoint).origin) == state.endpoint,
              MobileEndpoint.validIdentifier(state.deviceId) else { throw MobileError.storage }
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            var values = URLResourceValues(); values.isExcludedFromBackup = true
            var folder = directory; try folder.setResourceValues(values)
            var compacted = state; compacted.compact()
            let data = try JSONEncoder().encode(compacted)
            guard data.count <= 8_388_608 else { throw MobileError.storage }
            let url = directory.appendingPathComponent(state.scope + ".json")
            #if os(iOS)
            try data.write(to: url, options: [.atomic, .completeFileProtection])
            #else
            try data.write(to: url, options: .atomic)
            #endif
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        } catch { throw MobileError.storage }
    }
    public func archives() throws -> MobileArchiveRead {
        lock.lock(); defer { lock.unlock() }
        guard FileManager.default.fileExists(atPath: directory.path) else { return MobileArchiveRead(states: [], unreadableCount: 0) }
        let files: [URL]
        do {
            files = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
        } catch { throw MobileError.storage }
        var states: [MobileLocalState] = [], unreadableCount = 0
        for file in files where file.pathExtension == "json" {
            do { states.append(try readState(file, scope: file.deletingPathExtension().lastPathComponent)) }
            catch { unreadableCount += 1 }
        }
        return MobileArchiveRead(states: states, unreadableCount: unreadableCount)
    }
}
