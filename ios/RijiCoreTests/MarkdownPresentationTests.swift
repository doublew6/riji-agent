import XCTest
@testable import RijiCore

final class MarkdownPresentationTests: XCTestCase {
    private func visible(_ source: String) -> String {
        let presentation = MarkdownPresentation(source), text = source as NSString
        let result = NSMutableString(string: source)
        // Marker glyphs are drawn by UIKit; this helper checks only hidden source punctuation.
        for range in presentation.hidden.rangeView.reversed() { result.deleteCharacters(in: NSRange(range)) }
        XCTAssertTrue(presentation.spans.allSatisfy { NSMaxRange($0.range) <= text.length })
        return result as String
    }

    func testHeadingsEmphasisLinksAndTasksHaveExactSourceOffsets() {
        let raw = "# 日记 e\u{301} 🧠\r\n### **Morning**\r\n- [ ] **重点**\r\n- [X] done\r\n- item\r\n> quote\r\n_斜体_ ~~old~~ `code`\r\n[站点](https://example.com) [[folder/note|笔记]]\r\n"
        XCTAssertEqual(visible(raw), "日记 e\u{301} 🧠\r\nMorning\r\n重点\r\ndone\r\nitem\r\nquote\r\n斜体 old code\r\n站点 笔记\r\n")
        let projection = MarkdownPresentation(raw)
        XCTAssertEqual(projection.markers.map(\.kind), [.task(false), .task(true), .bullet, .quote])
        XCTAssertEqual(projection.markers.compactMap { $0.taskState.map { (raw as NSString).substring(with: NSRange(location: $0, length: 1)) } }, [" ", "X"])
        let important = (raw as NSString).range(of: "重点")
        XCTAssertTrue(projection.spans.contains { $0.style == .bold && $0.range == important })
    }

    func testThematicBreaksAndEmptyTemplateListItemsRenderWithoutConsumingMetadata() {
        let raw = "---\nunknown: keep\n---\n# Day\n\n---\n-\n- \n* * *\n___\n**text**\n"
        let presentation = MarkdownPresentation(raw)
        XCTAssertEqual(presentation.markers.map(\.kind), [.rule, .bullet, .bullet, .rule, .rule])
        XCTAssertEqual(visible(raw), "---\nunknown: keep\n---\nDay\n\n\n\n\n\n\ntext\n")
    }

    func testProtectedMetadataCodeAndTemplateMacrosRemainLiteral() {
        let raw = "---\r\nname: **keep**\r\n---\r\n```md\r\n# **literal**\r\n- [ ] code\r\n```\r\n~~~\r\n*keep*\r\n~~~\r\n    **indented**\r\n<% **template** %>\r\n{{*unknown*}}\r\n<!-- riji-record:**keep** -->\r\n"
        XCTAssertEqual(visible(raw), raw)
        XCTAssertTrue(MarkdownPresentation(raw).markers.isEmpty)
    }

    func testEscapesIncompleteSyntaxAndEmbedsAreNotMisinterpreted() {
        let raw = "\\*literal\\* **incomplete a_b_c ![[image.png]] ![image](asset.png)\n`**code**`\n"
        XCTAssertEqual(visible(raw), "*literal* **incomplete a_b_c ![[image.png]] ![image](asset.png)\n**code**\n")
        XCTAssertEqual(visible("**a** **b** *c* __d__ _e_ ~~f~~"), "a b c d e f")
    }

    func testSelectionRevealsOnlyItsLinesWithoutSplittingCRLFOrEmoji() {
        let raw = "# 标题 🧠\r\n**second**\r\n- [ ] third\r\n"
        let selected = (raw as NSString).range(of: "second")
        let active = MarkdownPresentation.activeLines(in: raw, selection: selected)
        XCTAssertEqual((raw as NSString).substring(with: active), "**second**\r\n")
        let end = MarkdownPresentation.activeLines(in: raw, selection: NSRange(location: raw.utf16.count, length: 0))
        XCTAssertEqual(end, NSRange(location: raw.utf16.count, length: 0))
        let all = MarkdownPresentation.activeLines(in: raw, selection: NSRange(location: 0, length: raw.utf16.count))
        XCTAssertEqual((raw as NSString).substring(with: all), raw)
    }

