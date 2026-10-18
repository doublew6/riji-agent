import Foundation

/// A display-only projection. Every range addresses the unchanged source in UTF-16 units.
/// Unsupported or incomplete constructs remain literal; this is not a Markdown serializer.
public struct MarkdownPresentation {
    public enum Style: Equatable { case heading(Int), bold, italic, strike, code, link }
    public struct Span {
        public let range: NSRange
        public let style: Style
    }
    public enum MarkerKind: Equatable { case bullet, task(Bool), quote, rule }
    public struct Marker {
        public let range: NSRange
        public let kind: MarkerKind
        public let taskState: Int?
    }
    public struct Table {
        public let range: NSRange
        public let rows: [TableRow]
        public let delimiter: NSRange
        public let alignments: [TableAlignment]
    }
    public struct TableRow {
        public let range: NSRange
        public let cells: [NSRange]
        /// Every actual source cell, including values beyond the displayed header width.
        public let sourceCells: [NSRange]
    }
    public enum TableAlignment { case left, center, right }
    public private(set) var tables: [Table] = []
    public private(set) var spans: [Span] = []
    public private(set) var hidden = IndexSet()
    public private(set) var markers: [Marker] = []
    public private(set) var savedAIResults: [SavedAIResult] = []
    private var literal = IndexSet()
    private var tableLiteral = IndexSet()
    private var delimiters = IndexSet()
    private let originalSource: String

    public init(_ source: String) {
        originalSource = source
        let full = NSRange(location: 0, length: (source as NSString).length)
        for range in BodyMarkdown.protectedRanges(in: source) {
            literal.insert(integersIn: range); tableLiteral.insert(integersIn: range)
        }
        for match in Self.matches(#"<%[\s\S]*?%>|\{\{[^\r\n]*?\}\}|<!--[\s\S]*?-->"#, source, full) {
            literal.insert(integersIn: match.range)
            if (source as NSString).substring(with: match.range).contains(where: { $0.isNewline }) {
                tableLiteral.insert(integersIn: match.range)
            }
        }
        parseTables(source)
        parseLines(source)
        savedAIResults = SavedAIResult.records(in: source)
    }

    /// Accessibility follows this exact display projection, including plain-text readers.
    /// A source change invalidates the old ranges until the next presentation is installed.
    public func readableMarkdown(in source: String) -> String {
        guard matchesSource(source) else { return source }
        return SavedAIResult.readableMarkdown(source, records: savedAIResults)
    }

    public func matchesSource(_ source: String) -> Bool {
        originalSource.utf8.elementsEqual(source.utf8)
    }

    /// Reveal every selected source line, including the caret's line while editing.
    public static func activeLines(in source: String, selection: NSRange, tables: [Table]? = nil) -> NSRange {
        let text = source as NSString
        let start = min(max(0, selection.location), text.length)
        let length = min(max(0, selection.length), text.length - start)
        let active = text.lineRange(for: NSRange(location: start, length: length))
        return (tables ?? MarkdownPresentation(source).tables).reduce(active) { range, table in
            NSIntersectionRange(range, table.range).length > 0 ? NSUnionRange(range, table.range) : range
        }
    }

