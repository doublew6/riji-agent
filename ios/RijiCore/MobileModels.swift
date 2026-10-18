import Foundation

public enum MobileError: Error, Equatable, LocalizedError {
    case invalidEndpoint, invalidInput, transport, invalidResponse, storage, credentialStorage, unauthorized, unresolved
    case server(Int, String)
    public var errorDescription: String? {
        switch self {
        case .invalidEndpoint: return "请输入有效的 HTTPS 服务地址，不含路径、账号、查询或片段。"
        case .invalidInput: return "内容或操作参数不符合要求，请缩短文字后重试；此次内容尚未发送。"
        case .transport: return "暂时无法连接 AI 服务。本机内容仍保留；若刚发送过消息，会先确认是否送达。"
        case .invalidResponse: return "服务响应无法核验，请稍后重新读取。"
        case .storage: return "本机恢复记录未能保存。请先复制或导出需要的文字；退出 App 后可能无法恢复。"
        case .credentialStorage: return "设备凭据未能读取或更新，连接变更尚未完成。请在设置中重试连接操作。"
        case .unauthorized: return "设备凭据已失效或撤销，请在设置中重新连接。"
        case .unresolved: return "原操作结果尚未确认，请先核对状态，不要重复提交。"
        case .server(_, let code):
            switch code {
            case "memory_version_changed": return "记忆版本已变化或需要核验，请比较当前记忆后再提交。"
            case "memory_page_changed": return "记忆列表已变化，请从第一页重新读取。"
            case "conversation_page_changed": return "对话或来源已变化，已重新读取最新消息。可再次查看更早消息。"
            case "mentor_busy": return "导师仍在处理上一条消息，请先核对原操作状态。"
            case "operation_conflict": return "原操作与提交内容不一致，请核对原结果。"
            case "invalid_input_unsent": return "内容超过请求限制，此次没有发送。请缩短文字后重新提交。"
            case "invalid_group": return "请选择二至四位可用导师后创建群聊。"
            case "invalid_group_name": return "请输入一至 120 个字符的群名称。"
            case "discussion_still_running": return "导师仍在回复，完成或停止后再继续操作。"
            case "problem_archived": return "这个群已归档，恢复后可以继续聊天。"
            case "group_chat_action_not_available": return "这个群暂不支持此操作，请重新读取当前状态。"
            default: return "服务未接受此操作，请重新读取当前状态。"
            }
        }
    }
}

public enum MobileConnectionState: Equatable, Sendable {
    case notConnected, connecting, connected, offline, unavailable, authorizationRequired, recoveryRequired

    public var label: String {
        switch self {
        case .notConnected: return "尚未连接"
        case .connecting: return "正在连接"
        case .connected: return "服务已连接"
        case .offline: return "暂时离线"
        case .unavailable: return "服务暂时不可用"
        case .authorizationRequired: return "需要重新连接"
        case .recoveryRequired: return "本机连接恢复未完成"
        }
    }
    public var guidance: String {
        switch self {
        case .notConnected: return "尚未启用 AI 服务，请更新个人安装。本地日记可继续使用。"
        case .connecting: return "正在确认 AI 服务是否可用，本地日记可继续使用。"
        case .connected: return "AI 服务连接正常。本地日记也可离线使用。"
        case .offline: return "暂时联系不上 AI 服务。草稿保留在本机，恢复连接后先确认未完成的发送。"
        case .unavailable: return "AI 服务暂时无法正常回应，请稍后重试。本地日记可继续使用。"
        case .authorizationRequired: return "此设备授权已失效或撤销，请更新个人安装。保留内容不会自动发送。"
        case .recoveryRequired: return "请恢复本机文件或设备凭据访问后重试连接。已有草稿仍保留。"
        }
    }
}