    func testUnknownFenceAndMultipleDelimitersDoNotConsumeFollowingBody() {
        XCTAssertEqual(visible("````\n```\n**literal**\n````\n**body**\n"), "````\n```\n**literal**\n````\nbody\n")
        XCTAssertEqual(visible("[nested](path(with).md)\n1. item\n| a | b |\n"), "[nested](path(with).md)\n1. item\n| a | b |\n")
    }
}

extension MarkdownPresentationTests {
    func testShortAndLongTableRowsKeepFollowingRowsAndOriginalRanges() throws {
        let source = "| A | B |\r\n| --- | --- |\r\n| one |\r\n| two | three | ignored |\r\n| four | five |\r\n"
        let projection = MarkdownPresentation(source)
        let table = try XCTUnwrap(projection.tables.first)
        XCTAssertEqual(table.rows.count, 4)
        XCTAssertEqual(table.rows.map { $0.cells.count }, [2, 2, 2, 2])
        XCTAssertEqual(table.rows[1].cells[1].length, 0)
        XCTAssertEqual((source as NSString).substring(with: table.rows[2].cells[1]).trimmingCharacters(in: .whitespaces), "three")
        let caret = NSRange(location: table.rows[3].cells[0].location, length: 0)
        XCTAssertEqual(MarkdownPresentation.activeLines(in: source, selection: caret, tables: projection.tables),
                       MarkdownPresentation.activeLines(in: source, selection: caret))
    }

    func testTablesRetainEmptyUnicodeCellsEscapesCodeWikiAliasesAndCRLFOffsets() throws {
        let source = "前言 🧠\r\n| | 名称 | 内容 |\r\n| --- | :---: | ---: |\r\n| | e\u{301} | \\| `a|b` [[note|别名]] |\r\n"
        let presentation = MarkdownPresentation(source)
        let table = try XCTUnwrap(presentation.tables.first)
        XCTAssertEqual(presentation.tables.count, 1); XCTAssertEqual(table.rows.count, 2)
        XCTAssertEqual(table.rows[0].cells.count, 3)
        XCTAssertEqual((source as NSString).substring(with: table.rows[1].cells[2]).trimmingCharacters(in: .whitespaces), "\\| `a|b` [[note|别名]]")
        XCTAssertEqual(table.alignments, [.left, .center, .right])
        let active = MarkdownPresentation.activeLines(in: source, selection: NSRange(location: table.rows[1].cells[1].location, length: 0))
        XCTAssertEqual(NSIntersectionRange(active, table.range), table.range)
    }

    func testFencedProtectedIndentedAndInvalidTablesRemainLiteral() {
        let table = "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        for source in ["```md\n" + table + "```\n", "---\n" + table + "---\n", table.split(separator: "\n").map { "    " + $0 }.joined(separator: "\n"), "| A | B |\n| -- | --- |\n"] {
            XCTAssertTrue(MarkdownPresentation(source).tables.isEmpty, source)
        }
        XCTAssertEqual(MarkdownPresentation("| A |\n| --- |\n| |\n").tables.first?.rows.count, 2)
    }
}

extension MarkdownPresentationTests {
    private func tableEdit(_ source: String, _ operation: MarkdownTableEditing.Operation) throws -> String {
        let table = try XCTUnwrap(MarkdownPresentation(source).tables.first)
        return try MarkdownTableEditing.patch(source: source, table: table, operation: operation).applying(to: source)
    }

