import Foundation

public enum TemplateKind: String, Codable, CaseIterable, Sendable, Identifiable {
    case daily, weekly, monthly
    public var id: String { rawValue }
    public var label: String { self == .daily ? "日模板" : self == .weekly ? "周模板" : "月模板" }
}

/// Raw UTF-8 bytes are retained independently of safe, literal substitutions.
public struct TemplateVersion: Codable, Identifiable, Equatable, Sendable {
    public let kind: TemplateKind
    public let raw: Data
    public let isInitial: Bool
    public var id: String { "\(kind.rawValue)-\(SourceHash.digest(raw))" }
    public var source: String { String(decoding: raw, as: UTF8.self) }
    public var label: String { isInitial ? DailyTemplate.label : "已导入\(kind.label) · \(SourceHash.digest(raw).prefix(8))" }
    public var warnings: [String] {
        let pattern = #"\{\{[^}]*\}\}|<%[\s\S]*?%>|\$\{[^}]*\}|`=(?:[^`]+)`"#
        let regex = try! NSRegularExpression(pattern: pattern)
        var values = regex.matches(in: source, range: NSRange(source.startIndex..., in: source)).compactMap { match -> String? in
            guard let range = Range(match.range, in: source) else { return nil }
            let value = String(source[range])
            return ["{{date}}", "{{title}}"].contains(value) ? nil : value
        }
        let stripped = source.replacingOccurrences(of: "{{date}}", with: "").replacingOccurrences(of: "{{title}}", with: "")
        if stripped.contains("{{") || stripped.contains("<%") || stripped.contains("${") || stripped.contains("```dataview") {
            values.append("未支持的宏、脚本或插件字段将按原文保留，不会执行。")
        }
        return Array(Set(values)).sorted()
    }

    public init(kind: TemplateKind, raw: Data, isInitial: Bool = false) throws {
        guard !raw.isEmpty, raw.count <= 262_144, let text = String(data: raw, encoding: .utf8),
              !text.contains("\0"), !text.contains("<!-- riji-record:") else { throw JournalError.invalidTemplate }
        self.kind = kind; self.raw = raw; self.isInitial = isInitial
    }

    public func instantiate(day: JournalDay, title: String? = nil) -> String {
        // Never recursively interpret replacement values or evaluate template code.
        source.replacingOccurrences(of: "{{date}}", with: day.value)
            .replacingOccurrences(of: "{{title}}", with: title ?? day.value)
    }

    public static func initial(_ kind: TemplateKind) -> Self {
        let source = kind == .daily ? DailyTemplate.initial : "# {{title}}\n\n## 本人复盘\n\n## 下一步计划\n\n"
        return try! Self(kind: kind, raw: Data(source.utf8), isInitial: true)
    }
}

public struct ReviewPeriod: Codable, Equatable, Hashable, Identifiable, Sendable {
    public let kind: TemplateKind
    public let start: JournalDay
    public let end: JournalDay
    public var id: String { "\(kind.rawValue)-\(start.value)" }
    public var title: String { "\(kind == .weekly ? "周报" : "月报") \(start.value) — \(end.value)" }
    public init(kind: TemplateKind, containing day: JournalDay, timeZone: TimeZone) throws {
        guard kind != .daily else { throw JournalError.invalidDay }
        var calendar = Calendar(identifier: kind == .weekly ? .iso8601 : .gregorian)
        calendar.timeZone = timeZone
        guard let interval = calendar.dateInterval(of: kind == .weekly ? .weekOfYear : .month, for: day.date(in: timeZone)),
              let last = calendar.date(byAdding: .day, value: -1, to: interval.end) else { throw JournalError.invalidDay }
        self.kind = kind
        start = JournalDay(date: interval.start, timeZone: timeZone)
        end = JournalDay(date: last, timeZone: timeZone)
    }
    public func days(timeZone: TimeZone) -> [JournalDay] {
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = timeZone
        var values = [JournalDay](); var date = start.date(in: timeZone)
        while values.count < 31 {
            let day = JournalDay(date: date, timeZone: timeZone)
            guard day <= end else { break }
            values.append(day)
            date = calendar.date(byAdding: .day, value: 1, to: date)!
        }
        return values
    }
}

public struct ReviewSource: Codable, Equatable, Identifiable, Sendable {
    public let day: JournalDay
    public let hash: String
    public var id: String { "daily/\(day.value)" }
}

