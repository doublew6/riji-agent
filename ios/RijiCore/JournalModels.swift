import Foundation
import CryptoKit

public enum JournalError: Error, LocalizedError, Equatable {
    case protectedMetadata, mitBodyChanged
    case invalidDay, invalidTemplate, invalidMarkdown, missingSection, ambiguousSection
    case changedSource, operationMismatch, pendingOperation, storage, noDiary, invalidDefinition, scanLimit

    public var errorDescription: String? {
        switch self {
        case .mitBodyChanged: return "MIT 正文已手动修改，与原确认记录不同。请先预览并确认新的计划，再记录实际反馈。原计划与正文均保留。"
        case .protectedMetadata: return "文档属性与来源标记需要保留。请在正文中编辑；双方版本均已保留。"
        case .scanLimit: return "文件超过本次本地扫描的大小上限。原文件保持不变，请缩小资料范围。"
        case .invalidDefinition: return "请补齐定义、完成标准或实际反馈；测量值需要注明来源和截止时间。输入已保留。"
        case .invalidDay: return "日期无效，请重新选择日期。"
        case .invalidTemplate: return "模板含有不支持的内容，草稿已保留。"
        case .invalidMarkdown: return "无法安全追加，请检查本段与日记中的标题、代码块或注释。输入和原文件均已保留。"
        case .missingSection: return "找不到目标区块，草稿已保留。请选择现有区块。"
        case .ambiguousSection: return "目标区块有重复标题，草稿已保留，无法安全追加。"
        case .changedSource: return "日记版本已变化，草稿已保留。请核对当前正文后再保存。"
        case .operationMismatch: return "这次保存的内容与原确认不一致，已停止写入。"
        case .pendingOperation: return "上次保存尚待核对，请先重试原来的保存。"
        case .storage: return "手机存储暂不可用，请检查可用空间并重试。尚未确认完成的文字会留在输入区。"
        case .noDiary: return "这一天还没有已保存的日记。保存一段文字后即可导出。"
        }
    }
}

public struct JournalDay: Hashable, Codable, Comparable, Identifiable, Sendable {
    public let value: String
    public var id: String { value }

    public init(_ value: String) throws {
        let formatter = Self.formatter(timeZone: TimeZone(secondsFromGMT: 0)!)
        guard value.count == 10, let date = formatter.date(from: value),
              formatter.string(from: date) == value else { throw JournalError.invalidDay }
        self.value = value
    }

    public init(date: Date, timeZone: TimeZone) {
        value = Self.formatter(timeZone: timeZone).string(from: date)
    }

    public func date(in timeZone: TimeZone) -> Date {
        Self.formatter(timeZone: timeZone).date(from: value)!
    }

    public static func < (lhs: Self, rhs: Self) -> Bool { lhs.value < rhs.value }

    public init(from decoder: Decoder) throws {
        try self.init(decoder.singleValueContainer().decode(String.self))
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(value)
    }

    private static func formatter(timeZone: TimeZone) -> DateFormatter {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.isLenient = false
        return formatter
    }
}

public enum SourceHash {
    public static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}

public struct JournalDraft: Codable, Equatable, Sendable {
    public let operationID: UUID
    public let day: JournalDay
    public var section: String
    public var text: String
    public let sourceHash: String?
    public let effectiveTemplate: String
    public let templateHash: String
    public var voiceInsertions: [VoiceInsertionStamp]?

    public init(day: JournalDay, section: String, text: String = "", sourceHash: String?,
                effectiveTemplate: String) {
        self.operationID = UUID()
        self.day = day
        self.section = section
        self.text = text
        self.sourceHash = sourceHash
        self.effectiveTemplate = effectiveTemplate
        self.templateHash = SourceHash.digest(Data(effectiveTemplate.utf8))
    }

    public static func == (lhs: JournalDraft, rhs: JournalDraft) -> Bool {
        lhs.operationID == rhs.operationID && lhs.day == rhs.day && lhs.sourceHash == rhs.sourceHash &&
            lhs.voiceInsertions == rhs.voiceInsertions && lhs.templateHash == rhs.templateHash && lhs.text.utf8.elementsEqual(rhs.text.utf8) &&
            lhs.section.utf8.elementsEqual(rhs.section.utf8) &&
            lhs.effectiveTemplate.utf8.elementsEqual(rhs.effectiveTemplate.utf8)
    }

    /// Editing creates a new candidate operation; an unchanged retry keeps its identity.
    public func updating(text: String, section: String) -> JournalDraft {
        guard !self.text.utf8.elementsEqual(text.utf8) || !self.section.utf8.elementsEqual(section.utf8) else { return self }
        var updated = JournalDraft(day: day, section: section, text: text, sourceHash: sourceHash,
                                   effectiveTemplate: effectiveTemplate)
        updated.voiceInsertions = voiceInsertions
        return updated
    }
}

public struct JournalReceipt: Codable, Equatable, Sendable {
    public let operationID: UUID
    public let payloadHash: String
    public let day: JournalDay
    public let afterHash: String
    public var voiceInsertions: [VoiceInsertionStamp]?
}

struct JournalTransaction: Codable, Sendable {
    let draft: JournalDraft
    let payloadHash: String
    let beforeHash: String?
    let afterHash: String
    let afterContent: Data
}

public struct JournalSnapshot: Sendable {
    public let day: JournalDay
    public let markdown: String
    public let sourceHash: String?
    public let draft: JournalDraft?
    public let isPending: Bool
    public var cloudConflict: CloudReview? = nil
    public var cloudHoldFailed = false
    public var isSaved: Bool { sourceHash != nil }
}

public struct JournalDirectoryEntry: Identifiable, Sendable {
    public let day: JournalDay
    public let hasDiary: Bool
    public let hasDraft: Bool
    public var id: String { day.value }
}