    func testInlineTableCellPatchPreservesWhitespaceUnicodeAlignmentsAndAllOtherBytes() throws {
        let source = "---\r\nunknown: keep\r\n---\r\n前言 e\u{301} 🧠\r\n| A | B |\r\n| :--- | ---: |\r\n| **原文** | value | hidden |\r\nAfter\r\n"
        let changed = try tableEdit(source, .cell(row: 1, column: 0, text: "**中文 e\u{301} 🧠**"))
        XCTAssertEqual(Data(changed.utf8), Data(source.replacingOccurrences(of: "**原文**", with: "**中文 e\u{301} 🧠**").utf8))
        XCTAssertEqual(MarkdownPresentation(changed).tables.first?.rows[1].sourceCells.count, 3)
        XCTAssertEqual(try tableEdit(source, .cell(row: 1, column: 0, text: "**原文**")), source)
    }

    func testInlineTableEncodingPreservesInlineMarkdownAndSafelyEncodesPaste() throws {
        let source = "| A | B |\n| --- | --- |\n| old | keep |\n"
        let cases: [(String, String)] = [
            ("x|y\\", "x\\|y\\\\"), ("one\r\ntwo\nthree", "one<br>two<br>three"),
            ("**bold** `a|b` [[note|标签]] \\|", "**bold** `a|b` [[note|标签]] \\|"),
            ("`unfinished | [[unfinished", "`unfinished \\| [[unfinished"),
            ("{{weather}} <% x %> <!-- inline -->", "{{weather}} <% x %> <!-- inline -->")
        ]
        for (input, encoded) in cases {
            let changed = try tableEdit(source, .cell(row: 1, column: 0, text: input))
            XCTAssertEqual(changed, source.replacingOccurrences(of: "old", with: encoded))
            XCTAssertEqual(MarkdownPresentation(changed).tables.first?.rows.count, 2)
            XCTAssertEqual(MarkdownPresentation(changed).tables.first?.rows[1].sourceCells.count, 2)
        }
    }

    func testInlineTableShortRowEditMaterializesOnlyMissingCells() throws {
        let source = "| A | B | C |\r\n| --- | :---: | ---: |\r\n| one |\r\n| long | row | stays | hidden |\r\n"
        let changed = try tableEdit(source, .cell(row: 1, column: 2, text: "新增"))
        XCTAssertEqual(changed, source.replacingOccurrences(of: "| one |\r\n", with: "| one |  | 新增 |\r\n"))
        XCTAssertTrue(changed.contains("| long | row | stays | hidden |\r\n"))
        XCTAssertThrowsError(try tableEdit(source, .appendColumn))
    }

    func testInlineTableHeaderAndBodyReturnPreserveCRLFAndEOF() throws {
        let source = "Before\r\n| A | B |\r\n| :--- | ---: |\r\n| old | keep |\r\nAfter\r\n"
        XCTAssertEqual(try tableEdit(source, .insertRow(after: 0)), source.replacingOccurrences(of: "| :--- | ---: |\r\n", with: "| :--- | ---: |\r\n|  |  |\r\n"))
        XCTAssertEqual(try tableEdit(source, .insertRow(after: 1)), source.replacingOccurrences(of: "| old | keep |\r\n", with: "| old | keep |\r\n|  |  |\r\n"))
        let eof = "A | B\n--- | ---\nold | keep"
        XCTAssertEqual(try tableEdit(eof, .insertRow(after: 1)), eof + "\n|  |  |")
    }

    func testInlineTableAddColumnRetainsUnevenRowBytesAndEscapedFinalPipe() throws {
        let source = "A | B\r\n:--- | ---:\r\none | two\\|\r\n| short |\r\n"
        let changed = try tableEdit(source, .appendColumn)
        XCTAssertEqual(changed, "A | B |  |\r\n:--- | ---: | --- |\r\none | two\\| |  |\r\n| short |  |  |\r\n")
        let table = try XCTUnwrap(MarkdownPresentation(changed).tables.first)
        XCTAssertEqual(table.alignments, [.left, .right, .left])
        XCTAssertEqual(table.rows.map { $0.sourceCells.count }, [3, 3, 3])
    }