public enum ReviewPhase: String, Sendable { case future = "尚未开始", current = "进行中", historical = "历史周期" }

public struct ReviewDraft: Codable, Equatable, Sendable {
    public var operationID: UUID
    public let period: ReviewPeriod
    public var text: String
    public let template: TemplateVersion
    public let sources: [ReviewSource]
    public let missingDays: [JournalDay]
    public let targetHash: String?
    public let asOf: JournalDay
    public let timeZone: String
    public var phase: ReviewPhase { asOf < period.start ? .future : (asOf <= period.end ? .current : .historical) }
    public var isCurrentPeriod: Bool { phase == .current }
    public func editing(_ text: String) -> Self {
        var copy = self
        if !self.text.utf8.elementsEqual(text.utf8) { copy.operationID = UUID(); copy.text = text }
        return copy
    }
}

public struct NorthStarDefinition: Codable, Equatable, Identifiable, Sendable {
    public let id: UUID
    public let previousID: UUID?
    public var name: String
    public var definition: String
    public var unit: String
    public var target: String
    public var period: String
    public var stage: String
    public var measurement: String?
    public var measurementSource: String?
    public let day: JournalDay
    public init(previousID: UUID? = nil, name: String, definition: String, unit: String, target: String,
                period: String, stage: String, measurement: String? = nil, measurementSource: String? = nil, day: JournalDay) {
        id = UUID(); self.previousID = previousID; self.name = name; self.definition = definition
        self.unit = unit; self.target = target; self.period = period; self.stage = stage
        self.measurement = measurement; self.measurementSource = measurementSource; self.day = day
    }
}

public enum MITStatus: String, Codable, CaseIterable, Sendable { case completed = "完成", partial = "部分完成", notDone = "未做" }
public struct MITEvent: Codable, Equatable, Identifiable, Sendable {
    public let id: UUID
    public let day: JournalDay
    public let replaces: UUID?
    public let goalID: UUID?
    public let action: String
    public let criterion: String
    public let rationale: String
    public let effort: String
    public let feedbackFor: UUID?
    public let status: MITStatus?
    public let result: String
    public init(day: JournalDay, replaces: UUID? = nil, goalID: UUID? = nil, action: String = "", criterion: String = "",
                rationale: String = "", effort: String = "", feedbackFor: UUID? = nil, status: MITStatus? = nil, result: String = "") {
        id = UUID(); self.day = day; self.replaces = replaces; self.goalID = goalID; self.action = action
        self.criterion = criterion; self.rationale = rationale; self.effort = effort
        self.feedbackFor = feedbackFor; self.status = status; self.result = result
    }
}

public struct MITDraft: Codable, Equatable, Sendable {
    public let event: MITEvent
    public let section: String
    public let sourceHash: String?
    public let effectiveTemplate: String
    public var operationID: UUID { event.id }
}

public struct BlankDayDraft: Codable, Equatable, Sendable {
    public let operationID: UUID
    public let day: JournalDay
    public let template: TemplateVersion
}

public enum SavedFile: Codable, Hashable, Sendable {
    case daily(JournalDay), review(ReviewPeriod)
    public var date: JournalDay { switch self { case .daily(let day): return day; case .review(let period): return period.start } }
    public var title: String { switch self { case .daily(let day): return day.value; case .review(let period): return period.title } }
    public var label: String { switch self { case .daily: return "日记"; case .review(let p): return p.kind == .weekly ? "周报" : "月报" } }
    public var id: String { switch self { case .daily(let day): return "daily-\(day.value)"; case .review(let p): return p.id } }
}
public struct SearchMatch: Identifiable, Sendable {
    public let file: SavedFile
    public let snippet: String
    public var id: String { file.id }
}
public struct SearchResults: Sendable {
    public let matches: [SearchMatch]
    public let scanned: Int
    public let truncated: Bool
    public let skippedOversized: Int
}

// Only these application-owned targets can be written or recovered.
enum WorkflowTarget: Codable, Equatable {
    case daily(JournalDay), review(ReviewPeriod), template(TemplateKind, String), goal(UUID)
}
struct WorkflowTransaction: Codable {
    let id: UUID
    let payloadHash: String
    let target: WorkflowTarget
    let beforeHash: String?
    let afterHash: String
    let after: Data
}
struct WorkflowReceipt: Codable {
    let id: UUID
    let payloadHash: String
    let afterHash: String
}