public enum MobileJSON: Codable, Equatable, Sendable {
    case string(String), number(Double), bool(Bool), object([String: MobileJSON]), array([MobileJSON]), null
    public init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() { self = .null }
        else if let item = try? value.decode(Bool.self) { self = .bool(item) }
        else if let item = try? value.decode(String.self) { self = .string(item) }
        else if let item = try? value.decode(Double.self) { self = .number(item) }
        else if let item = try? value.decode([String: MobileJSON].self) { self = .object(item) }
        else { self = .array(try value.decode([MobileJSON].self)) }
    }
    public func encode(to encoder: Encoder) throws {
        var value = encoder.singleValueContainer()
        switch self {
        case .string(let item): try value.encode(item)
        case .number(let item): try value.encode(item)
        case .bool(let item): try value.encode(item)
        case .object(let item): try value.encode(item)
        case .array(let item): try value.encode(item)
        case .null: try value.encodeNil()
        }
    }
    public var string: String? { if case .string(let value) = self { return value }; return nil }
}

public struct MobilePairing: Decodable, Sendable {
    public let apiVersion: Int
    public let deviceId: String
    public let deviceToken: String
}
public struct MobileMentor: Decodable, Identifiable, Equatable, Sendable {
    public let id: String
    public let name: String
    public let lastReplyId: String?
}
public struct MobileCapabilities: Decodable, Sendable {
    public struct Device: Decodable, Sendable { public let id: String; public let name: String }
    public let apiVersion: Int
    public let device: Device
    public let capabilities: [String: Bool]
    public let mentors: [MobileMentor]
}
public struct MobileConversation: Decodable, Sendable {
    public let conversationId: String?
    public let status: String
    public let revision: Int
    public let messages: [MobileMessage]
    public let notice: String?
    public let nextCursor: String?
    public let historyVersion: String?
    public let historyHasMore: Bool?
    public let lastReplyId: String?
    public var active: Bool { ["queued", "running", "delivering"].contains(status) }
    public var needsNewMessage: Bool { ["source_changed_or_revoked", "source_revoked", "source_changed"].contains(notice ?? "") }
    public var requiresStopBeforeNewMessage: Bool { notice == "model_refused" && status == "interrupted" }
    public var canStop: Bool { active || requiresStopBeforeNewMessage }
    public var acceptsNewMessage: Bool {
        ["empty", "completed", "partial", "stopped"].contains(status) || (status == "interrupted" && needsNewMessage)
    }
    public var resumable: Bool { !needsNewMessage && notice != "model_refused" && ["interrupted", "stopped"].contains(status) }
    public var noticeText: String? {
        guard let notice else { return nil }
        switch notice {
        case "source_changed_or_revoked", "source_revoked", "source_changed": return "本次使用的来源已变化或撤权。请核对资料范围后发送一条新消息，按当前来源继续；旧请求不能直接继续。"
        case "device_revoked": return "此设备已撤销，请在设置中重新连接。草稿不会自动发送。"
        case "interrupted", "worker_interrupted", "runtime_interrupted": return "回复处理中断。请先核对原操作状态，再按当前状态继续。"
        case "step_reconciliation_required", "generation_unknown", "delivery_unknown": return "原回复结果尚未核实。请先核对原操作和 Air 服务状态，避免重复生成。"
        case "budget_exhausted", "source_budget_exhausted": return "本次处理达到已设置的用量限制。请在 Air 核对用量与资料范围，再决定是否继续。"
        case "model_timeout": return "等待模型回复超时，结果尚未核实。请先核对原操作和 Air 状态，避免重复生成。"
        case "model_connection_failed", "model_transport_failed": return "连接模型服务时中断，结果尚未核实。请先核对原操作和 Air 的网络状态。"
        case "model_server_failed": return "模型服务出现故障，结果尚未核实。请先核对原操作和 Air 状态。"
        case "model_output_invalid": return "模型返回内容无法核验，结果尚未核实。请先核对原操作和 Air 状态。"
        case "model_request_failed": return "模型请求未正常完成，结果尚未核实。请先核对原操作和 Air 状态。"
        case "model_queue_timeout": return "等待模型处理排队超时。请核对 Air 的排队状态，再决定是否继续。"
        case "model_authentication_failed": return "Air 的模型凭据需要更新。请在 Air 核对模型登录状态。"
        case "model_permission_denied": return "模型服务拒绝了访问权限。请在 Air 核对模型账号与配置。"
        case "model_rate_limited": return "已达到模型服务的速率限制。请核对 Air 状态，稍后再决定是否继续。"
        case "model_quota_exhausted": return "模型服务额度不足。请在 Air 核对可用额度，再决定是否继续。"
        case "model_request_rejected": return "模型服务未接受请求。请核对内容与 Air 配置后再决定是否继续。"
        case "model_refused": return status == "stopped" ? "上次请求已停止。请核对并调整问题后发送新消息。" : "模型拒绝回答这次请求。请先点停止，确认已停止后，再调整问题并发送新消息。"
        case "model_runtime_unavailable": return "Air 的模型运行环境不可用。请在 Air 核对服务配置与运行状态。"
        default: return "本次回复未能完成。请重新读取对话并核对原操作状态；草稿仍保留。"
        }
    }
}
public struct MobileMessage: Decodable, Identifiable, Sendable {
    public let id: String
    public let actor: String
    public let text: String
    public let createdAt: Double
    public let unavailable: String?
    public let sourceIds: [String]
    public var visibleText: String { unavailable == nil ? text : "来源已变化或撤权，此回复暂不可用。" }
}
public struct MobileReceipt: Codable, Sendable {
    public let operationId: String
    public let kind: String
    public let state: String
    public let result: [String: MobileJSON]?
    public let error: String?
}
public struct MobileMemoryPage: Decodable, Sendable {
    public let items: [MobileMemory]
    public let nextCursor: String?
    public let updatedAt: String
}
public struct MobileMemory: Codable, Identifiable, Sendable {
    public let id: String
    public let content: String
    public let version: String
    public let scope: String
    public let personaId: String?
    public let status: String
    public let state: String
    public let privacy: String
    public let updatedAt: String?
    public let reviewedAt: String?
    public let sources: [MobileSource]
    public let observedAt: String?
    public let kind: String?
    public let certainty: String?
}
public struct MobileSource: Codable, Identifiable, Sendable {
    public let id: String
    public let relativePath: String?
    public let line: Int?
    public let section: String?
    public let version: String?
    public let kind: String?
    public var safeRelativePath: String? {
        guard let path = relativePath, !path.hasPrefix("/"), !path.contains("\\"),
              !path.contains(":"), !path.contains("%"), path.hasSuffix(".md"),
              path.split(separator: "/", omittingEmptySubsequences: false).allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }) else { return nil }
        return path
    }
}
public struct MobilePermissions: Codable, Equatable, Sendable {
    public var history = false
    public var incremental = false
    public var organization = false
    public var recall = false
    public init() {}
    public var json: MobileJSON { .object(["history": .bool(history), "incremental": .bool(incremental), "organization": .bool(organization), "recall": .bool(recall)]) }
}
public struct MobilePrivacy: Decodable, Sendable {
    public struct SourceScope: Decodable, Sendable {
        public let sections: [String]
        public let dateFrom: String?
        public let dateTo: String?
        public let mentors: [String]
    }
    public struct Target: Decodable, Sendable { public let provider: String; public let model: String; public let host: String }
    public let available: Bool
    public let valid: Bool?
    public let binding: String?
    public let permissions: MobilePermissions?
    public let paused: Bool?
    public let targets: [String: Target]?
    public let sourceScope: SourceScope?
    public var canGrant: Bool { available && binding != nil && sourceScope != nil && targets?["extraction"] != nil && targets?["recall"] != nil }
    public let limits: [String: Int]?
    public let progress: [String: Int]?
}