    func testInlineTableStaleProtectedAndBoundedOperationsAreRejected() throws {
        let source = "| A | B |\n| --- | --- |\n| old | keep |\n"
        let table = try XCTUnwrap(MarkdownPresentation(source).tables.first)
        let patch = try MarkdownTableEditing.patch(source: source, table: table, operation: .cell(row: 1, column: 0, text: "new"))
        XCTAssertThrowsError(try patch.applying(to: source.replacingOccurrences(of: "old", with: "OLD")))
        XCTAssertThrowsError(try tableEdit(source, .cell(row: 1, column: 0, text: "<!-- riji-record:ai:injected -->")))
        XCTAssertThrowsError(try tableEdit(source, .cell(row: 1, column: 0, text: String(repeating: "x", count: 32_769))))
        let huge = "| A | B |\n| --- | --- |\n" + String(repeating: "| a | b |\n", count: 511)
        XCTAssertThrowsError(try tableEdit(huge, .insertRow(after: 1)))
        XCTAssertThrowsError(try tableEdit(huge, .appendColumn))
        XCTAssertThrowsError(try tableEdit(source, .cell(row: 8, column: 0, text: "bad")))
    }
}

extension MarkdownPresentationTests {
    func testInlineTableTypingSessionPreservesLeadingTrailingAndAllSpaceInput() throws {
        for original in ["", "old"] {
            let source = "| A | B |\r\n| --- | --- |\r\n|  " + original + "     | untouched |\r\n"
            var current = source, previous = original
            let offset = original.isEmpty ? 3 : 2
            for value in ["", " ", "  ", " N", " New ", " New r", " New row ", " New row", ""] {
                let table = try XCTUnwrap(MarkdownPresentation(current).tables.first)
                current = try MarkdownTableEditing.patch(source: current, table: table,
                    operation: .cell(row: 1, column: 0, text: value, previousSourceText: previous, contentOffset: offset)).applying(to: current)
                let paddingBefore = String(repeating: " ", count: offset)
                let paddingAfter = String(repeating: " ", count: 7 - offset)
                XCTAssertEqual(current, "| A | B |\r\n| --- | --- |\r\n|" + paddingBefore + value + paddingAfter + "| untouched |\r\n")
                previous = value
            }
        }
    }
}


extension MarkdownPresentationTests {
    func testInlineTableEditingUnmatchedOriginalMarkdownUsesItsExactSourceSpelling() throws {
        for value in ["`unfinished", "[[unfinished"] {
            let source = "| A | B |\n| --- | --- |\n| " + value + " | keep |\n"
            let table = try XCTUnwrap(MarkdownPresentation(source).tables.first)
            let patch = try MarkdownTableEditing.patch(source: source, table: table, operation: .cell(row: 1, column: 0, text: "changed", previousSourceText: value, contentOffset: 1))
            XCTAssertEqual(try patch.applying(to: source), source.replacingOccurrences(of: value, with: "changed"))
        }
    }
}


extension MarkdownPresentationTests {
    func testInlineTableMinimalPatchPreservesExactGraphemeBoundaries() throws {
        for (before, after) in [("e\u{301}", "é"), ("👩🏽‍💻", "👩🏻‍💻")] {
            let source = "| A | B |\n| --- | --- |\n| prefix " + before + " suffix | keep |\n"
            let table = try XCTUnwrap(MarkdownPresentation(source).tables.first)
            let patch = try MarkdownTableEditing.patch(source: source, table: table,
                operation: .cell(row: 1, column: 0, text: "prefix " + after + " suffix"))
            XCTAssertEqual(patch.range, (source as NSString).range(of: before))
            XCTAssertEqual(Data(patch.replacement.utf8), Data(after.utf8))
            XCTAssertEqual(Data(try patch.applying(to: source).utf8), Data(source.replacingOccurrences(of: before, with: after).utf8))
        }
    }
}


