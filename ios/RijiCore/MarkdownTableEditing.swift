import Foundation

/// Bounded edits against an exact source snapshot; never a document serializer.
public enum MarkdownTableEditing {
    public enum Operation { case cell(row: Int, column: Int, text: String, previousSourceText: String? = nil, contentOffset: Int? = nil), insertRow(after: Int), appendColumn }
    public enum Failure: Error { case staleSource, unsupported, protectedSource }
    public struct Patch {
        public let range: NSRange
        public let replacement: String
        public let focusRow: Int
        public let focusColumn: Int
        private let source: String
        fileprivate init(range: NSRange, replacement: String, row: Int, column: Int, source: String) {
            self.range = range; self.replacement = replacement
            focusRow = row; focusColumn = column; self.source = source
        }
        public func applying(to current: String) throws -> String {
            guard current.utf8.elementsEqual(source.utf8) else { throw Failure.staleSource }
            guard BodyMarkdown.permitsEdit(in: current, range: range, replacement: replacement) else { throw Failure.protectedSource }
            return (current as NSString).replacingCharacters(in: range, with: replacement)
        }
    }
    public static let maximumCells = 1024
    public static let maximumCellUnits = 32_768

    public static func patch(source: String, table: MarkdownPresentation.Table, operation: Operation) throws -> Patch {
        guard let live = MarkdownPresentation(source).tables.first(where: { $0.range == table.range }),
              live.rows.map(\.sourceCells) == table.rows.map(\.sourceCells), live.delimiter == table.delimiter else { throw Failure.staleSource }
        guard table.rows.count * table.alignments.count <= maximumCells else { throw Failure.unsupported }
        let result: Patch
        switch operation {
        case let .cell(row, column, text, previous, offset): result = try cell(source, table, edit: CellEdit(row: row, column: column, value: text, previous: previous, offset: offset))
        case let .insertRow(after): result = try insertRow(source, table, after: after)
        case .appendColumn: result = try appendColumn(source, table)
        }
        let next = try result.applying(to: source)
        guard let edited = MarkdownPresentation(next).tables.first(where: { $0.range.location == table.range.location }) else { throw Failure.unsupported }
        switch operation {
        case let .cell(row, column, _, _, _):
            guard edited.rows.count == table.rows.count, edited.alignments.count == table.alignments.count,
                  edited.rows.enumerated().allSatisfy({ index, value in
                      value.sourceCells.count == (index == row ? max(table.rows[index].sourceCells.count, column + 1) : table.rows[index].sourceCells.count)
                  }) else { throw Failure.unsupported }
        case .insertRow: guard edited.rows.count == table.rows.count + 1, edited.alignments.count == table.alignments.count else { throw Failure.unsupported }
        case .appendColumn: guard edited.rows.count == table.rows.count, edited.alignments.count == table.alignments.count + 1 else { throw Failure.unsupported }
        }
        return result
    }

    public static func cellText(source: String, range: NSRange) -> String {
        (source as NSString).substring(with: contentRange(source, range))
    }

    private static func contentRange(_ source: String, _ range: NSRange) -> NSRange {
        let value = (source as NSString).substring(with: range) as NSString
        let first = value.rangeOfCharacter(from: .whitespaces.inverted)
        guard first.location != NSNotFound else { return NSRange(location: range.location + range.length / 2, length: 0) }
        let last = value.rangeOfCharacter(from: .whitespaces.inverted, options: .backwards)
        return NSRange(location: range.location + first.location, length: NSMaxRange(last) - first.location)
    }

    private struct CellEdit { let row: Int; let column: Int; let value: String; let previous: String?; let offset: Int? }

    private static func cell(_ source: String, _ table: MarkdownPresentation.Table, edit: CellEdit) throws -> Patch {
        let row = edit.row, column = edit.column
        guard table.rows.indices.contains(row), table.alignments.indices.contains(column), edit.value.utf16.count <= maximumCellUnits else { throw Failure.unsupported }
        let line = table.rows[row], encoded = encode(edit.value)
        if column < line.sourceCells.count {
            let range = try edit.previous.map { try previousContentRange(source, line.sourceCells[column], previous: $0, offset: edit.offset) }
                ?? contentRange(source, line.sourceCells[column])
            let minimal = minimalReplacement((source as NSString).substring(with: range), with: encoded)
            return Patch(range: NSRange(location: range.location + minimal.0.location, length: minimal.0.length), replacement: minimal.1, row: row, column: column, source: source)
        }
        let raw = (source as NSString).substring(with: line.range)
        let extra = Array(repeating: "", count: column - line.sourceCells.count) + [encoded]
        return Patch(range: line.range, replacement: append(extra, to: raw), row: row, column: column, source: source)
    }