    private mutating func parseTables(_ source: String) {
        let text = source as NSString
        var lines: [(NSRange, Bool)] = [], offset = 0, fence: (String, Int)?
        while offset < text.length {
            var end = 0, contentEnd = 0
            text.getLineStart(nil, end: &end, contentsEnd: &contentEnd, for: NSRange(location: offset, length: 0))
            let range = NSRange(location: offset, length: contentEnd - offset), line = text.substring(with: range)
            var eligible = fence == nil && !tableLiteral.intersects(integersIn: range) && !line.hasPrefix("    ") && !line.hasPrefix("\t")
            if let current = fence {
                if !Self.matches("^ {0,3}" + NSRegularExpression.escapedPattern(for: current.0) + "{\(current.1),}[ \t]*$", line, NSRange(location: 0, length: (line as NSString).length)).isEmpty { fence = nil }
            } else if let match = Self.matches(#"^ {0,3}(`{3,}|~{3,})"#, line, NSRange(location: 0, length: (line as NSString).length)).first {
                let marker = (line as NSString).substring(with: match.range(at: 1))
                fence = (String(marker.prefix(1)), marker.count); eligible = false
            }
            lines.append((range, eligible)); offset = end
        }
        var index = 0
        while index + 1 < lines.count {
            defer { index += 1 }
            guard lines[index].1, lines[index + 1].1,
                  let header = Self.cells(text, range: lines[index].0),
                  let separator = Self.cells(text, range: lines[index + 1].0), header.count == separator.count,
                  separator.allSatisfy({ !Self.matches(#"^:?-{3,}:?$"#, text.substring(with: $0).trimmingCharacters(in: .whitespaces), NSRange(location: 0, length: (text.substring(with: $0).trimmingCharacters(in: .whitespaces) as NSString).length)).isEmpty }) else { continue }
            let start = index, delimiter = lines[index + 1].0
            let alignments: [TableAlignment] = separator.map {
                let value = text.substring(with: $0).trimmingCharacters(in: .whitespaces)
                return value.hasSuffix(":") ? (value.hasPrefix(":") ? .center : .right) : .left
            }
            var rows = [TableRow(range: lines[index].0, cells: header, sourceCells: header)]
            index += 2
            while index < lines.count, lines[index].1, let cells = Self.cells(text, range: lines[index].0) {
                var displayed = Array(cells.prefix(header.count))
                while displayed.count < header.count {
                    displayed.append(NSRange(location: NSMaxRange(lines[index].0), length: 0))
                }
                rows.append(TableRow(range: lines[index].0, cells: displayed, sourceCells: cells)); index += 1
            }
            let end = NSMaxRange(rows.last!.range) > NSMaxRange(delimiter) ? NSMaxRange(rows.last!.range) : NSMaxRange(delimiter)
            tables.append(Table(range: NSRange(location: lines[start].0.location, length: end - lines[start].0.location), rows: rows, delimiter: delimiter, alignments: alignments))
            index -= 1
        }
    }

    /// Split only structural pipes, retaining empty cells and original UTF-16 offsets.
    private static func cells(_ text: NSString, range: NSRange) -> [NSRange]? {
        let units = Array(text.substring(with: range).utf16)
        let pipes = structuralPipes(in: units)
        guard !pipes.isEmpty else { return nil }
        let first = units.firstIndex(where: { $0 != 32 && $0 != 9 }), last = units.lastIndex(where: { $0 != 32 && $0 != 9 })
        var boundaries = [-1] + pipes + [units.count]
        if first == pipes.first { boundaries.removeFirst() }
        if last == pipes.last { boundaries.removeLast() }
        guard boundaries.count >= 2, boundaries.count <= 65 else { return nil }
        return zip(boundaries, boundaries.dropFirst()).map { left, right in
            NSRange(location: range.location + left + 1, length: right - left - 1)
        }
    }

    /// ASCII syntax is scanned as UTF-16, even when combined with a Unicode mark.
    static func structuralPipes(in units: [UInt16]) -> [Int] {
        let code = Dictionary(uniqueKeysWithValues: codeRuns(in: units).map { ($0.open, $0.close + $0.width) })
        var pipes: [Int] = [], index = 0
        while index < units.count {
            if units[index] == 92 { index += 2; continue }
            if let end = code[index] { index = end; continue }
            if units[index] == 91, index + 1 < units.count, units[index + 1] == 91 {
                var end = index + 2
                while end + 1 < units.count {
                    if units[end] == 92 { end += 2; continue }
                    if units[end] == 93, units[end + 1] == 93 { break }
                    end += 1
                }
                if end + 1 < units.count { index = end + 2; continue }
            }
            if units[index] == 124 { pipes.append(index) }
            index += 1
        }
        return pipes
    }

    /// Only a complete, unescaped run of exactly the opening width closes code.
    private static func codeRuns(in units: [UInt16]) -> [(open: Int, close: Int, width: Int)] {
        var runs: [(start: Int, width: Int)] = [], index = 0
        while index < units.count {
            guard units[index] == 96 else { index += 1; continue }
            let start = index
            while index < units.count, units[index] == 96 { index += 1 }
            var preceding = start
            while preceding > 0, units[preceding - 1] == 92 { preceding -= 1 }
            if (start - preceding) % 2 == 0 { runs.append((start, index - start)) }
        }
        var pairs: [(open: Int, close: Int, width: Int)] = [], run = 0
        while run < runs.count {
            if let close = runs.indices.dropFirst(run + 1).first(where: { runs[$0].width == runs[run].width }) {
                pairs.append((runs[run].start, runs[close].start, runs[run].width)); run = close + 1
            } else { run += 1 }
        }
        return pairs
    }

    private mutating func parseLines(_ source: String) {
        let text = source as NSString
        var offset = 0, fence: (String, Int)?
        while offset < text.length {
            var end = 0, contentsEnd = 0
            text.getLineStart(nil, end: &end, contentsEnd: &contentsEnd, for: NSRange(location: offset, length: 0))
            let range = NSRange(location: offset, length: contentsEnd - offset)
            offset = end
            if literal.intersects(integersIn: range) { continue }
            let line = text.substring(with: range)
            if let current = fence {
                spans.append(.init(range: range, style: .code))
                let pattern = "^ {0,3}" + NSRegularExpression.escapedPattern(for: current.0) + "{\(current.1),}[ \\t]*$"
                if !Self.matches(pattern, line, NSRange(location: 0, length: (line as NSString).length)).isEmpty { fence = nil }
                continue
            }
            if let match = Self.matches(#"^ {0,3}(`{3,}|~{3,})"#, line, NSRange(location: 0, length: (line as NSString).length)).first {
                let delimiter = (line as NSString).substring(with: match.range(at: 1))
                fence = (String(delimiter.prefix(1)), delimiter.count)
                spans.append(.init(range: range, style: .code)); continue
            }
            if line.hasPrefix("    ") || line.hasPrefix("\t") {
                // Indented code remains literal, including Obsidian/plugin syntax.
                spans.append(.init(range: range, style: .code)); continue
            }
            parseBlock(source, range)
            parseInline(source, range)
        }
    }

    private mutating func parseBlock(_ source: String, _ range: NSRange) {
        if !Self.matches(#"\G {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$"#, source, range).isEmpty {
            addMarker(range, .rule, nil); return
        }
        if let match = Self.matches(#"\G {0,3}(#{1,6})[ \t]+"#, source, range).first {
            spans.append(.init(range: range, style: .heading(match.range(at: 1).length)))
            hide(match.range)
        }
        if let match = Self.matches(#"\G {0,3}([-+*])[ \t]+\[([ xX])\][ \t]+"#, source, range).first {
            let start = match.range(at: 1).location
            let state = match.range(at: 2).location
            addMarker(NSRange(location: start, length: NSMaxRange(match.range) - start),
                      .task((source as NSString).substring(with: match.range(at: 2)) != " "), state)
        } else if let match = Self.matches(#"\G {0,3}([-+*])(?:[ \t]+|$)"#, source, range).first {
            let start = match.range(at: 1).location
            addMarker(NSRange(location: start, length: NSMaxRange(match.range) - start), .bullet, nil)
        } else if let match = Self.matches(#"\G {0,3}(>)[ \t]?"#, source, range).first {
            let start = match.range(at: 1).location
            addMarker(NSRange(location: start, length: NSMaxRange(match.range) - start), .quote, nil)
        }
    }

    private mutating func addMarker(_ range: NSRange, _ kind: MarkerKind, _ state: Int?) {
        hide(range)
        markers.append(.init(range: range, kind: kind, taskState: state))
    }

    private mutating func parseInline(_ source: String, _ range: NSRange) {
        let units = Array((source as NSString).substring(with: range).utf16)
        for run in Self.codeRuns(in: units) {
            let full = NSRange(location: range.location + run.open, length: run.close + run.width - run.open)
            guard !literal.intersects(integersIn: full) else { continue }
            spans.append(.init(range: NSRange(location: range.location + run.open + run.width, length: run.close - run.open - run.width), style: .code))
            hide(NSRange(location: full.location, length: run.width))
            hide(NSRange(location: range.location + run.close, length: run.width))
            literal.insert(integersIn: full)
        }
        for match in Self.matches(#"\\[\\`*_{}\[\]()#+\-.!>~|]"#, source, range) {
            guard !literal.intersects(integersIn: match.range) else { continue }
            hide(NSRange(location: match.range.location, length: 1)); literal.insert(integersIn: match.range)
        }
        parseLinks(source, range)
        let formats: [(String, Style, Int)] = [
            (#"(?<![\\*])\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)"#, .bold, 2),
            (#"(?<![\w\\_])__(?=\S)(.+?)(?<=\S)__(?!\w|_)"#, .bold, 2),
            (#"(?<![\\*])\*(?!\*|\s)([^*]+?)(?<=\S)\*(?!\*)"#, .italic, 1),
            (#"(?<![\w\\_])_(?!_|\s)([^_]+?)(?<=\S)_(?!\w|_)"#, .italic, 1),
            (#"(?<![\\~])~~(?=\S)(.+?)(?<=\S)~~(?!~)"#, .strike, 2)
        ]
        for (pattern, style, width) in formats {
            for match in Self.matches(pattern, source, range) {
                let opening = NSRange(location: match.range.location, length: width)
                let closing = NSRange(location: NSMaxRange(match.range) - width, length: width)
                guard !literal.intersects(integersIn: match.range),
                      !delimiters.intersects(integersIn: opening), !delimiters.intersects(integersIn: closing) else { continue }
                spans.append(.init(range: match.range(at: 1), style: style)); hide(opening); hide(closing)
            }
        }
    }

    private mutating func parseLinks(_ source: String, _ range: NSRange) {
        let patterns = [#"(?<!!|\\)\[([^\[\]\r\n]+)\]\(([^()\s]+)\)"#,
                        #"(?<!!|\\)\[\[([^\]\r\n|]+)(?:\|([^\]\r\n]+))?\]\]"#]
        for (index, pattern) in patterns.enumerated() {
            for match in Self.matches(pattern, source, range) {
                guard !literal.intersects(integersIn: match.range) else { continue }
                let label = match.range(at: index == 1 && match.range(at: 2).location != NSNotFound ? 2 : 1)
                spans.append(.init(range: label, style: .link))
                hide(NSRange(location: match.range.location, length: label.location - match.range.location))
                hide(NSRange(location: NSMaxRange(label), length: NSMaxRange(match.range) - NSMaxRange(label)))
                // Do not interpret URL/path punctuation as emphasis.
                literal.insert(integersIn: NSRange(location: NSMaxRange(label), length: NSMaxRange(match.range) - NSMaxRange(label)))
            }
        }
    }

    private mutating func hide(_ range: NSRange) {
        hidden.insert(integersIn: range); delimiters.insert(integersIn: range)
    }

    private static func matches(_ pattern: String, _ source: String, _ range: NSRange) -> [NSTextCheckingResult] {
        (try? NSRegularExpression(pattern: pattern).matches(in: source, range: range)) ?? []
    }
}

private extension IndexSet {
    mutating func insert(integersIn range: NSRange) { insert(integersIn: range.location..<NSMaxRange(range)) }
    func intersects(integersIn range: NSRange) -> Bool { intersects(integersIn: range.location..<NSMaxRange(range)) }
}

/// Local saved-source information, never proof that a comment is authentic or remotely current.
public struct SavedAIResult {
    public let range: NSRange
    public let provenance: DiscussionProvenance
    /// Complete source lines, including their original LF/CRLF terminators.
    public let concealedRanges: [NSRange]

    private struct Line {
        let range: NSRange
        let content: String
    }
    private static let opening = "<!-- riji:ai-discussion-result "
    private static let closing = "<!-- /riji:ai-discussion-result -->"

    public var mentorNames: String {
        let names = ["host": "主持人", "gentle_reviewer": "温柔回顾者", "blunt_coach": "直率教练",
                     "future_self": "未来的我", "wang_yangming": "王阳明"]
        return provenance.mentors.map { names[$0] ?? "其他导师" }.joined(separator: "、")
    }

    /// Only complete, top-level, canonical records qualify. Ambiguous regions stay literal.
    public static func records(in source: String) -> [Self] {
        guard source.contains("<!-- riji:ai-discussion-result") else { return [] }
        let lines = sourceLines(source)
        var records: [Self] = [], start: Int?, depth = 0, nested = false
        var fence: (Character, Int)?, comment = false, template = false, html: HTMLBlock?
        var frontmatter = lines.first?.content.replacingOccurrences(of: "\u{FEFF}", with: "") == "---"
        for (index, line) in lines.enumerated() {
            if frontmatter {
                if index > 0 && ["---", "..."].contains(line.content) { frontmatter = false }
                continue
            }
            if depth == 0 {
                if comment || template {
                    updateOpaqueState(line.content, comment: &comment, template: &template)
                    continue
                }
                if var block = html {
                    block.consume(line.content); html = block.isOpen ? block : nil
                    continue
                }
                if let current = fence {
                    if closesFence(line.content, current) { fence = nil }
                    continue
                }
                if let marker = opensFence(line.content) { fence = marker; continue }
                if HTMLBlock.starts(line.content) {
                    var block = HTMLBlock(); block.consume(line.content)
                    html = block.isOpen ? block : nil
                    continue
                }
                if !line.content.hasPrefix("<!-- riji:ai-discussion-result") {
                    updateOpaqueState(line.content, comment: &comment, template: &template)
                    continue
                }
            }
            if line.content.hasPrefix("<!-- riji:ai-discussion-result") {
                if depth == 0 { start = index; nested = false } else { nested = true }
                depth += 1
            } else if line.content.hasPrefix("<!-- /riji:ai-discussion-result"), depth > 0 {
                depth -= 1
                if depth == 0, let first = start, !nested,
                   let record = recognize(Array(lines[first...index])) { records.append(record) }
            }
        }
        return records
    }

    /// Separate accessibility presentation; the editor's actual source and edit offsets stay intact.
    public static func readableMarkdown(_ source: String) -> String {
        readableMarkdown(source, records: records(in: source))
    }

    fileprivate static func readableMarkdown(_ source: String, records: [Self]) -> String {
        guard !records.isEmpty else { return source }
        let readable = NSMutableString(string: source)
        for range in records.flatMap(\.concealedRanges).reversed() {
            readable.deleteCharacters(in: range)
        }
        return readable as String
    }

    private static func recognize(_ lines: [Line]) -> Self? {
        guard lines.count >= 9, let first = lines.first, first.content.hasPrefix(opening),
              first.content.hasSuffix(" -->"), lines.last?.content == closing else { return nil }
        let metadata = first.content.dropFirst(opening.count).dropLast(4)
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        guard let provenance = try? decoder.decode(DiscussionProvenance.self, from: Data(metadata.utf8)),
              structurallyValid(provenance), lines[7].content == ">" else { return nil }
        // Only the fixed generated header is canonical. Ordinary advice may acquire new
        // paragraphs or lose quote punctuation while edited; every body line stays visible.
        guard let canonical = try? AIResultMarkdown.render("Saved advice", provenance: provenance),
              canonical.components(separatedBy: "\n").prefix(8).joined(separator: "\n").utf8
                .elementsEqual(lines.prefix(8).map(\.content).joined(separator: "\n").utf8) else { return nil }
        let end = NSMaxRange(lines.last!.range)
        return Self(range: NSRange(location: first.range.location, length: end - first.range.location),
                    provenance: provenance, concealedRanges: [first.range] + lines[3...7].map(\.range) + [lines.last!.range])
    }

    private static func structurallyValid(_ value: DiscussionProvenance) -> Bool {
        let ids = [value.resultId, value.problemId, value.discussionId] + value.artifactIds + value.mentors
        let formatter = ISO8601DateFormatter()
        let plainDate = formatter.date(from: value.recordedAt)
        formatter.formatOptions.insert(.withFractionalSeconds)
        return ids.allSatisfy(MobileEndpoint.validIdentifier)
            && (1...5).contains(value.artifactIds.count) && (1...5).contains(value.mentors.count)
            && value.inputRevision > 0 && value.summaryVersion >= 0 && value.correctionVersion >= 0
            && value.topic.count <= 200 && value.recordedAt.count <= 50
            && (plainDate != nil || formatter.date(from: value.recordedAt) != nil)
            && (try? JournalDay(value.savedDate)) != nil
    }

    private static func sourceLines(_ source: String) -> [Line] {
        let text = source as NSString, units = Array(source.utf16)
        var start = 0, index = 0, lines: [Line] = []
        while index < units.count {
            guard units[index] == 10 || units[index] == 13 else { index += 1; continue }
            let contentEnd = index
            index += units[index] == 13 && index + 1 < units.count && units[index + 1] == 10 ? 2 : 1
            lines.append(Line(range: NSRange(location: start, length: index - start),
                              content: text.substring(with: NSRange(location: start, length: contentEnd - start))))
            start = index
        }
        if start < units.count {
            let range = NSRange(location: start, length: units.count - start)
            lines.append(Line(range: range, content: text.substring(with: range)))
        }
        return lines
    }

    private static func opensFence(_ line: String) -> (Character, Int)? {
        let spaces = line.prefix(while: { $0 == " " }).count
        guard spaces <= 3, let marker = line.dropFirst(spaces).first, marker == "`" || marker == "~" else { return nil }
        let rest = line.dropFirst(spaces), count = rest.prefix(while: { $0 == marker }).count
        guard count >= 3, marker != "`" || !rest.dropFirst(count).contains("`") else { return nil }
        return (marker, count)
    }

    private static func closesFence(_ line: String, _ fence: (Character, Int)) -> Bool {
        guard let marker = opensFence(line), marker.0 == fence.0, marker.1 >= fence.1 else { return false }
        return line.dropFirst(line.prefix(while: { $0 == " " }).count + marker.1).allSatisfy { $0 == " " || $0 == "\t" }
    }

    private static func updateOpaqueState(_ line: String, comment: inout Bool, template: inout Bool) {
        // Scan complete comments too, so one inline comment cannot conceal a later record.
        var remainder = line[...]
        while !remainder.isEmpty {
            let closing = comment ? "-->" : template ? "%>" : nil
            if let closing {
                guard let end = remainder.range(of: closing) else { return }
                remainder = remainder[end.upperBound...]; comment = false; template = false
            } else {
                let commentStart = remainder.range(of: "<!--"), templateStart = remainder.range(of: "<%")
                guard let start = [commentStart, templateStart].compactMap({ $0 }).min(by: { $0.lowerBound < $1.lowerBound }) else { break }
                comment = start == commentStart; template = !comment; remainder = remainder[start.upperBound...]
            }
        }
    }
    /// This scanner only excludes HTML containers from saved-result recognition. It does not
    /// render HTML or change the Markdown projection, and incomplete tags remain opaque.
    private struct HTMLBlock {
        private var stack: [String] = []
        private var pending = ""
        private var quote: Character?
        private var comment = false
        private static let rawTags: Set<String> = ["pre", "code", "script", "style", "textarea"]
        private static let voidTags: Set<String> = ["area", "base", "basefont", "br", "col", "embed", "frame", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"]
        private static let blockTags = Set(("address article aside base basefont blockquote body caption center code col colgroup dd details dialog dir div dl dt fieldset figcaption figure footer form frame frameset h1 h2 h3 h4 h5 h6 head header hr html iframe legend li link main menu menuitem nav noframes ol optgroup option p param pre script search section style summary table tbody td textarea tfoot th thead title tr track ul").split(separator: " ").map(String.init))
        var isOpen: Bool { !stack.isEmpty || !pending.isEmpty || comment }

        static func starts(_ line: String) -> Bool {
            guard let name = tagName(line, pattern: #"^ {0,3}<([A-Za-z][A-Za-z0-9-]*)(?=[\t />]|$)"#) else { return false }
            return blockTags.contains(name)
        }

        mutating func consume(_ line: String) {
            var remaining = (line + "\n")[...]
            while !remaining.isEmpty {
                if comment {
                    guard let end = remaining.range(of: "-->") else { return }
                    remaining = remaining[end.upperBound...]; comment = false
                    continue
                } else if pending.isEmpty {
                    if let raw = stack.last, Self.rawTags.contains(raw) {
                        guard let close = remaining.range(of: "</" + raw, options: .caseInsensitive) else { return }
                        remaining = remaining[close.lowerBound...]
                    }
                    guard let start = remaining.firstIndex(of: "<") else { return }
                    remaining = remaining[start...]
                    if remaining.hasPrefix("<!--") { comment = true; remaining = remaining.dropFirst(4); continue }
                    pending = "<"; remaining = remaining.dropFirst()
                }
                consumeTag(&remaining)
            }
        }

        private mutating func consumeTag(_ remaining: inout Substring) {
            while let character = remaining.first {
                remaining = remaining.dropFirst(); pending.append(character)
                if let current = quote { if character == current { quote = nil } }
                else if character == "\"" || character == "'" { quote = character }
                else if character == ">" { finishTag(); return }
            }
        }

        private mutating func finishTag() {
            defer { pending = ""; quote = nil }
            guard let name = Self.tagName(pending, pattern: #"^</?([A-Za-z][A-Za-z0-9-]*)(?=[\t\r\n />])"#) else { return }
            if pending.hasPrefix("</") {
                if let index = stack.lastIndex(of: name) { stack.removeSubrange(index...) }
            } else if !pending.hasSuffix("/>") && !Self.voidTags.contains(name) { stack.append(name) }
        }

        private static func tagName(_ text: String, pattern: String) -> String? {
            let source = text as NSString
            guard let regex = try? NSRegularExpression(pattern: pattern),
                  let match = regex.firstMatch(in: text, range: NSRange(location: 0, length: source.length)) else { return nil }
            return source.substring(with: match.range(at: 1)).lowercased()
        }
    }

}