extension MarkdownPresentationTests {
    func testInlineTableCombiningPipeEncodingKeepsActualCellsAndUntouchedNeighbor() throws {
        for mark in ["\u{301}", "\u{FE0F}"] {
            let source = "| A | B |\r\n| --- | --- |\r\n| old | keep |\r\nAfter\r\n"
            let changed = try tableEdit(source, .cell(row: 1, column: 0, text: "x|" + mark + "y"))
            XCTAssertEqual(Data(changed.utf8), Data(source.replacingOccurrences(of: "old", with: "x\\|" + mark + "y").utf8))
            let row = try XCTUnwrap(MarkdownPresentation(changed).tables.first?.rows[1])
            XCTAssertEqual(row.sourceCells.count, 2)
            XCTAssertEqual((changed as NSString).substring(with: row.sourceCells[1]), " keep ")
        }
        let acrossCells = "| A | B |\n| --- | --- |\n| old | `keep |\n"
        XCTAssertThrowsError(try tableEdit(acrossCells, .cell(row: 1, column: 0, text: "`new")))
    }

    func testMultilineLiteralBlocksExcludeTablesButInlineLiteralCellsStayTables() throws {
        let table = "| A | B |\r\n| --- | --- |\r\n| one | keep |\r\n"
        for (open, close) in [("<!--\r\n", "-->\r\n"), ("<%*\r\n", "%>\r\n")] {
            let source = open + table + close
            XCTAssertTrue(MarkdownPresentation(source).tables.isEmpty)
            XCTAssertEqual(Data(visible(source).utf8), Data(source.utf8))
        }
        let inline = table.replacingOccurrences(of: "one", with: "{{weather}} <% value %> <!-- inline -->")
        XCTAssertEqual(MarkdownPresentation(inline).tables.count, 1)
        XCTAssertEqual(Data(visible(inline).utf8), Data(inline.utf8))
        XCTAssertEqual(try tableEdit(inline, .cell(row: 1, column: 1, text: "changed")), inline.replacingOccurrences(of: "keep", with: "changed"))
    }

    func testInlineTablePartialAppendPreservesIncompleteOriginalSyntaxAndExactCodeRuns() throws {
        for original in ["`unfinished", "[[unfinished"] {
            let source = "| A | B |\n| --- | --- |\n| " + original + " | keep |\n"
            XCTAssertEqual(try tableEdit(source, .cell(row: 1, column: 0, text: original + " more")), source.replacingOccurrences(of: original, with: original + " more"))
        }
        let source = "| A | B |\n| --- | --- |\n| old | keep |\n"
        let cases = [
            ("`open `` other | tail", "`open `` other \\| tail"),
            ("`open \\` escaped | tail", "`open \\` escaped \\| tail"),
            ("``open `short | tail", "``open `short \\| tail"),
            ("``a ` b|c``", "``a ` b|c``"),
            ("`a\\`b|c`", "`a\\`b|c`"),
            ("``a\\``` | tail", "``a\\``` \\| tail")
        ]
        for (input, encoded) in cases {
            let changed = try tableEdit(source, .cell(row: 1, column: 0, text: input))
            XCTAssertEqual(Data(changed.utf8), Data(source.replacingOccurrences(of: "old", with: encoded).utf8))
            XCTAssertEqual(MarkdownPresentation(changed).tables.first?.rows[1].sourceCells.count, 2)
        }
        for literal in ["`open `` other", "`open \\` escaped", "``a\\```"] {
            XCTAssertFalse(MarkdownPresentation(literal).spans.contains { $0.style == .code })
        }
    }
}

extension MarkdownPresentationTests {
    private func savedAIBlock(_ id: String = "one", advice: String = "先做一个小实验。", edited: Bool = false) throws -> String {
        let provenance = DiscussionProvenance(resultId: "result-" + id, problemId: "problem-" + id,
            discussionId: "discussion-" + id, artifactIds: ["artifact-" + id], inputRevision: 1,
            summaryVersion: 2, correctionVersion: 0, recordedAt: "2020-01-02T03:04:05Z",
            mentors: ["gentle_reviewer", "wang_yangming"], topic: "合成问题 " + id,
            savedDate: "2026-10-17", editedByUser: edited)
        return try AIResultMarkdown.render(advice, provenance: provenance)
    }

