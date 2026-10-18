import Foundation

public struct MobileMemoryOverview: Codable, Sendable {
    public let version: String
    public let updatedAt: String
    public let organizedAt: String?
    public let cacheExpiresAt: Double
    public let state: String
    public let jobStatus: String
    public let coverage: MobileOverviewCoverage
    public let categories: [MobileOverviewCategory]
    public var summaries: [MobileOverviewSummary]
    public var observations: [MobileOverviewObservation]
    public var comparisons: [MobileOverviewComparison]
    public var evidence: [MobileMemory]
    public var nextCursor: String?
    public let canOrganize: Bool
    public let organizeBinding: String
    public let organizeReason: String?

    public var isExpired: Bool { cacheExpiresAt <= Date().timeIntervalSince1970 }
    public var stateText: String {
        switch state {
        case "ready": return "已有整理结果"
        case "partial": return "部分已整理，仍有待更新或停止的条目"
        case "unorganized": return "已有原始记忆，尚未形成概览"
        case "empty": return "还没有可见记忆"
        case "permission_denied": return "当前资料使用范围不允许显示概览"
        case "failed": return "这次整理未完成；有效旧结果仍可查看"
        default: return "依据已变化，旧认识暂不展示"
        }
    }
    public var organizationText: String {
        switch organizeReason {
        case "paused": return "整理已暂停，可在资料使用设置中核对。"
        case "restoring": return "正在恢复记忆，稍后重新读取。"
        case "permission_denied": return "请先核对资料使用授权。"
        case "already_pending": return "已排队或正在整理；服务会按预算分批继续，停止的历史项保持停止。"
        case "no_eligible_work": return "当前没有可请求的新整理内容。"
        default: return "请求后在已授权范围与预算内分批整理。"
        }
    }
    public var themeSummaries: [MobileOverviewSummary] {
        summaries.filter { $0.scope == "shared" && !$0.states.contains("observation") }
    }
    public var mentorSummaries: [MobileOverviewSummary] {
        summaries.filter { $0.scope == "persona" || $0.states.contains("observation") }
    }
    public func validated(now: Date = Date()) throws -> Self {
        let themeIDs = Set(["preferences", "goals", "work", "relationships", "events", "other"])
        let states = Set(["ready", "partial", "unorganized", "empty", "permission_denied", "failed", "invalid"])
        guard Self.hash(version), Self.hash(organizeBinding), states.contains(state),
              Self.date(updatedAt) != nil, organizedAt == nil || Self.date(organizedAt!) != nil,
              cacheExpiresAt.isFinite, cacheExpiresAt > now.timeIntervalSince1970,
              cacheExpiresAt <= now.timeIntervalSince1970 + 901,
              cacheExpiresAt <= (Self.date(updatedAt)?.timeIntervalSince1970 ?? 0) + 901,
              categories.count == 6, Set(categories.map(\.id)) == themeIDs,
              categories.allSatisfy({ $0.summaryCount >= 0 && !$0.title.isEmpty }), coverage.valid,
              summaries.count + observations.count + comparisons.count <= 20,
              ["idle", "pending", "processing", "ready", "failed"].contains(jobStatus),
              !canOrganize || (organizeReason == nil && !["permission_denied", "empty"].contains(state)),
              nextCursor == nil || Self.cursor(nextCursor!),
              Set((summaries.map(\.id) + observations.map(\.id) + comparisons.map(\.id))).count == summaries.count + observations.count + comparisons.count,
              Set(evidence.map(\.id)).count == evidence.count else { throw MobileError.invalidResponse }
        let known = Dictionary(uniqueKeysWithValues: evidence.map { ($0.id, $0) })
        var referenced = Set<String>()
        for item in summaries {
            guard (themeIDs.contains(item.category) || item.category == "observations"),
                  !item.title.isEmpty, item.title.count <= 40, !item.summary.isEmpty, item.summary.count <= 300 else { throw MobileError.invalidResponse }
            try validateRefs(item.evidenceIds, scope: item.scope, persona: item.personaId, known: known)
            referenced.formUnion(item.evidenceIds)
        }
        for item in observations {
            guard item.supportIds.count >= 2, Set(item.supportIds).isDisjoint(with: item.counterIds),
                  !item.summary.isEmpty, item.summary.count <= 240, !item.limitation.isEmpty, item.limitation.count <= 180 else { throw MobileError.invalidResponse }
            let ids = item.supportIds + item.counterIds
            try validateRefs(ids, scope: item.scope, persona: item.personaId, known: known); referenced.formUnion(ids)
        }
        for item in comparisons {
            guard item.evidenceIds.count == 2, ["duplicate", "conflict", "update", "related"].contains(item.kind),
                  !item.reason.isEmpty, item.reason.count <= 240 else { throw MobileError.invalidResponse }
            try validateRefs(item.evidenceIds, scope: item.scope, persona: item.personaId, known: known)
            referenced.formUnion(item.evidenceIds)
        }
        guard referenced == Set(known.keys) else { throw MobileError.invalidResponse }
        return self
    }
    private func validateRefs(_ ids: [String], scope: String, persona: String?, known: [String: MobileMemory]) throws {
        guard !ids.isEmpty, ids.count <= 20, Set(ids).count == ids.count,
              (scope == "shared" && persona == nil) || (scope == "persona" && Self.personas.contains(persona ?? "")),
              ids.allSatisfy({ id in
                  guard let item = known[id] else { return false }
                  return MobileEndpoint.validIdentifier(id) && item.scope == scope && item.personaId == persona
                      && item.privacy == "cloud" && item.status == "active" && !["source_invalid", "deleted", "pending"].contains(item.state)
                      && !item.content.isEmpty && item.content.count <= 2000
              }) else { throw MobileError.invalidResponse }
    }
    public func appending(_ page: Self) throws -> Self {
        guard version == page.version, organizeBinding == page.organizeBinding,
              categories == page.categories, coverage == page.coverage,
              Set(summaries.map(\.id)).isDisjoint(with: page.summaries.map(\.id)),
              Set(observations.map(\.id)).isDisjoint(with: page.observations.map(\.id)),
              Set(comparisons.map(\.id)).isDisjoint(with: page.comparisons.map(\.id)) else { throw MobileError.server(409, "memory_overview_changed") }
        var result = self
        result.summaries += page.summaries; result.observations += page.observations; result.comparisons += page.comparisons
        var known = Dictionary(uniqueKeysWithValues: evidence.map { ($0.id, $0) })
        for item in page.evidence {
            guard known[item.id] == nil || known[item.id]?.version == item.version else { throw MobileError.invalidResponse }
            known[item.id] = item
        }
        result.evidence = known.values.sorted { $0.id < $1.id }; result.nextCursor = page.nextCursor
        return result
    }
    public func retainingEntries(from previous: Self) -> Self {
        guard version == previous.version else { return self }
        var result = self
        result.summaries = previous.summaries; result.observations = previous.observations
        result.comparisons = previous.comparisons; result.evidence = previous.evidence; result.nextCursor = previous.nextCursor
        return result
    }
    public static let personas = ["gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"]
    private static func hash(_ value: String) -> Bool { value.count == 64 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) } }
    private static func cursor(_ value: String) -> Bool { !value.isEmpty && value.count <= 512 && value.utf8.allSatisfy { (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 || $0 == 95 } }
    private static func date(_ value: String) -> Date? {
        let formatter = ISO8601DateFormatter(); formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }
}

