import XCTest
@testable import RijiCore

final class MarkdownVersionDiffTests: XCTestCase {
    private func bytes(_ text: String) -> Data { Data(text.utf8) }
    private func compare(_ a: String, _ b: String) throws -> MarkdownVersionDiff { try MarkdownVersionDiff(current: bytes(a), other: bytes(b)) }

    func testOnlyChangedLinesAppearWithHeadingAndLimitedContext() throws {
        let diff = try compare("# Diary\n## Notes\nOne\nKeep\nTwo\nTail\n", "# Diary\n## Notes\nFirst\nKeep\nSecond\nTail\n")
        XCTAssertEqual(diff.hunks.count, 2); XCTAssertEqual(diff.identicalLines, 4)
        XCTAssertEqual(diff.hunks[0].current, bytes("One\n")); XCTAssertEqual(diff.hunks[1].other, bytes("Second\n"))
        XCTAssertEqual(diff.hunks.map(\.section), ["Notes", "Notes"])
        XCTAssertEqual(diff.hunks[0].contextBefore, "# Diary\n## Notes\n")
        XCTAssertEqual(try diff.applying([0: .other], to: diff.current), bytes("# Diary\n## Notes\nFirst\nKeep\nTwo\nTail\n"))
    }
    func testInsertionDeletionEmptyAndNoFinalNewlineRoundTrip() throws {
        let pairs = [("", "New"), ("Old\n", ""), ("A\nB", "A\nX\nB"), ("A\nB\nC\n", "A\nC\n"), ("Last", "Last\n")]
        for (a, b) in pairs {
            let diff = try compare(a, b)
            XCTAssertEqual(try diff.applying([:], to: bytes(a)), bytes(a))
            XCTAssertEqual(try diff.applying(Dictionary(uniqueKeysWithValues: diff.hunks.map { ($0.id, .other) }), to: bytes(a)), bytes(b))
        }
    }
    func testCRLFUnicodeAndUnchangedProtectedPropertiesRemainExact() throws {
        let a = "---\r\nprivate: true\r\n---\r\n# 日记\r\n- [ ] running\r\n🧠 e\u{301}\r\n"
        let b = a.replacingOccurrences(of: "[ ]", with: "[x]")
        let diff = try compare(a, b)
        XCTAssertEqual(diff.hunks.count, 1)
        XCTAssertEqual(try diff.applying([0: .other], to: bytes(a)), bytes(b))
        let normalization = try compare("e\u{301}\n", "é\n")
        XCTAssertEqual(normalization.hunks.count, 1)
        XCTAssertEqual(try normalization.applying([0: .other], to: normalization.current), bytes("é\n"))
    }
    func testManualFragmentAndMultipleChoicesDoNotTouchNeighbors() throws {
        let diff = try compare("A\nLeft\nMiddle\nRight\nEnd\n", "A\nNew left\nMiddle\nNew right\nEnd\n")
        let choices: [Int: MarkdownVersionDiff.Choice] = [0: .edited(bytes("Custom 🧠\n")), 1: .other]
        XCTAssertEqual(try diff.applying(choices, to: diff.current), bytes("A\nCustom 🧠\nMiddle\nNew right\nEnd\n"))
        XCTAssertEqual(try diff.applying([0: .current, 1: .other], to: diff.current), bytes("A\nLeft\nMiddle\nNew right\nEnd\n"))
        XCTAssertThrowsError(try diff.applying(choices, to: bytes("Later draft")))
        XCTAssertThrowsError(try diff.applying([12: .other], to: diff.current))
    }
    func testTableRowPreviewIncludesHeaderButReplacementDoesNot() throws {
        let a = "## Workout\n| Exercise | Kg |\n| --- | --- |\n| First | 5 |\n| Bench | 10 |\nEnd\n"
        let b = a.replacingOccurrences(of: "Bench | 10", with: "Bench | 12")
        let diff = try compare(a, b), hunk = try XCTUnwrap(diff.hunks.first)
        XCTAssertEqual(hunk.section, "Workout"); XCTAssertEqual(hunk.current, bytes("| Bench | 10 |\n"))
        XCTAssertTrue(hunk.currentPreview.hasPrefix("| Exercise | Kg |\n| --- | --- |\n"))
        XCTAssertEqual(try diff.applying([0: .other], to: diff.current), bytes(b))
    }
    func testMetadataDifferencesAreVisibleAndRequireAnExplicitChoice() throws {
        let diff = try compare("---\nprivate: true\n---\n# Same\n", "---\nprivate: false\n---\n# Same\n")
        XCTAssertEqual(diff.hunks.first?.section, "文档属性")
        XCTAssertEqual(try diff.applying([:], to: diff.current), diff.current)
        XCTAssertEqual(try diff.applying([0: .other], to: diff.current), diff.other)
    }
    func testLargeSeparatedChangesUseAnchorsAndPreserveExactRoundTrip() throws {
        let a = (0..<3000).map { "Line \($0) 🧠\n" }.joined()
        let b = a.replacingOccurrences(of: "Line 2 ", with: "Changed 2 ").replacingOccurrences(of: "Line 2998 ", with: "Changed 2998 ")
        let diff = try compare(a, b)
        XCTAssertEqual(diff.hunks.count, 2); XCTAssertFalse(diff.usedCoarseComparison)
        XCTAssertEqual(try diff.applying([0: .other, 1: .other], to: diff.current), bytes(b))
    }
    func testLargeRepeatedAndLineHeavyInputsAreBoundedAndStillLossless() throws {
        for (a, b) in [(String(repeating: "a\n", count: 1500), String(repeating: "b\n", count: 1500)),
                       (String(repeating: "\n", count: 21000), String(repeating: "\r\n", count: 21000))] {
            let diff = try compare(a, b)
            XCTAssertTrue(diff.usedCoarseComparison)
            XCTAssertEqual(try diff.applying(Dictionary(uniqueKeysWithValues: diff.hunks.map { ($0.id, .other) }), to: diff.current), bytes(b))
        }
    }
    func testDuplicateLinesAndMovesNeverLoseOrRepeatBytes() throws {
        let samples = ["", "A\n", "A\nA\n", "B\nA\nB\n", "🧠\r\nA\nB", "B\nA\nA\n", "é\n", "e\u{301}\n"]
        for a in samples { for b in samples {
            let diff = try compare(a, b)
            XCTAssertEqual(try diff.applying([:], to: diff.current), bytes(a))
            XCTAssertEqual(try diff.applying(Dictionary(uniqueKeysWithValues: diff.hunks.map { ($0.id, .other) }), to: diff.current), bytes(b))
        } }
    }
    func testInvalidAndOversizedInputCannotProduceAReplacement() throws {
        XCTAssertThrowsError(try MarkdownVersionDiff(current: Data([0xff]), other: bytes("valid")))
        XCTAssertThrowsError(try MarkdownVersionDiff(current: bytes("a"), other: Data(repeating: 65, count: CloudReview.maximumBytes + 1)))
        let diff = try compare("a", "b")
        XCTAssertThrowsError(try diff.applying([0: .edited(Data([0xff]))], to: diff.current))
        XCTAssertThrowsError(try diff.applying([0: .edited(Data(repeating: 65, count: CloudReview.maximumBytes + 1))], to: diff.current))
    }
}