    func testSavedAIResultsFoldOnlyCanonicalMachineLinesAndKeepOldAdviceAndAINature() throws {
        let advice = "第一条建议。\n<!-- riji:ai-discussion-result {} -->\n<!-- /riji:ai-discussion-result -->\n> 参与导师：literal\n讨论：keep\n# Source-like heading"
        let block = try savedAIBlock(advice: advice, edited: true)
        let source = "前言 🧠 e\u{301}\n" + block + "\n后文\n"
        let records = SavedAIResult.records(in: source)
        let record = try XCTUnwrap(records.first)
        XCTAssertEqual(records.count, 1)
        XCTAssertEqual(record.range.location, (source as NSString).range(of: block).location)
        XCTAssertEqual((source as NSString).substring(with: record.range), block + "\n")
        XCTAssertEqual(record.mentorNames, "温柔回顾者、王阳明")
        XCTAssertTrue(record.provenance.editedByUser)
        let readable = SavedAIResult.readableMarkdown(source)
        XCTAssertTrue(readable.contains("> [!note] AI 导师讨论结果\n> 这是 AI 讨论资料；保存不代表采纳建议，也不代表计划已经执行。"))
        XCTAssertTrue(readable.contains(advice.components(separatedBy: "\n").map { "> " + $0 }.joined(separator: "\n")))
        XCTAssertFalse(readable.contains("\"result_id\"")); XCTAssertFalse(readable.contains("gentle_reviewer"))
        XCTAssertFalse(readable.contains("> 问题：合成问题")); XCTAssertFalse(readable.contains("> 讨论：discussion-"))
        XCTAssertTrue(readable.hasPrefix("前言 🧠 e\u{301}\n")); XCTAssertTrue(readable.hasSuffix("后文\n"))
        XCTAssertEqual(MarkdownPresentation(source).savedAIResults.count, 1)
    }

    func testSavedAIResultsRetainAdjacentBlockAssociationAndExactUnicodeCRLFRanges() throws {
        for newline in ["\n", "\r\n"] {
            let first = try savedAIBlock("one"), second = try savedAIBlock("two", advice: "第二条 🧠 e\u{301}")
            let source = ("🧠 e\u{301}\n" + first + "\n" + second + "\n尾声").replacingOccurrences(of: "\n", with: newline)
            let results = SavedAIResult.records(in: source)
            XCTAssertEqual(results.map(\.provenance.resultId), ["result-one", "result-two"])
            for (record, expected) in zip(results, [first, second]) {
                XCTAssertEqual((source as NSString).substring(with: record.range), expected.replacingOccurrences(of: "\n", with: newline) + newline)
                XCTAssertTrue(record.concealedRanges.allSatisfy { NSIntersectionRange($0, record.range) == $0 })
            }
            let readable = SavedAIResult.readableMarkdown(source)
            XCTAssertTrue(readable.contains("> 第二条 🧠 e\u{301}" + newline + "尾声"))
            XCTAssertEqual(readable.components(separatedBy: "AI 导师讨论结果").count, 3)
        }
    }

