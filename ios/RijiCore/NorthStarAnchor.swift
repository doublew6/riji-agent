import Foundation

public enum NorthStarAnchorError: Error, LocalizedError {
    case invalid, stale, unavailable, storage, tooLarge
    public var errorDescription: String? {
        switch self {
        case .invalid: return "关联内容无效，请重新选择来源与段落。"
        case .stale: return "北极星来源已变化，请重新选择并确认本阶段方向。"
        case .unavailable: return "暂时无法核验北极星来源。请恢复目录访问或处理文件冲突后重试。"
        case .storage: return "关联尚未保存，请重试。原目标文件保持原样。"
        case .tooLarge: return "北极星来源文件超过 2 MiB，请选择不超过 2 MiB 的文本文件。"
        }
    }
}

/// Only a reference is persisted. Goal prose is always read from its original source.
public struct NorthStarAnchor: Codable, Equatable, Sendable {
    public enum Source: Codable, Equatable, Sendable {
        case definition(UUID, hash: String)
        case paragraph(path: [String], start: Int, end: Int, hash: String)
    }
    public let scope: String
    public let source: Source
    public init(scope: String, source: Source) throws {
        self.scope = scope; self.source = source
        try validate()
    }
    public func validate() throws {
        guard Self.validHash(scope) else { throw NorthStarAnchorError.invalid }
        switch source {
        case .definition(_, let hash):
            guard Self.validHash(hash) else { throw NorthStarAnchorError.invalid }
        case .paragraph(let path, let start, let end, let hash):
            guard Self.validPath(path), Self.validHash(hash), start >= 0, end > start,
                  end <= NorthStarParagraph.maximumFileBytes,
                  end - start <= NorthStarParagraph.maximumParagraphBytes else { throw NorthStarAnchorError.invalid }
        }
    }
    public static func scope(source: String, account: String) -> String {
        SourceHash.digest(Data((String(source.utf8.count) + ":" + source + account).utf8))
    }
    public static func definitionHash(_ value: NorthStarDefinition) throws -> String {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        return SourceHash.digest(try encoder.encode(value))
    }
    public static func validPath(_ path: [String]) -> Bool {
        !path.isEmpty && path.count <= 32 && path.joined(separator: "/").utf8.count <= 4096 && path.allSatisfy {
            !$0.isEmpty && $0 != "." && $0 != ".." && !$0.contains("/") && !$0.contains("\0") && !$0.hasPrefix(".")
        } && ["md", "markdown", "txt"].contains((path.last! as NSString).pathExtension.lowercased())
    }
    private static func validHash(_ value: String) -> Bool {
        value.utf8.count == 64 && value.allSatisfy { "0123456789abcdef".contains($0) }
    }
}

public struct NorthStarParagraph: Identifiable, Equatable, Sendable {
    public static let maximumFileBytes = 2 * 1024 * 1024
    public static let maximumParagraphBytes = 16 * 1024
    public let start: Int
    public let end: Int
    public let text: String
    public var id: Int { start }

    /// Offsets refer to exact UTF-8 bytes, including original CRLF and combining marks.
    public static func read(_ data: Data) throws -> [NorthStarParagraph] {
        guard data.count <= maximumFileBytes else { throw NorthStarAnchorError.tooLarge }
        guard String(data: data, encoding: .utf8) != nil else { throw NorthStarAnchorError.invalid }
        let bytes = [UInt8](data)
        var result: [NorthStarParagraph] = [], beginning: Int?, lineStart = 0
        for index in 0...bytes.count where index == bytes.count || bytes[index] == 10 {
            let line = bytes[lineStart..<index]
            let blank = line.allSatisfy { $0 == 13 || $0 == 32 || $0 == 9 }
            if blank, let start = beginning {
                append(data, start, lineStart, to: &result); beginning = nil
            } else if !blank && beginning == nil { beginning = lineStart }
            lineStart = index + 1
        }
        if let start = beginning { append(data, start, bytes.count, to: &result) }
        return result
    }
    private static func append(_ data: Data, _ start: Int, _ rawEnd: Int, to result: inout [NorthStarParagraph]) {
        var end = rawEnd
        while end > start && (data[end - 1] == 10 || data[end - 1] == 13) { end -= 1 }
        guard end > start, end - start <= maximumParagraphBytes else { return }
        result.append(NorthStarParagraph(start: start, end: end, text: String(decoding: data[start..<end], as: UTF8.self)))
    }
    public static func resolve(_ anchor: NorthStarAnchor, in data: Data) throws -> String {
        try anchor.validate()
        guard case .paragraph(_, let start, let end, let hash) = anchor.source,
              SourceHash.digest(data) == hash,
              let paragraph = try read(data).first(where: { $0.start == start && $0.end == end }) else { throw NorthStarAnchorError.stale }
        return paragraph.text
    }
}

public actor NorthStarAnchorStore {
    private let directory: URL
    private let failure: @Sendable () throws -> Void
    public init(directory: URL, failure: @escaping @Sendable () throws -> Void = {}) {
        self.directory = directory.appendingPathComponent("north-star-anchors"); self.failure = failure
    }
    public func read(scope: String) throws -> NorthStarAnchor? {
        let url = try location(scope)
        if FileManager.default.fileExists(atPath: directory.path) { try excludeFromBackup() }
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        do {
            let handle = try FileHandle(forReadingFrom: url)
            defer { try? handle.close() }
            let bytes = try handle.read(upToCount: 16_385) ?? Data()
            guard bytes.count <= 16_384 else { throw NorthStarAnchorError.invalid }
            let anchor = try JSONDecoder().decode(NorthStarAnchor.self, from: bytes)
            try anchor.validate()
            guard anchor.scope == scope else { throw NorthStarAnchorError.invalid }
            return anchor
        } catch { throw NorthStarAnchorError.invalid }
    }
    public func save(_ anchor: NorthStarAnchor) throws {
        try anchor.validate()
        do {
            try failure()
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            try excludeFromBackup()
            try JSONEncoder().encode(anchor).write(to: location(anchor.scope), options: .atomic)
        } catch { throw NorthStarAnchorError.storage }
    }
    public func remove(scope: String) throws {
        do {
            let url = try location(scope); try failure()
            if FileManager.default.fileExists(atPath: url.path) { try FileManager.default.removeItem(at: url) }
        } catch { throw NorthStarAnchorError.storage }
    }
    private func excludeFromBackup() throws {
        var values = URLResourceValues(); values.isExcludedFromBackup = true
        var folder = directory; try folder.setResourceValues(values)
    }
    private func location(_ scope: String) throws -> URL {
        _ = try NorthStarAnchor(scope: scope, source: .definition(UUID(), hash: scope))
        return directory.appendingPathComponent(scope + ".json")
    }
}
