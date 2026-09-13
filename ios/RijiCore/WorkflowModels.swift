import Foundation

public enum TemplateKind: String, Codable, CaseIterable, Sendable, Identifiable {
    case daily, weekly, monthly
    public var id: String { rawValue }
    public var label: String { self == .daily ? "日模板" : self == .weekly ? "周模板" : "月模板" }
}

public enum WeekdayLanguage: String, Codable, CaseIterable, Sendable, Identifiable {
    case chinese, english
    public var id: String { rawValue }
    public var label: String { self == .chinese ? "中文（星期一）" : "English (Monday)" }

    func weekday(for day: JournalDay) -> String {
        let zone = TimeZone(secondsFromGMT: 0)!
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = zone
        let index = calendar.component(.weekday, from: day.date(in: zone)) - 1
        let names = self == .chinese
            ? ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"]
            : ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        return names[index]
    }
}

/// Raw UTF-8 bytes are retained independently of safe, literal substitutions.
public struct TemplateVersion: Codable, Identifiable, Equatable, Sendable {
    public let kind: TemplateKind
    public let raw: Data
    public let isInitial: Bool
    public var id: String { "\(kind.rawValue)-\(SourceHash.digest(raw))" }
    public var source: String { String(decoding: raw, as: UTF8.self) }
    public var label: String { isInitial ? DailyTemplate.label : "已导入\(kind.label) · \(SourceHash.digest(raw).prefix(8))" }
    private static let supportedTokens = ["{{date}}", "{{title}}", "{{date:YYYY-MM-DD}}", "{{date:dddd}}"]
    public var warnings: [String] { Self.warnings(in: source) }
    public static func warnings(in source: String) -> [String] {
        let bytes = Array(source.utf8)
        let values = TemplateSyntax.parts(in: bytes).compactMap { part -> String? in
            let value = String(decoding: bytes[part.range], as: UTF8.self)
            if part.isScriptFence { return "未支持的 Dataview 脚本区块将按原文保留，不会执行。" }
            return Self.supportedTokens.contains(value) ? nil : value
        }
        return Array(Set(values)).sorted()
    }

    public init(kind: TemplateKind, raw: Data, isInitial: Bool = false) throws {
        guard !raw.isEmpty, raw.count <= 262_144, let text = String(data: raw, encoding: .utf8),
              !text.contains("\0"), !text.contains("<!-- riji-record:") else { throw JournalError.invalidTemplate }
        self.kind = kind; self.raw = raw; self.isInitial = isInitial
    }

    public func instantiate(day: JournalDay, title: String? = nil, weekdayLanguage: WeekdayLanguage = .chinese) -> String {
        let replacements = ["{{date}}": day.value, "{{title}}": title ?? day.value,
                            "{{date:YYYY-MM-DD}}": day.value, "{{date:dddd}}": weekdayLanguage.weekday(for: day)]
        let bytes = Array(raw)
        var rendered = Data(); var cursor = 0
        // Scan source boundaries once; unsupported expressions are opaque, even when unfinished.
        for part in TemplateSyntax.parts(in: bytes) {
            rendered.append(contentsOf: bytes[cursor..<part.range.lowerBound])
            let token = String(decoding: bytes[part.range], as: UTF8.self)
            rendered.append(contentsOf: replacements[token].map { Array($0.utf8) } ?? Array(bytes[part.range]))
            cursor = part.range.upperBound
        }
        rendered.append(contentsOf: bytes[cursor...])
        return String(decoding: rendered, as: UTF8.self)
    }

