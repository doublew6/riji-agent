import Foundation

public struct MobileRoundtableList: Decodable, Sendable {
    public struct Item: Decodable, Identifiable, Sendable {
        public let id: String; public let title: String; public let status: String
        public let updatedAt: Double; public let revision: Int
        public let chatKind: String?
        public let personas: [String]?
        public let lastReplyId: String?
        public var isGroup: Bool { chatKind == "group" }
    }
    public var items: [Item]; public let currentId: String?; public let truncated: Bool
    public let nextCursor: String?; public let historyVersion: String?
    public init(cached pages: [MobileRoundtable]) {
        items = pages.map { Item(id: $0.conversationId, title: $0.displayTitle, status: $0.status,
            updatedAt: $0.messages.last?.createdAt ?? 0, revision: $0.revision, chatKind: $0.chatKind, personas: $0.personas,
            lastReplyId: $0.lastReplyId) }
        currentId = pages.first?.conversationId; truncated = true; nextCursor = nil; historyVersion = nil
    }
    public func validated() throws -> Self {
        guard items.count <= 100, Set(items.map(\.id)).count == items.count,
              items.allSatisfy({ item in
                  guard MobileEndpoint.validIdentifier(item.id), item.title.count <= 200, item.revision > 0 else { return false }
                  if !item.isGroup { return item.chatKind == nil || item.chatKind == "legacy" }
                  let members = item.personas ?? []
                  return (2...4).contains(members.count) && Set(members).count == members.count &&
                      Set(members).isSubset(of: Set(["gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"]))
              }),
              nextCursor == nil || (nextCursor!.count <= 512 && historyVersion != nil) else { throw MobileError.invalidResponse }
        return self
    }
}
public struct MobileRoundtable: Codable, Sendable {
    public let conversationId: String
    public let chatKind: String?
    public let title: String?
    public let personas: [String]?
    public let lastReplyId: String?
    public let turnEnded: Bool?
    public let status: String
    public let revision: Int
    public let stateRevision: Int
    public let summaryVersion: Int
    public let correctionVersion: Int
    public let runId: String
    public let runKind: String
    public let runPersonas: [String]
    public let mode: String
    public let rounds: Int
    public let suspendedRunId: String
    public var messages: [MobileRoundtableMessage]
    public let materialSources: [MobileRoundtableSource]?
    public let progress: MobileRoundtableProgress?
    public let currentSummary: MobileJSON?
    public let runs: [MobileJSON]
    public let audience: [String]
    public let notice: String?
    public let historyVersion: String
    public var nextCursor: String?
    public let selectedArtifactIds: [String]
    public let cacheExpiresAt: Double
    public var completionNotice: String? {
        guard ["partial", "interrupted"].contains(status), let notice, !notice.isEmpty else { return nil }
        switch notice {
        case "source_budget_exhausted": return "本次依据的使用额度已用完，讨论提前结束；已完成的回复仍保留，可先查看这些建议。"
        case "budget_exhausted": return "本次讨论已达到时间或请求上限；已完成的回复仍保留。"
        default: return "本次讨论未能全部完成；已完成的回复仍保留，可核对后继续。"
        }
    }
    public var active: Bool { ["queued", "running", "delivering"].contains(status) }
    public var isGroup: Bool { chatKind == "group" }
    public var members: [String] { isGroup ? (personas ?? []) : audience }
    public var displayTitle: String { title?.isEmpty == false ? title! : "圆桌记录" }
    public var ready: Bool { ["completed", "partial", "stopped"].contains(status) }
    public func validated(now: Date = Date()) throws -> Self {
        guard MobileEndpoint.validIdentifier(conversationId), revision > 0, messages.count <= 200,
              Set(messages.map(\.id)).count == messages.count, messages.allSatisfy({ MobileEndpoint.validIdentifier($0.id) && $0.text.count <= 12000 }),
              validAudience,
              cacheExpiresAt > now.timeIntervalSince1970, cacheExpiresAt <= now.timeIntervalSince1970 + 901 else { throw MobileError.invalidResponse }
        return self
    }
    private var validAudience: Bool {
        let allowed = Set(["gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"])
        if isGroup {
            let members = personas ?? []
            return (2...4).contains(members.count) && Set(members).count == members.count &&
                Set(members).isSubset(of: allowed) && Set(audience) == Set(members) && audience.count == members.count &&
                Set(runPersonas).isSubset(of: Set(members)) &&
                (progress == nil || members.contains(progress!.actor)) &&
                (title?.count ?? 0) <= 200 && messages.allSatisfy { $0.actor == "user" || members.contains($0.actor) }
        }
        return (chatKind == nil || chatKind == "legacy") && Set(audience) == allowed.union(["host"])
    }
}
public struct MobileRoundtableMessage: Codable, Identifiable, Sendable {
    public let id: String; public let actor: String; public let kind: String; public let text: String
    public let createdAt: Double; public let inputRevision: Int; public let runId: String; public let originKind: String
    public let superseded: Bool; public let unavailable: String?; public let uncertainties: [String]
    public let nextSteps: [String]; public let sourceIds: [String]
    public var visibleText: String { unavailable == nil ? text : "依据已失效，此内容暂不可用。" }
}
public struct MobileMaterialPreview: Decodable, Sendable {
    public let previewHash: String; public let artifactIds: [String]; public let audience: [String]
    public let personas: [String]; public let mode: String; public let rounds: Int; public let revision: Int
    public let memories: [MobileMemory]
}
public struct DiscussionProvenance: Codable, Equatable, Sendable {
    public let resultId: String; public let problemId: String; public let discussionId: String
    public let artifactIds: [String]; public let inputRevision: Int; public let summaryVersion: Int
    public let correctionVersion: Int; public let recordedAt: String; public let mentors: [String]; public let topic: String
    public var savedDate: String; public var editedByUser: Bool
}
public struct MobileResultValidation: Codable, Equatable, Sendable {
    public let resultId: String; public let text: String; public let provenance: DiscussionProvenance
    public let binding: String; public let validatedAt: Double
    public func validate(now: Date = Date()) throws {
        guard resultId == provenance.resultId, !text.isEmpty, text.count <= 6000,
              [resultId, provenance.problemId, provenance.discussionId].allSatisfy(MobileEndpoint.validIdentifier),
              (1...5).contains(provenance.artifactIds.count), provenance.artifactIds.allSatisfy(MobileEndpoint.validIdentifier),
              (1...5).contains(provenance.mentors.count), provenance.mentors.allSatisfy(MobileEndpoint.validIdentifier),
              provenance.inputRevision > 0, provenance.summaryVersion >= 0, provenance.correctionVersion >= 0,
              provenance.topic.count <= 200, provenance.recordedAt.count <= 50, binding.count == 64,
              abs(now.timeIntervalSince1970 - validatedAt) <= 60 else { throw MobileError.invalidResponse }
    }
}

public struct MobileRoundtableSource: Codable, Identifiable, Sendable {
    public let id: String
    public let kind: String
    public let text: String
    public let unavailable: String?
}
public struct MobileRoundtableProgress: Codable, Sendable {
    public let actor: String
    public let stage: String
    public let round: Int
}
public struct MobileRoundtableReadingState: Codable, Sendable {
    public var selectedIDs: [String]
    public var anchor: String?
    public init(selectedIDs: [String], anchor: String?) { self.selectedIDs = selectedIDs; self.anchor = anchor }
}