    func testCorruptUnsupportedAndUnpairedAIRecordsConcealNothing() throws {
        let block = try savedAIBlock()
        let opening = block.components(separatedBy: "\n")[0], closing = "<!-- /riji:ai-discussion-result -->"
        let unsupported = [
            block.replacingOccurrences(of: "\"summary_version\":2", with: "\"summary_version\":-1"),
            block.replacingOccurrences(of: "\"input_revision\":1", with: "\"input_revision\":0"),
            block.replacingOccurrences(of: "2020-01-02T03:04:05Z", with: "not-a-date"),
            block.replacingOccurrences(of: "2026-10-17", with: "2026-02-30"),
            block.replacingOccurrences(of: "\"result_id\":\"result-one\"", with: "\"result_id\":null"),
            block.replacingOccurrences(of: "\"result_id\":\"result-one\"", with: "\"result_id\":\"result-one\",\"unknown_field\":true"),
            block.replacingOccurrences(of: "\"result_id\":\"result-one\"", with: "\"result_id\":\"../private\""),
            block.replacingOccurrences(of: opening, with: "<!-- riji:ai-discussion-result {broken} -->"),
            block.replacingOccurrences(of: "> 问题：合成问题 one", with: "> 问题：different"),
            block.replacingOccurrences(of: "> 参与导师：gentle_reviewer、wang_yangming", with: "> 参与导师：other"),
            block.replacingOccurrences(of: closing, with: ""), block.replacingOccurrences(of: opening + "\n", with: ""),
            opening + "\n" + block + "\n" + closing,
            opening + "\nUnrelated user body\n" + closing,
            "<!-- riji:ai-discussion-result {\"unrecognized\":true} -->\nUser body\n" + closing
        ]
        for source in unsupported {
            XCTAssertTrue(SavedAIResult.records(in: source).isEmpty, source)
            XCTAssertEqual(Data(SavedAIResult.readableMarkdown(source).utf8), Data(source.utf8))
        }
    }

    func testCodeContainedQuotedAndArbitraryMetadataRemainLiteral() throws {
        let block = try savedAIBlock()
        let contexts = ["```markdown\n" + block + "\n```", "~~~~\n" + block + "\n~~~~",
                        block.components(separatedBy: "\n").map { "    " + $0 }.joined(separator: "\n"),
                        block.components(separatedBy: "\n").map { "\t" + $0 }.joined(separator: "\n"),
                        block.components(separatedBy: "\n").map { "> " + $0 }.joined(separator: "\n"),
                        "---\n" + block + "\n---\n", "<!-- arbitrary\n" + block + "\n-->\n",
                        "<%*\n" + block + "\n%>\n", "<pre>\n" + block + "\n</pre>\n",
                        "<div>\n" + block + "\n</div>\n",
                        "<!-- unclosed\n" + block, "```\n" + block]
        for source in contexts {
            XCTAssertTrue(SavedAIResult.records(in: source).isEmpty, source)
            XCTAssertEqual(Data(SavedAIResult.readableMarkdown(source).utf8), Data(source.utf8))
        }
        let arbitrary = "---\nuser: keep\n---\n<!-- arbitrary comment -->\n> 讨论：manual\n> 参与导师：manual\n"
        XCTAssertEqual(SavedAIResult.readableMarkdown(arbitrary), arbitrary)
        let outside = "<!-- inline comment -->\n" + block + "\n<!-- /riji:ai-discussion-result -->\n"
        XCTAssertEqual(SavedAIResult.records(in: outside).count, 1)
        XCTAssertTrue(SavedAIResult.readableMarkdown(outside).hasSuffix("<!-- /riji:ai-discussion-result -->\n"))
    }
}

extension MarkdownPresentationTests {
    func testOpaqueContentCannotOpenCodeFencesOrChangeFollowingRecordAssociation() throws {
        let first = try savedAIBlock("one"), second = try savedAIBlock("two")
        for opaque in ["<!-- note\n```\n-->\n", "<pre>\n```\n</pre>\n", "<%*\n```\n%>\n"] {
            let outside = opaque + first
            XCTAssertEqual(SavedAIResult.records(in: outside).map(\.provenance.resultId), ["result-one"])
            let withCode = opaque + "```markdown\n" + first + "\n```\n" + second
            XCTAssertEqual(SavedAIResult.records(in: withCode).map(\.provenance.resultId), ["result-two"])
            let readable = SavedAIResult.readableMarkdown(withCode)
            XCTAssertTrue(readable.contains("```markdown\n" + first + "\n```\n"))
            XCTAssertFalse(readable.contains("\"result_id\":\"result-two\""))
        }
    }

