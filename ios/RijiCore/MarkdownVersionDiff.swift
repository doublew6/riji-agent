import Foundation

/// A byte-preserving, line-oriented comparison. Rendering never supplies replacement bytes.
public struct MarkdownVersionDiff: Sendable {
    public struct Hunk: Identifiable, Sendable {
        public let id: Int
        public let currentRange: Range<Int>
        public let otherRange: Range<Int>
        public let current: Data
        public let other: Data
        public let section: String
        public let contextBefore: String
        public let contextAfter: String
        public let currentPreview: String
        public let otherPreview: String
        public var kind: String { current.isEmpty ? "另一版新增" : other.isEmpty ? "另一版缺少" : "内容不同" }
        public var visuallyEqual: Bool {
            String(decoding: current, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
                == String(decoding: other, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        }
    }

    public enum Choice: Sendable { case current, other, edited(Data) }
    public enum DiffError: Error { case invalidText, oversized, stale, invalidChoice }
    public let current: Data
    public let other: Data
    public let hunks: [Hunk]
    public let identicalLines: Int
    public let usedCoarseComparison: Bool

    public init(current source: Data, other candidate: Data) throws {
        let current = Data(source), other = Data(candidate)
        guard current.count <= CloudReview.maximumBytes, other.count <= CloudReview.maximumBytes else { throw DiffError.oversized }
        guard String(data: current, encoding: .utf8) != nil, String(data: other, encoding: .utf8) != nil else { throw DiffError.invalidText }
        self.current = current; self.other = other
        let left = Lines(current), right = Lines(other)
        var matcher = Matcher(left: left.values, right: right.values)
        matcher.match(0..<left.values.count, 0..<right.values.count)
        let matches = matcher.matches.sorted { $0.0 < $1.0 }
        identicalLines = matches.count; usedCoarseComparison = matcher.coarse || left.coarse || right.coarse
        var changes: [Hunk] = [], a = 0, b = 0
        for (x, y) in matches + [(left.values.count, right.values.count)] {
            if a != x || b != y {
                let lr = left.bytes(a..<x), rr = right.bytes(b..<y)
                let old = current.subdata(in: lr), new = other.subdata(in: rr)
                changes.append(Hunk(id: changes.count, currentRange: lr, otherRange: rr, current: old, other: new,
                    section: left.section(at: a) ?? right.section(at: b) ?? "日记正文",
                    contextBefore: left.text(max(0, a - 2)..<a), contextAfter: left.text(x..<min(x + 2, left.values.count)),
                    currentPreview: left.preview(a..<x), otherPreview: right.preview(b..<y)))
            }
            a = x + 1; b = y + 1
        }
        hunks = changes
    }

    /// Choices always address this immutable comparison, and must match the visible working draft.
    public func applying(_ choices: [Int: Choice], to expected: Data) throws -> Data {
        guard expected == current else { throw DiffError.stale }
        guard choices.keys.allSatisfy({ hunks.indices.contains($0) }) else { throw DiffError.invalidChoice }
        var result = Data(), cursor = 0
        for hunk in hunks {
            result.append(current.subdata(in: cursor..<hunk.currentRange.lowerBound))
            switch choices[hunk.id] ?? .current {
            case .current: result.append(hunk.current)
            case .other: result.append(hunk.other)
            case .edited(let content):
                guard String(data: content, encoding: .utf8) != nil else { throw DiffError.invalidText }
                result.append(content)
            }
            guard result.count <= CloudReview.maximumBytes else { throw DiffError.oversized }
            cursor = hunk.currentRange.upperBound
        }
        result.append(current.subdata(in: cursor..<current.count))
        guard result.count <= CloudReview.maximumBytes else { throw DiffError.oversized }
        return result
    }

    private struct Lines {
        let values: [Data]
        let offsets: [Int]
        let sections: [String?]
        let coarse: Bool
        init(_ data: Data) {
            let bytes = [UInt8](data)
            var lines: [Data] = [], starts = [0], start = 0, index = 0
            while index < bytes.count {
                if bytes[index] == 10 || bytes[index] == 13 {
                    if bytes[index] == 13 && index + 1 < bytes.count && bytes[index + 1] == 10 { index += 1 }
                    lines.append(Data(bytes[start...index])); start = index + 1; starts.append(start)
                    if lines.count == 20_000 && start < bytes.count { break }
                }
                index += 1
            }
            if start < bytes.count { lines.append(Data(bytes[start...])); starts.append(bytes.count) }
            values = lines; offsets = starts; coarse = lines.count > 20_000
            var labels: [String?] = [], heading: String?, fence: String?, properties = false
            for (index, line) in lines.enumerated() {
                let text = String(decoding: line, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
                if index == 0 && text == "---" { properties = true }
                if properties { labels.append("文档属性"); if index > 0 && (text == "---" || text == "...") { properties = false }; continue }
                if let active = fence { if text.hasPrefix(active) { fence = nil } }
                else if text.hasPrefix("```") || text.hasPrefix("~~~") { fence = String(text.prefix(3)) }
                else if text.hasPrefix("#") {
                    let suffix = text.drop(while: { $0 == "#" })
                    if suffix.first == " " { heading = String(suffix).trimmingCharacters(in: .whitespaces) }
                }
                labels.append(heading)
            }
            sections = labels
        }
        func bytes(_ range: Range<Int>) -> Range<Int> { offsets[range.lowerBound]..<offsets[range.upperBound] }
        func text(_ range: Range<Int>) -> String { String(decoding: values[range].reduce(into: Data(), { $0.append($1) }), as: UTF8.self) }
        func section(at index: Int) -> String? { sections.isEmpty ? nil : sections[min(index, sections.count - 1)] }
        func preview(_ range: Range<Int>) -> String {
            guard !range.isEmpty else { return "" }
            let raw = text(range)
            // Include an unchanged table header for readable changed rows, without patching it.
            guard range.lowerBound > 1, raw.trimmingCharacters(in: .whitespacesAndNewlines).hasPrefix("|") else { return raw }
            var start = range.lowerBound
            while start > 0 && text((start - 1)..<start).trimmingCharacters(in: .whitespacesAndNewlines).hasPrefix("|") { start -= 1 }
            guard start + 1 < range.lowerBound else { return raw }
            let header = text(start..<(start + 2))
            guard !MarkdownPresentation(header).tables.isEmpty else { return raw }
            return header + raw
        }
    }

    /// Patience anchors split large diaries; small unmatched spans use exact LCS with a work budget.
    private struct Matcher {
        let left: [Data], right: [Data]
        var matches: [(Int, Int)] = []
        var budget = 1_000_000
        var coarse = false
        mutating func match(_ lhs: Range<Int>, _ rhs: Range<Int>, depth: Int = 0) {
            var a = lhs.lowerBound, b = rhs.lowerBound, x = lhs.upperBound, y = rhs.upperBound
            while a < x && b < y && left[a] == right[b] { matches.append((a, b)); a += 1; b += 1 }
            var suffix: [(Int, Int)] = []
            while a < x && b < y && left[x - 1] == right[y - 1] { x -= 1; y -= 1; suffix.append((x, y)) }
            defer { matches.append(contentsOf: suffix.reversed()) }
            guard a < x && b < y else { return }
            let cost = (x - a + 1) * (y - b + 1)
            if cost <= min(budget, 250_000) { budget -= cost; lcs(a..<x, b..<y); return }
            guard depth < 32 else { coarse = true; return }
            let anchors = anchors(a..<x, b..<y)
            guard !anchors.isEmpty else { coarse = true; return }
            for (i, j) in anchors {
                match(a..<i, b..<j, depth: depth + 1); matches.append((i, j)); a = i + 1; b = j + 1
            }
            match(a..<x, b..<y, depth: depth + 1)
        }
        mutating func lcs(_ a: Range<Int>, _ b: Range<Int>) {
            let width = b.count + 1
            var lengths = [Int32](repeating: 0, count: (a.count + 1) * width)
            for i in (0..<a.count).reversed() {
                for j in (0..<b.count).reversed() {
                    lengths[i * width + j] = left[a.lowerBound + i] == right[b.lowerBound + j]
                        ? 1 + lengths[(i + 1) * width + j + 1]
                        : max(lengths[(i + 1) * width + j], lengths[i * width + j + 1])
                }
            }
            var i = 0, j = 0
            while i < a.count && j < b.count {
                if left[a.lowerBound + i] == right[b.lowerBound + j] { matches.append((a.lowerBound + i, b.lowerBound + j)); i += 1; j += 1 }
                else if lengths[(i + 1) * width + j] >= lengths[i * width + j + 1] { i += 1 }
                else { j += 1 }
            }
        }
        func anchors(_ a: Range<Int>, _ b: Range<Int>) -> [(Int, Int)] {
            var l: [Data: Int] = [:], r: [Data: Int] = [:]
            for i in a { l[left[i]] = l[left[i]] == nil ? i : -1 }
            for j in b { r[right[j]] = r[right[j]] == nil ? j : -1 }
            let pairs = a.compactMap { i -> (Int, Int)? in
                guard l[left[i]] == i, let j = r[left[i]], j >= 0 else { return nil }; return (i, j)
            }
            var tails: [Int] = [], previous = [Int](repeating: -1, count: pairs.count)
            for index in pairs.indices {
                var low = 0, high = tails.count
                while low < high { let middle = (low + high) / 2; if pairs[tails[middle]].1 < pairs[index].1 { low = middle + 1 } else { high = middle } }
                if low > 0 { previous[index] = tails[low - 1] }
                if low == tails.count { tails.append(index) } else { tails[low] = index }
            }
            var chain: [(Int, Int)] = [], index = tails.last ?? -1
            while index >= 0 { chain.append(pairs[index]); index = previous[index] }
            return chain.reversed()
        }
    }
}
