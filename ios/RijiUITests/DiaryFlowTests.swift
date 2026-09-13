import XCTest
import CryptoKit

final class DiaryFlowTests: XCTestCase {
    private var app: XCUIApplication!
    private var container: String!

    override func setUpWithError() throws {
        continueAfterFailure = false
        container = UUID().uuidString
        app = XCUIApplication()
        app.launchEnvironment["RIJI_TEST_CONTAINER"] = container
        app.launchEnvironment["RIJI_TEST_DATE"] = "2026-09-12T08:00:00Z"
        app.launchArguments = ["-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
    }

    private var input: XCUIElement { app.descendants(matching: .any).matching(identifier: "diaryInput").firstMatch }
    private var status: XCUIElement { app.staticTexts["saveStatus"] }

    private func waitForStatus(_ value: String) {
        let predicate = NSPredicate(format: "label CONTAINS %@", value)
        expectation(for: predicate, evaluatedWith: status)
        waitForExpectations(timeout: 10)
    }

    private func type(_ value: String) {
        input.tap()
        input.typeText(value)
        waitForStatus("草稿已保留在手机")
        XCTAssertEqual(input.value as? String, value)
    }

    private func reveal(_ element: XCUIElement) {
        for _ in 0..<7 {
            if element.isHittable { return }
            app.swipeUp()
        }
        XCTAssertTrue(element.isHittable)
    }

    func testSyntheticTemplateRoundTripThroughSystemFilesPicker() {
        importSyntheticTemplateFile()
    }

    private func importSyntheticTemplateFile() {
        app.buttons["diaryDirectory"].tap(); app.buttons["templatesFlow"].tap()
        let export = app.buttons["exportSampleTemplate"]; reveal(export); export.tap()
        let filename = app.textFields["DOCPicker.filenameTextField"]
        XCTAssertTrue(filename.waitForExistence(timeout: 10))
        let sampleName = "Riji-Synthetic-Import-" + container
        filename.tap()
        let previousName = filename.value as? String ?? ""
        filename.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: previousName.count))
        filename.typeText(sampleName)
        app.buttons["保存"].tap()
        let importer = app.buttons["importTemplateFile"]
        for _ in 0..<5 { if importer.isHittable { break }; app.swipeDown() }
        XCTAssertTrue(importer.waitForExistence(timeout: 10)); importer.tap()
        let file = app.cells.matching(NSPredicate(format: "identifier BEGINSWITH %@", sampleName)).firstMatch
        if !file.waitForExistence(timeout: 5) {
            let recents = app.buttons["最近项目"]
            if recents.exists { recents.tap() }
        }
        XCTAssertTrue(file.waitForExistence(timeout: 10))
        // Files treats the filename separately; open the document thumbnail.
        file.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.25)).tap()
        XCTAssertTrue(app.buttons["confirmTemplateImport"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "synthetic-manual-acceptance")).firstMatch.exists)
        app.buttons["confirmTemplateImport"].tap()
        let imported = app.buttons["template-imported"]
        for _ in 0..<5 { if imported.isHittable { break }; app.swipeDown() }
        XCTAssertTrue(imported.waitForExistence(timeout: 5))
    }

    func testFormattedDateStyledNotesCaptureAndLanguageRelaunchExport() {
        importSyntheticTemplateFile()
        let language = app.buttons["weekdayLanguage"]
        XCTAssertTrue(language.label.contains("中文"))
        app.buttons["template-imported"].tap(); app.buttons["selectTemplate"].tap()
        language.tap(); app.buttons["English (Monday)"].tap()
        XCTAssertTrue(language.label.contains("English"))
        app.terminate()
        app.launchEnvironment["RIJI_TEST_DATE"] = "2026-09-13T08:00:00Z"
        app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-13"))
        XCTAssertTrue(app.buttons["sectionPicker"].label.contains("🧠  **Notes**"))
        let styled = app.staticTexts["🧠  Notes"]
        XCTAssertTrue(styled.exists, "Heading style is rendered without changing its raw anchor")
        type("Synthetic styled capture")
        app.buttons["saveEntry"].tap(); waitForStatus("已保存在手机")
        app.buttons["diaryDirectory"].tap(); app.buttons["templatesFlow"].tap()
        XCTAssertTrue(language.label.contains("English"))
        language.tap(); app.buttons["中文（星期一）"].tap()
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10)); waitForStatus("已保存在手机")
        app.buttons["moreMenu"].tap(); app.buttons["exportMarkdown"].tap()
        let filename = app.textFields["DOCPicker.filenameTextField"]
        XCTAssertTrue(filename.waitForExistence(timeout: 10)); filename.tap()
        let previousName = filename.value as? String ?? ""
        filename.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: previousName.count))
        filename.typeText("Riji-Styled-Capture-" + container)
        app.buttons["保存"].tap(); waitForStatus("Markdown 已导出")
        let expected = "---\nfixture: synthetic-manual-acceptance\ndate: '2026-09-13'\nweekday: 'Sunday'\nunknown: keep-this-field\n---\n# 2026-09-13\n\n## 🎯 Most Important Task (MIT)\n\n## 🌆 Evening\n\n## 🧠  **Notes**\n\nWeather: {{weather}}\n<% unsupported_synthetic_macro %>\nSynthetic styled capture\n"
        let digest = SHA256.hash(data: Data(expected.utf8)).map { String(format: "%02x", $0) }.joined()
        XCTAssertEqual(status.value as? String, digest, "Export retains the confirmed language, raw heading and unsupported macros")
    }

    func testBlankPreviewStaysFixedThroughLanguageChangeAndExportsConfirmedBytes() {
        app.buttons["diaryDirectory"].tap(); app.buttons["templatesFlow"].tap()
        let sample = app.buttons["previewSampleTemplate"]; reveal(sample); sample.tap()
        app.buttons["confirmTemplateImport"].tap()
        let imported = app.buttons["template-imported"]
        for _ in 0..<5 { if imported.isHittable { break }; app.swipeDown() }
        imported.tap(); app.buttons["selectTemplate"].tap()
        app.navigationBars.buttons.element(boundBy: 0).tap(); app.buttons["blankDayFlow"].tap()
        app.buttons["previewBlankDay"].tap()
        let preview = app.staticTexts["blankDayPreview"]
        XCTAssertTrue(preview.waitForExistence(timeout: 5))
        let original = preview.label
        XCTAssertTrue(app.staticTexts["fixedPreviewNotice"].label.contains("仍显示的变量会按原文保存，不再替换"))
        XCTAssertTrue(original.contains("date: '2026-09-12'")); XCTAssertTrue(original.contains("weekday: '星期六'"))
        XCTAssertTrue(original.contains("## 🧠  **Notes**"))
        app.navigationBars.buttons.element(boundBy: 0).tap(); app.buttons["templatesFlow"].tap()
        app.buttons["weekdayLanguage"].tap(); app.buttons["English (Monday)"].tap()
        app.navigationBars.buttons.element(boundBy: 0).tap(); app.buttons["blankDayFlow"].tap()
        XCTAssertTrue(preview.waitForExistence(timeout: 5)); XCTAssertEqual(preview.label, original)
        let confirm = app.buttons["confirmBlankDay"]; reveal(confirm); confirm.tap()
        let saved = app.staticTexts["workflowStatus"]
        expectation(for: NSPredicate(format: "label CONTAINS %@", "已保存在手机"), evaluatedWith: saved)
        waitForExpectations(timeout: 10)
        app.navigationBars.buttons.element(boundBy: 0).tap(); app.buttons["完成"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5)); waitForStatus("已保存在手机")
        app.buttons["moreMenu"].tap(); app.buttons["exportMarkdown"].tap()
        let filename = app.textFields["DOCPicker.filenameTextField"]
        XCTAssertTrue(filename.waitForExistence(timeout: 10)); filename.tap()
        let previousName = filename.value as? String ?? ""
        filename.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: previousName.count))
        filename.typeText("Riji-Frozen-Preview-" + container)
        app.buttons["保存"].tap(); waitForStatus("Markdown 已导出")
        let digest = SHA256.hash(data: Data(original.utf8)).map { String(format: "%02x", $0) }.joined()
        XCTAssertEqual(status.value as? String, digest, "The actual exported bytes must match the frozen preview")
    }

    func testTemplateMITAndWeeklyReviewNativeWorkflow() {
        app.buttons["diaryDirectory"].tap()
        app.buttons["templatesFlow"].tap()
        let sample = app.buttons["previewSampleTemplate"]
        reveal(sample); sample.tap()
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "{{weather}}")).firstMatch.exists)
        app.buttons["confirmTemplateImport"].tap()
        let imported = app.buttons["template-imported"]
        for _ in 0..<5 { if imported.isHittable { break }; app.swipeDown() }
        XCTAssertTrue(imported.waitForExistence(timeout: 5)); imported.tap()
        app.buttons["selectTemplate"].tap()
        app.navigationBars.buttons.element(boundBy: 0).tap()
        // The new template must work without reopening the diary to refresh it.
        app.buttons["mitFlow"].tap()
        let action = app.textFields["mit-action"]
        XCTAssertTrue(action.waitForExistence(timeout: 5)); action.tap(); action.typeText("Synthetic native MIT")
        app.textFields["mit-criterion"].tap(); app.textFields["mit-criterion"].typeText("One checked artifact")
        let section = app.buttons["mitSection"]; reveal(section); section.tap()
        app.buttons["🎯 Most Important Task (MIT)"].tap()
        let preview = app.buttons["previewMIT"]; reveal(preview); preview.tap()
        XCTAssertTrue(app.buttons["confirmMIT"].waitForExistence(timeout: 5)); app.buttons["confirmMIT"].tap()
        XCTAssertTrue(app.staticTexts["currentMIT"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.staticTexts["currentMIT"].label, "Synthetic native MIT")
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        app.buttons["diaryDirectory"].tap(); app.buttons["mitFlow"].tap()
        XCTAssertTrue(app.staticTexts["currentMIT"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.staticTexts["currentMIT"].label, "Synthetic native MIT")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.buttons["reviewsFlow"].tap(); app.buttons["openReviewPeriod"].tap()
        let editor = app.textViews["reviewEditor"]; reveal(editor); editor.tap(); editor.typeText("Synthetic native weekly reflection")
        let reviewPreview = app.buttons["previewReview"]; reveal(reviewPreview); reviewPreview.tap()
        let confirm = app.buttons["confirmReview"]; reveal(confirm); confirm.tap()
        XCTAssertTrue(app.staticTexts["savedReview"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["savedReview"].label.contains("Synthetic native weekly reflection"))
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        app.buttons["diaryDirectory"].tap(); app.buttons["reviewsFlow"].tap(); app.buttons["openReviewPeriod"].tap()
        XCTAssertTrue(app.staticTexts["savedReview"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["savedReview"].label.contains("Synthetic native weekly reflection"))
    }

    func testFirstLaunchShowsTodayWithoutSetup() {
        XCTAssertTrue(app.staticTexts["templateLabel"].exists)
        XCTAssertEqual(app.staticTexts["templateLabel"].label, "产品初始模板")
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-12"))
        XCTAssertTrue(input.isEnabled)
        XCTAssertFalse(app.buttons["saveEntry"].isEnabled)
        XCTAssertFalse(app.buttons["登录"].exists)
    }

    func testDraftRetainsDateAndSectionAfterRelaunchAcrossMidnight() {
        app.buttons["sectionPicker"].tap()
        app.buttons["🌆 Evening"].tap()
        type("Synthetic evening draft")
        app.terminate()
        app.launchEnvironment["RIJI_TEST_DATE"] = "2026-09-13T08:00:00Z"
        app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        XCTAssertEqual(input.value as? String, "Synthetic evening draft")
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-12"))
        XCTAssertTrue(app.buttons["sectionPicker"].label.contains("🌆 Evening"))
        XCTAssertTrue(app.buttons["returnToday"].exists)
        app.buttons["returnToday"].tap()
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-13"))
        app.buttons["diaryDirectory"].tap()
        let oldDay = app.buttons["directory-2026-09-12"]
        XCTAssertTrue(oldDay.waitForExistence(timeout: 5))
        oldDay.tap()
        XCTAssertEqual(input.value as? String, "Synthetic evening draft")
    }

    func testSingleSaveStaysOnDayAndSurvivesRelaunch() {
        type("Synthetic confirmed text")
        app.buttons["saveEntry"].tap()
        waitForStatus("已保存在手机")
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-12"))
        XCTAssertFalse(app.buttons["saveEntry"].isEnabled)
        app.terminate()
        app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        waitForStatus("已保存在手机")
        app.swipeUp()
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "Synthetic confirmed text")).firstMatch.exists)
        XCTAssertFalse(app.buttons["saveEntry"].isEnabled)
    }

    func testSaveKeepsTheCurrentReadingPosition() {
        let beforeMarker = (1...24).map { "Synthetic earlier line \($0)" }.joined(separator: "\n")
        let afterMarker = (1...24).map { "Synthetic later line \($0)" }.joined(separator: "\n")
        type(beforeMarker + "\n### Middle marker\n" + afterMarker)
        app.buttons["saveEntry"].tap()
        waitForStatus("已保存在手机")
        app.buttons["sectionPicker"].tap()
        app.buttons["🌅 Morning"].tap()
        type("A new entry in the earlier section.\nAnother exact line.")
        app.buttons["收起键盘"].tap()
        let heading = app.staticTexts["Middle marker"]
        let scroll = app.scrollViews.firstMatch
        for _ in 0..<12 {
            if heading.isHittable && heading.frame.minY < 400 { break }
            let start = scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.6))
            let end = scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.35))
            start.press(forDuration: 0.05, thenDragTo: end)
        }
        XCTAssertTrue(heading.isHittable)
        XCTAssertGreaterThan(heading.frame.minY, 110)
        XCTAssertLessThan(heading.frame.minY, 400)
        XCTAssertFalse(app.staticTexts["templateLabel"].isHittable)
        let before = heading.frame.minY
        app.buttons["saveEntry"].tap()
        waitForStatus("已保存在手机")
        XCTAssertTrue(heading.isHittable)
        XCTAssertEqual(heading.frame.minY, before, accuracy: 2)
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-12"))
    }

    func testIndependentRepeatedTextAndExportSheet() {
        for _ in 0..<2 {
            type("Repeated deliberate record")
            app.buttons["saveEntry"].tap()
            waitForStatus("已保存在手机")
        }
        app.swipeUp()
        let body = app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "Repeated deliberate record")).firstMatch
        XCTAssertTrue(body.exists)
        XCTAssertEqual(body.label.components(separatedBy: "Repeated deliberate record").count, 3)
        type("Unconfirmed retained draft must not be exported")
        app.buttons["收起键盘"].tap()
        app.buttons["moreMenu"].tap()
        app.buttons["exportMarkdown"].tap()
        let filename = app.textFields["DOCPicker.filenameTextField"]
        XCTAssertTrue(filename.waitForExistence(timeout: 10))
        XCTAssertTrue((filename.value as? String)?.contains("2026-09-12") == true)
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Markdown export destination picker"
        attachment.lifetime = .keepAlways
        add(attachment)
        filename.tap()
        let previousName = filename.value as? String ?? ""
        filename.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: previousName.count))
        filename.typeText("Riji-test-" + container)
        app.buttons["保存"].tap()
        waitForStatus("Markdown 已导出")
        let expected = "# 2026-09-12\n\n## 🌅 Morning\n\n## 🌆 Evening\n\n## 🪞 Self Reflection\n\n## 📚 Daily Learning\n\n## 🧠 Notes\nRepeated deliberate record\nRepeated deliberate record\n"
        let digest = SHA256.hash(data: Data(expected.utf8)).map { String(format: "%02x", $0) }.joined()
        XCTAssertEqual(status.value as? String, digest, "Read back the actual system destination bytes")
        XCTAssertEqual(input.value as? String, "Unconfirmed retained draft must not be exported")
        XCTAssertTrue(input.exists)
        XCTAssertFalse(app.staticTexts["storageError"].exists)
        app.buttons["moreMenu"].tap()
        app.buttons["exportMarkdown"].tap()
        XCTAssertTrue(filename.waitForExistence(timeout: 10))
        let back = app.buttons["BackButton"]
        if back.exists { back.tap() }
        let cancel = app.buttons.matching(NSPredicate(format: "label == '取消' OR label == 'Cancel'")).firstMatch
        XCTAssertTrue(cancel.waitForExistence(timeout: 10))
        cancel.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["storageError"].exists)
    }
}