    /// Missing-field operations must retain the pre-upgrade after bytes and retry identity.
    func instantiateLegacy(day: JournalDay) -> String {
        source.replacingOccurrences(of: "{{date}}", with: day.value)
            .replacingOccurrences(of: "{{title}}", with: day.value)
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
    // Synthesized Codable omits nil, preserving legacy encoded payload hashes.
    public let renderedContent: String?
    public var preview: String { renderedContent ?? template.instantiateLegacy(day: day) }

    public init(operationID: UUID, day: JournalDay, template: TemplateVersion, renderedContent: String? = nil) {
        self.operationID = operationID; self.day = day; self.template = template; self.renderedContent = renderedContent
    }
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

/// Only lexical boundaries are recognized. Script contents are never interpreted or executed.
private enum TemplateSyntax {
    struct Part { let range: Range<Int>; var isScriptFence = false }

    static func parts(in bytes: [UInt8]) -> [Part] {
        var result = [Part](); var cursor = 0
        while cursor < bytes.count {
            if (cursor == 0 || bytes[cursor - 1] == 10), let end = scriptFenceEnd(bytes, at: cursor) {
                result.append(Part(range: cursor..<end, isScriptFence: true)); cursor = end; continue
            }
            let end: Int?
            if starts(bytes, cursor, "{{") { end = braceEnd(bytes, at: cursor) }
            else if starts(bytes, cursor, "${") { end = braceEnd(bytes, at: cursor + 1) }
            else if starts(bytes, cursor, "<%") { end = scriptEnd(bytes, at: cursor) }
            else if starts(bytes, cursor, "`=") { end = inlineEnd(bytes, at: cursor) }
            else { end = nil }
            if let end { result.append(Part(range: cursor..<end)); cursor = end }
            else { cursor += 1 }
        }
        return result
    }

    private static func starts(_ bytes: [UInt8], _ offset: Int, _ marker: String) -> Bool {
        bytes[offset...].starts(with: marker.utf8)
    }

    private static func quotedEnd(_ bytes: [UInt8], at start: Int) -> Int {
        let quote = bytes[start]; var cursor = start + 1
        while cursor < bytes.count {
            if bytes[cursor] == 92 { cursor = min(bytes.count, cursor + 2); continue }
            if bytes[cursor] == quote { return cursor + 1 }
            cursor += 1
        }
        return bytes.count
    }

    private static func braceEnd(_ bytes: [UInt8], at start: Int) -> Int {
        var cursor = start; var depth = 0
        while cursor < bytes.count {
            let byte = bytes[cursor]
            if let end = commentEnd(bytes, at: cursor) { cursor = end; continue }
            if [UInt8(34), 39, 96].contains(byte) { cursor = quotedEnd(bytes, at: cursor); continue }
            if byte == 123 { depth += 1 }
            if byte == 125 { depth -= 1; if depth == 0 { return cursor + 1 } }
            cursor += 1
        }
        return bytes.count
    }

    private static func scriptEnd(_ bytes: [UInt8], at start: Int) -> Int {
        var cursor = start + 2; var depth = 1
        while cursor < bytes.count {
            if let end = commentEnd(bytes, at: cursor) { cursor = end; continue }
            if [UInt8(34), 39, 96].contains(bytes[cursor]) { cursor = quotedEnd(bytes, at: cursor); continue }
            if starts(bytes, cursor, "<%") { depth += 1; cursor += 2 }
            else if starts(bytes, cursor, "%>") {
                depth -= 1; cursor += 2; if depth == 0 { return cursor }
            } else { cursor += 1 }
        }
        return bytes.count
    }

    private static func commentEnd(_ bytes: [UInt8], at start: Int) -> Int? {
        if starts(bytes, start, "//") { return lineEnd(bytes, at: start) }
        guard starts(bytes, start, "/*") else { return nil }
        var cursor = start + 2
        while cursor < bytes.count {
            if starts(bytes, cursor, "*/") { return cursor + 2 }
            cursor += 1
        }
        return bytes.count
    }

    private static func inlineEnd(_ bytes: [UInt8], at start: Int) -> Int {
        var cursor = start + 2
        while cursor < bytes.count {
            if bytes[cursor] == 92 { cursor = min(bytes.count, cursor + 2); continue }
            if bytes[cursor] == 96 { return cursor + 1 }
            if [UInt8(34), 39].contains(bytes[cursor]) { cursor = quotedEnd(bytes, at: cursor) }
            else { cursor += 1 }
        }
        return bytes.count
    }

    private static func scriptFenceEnd(_ bytes: [UInt8], at start: Int) -> Int? {
        let end = lineEnd(bytes, at: start)
        let line = Array(bytes[start..<end].drop(while: { $0 == 32 }))
        let spaces = bytes[start..<end].prefix(while: { $0 == 32 }).count
        guard spaces <= 3, let marker = line.first, marker == 96 || marker == 126 else { return nil }
        let count = line.prefix(while: { $0 == marker }).count
        let info = String(decoding: line.dropFirst(count), as: UTF8.self).split(whereSeparator: { $0.isWhitespace }).first?.lowercased()
        guard count >= 3, info == "dataview" || info == "dataviewjs" else { return nil }
        var cursor = end
        while cursor < bytes.count {
            let next = lineEnd(bytes, at: cursor)
            let closing = bytes[cursor..<next].drop(while: { $0 == 32 })
            let indent = bytes[cursor..<next].prefix(while: { $0 == 32 }).count
            let run = closing.prefix(while: { $0 == marker }).count
            if indent <= 3, run >= count, closing.dropFirst(run).allSatisfy({ [UInt8(9), 10, 13, 32].contains($0) }) { return next }
            cursor = next
        }
        return bytes.count
    }

    private static func lineEnd(_ bytes: [UInt8], at start: Int) -> Int {
        var cursor = start
        while cursor < bytes.count && bytes[cursor] != 10 { cursor += 1 }
        return cursor < bytes.count ? cursor + 1 : cursor
    }
}