    func testReadableProjectionRequiresTheInstalledPresentationAndExactSourceBytes() throws {
        let source = "🧠 e\u{301}\n" + (try savedAIBlock())
        let projection = MarkdownPresentation(source)
        XCTAssertFalse(projection.readableMarkdown(in: source).contains("\"result_id\""))
        XCTAssertEqual(MarkdownPresentation("").readableMarkdown(in: source), source)
        for changed in ["New prefix\n" + source, source.replacingOccurrences(of: "e\u{301}", with: "é"),
                        source.replacingOccurrences(of: "\n", with: "\r\n")] {
            XCTAssertEqual(Data(projection.readableMarkdown(in: changed).utf8), Data(changed.utf8))
        }
    }
}


extension MarkdownPresentationTests {
    func testBlockHTMLContainersKeepCanonicalExamplesLiteralAndRecognizeNextOutsideRecord() throws {
        let example = try savedAIBlock("one"), outside = try savedAIBlock("two")
        let wrappers = [
            ("<article>\n", "\n</article>\n"), ("<main>\n", "\n</main>\n"),
            ("<div\tclass=\"example\">\n", "\n</div>\n"),
            ("<section\n class=\"example > value\"\n>\n", "\n</section>\n"),
            ("<div><div>nested</div>\n", "\n</div>\n"),
            ("<article>\n<main\tdata-kind=\"example\">\n<div>\n", "\n</div>\n</main>\n</article>\n"),
            ("<pre\tclass=\"language-md\">\n<not-a-container>\n", "\n</pre>\n")
        ]
        for (open, close) in wrappers {
            let source = open + example + close + outside
            XCTAssertEqual(SavedAIResult.records(in: source).map(\.provenance.resultId), ["result-two"], source)
            XCTAssertTrue(SavedAIResult.readableMarkdown(source).hasPrefix(open + example + close))
        }
        let nested = "<div>\n<div>\n" + example + "\n</div>\n" + outside + "\n</div>\n" + (try savedAIBlock("three"))
        XCTAssertEqual(SavedAIResult.records(in: nested).map(\.provenance.resultId), ["result-three"])
        XCTAssertTrue(SavedAIResult.readableMarkdown(nested).contains(outside))
    }

    func testSavedAIAdviceKeepsUnicodeSeparatorsAndEditedParagraphsWithExactUTF16Ranges() throws {
        let advice = "First 🧠 e\u{301}\u{2028}still advice\u{2029}next idea\nLast paragraph"
        let block = try savedAIBlock(advice: advice)
        for newline in ["\n", "\r\n"] {
            let prefix = "Before 🧠\u{2028}e\u{301}\u{2029}\n"
            let source = (prefix + block + "\nAfter").replacingOccurrences(of: "\n", with: newline)
            let record = try XCTUnwrap(SavedAIResult.records(in: source).first)
            let originalBlock = block.replacingOccurrences(of: "\n", with: newline)
            XCTAssertEqual((source as NSString).substring(with: record.range), originalBlock + newline)
            XCTAssertEqual(record.range.location, (prefix.replacingOccurrences(of: "\n", with: newline) as NSString).length)
            let readable = SavedAIResult.readableMarkdown(source)
            XCTAssertTrue(readable.contains("> First 🧠 e\u{301}\u{2028}still advice\u{2029}next idea"))
            XCTAssertFalse(readable.contains("\"result_id\""))
            let edited = source.replacingOccurrences(of: "> Last paragraph", with: "Unquoted user paragraph" + newline + "Another paragraph")
            XCTAssertEqual(SavedAIResult.records(in: edited).count, 1)
            XCTAssertTrue(SavedAIResult.readableMarkdown(edited).contains("Unquoted user paragraph" + newline + "Another paragraph"))
            XCTAssertFalse(SavedAIResult.readableMarkdown(edited).contains("\"result_id\""))
        }
    }
}