public struct MobileOverviewCoverage: Codable, Equatable, Sendable {
    public let eligible: Int, organized: Int, unorganized: Int, stopped: Int, invalid: Int
    public var valid: Bool { [eligible, organized, unorganized, stopped, invalid].allSatisfy { $0 >= 0 } && organized + unorganized + stopped == eligible }
}
public struct MobileOverviewCategory: Codable, Identifiable, Equatable, Sendable {
    public let id: String, title: String
    public let summaryCount: Int
}
public struct MobileOverviewSummary: Codable, Identifiable, Sendable {
    public let id: String, category: String, title: String, summary: String, scope: String
    public let personaId: String?
    public let states: [String], evidenceIds: [String]
    public let updatedAt: String?
    public var stateText: String {
        states.map { ["current": "来源有效", "historical": "历史阶段", "superseded": "已变化", "plan": "计划", "time_bound": "阶段性内容", "observation": "待验证观察", "uncertain": "尚不确定", "disputed": "待核对"][$0] ?? "需要核对" }.joined(separator: " · ")
    }
}
public struct MobileOverviewObservation: Codable, Identifiable, Sendable {
    public let id: String, scope: String, summary: String, limitation: String
    public let personaId: String?
    public let supportIds: [String], counterIds: [String]
}
public struct MobileOverviewComparison: Codable, Identifiable, Sendable {
    public let id: String, scope: String, kind: String, reason: String
    public let personaId: String?
    public let evidenceIds: [String]
    public var title: String { ["duplicate": "可能重复", "conflict": "需要核对的差异", "update": "可能是状态变化", "related": "相关但应分别保留"][kind] ?? "待复核" }
}
