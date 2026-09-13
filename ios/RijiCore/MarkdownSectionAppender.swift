import Foundation

public struct MarkdownHeading: Equatable, Sendable {
    public let title: String
    public let level: Int
    public let start: Int
    public let end: Int
}

public enum MarkdownSectionAppender {
    public static func headings(in data: Data) throws -> [MarkdownHeading] {
        guard String(data: data, encoding: .utf8) != nil else { throw JournalError.invalidMarkdown }
        let bytes = Array(data)
        var headings: [MarkdownHeading] = []
        var offset = 0
        var fence: (marker: UInt8, count: Int)?
        var frontmatter = false
        var comment = false
        while offset < bytes.count {
            let start = offset
            while offset < bytes.count && bytes[offset] != 10 { offset += 1 }
            let raw = Array(bytes[start..<offset])
            if offset < bytes.count { offset += 1 }
            let line = raw.last == 13 ? Array(raw.dropLast()) : raw
            if start == 0 && line == Array("---".utf8) { frontmatter = true; continue }
            if frontmatter {
                if line == Array("---".utf8) || line == Array("...".utf8) { frontmatter = false }
                continue
            }
            if comment { _ = consumeComments(line, inside: &comment); continue }
            let spaces = line.prefix(while: { $0 == 32 }).count
            guard spaces <= 3 else { continue }
            let content = Array(line.dropFirst(spaces))
            if let open = fence {
                let run = content.prefix(while: { $0 == open.marker }).count
                if run >= open.count && content.dropFirst(run).allSatisfy({ $0 == 32 || $0 == 9 }) { fence = nil }
                continue
            }
            if let first = content.first, first == 96 || first == 126 {
                let run = content.prefix(while: { $0 == first }).count
                if run >= 3 && (first == 126 || !content.dropFirst(run).contains(96)) {
                    fence = (first, run)
                    continue
                }
            }
            if consumeComments(line, inside: &comment) { continue }
            if let heading = parseHeading(content, start: start, end: offset) { headings.append(heading) }
        }
        return headings
    }

    private static func consumeComments(_ line: [UInt8], inside: inout Bool) -> Bool {
        var touched = inside
        var index = 0
        while index < line.count {
            let marker = Array((inside ? "-->" : "<!--").utf8)
            if line[index...].starts(with: marker) {
                inside.toggle()
                touched = true
                index += marker.count
            } else { index += 1 }
        }
        return touched
    }

    private static func validateAnchors(_ before: [MarkdownHeading], in data: Data, insertion: Int, delta: Int) throws {
        let after = try headings(in: data)
        for heading in before {
            let expectedStart = heading.start + (heading.start >= insertion ? delta : 0)
            guard after.contains(where: { $0.start == expectedStart && $0.level == heading.level &&
                $0.title.utf8.elementsEqual(heading.title.utf8) }) else { throw JournalError.invalidMarkdown }
            let oldCount = before.filter { $0.title.utf8.elementsEqual(heading.title.utf8) }.count
            let newCount = after.filter { $0.title.utf8.elementsEqual(heading.title.utf8) }.count
            guard newCount <= oldCount else { throw JournalError.ambiguousSection }
        }
    }

    private static func parseHeading(_ line: [UInt8], start: Int, end: Int) -> MarkdownHeading? {
        let level = line.prefix(while: { $0 == 35 }).count
        guard (1...6).contains(level), line.count == level || line[level] == 32 || line[level] == 9 else { return nil }
        var title = String(decoding: line.dropFirst(level), as: UTF8.self).trimmingCharacters(in: .whitespaces)
        let suffix = title.reversed().prefix(while: { $0 == "#" }).count
        if suffix > 0 {
            let prefix = String(title.dropLast(suffix))
            if prefix.isEmpty || prefix.last == " " || prefix.last == "\t" {
                title = prefix.trimmingCharacters(in: .whitespaces)
            }
        }
        return MarkdownHeading(title: title, level: level, start: start, end: end)
    }

    public static func append(text: String, to section: String, in data: Data) throws -> Data {
        let headings = try headings(in: data)
        let matches = headings.filter { $0.title.utf8.elementsEqual(section.utf8) }
        guard !matches.isEmpty else { throw JournalError.missingSection }
        guard matches.count == 1, let heading = matches.first else { throw JournalError.ambiguousSection }
        let end = headings.first { $0.start > heading.start && $0.level <= heading.level }?.start ?? data.count
        let prefix = data.prefix(end)
        let newline = data.contains(13) && data.contains(10) ? "\r\n" : "\n"
        let leading = prefix.isEmpty || prefix.last == 10 ? "" : newline
        let trailing = text.hasSuffix("\n") ? "" : newline
        var result = Data(prefix)
        result.append(Data((leading + text + trailing).utf8))
        result.append(data.suffix(from: end))
        try validateAnchors(headings, in: result, insertion: end, delta: result.count - data.count)
        return result
    }
}