    /// Compare exact character spellings, preserving surrogate and grapheme boundaries.
    private static func minimalReplacement(_ old: String, with new: String) -> (NSRange, String) {
        let before = Array(old), after = Array(new)
        var prefix = 0, suffix = 0
        while prefix < min(before.count, after.count), String(before[prefix]).utf8.elementsEqual(String(after[prefix]).utf8) { prefix += 1 }
        while suffix < min(before.count, after.count) - prefix,
              String(before[before.count - suffix - 1]).utf8.elementsEqual(String(after[after.count - suffix - 1]).utf8) { suffix += 1 }
        let start = String(before.prefix(prefix)).utf16.count
        let removed = String(before[prefix..<(before.count - suffix)]).utf16.count
        return (NSRange(location: start, length: removed), String(after[prefix..<(after.count - suffix)]))
    }

    private static func previousContentRange(_ source: String, _ range: NSRange, previous: String, offset: Int?) throws -> NSRange {
        let raw = (source as NSString).substring(with: range) as NSString, encoded = previous
        let count = (encoded as NSString).length
        guard count <= raw.length else { throw Failure.staleSource }
        let inferred = encoded.trimmingCharacters(in: .whitespaces).isEmpty
            ? NSRange(location: (raw.length - count) / 2, length: count) : raw.range(of: encoded)
        let local = offset.map { NSRange(location: $0, length: count) } ?? inferred
        guard local.location >= 0, local.location != NSNotFound, NSMaxRange(local) <= raw.length, raw.substring(with: local).utf8.elementsEqual(encoded.utf8),
              raw.substring(to: local.location).trimmingCharacters(in: .whitespaces).isEmpty,
              raw.substring(from: NSMaxRange(local)).trimmingCharacters(in: .whitespaces).isEmpty else { throw Failure.staleSource }
        return NSRange(location: range.location + local.location, length: local.length)
    }

    /// Preserve Markdown tokens and existing escapes, escaping only structural pipes.
    private static func encode(_ value: String) -> String {
        let text = value.replacingOccurrences(of: "\r\n", with: "<br>").replacingOccurrences(of: "\r", with: "<br>").replacingOccurrences(of: "\n", with: "<br>")
        let units = Array(text.utf16), pipes = Set(MarkdownPresentation.structuralPipes(in: Array(text.utf16)))
        var encoded: [UInt16] = []
        for (index, unit) in units.enumerated() {
            if pipes.contains(index) { encoded.append(92) }
            encoded.append(unit)
        }
        // An unmatched final escape must not consume the cell's closing pipe.
        if units.reversed().prefix(while: { $0 == 92 }).count % 2 == 1 { encoded.append(92) }
        return String(decoding: encoded, as: UTF16.self)
    }

    private static func insertRow(_ source: String, _ table: MarkdownPresentation.Table, after: Int) throws -> Patch {
        guard table.rows.indices.contains(after), (table.rows.count + 1) * table.alignments.count <= maximumCells else { throw Failure.unsupported }
        let text = source as NSString, anchor = after == 0 ? table.delimiter : table.rows[after].range
        let full = text.lineRange(for: anchor), newline = newlineStyle(source, table)
        let row = "|" + String(repeating: "  |", count: table.alignments.count)
        let hasNewline = NSMaxRange(full) > NSMaxRange(anchor)
        return Patch(range: NSRange(location: NSMaxRange(full), length: 0), replacement: hasNewline ? row + newline : newline + row,
                     row: after + 1, column: 0, source: source)
    }

    private static func appendColumn(_ source: String, _ table: MarkdownPresentation.Table) throws -> Patch {
        let columns = table.alignments.count
        guard columns < 64, table.rows.count * (columns + 1) <= maximumCells,
              table.rows.allSatisfy({ $0.sourceCells.count <= columns }) else { throw Failure.unsupported }
        let text = source as NSString, result = NSMutableString(string: text.substring(with: table.range))
        var edits = table.rows.map { ($0.range, Array(repeating: "", count: columns + 1 - $0.sourceCells.count)) }
        edits.append((table.delimiter, ["---"]))
        for (range, values) in edits.sorted(by: { $0.0.location > $1.0.location }) {
            result.replaceCharacters(in: NSRange(location: range.location - table.range.location, length: range.length), with: append(values, to: text.substring(with: range)))
        }
        return Patch(range: table.range, replacement: result as String, row: 0, column: columns, source: source)
    }

    private static func append(_ values: [String], to row: String) -> String {
        let suffix = row.reversed().prefix { $0 == " " || $0 == "\t" }.reversed()
        let body = String(row.dropLast(suffix.count))
        let beforePipe = body.hasSuffix("|") ? String(body.dropLast()) : body
        let backslashes = beforePipe.reversed().prefix { $0 == "\\" }.count
        let trailingPipe = body.hasSuffix("|") && backslashes % 2 == 0
        return body + (trailingPipe ? "" : " |") + values.map { " " + $0 + " |" }.joined() + suffix
    }

    private static func newlineStyle(_ source: String, _ table: MarkdownPresentation.Table) -> String {
        let text = source as NSString, row = table.rows[0].range, full = text.lineRange(for: row)
        let ending = text.substring(with: NSRange(location: NSMaxRange(row), length: NSMaxRange(full) - NSMaxRange(row)))
        return ending.isEmpty ? "\n" : ending
    }
}
