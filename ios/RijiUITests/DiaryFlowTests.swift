import XCTest
import CryptoKit

final class DiaryFlowTests: XCTestCase {
    private var app: XCUIApplication!
    private var container: String!
    private var bodyReviewGeometry: [String] = []

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

    private var input: XCUIElement { app.descendants(matching: .any).matching(identifier: "diaryBodyEditor").firstMatch }
    private var status: XCUIElement { app.staticTexts["templateLabel"] }

    private func waitForStatus(_ value: String) {
        let predicate = NSPredicate(format: "label CONTAINS %@", value)
        expectation(for: predicate, evaluatedWith: status)
        waitForExpectations(timeout: 10)
    }

    private func type(_ value: String, savedStatus: String = "已保存在手机") {
        let before = input.value as? String ?? ""
        input.tap()
        // Establish the software keyboard before targeting its moving accessory toolbar.
        input.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        let keyboard = app.keyboards.firstMatch
        XCTAssertTrue(keyboard.waitForExistence(timeout: 5))
        let end = app.buttons["文末"]
        let ready = NSPredicate { _, _ in
            end.isHittable && end.frame.maxY <= keyboard.frame.minY + 1
        }
        expectation(for: ready, evaluatedWith: end)
        waitForExpectations(timeout: 5)
        XCTAssertEqual(input.value as? String, before, "Preparing keyboard focus must not edit the document")
        end.tap()
        input.typeText(value + "\n")
        waitForStatus(savedStatus)
        XCTAssertEqual(input.value as? String, before + value + "\n")
        app.buttons["收起键盘"].tap()
    }

    private func reveal(_ element: XCUIElement, requiringHitPoint: Bool = true) {
        for _ in 0..<7 {
            if hasVisibleFrame(element), !requiringHitPoint || element.isHittable { return }
            app.swipeUp()
        }
        guard hasVisibleFrame(element) else {
            XCTFail("The requested control must have a visible frame before checking its hit point"); return
        }
        XCTAssertTrue(!requiringHitPoint || element.isHittable)
    }

    private func hasVisibleFrame(_ element: XCUIElement) -> Bool {
        guard element.exists else { return false }
        let frame = element.frame
        // SwiftUI can expose an unmaterialized row with an infinite or empty frame.
        // Asking XCTest for its hit point then throws instead of returning false.
        return frame.minX.isFinite && frame.minY.isFinite && frame.width.isFinite && frame.height.isFinite
            && !frame.isEmpty && app.frame.intersects(frame)
    }

    private func isFiniteFrame(_ frame: CGRect) -> Bool {
        frame.minX.isFinite && frame.minY.isFinite && frame.width.isFinite && frame.height.isFinite && !frame.isEmpty
    }

    private func revealAboveToolbar(_ element: XCUIElement) {
        for _ in 0..<8 {
            if hasVisibleFrame(element), element.frame.minY > app.frame.minY + 150, element.isHittable { return }
            app.swipeDown()
        }
        guard hasVisibleFrame(element), element.frame.minY > app.frame.minY + 150 else {
            XCTFail("The requested control must clear the navigation bar before checking its hit point"); return
        }
        XCTAssertTrue(element.isHittable)
    }

    private func launchRecoveryFixture(_ scenario: String) {
        app.terminate()
        app.launchEnvironment["RIJI_TEST_BODY_RECOVERY"] = scenario
        app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
    }

    private func launchNavigationFixture(failing: Bool = false, tree: Bool = false) {
        app.launchEnvironment["RIJI_TEST_NAVIGATION"] = "1"
        if tree { app.launchEnvironment["RIJI_TEST_VAULT_BROWSER"] = "1" }
        launchRecoveryFixture(failing ? "navigation-failure" : "navigation")
    }

    private func viewport(_ key: String) -> Int {
        let parts = input.label.components(separatedBy: key + "=")
        guard parts.count == 2, let value = Int(parts[1].prefix { $0 == "-" || $0.isNumber }) else {
            XCTFail("The production editor must expose its actual test viewport: \(input.label)"); return 0
        }
        return value
    }

    private func dragBody(from start: CGVector, to end: CGVector) {
        input.coordinate(withNormalizedOffset: start).press(forDuration: 0.01,
            thenDragTo: input.coordinate(withNormalizedOffset: end))
    }

    private var legacyRoundtableIdentifier: String { "mobileGroup-00000000-0000-4000-8000-000000000056" }
    private var legacyRoundtableEntry: XCUIElement { app.buttons[legacyRoundtableIdentifier] }
    private var groupComposer: XCUIElement { app.descendants(matching: .any).matching(identifier: "roundtableInput").firstMatch }
    private var currentGroup: XCUIElement { app.descendants(matching: .any).matching(identifier: "mobileCurrentGroup").firstMatch }

    private func assertInlineGroup(named name: String? = nil, exactName: Bool = false) {
        XCTAssertTrue(currentGroup.waitForExistence(timeout: 10))
        if let name {
            let predicate = NSPredicate(format: exactName ? "label == %@" : "label CONTAINS %@", name)
            let named = XCTNSPredicateExpectation(predicate: predicate, object: currentGroup)
            XCTAssertEqual(XCTWaiter.wait(for: [named], timeout: 10), .completed)
        }
        XCTAssertTrue(app.navigationBars["导师"].staticTexts["导师"].exists)
        XCTAssertFalse(app.navigationBars.buttons["导师"].exists, "A group must be displayed on Mentors home without a back destination")
        XCTAssertTrue(app.buttons["mobileGroupSwitcher"].isHittable)
        XCTAssertTrue(groupComposer.waitForExistence(timeout: 5))
        XCTAssertEqual(app.descendants(matching: .any).matching(identifier: "roundtableInput").count, 1,
                       "Only the selected group's composer may be mounted")
    }

    private func openGroupSwitcher() {
        let switcher = app.buttons["mobileGroupSwitcher"]
        XCTAssertTrue(switcher.waitForExistence(timeout: 5)); XCTAssertTrue(switcher.isHittable)
        switcher.tap()
    }

    private func selectLegacyRoundtable() {
        openGroupSwitcher()
        // Use the same five-second stability gate as other group choices, but
        // preserve this caller's one native tap and select by its exact fixture ID.
        tapStableGroupMenuEntry(named: "已有的合成圆桌记录", identifier: legacyRoundtableIdentifier, nativeTap: true)
        let closed = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            !self.legacyRoundtableEntry.exists
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [closed], timeout: 5), .completed,
                       "Choosing the legacy group must dismiss its menu before checking the destination")
        // The legacy fixture has no explicit detail title; its loaded displayTitle
        // is distinct from the list's first-message title.
        assertInlineGroup(named: "圆桌记录", exactName: true)
    }

    private func selectSyntheticGroup(_ name: String, archived: Bool = false) {
        openGroupSwitcher()
        if archived { tapVisibleGroupAction(app.buttons["mobileArchivedGroups"]) }
        let entry = syntheticGroupRow(name)
        chooseGroupMenuEntry(entry, named: name)
    }

    private func chooseGroupMenuEntry(_ entry: XCUIElement, named name: String) {
        XCTAssertTrue(entry.waitForExistence(timeout: 5))
        tapStableGroupMenuEntry(named: name)
        let closed = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in !entry.exists }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [closed], timeout: 5), .completed,
                       "Choosing a group must dismiss its native menu before checking the selected conversation")
        assertInlineGroup(named: name)
    }

    private struct GroupMenuTarget: Equatable {
        let identifier: String
        let label: String
        let frame: CGRect
        let bounds: CGRect
    }

    private func tapStableGroupMenuEntry(named name: String, identifier: String? = nil, nativeTap: Bool = false) {
        var previous: GroupMenuTarget?
        var captured: GroupMenuTarget?
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            guard let visible = self.groupMenuEntryFrame(named: name, identifier: identifier) else {
                previous = nil; return false
            }
            let stable = previous == visible
            previous = visible
            if stable { captured = visible }
            return stable
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 5), .completed,
                       "Two root snapshots must agree on the unique visible enabled group menu entry")
        guard let visible = captured else { XCTFail("The group menu entry must have a stable captured frame"); return }
        let evidence = XCTAttachment(string: """
            Requested group: \(name)
            Identifier: \(visible.identifier)
            Label: \(visible.label)
            Frame: \(visible.frame)
            App bounds: \(visible.bounds)
            """)
        evidence.name = "Stable group menu target before native tap"; evidence.lifetime = .keepAlways; add(evidence)
        let identity = visible.identifier.isEmpty
            ? NSPredicate(format: "label == %@ AND (identifier == %@ OR identifier == nil)", visible.label, "")
            : NSPredicate(format: "identifier == %@ AND label == %@", visible.identifier, visible.label)
        let matches = app.buttons.matching(identity)
        let count = matches.count
        guard count == 1 else { XCTFail("The captured group menu identity must resolve uniquely; found \(count)"); return }
        // A single deliberate touch must activate the settled native menu. Keep
        // dismissal and conversation assertions in the caller; never retry it.
        if nativeTap {
            XCTAssertTrue(matches.element.isHittable, "The stable exact group entry must have a native hit target")
            matches.element.tap()
        } else { matches.element.press(forDuration: 0.15) }
    }

    private func groupMenuEntryFrame(named name: String, identifier: String? = nil) -> GroupMenuTarget? {
        let labels = syntheticGroupMenuLabels(name)
        return uniqueVisibleButton { node in
            if let identifier { return node.identifier == identifier }
            return labels.contains(node.label) && (node.identifier.isEmpty || node.identifier.hasPrefix("mobileGroup-"))
        }
    }

    private func uniqueVisibleButton(matching identity: (XCUIElementSnapshot) -> Bool) -> GroupMenuTarget? {
        guard let snapshot = try? app.snapshot() else {
            XCTFail("The application must provide a snapshot of the requested button"); return nil
        }
        var nodes: [XCUIElementSnapshot] = [snapshot]
        var matches: [XCUIElementSnapshot] = []
        while let node = nodes.popLast() {
            if node.elementType == .button, identity(node) { matches.append(node) }
            nodes.append(contentsOf: node.children)
        }
        guard matches.count <= 1 else {
            XCTFail("The requested identity must match exactly one visible native button"); return nil
        }
        guard let entry = matches.first, entry.isEnabled, isFiniteFrame(snapshot.frame),
              isFiniteFrame(entry.frame), snapshot.frame.contains(entry.frame) else { return nil }
        return GroupMenuTarget(identifier: entry.identifier, label: entry.label, frame: entry.frame, bounds: snapshot.frame)
    }

    private func assertInlineInputReachableWithKeyboard() {
        let keyboard = app.keyboards.firstMatch
        XCTAssertTrue(keyboard.waitForExistence(timeout: 5))
        for control in [groupComposer, app.buttons["roundtableSend"], app.buttons["mobileGroupSwitcher"]] {
            XCTAssertTrue(control.isHittable)
            XCTAssertGreaterThan(control.frame.height, 0)
            XCTAssertGreaterThanOrEqual(control.frame.minY, app.navigationBars.firstMatch.frame.maxY)
            XCTAssertLessThanOrEqual(control.frame.maxY, keyboard.frame.minY + 1)
        }
        XCTAssertLessThan(app.buttons["mobileGroupSwitcher"].frame.maxY, groupComposer.frame.minY,
                          "Switching and writing must both remain visible in the same home viewport")
        assertInlineMessageViewportWithKeyboard()
    }

    private func assertInlineMessageViewportWithKeyboard() {
        let keyboard = app.keyboards.firstMatch
        XCTAssertTrue(keyboard.waitForExistence(timeout: 5))
        XCTAssertTrue(groupComposer.isHittable)
        XCTAssertLessThanOrEqual(groupComposer.frame.maxY, keyboard.frame.minY + 1)
        let messages = app.scrollViews["roundtableMessages"]
        XCTAssertTrue(messages.exists)
        XCTAssertGreaterThan(messages.frame.height, 56,
                             "New replies and selection controls must not collapse the message viewport")
        XCTAssertGreaterThanOrEqual(messages.frame.minY, app.buttons["mobileGroupSwitcher"].frame.maxY)
        XCTAssertLessThanOrEqual(messages.frame.maxY, groupComposer.frame.minY)
    }

    private func launchMobileFixture(_ mode: String) {
        app.terminate()
        app.launchEnvironment["RIJI_TEST_MOBILE"] = mode
        app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
    }

    func testMentorNorthStarOriginalParagraphPersistsAcrossChatsAndSourceSwitchClearsIt() {
        app.terminate()
        for key in ["RIJI_TEST_VAULT_BROWSER", "RIJI_TEST_ORIGINAL_VAULT", "RIJI_TEST_NORTH_STAR"] { app.launchEnvironment[key] = "1" }
        launchMobileFixture("roundtable")
        let original = input.value as? String
        app.tabBars.buttons["导师"].tap()
        let entranceTitle = app.navigationBars["导师"].staticTexts["导师"]
        XCTAssertTrue(entranceTitle.waitForExistence(timeout: 5)); XCTAssertTrue(entranceTitle.isHittable)
        assertInlineGroup()
        for actor in ["gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"] {
            XCTAssertTrue(app.buttons["mobileMentor-" + actor].exists)
        }
        attachMentorScreenshot("Approved mentor entrance — forest and warm portraits")
        verifyNorthStarPickerFromFocusedGroup()
        app.buttons["mobileMentor-future_self"].tap()
        let anchor = app.buttons["northStarAnchor"]
        XCTAssertTrue(anchor.waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["northStarCurrentGoal"].exists)
        anchor.tap()
        let file = app.buttons["northStarFile-Phase.md"]
        XCTAssertTrue(file.waitForExistence(timeout: 5))
        XCUIDevice.shared.press(.home); app.activate()
        XCTAssertTrue(file.waitForExistence(timeout: 5)); file.tap()
        let paragraph = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "northStarParagraph-")).element(boundBy: 1)
        XCTAssertTrue(paragraph.waitForExistence(timeout: 5)); paragraph.tap()
        let confirm = app.buttons["northStarConfirm"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 5)); confirm.tap()
        let current = app.staticTexts["northStarCurrentGoal"]
        XCTAssertTrue(current.waitForExistence(timeout: 5))
        XCTAssertEqual(current.label, "Synthetic phase direction: practice small steps each week.")
        attachMentorScreenshot("Approved single mentor stage — verified synthetic direction")
        let singleInput = app.descendants(matching: .any).matching(identifier: "mobileChatDraft").firstMatch
        singleInput.tap(); singleInput.typeText("Synthetic next step")
        app.buttons["mobileSend"].tap()
        let reply = app.staticTexts["Synthetic mentor reply"]
        XCTAssertTrue(reply.waitForExistence(timeout: 10))
        attachMentorScreenshot("North Star selected — future self")
        reveal(reply); attachMentorScreenshot("North Star future self — complete dialogue")
        app.navigationBars.buttons["导师"].tap()
        selectLegacyRoundtable()
        XCTAssertTrue(current.waitForExistence(timeout: 5))
        let roundtableInput = app.descendants(matching: .any).matching(identifier: "roundtableInput").firstMatch
        roundtableInput.tap(); roundtableInput.typeText("Synthetic next step discussion")
        app.buttons["roundtableSend"].tap()
        let invite = app.buttons["roundtableInvite"]
        XCTAssertTrue(invite.waitForExistence(timeout: 10)); invite.tap()
        app.buttons["roundtablePreviewInvite"].tap()
        let invitation = app.buttons["roundtableConfirmInvite"]
        XCTAssertTrue(invitation.waitForExistence(timeout: 5)); invitation.tap()
        let invitationClosed = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: invitation)
        XCTAssertEqual(XCTWaiter.wait(for: [invitationClosed], timeout: 5), .completed)
        let participants = app.staticTexts["roundtableParticipants"]
        // The inline conversation restores the latest message; its lazy header must be scrolled into view.
        revealNorthStarRoundtableElement(participants, towardEarlier: true)
        let participating = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label CONTAINS %@", "本轮发言"), object: participants)
        XCTAssertEqual(XCTWaiter.wait(for: [participating], timeout: 10), .completed)
        XCTAssertTrue(participants.label.contains("温柔回顾者")); XCTAssertTrue(participants.label.contains("直率教练"))
        XCTAssertFalse(participants.label.contains("未来的我")); XCTAssertFalse(participants.label.contains("王阳明"))
        attachMentorScreenshot("North Star selected — roundtable")
        let latest = app.staticTexts["建议先降低起步难度，每周做一个小练习，避免把学习变成额外压力。"]
        revealNorthStarRoundtableElement(latest, towardEarlier: false); attachMentorScreenshot("Roundtable — current mentor dialogue")
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10)); XCTAssertEqual(input.value as? String, original)
        app.tabBars.buttons["导师"].tap(); app.buttons["mobileMentor-future_self"].tap()
        XCTAssertTrue(current.waitForExistence(timeout: 5))
        app.tabBars.buttons["设置"].tap(); app.buttons["settingsFiles"].tap(); app.buttons["settingsLocalDiaries"].tap()
        app.tabBars.buttons["导师"].tap()
        XCTAssertTrue(anchor.waitForExistence(timeout: 5)); XCTAssertFalse(current.exists)
        app.tabBars.buttons["日记"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        attachMentorScreenshot("Diary after mentor destination — local appearance")
    }

    private func revealNorthStarRoundtableElement(_ element: XCUIElement, towardEarlier: Bool) {
        let messages = app.scrollViews["roundtableMessages"]
        XCTAssertTrue(messages.waitForExistence(timeout: 5))
        var previousPosition: String?
        var unchangedPositions = 0
        for _ in 0..<16 {
            let visible = messages.frame.insetBy(dx: 4, dy: 8)
            XCTAssertGreaterThan(visible.height, 60)
            var earlier = towardEarlier
            var distance = visible.height * 0.6
            if element.exists {
                let frame = element.frame
                if element.isHittable && visible.contains(frame) { return }
                earlier = frame.midY < visible.midY
                distance = min(distance, max(20, abs(frame.midY - visible.midY)))
            }
            let cards = messages.descendants(matching: .any).matching(
                NSPredicate(format: "identifier BEGINSWITH %@", "roundtableMessage-"))
            let position = cards.allElementsBoundByIndex.filter { $0.frame.intersects(visible) }
                .map { $0.identifier + ":" + String(Int($0.frame.minY.rounded())) }.joined(separator: "|")
            unchangedPositions = position == previousPosition ? unchangedPositions + 1 : 0
            guard unchangedPositions < 2 else {
                XCTFail("The roundtable stopped scrolling before the requested content became visible"); return
            }
            previousPosition = position
            // This target's direction is known; do not reverse halfway through the six-page conversation.
            let origin = messages.coordinate(withNormalizedOffset: .zero)
            let startY = visible.minY - messages.frame.minY + visible.height * (earlier ? 0.2 : 0.8)
            let start = origin.withOffset(CGVector(dx: messages.frame.width / 2, dy: startY))
            start.press(forDuration: 0.05, thenDragTo: start.withOffset(CGVector(dx: 0, dy: earlier ? distance : -distance)),
                        withVelocity: .slow, thenHoldForDuration: 0.2)
        }
        XCTFail("The requested North Star conversation content must be visible within the message viewport")
    }

    func testMentorAccessibilityTextKeyboardAndRoundtableControlsRemainReachable() {
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        launchMobileFixture("roundtable")
        app.tabBars.buttons["导师"].tap()
        let entranceTitle = app.navigationBars["导师"].staticTexts["导师"]
        XCTAssertTrue(entranceTitle.waitForExistence(timeout: 5)); XCTAssertTrue(entranceTitle.isHittable)
        assertInlineGroup()
        attachMentorScreenshot("Approved mentor entrance — accessibility XXXL")
        let mentor = app.buttons["mobileMentor-future_self"]; reveal(mentor); mentor.tap()
        let draft = app.descendants(matching: .any).matching(identifier: "mobileChatDraft").firstMatch
        XCTAssertTrue(draft.waitForExistence(timeout: 5)); draft.tap()
        draft.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        draft.typeText("Synthetic accessible draft")
        XCTAssertTrue(app.buttons["mobileSend"].isHittable)
        XCTAssertLessThanOrEqual(app.buttons["mobileSend"].frame.maxY, app.keyboards.firstMatch.frame.minY + 1)
        attachMentorScreenshot("Accessibility XXXL — mentor keyboard")
        app.buttons["mobileSend"].tap()
        app.navigationBars.buttons["导师"].tap()
        selectLegacyRoundtable()
        let composer = app.descendants(matching: .any).matching(identifier: "roundtableInput").firstMatch
        XCTAssertTrue(composer.waitForExistence(timeout: 5)); composer.tap()
        composer.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        composer.typeText("Synthetic accessible roundtable")
        XCTAssertTrue(app.buttons["roundtableSend"].isHittable)
        XCTAssertLessThanOrEqual(app.buttons["roundtableSend"].frame.maxY, app.keyboards.firstMatch.frame.minY + 1)
        attachMentorScreenshot("Accessibility XXXL — roundtable keyboard")
        app.buttons["roundtableSend"].tap()
        XCTAssertTrue(app.buttons["roundtableInvite"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["roundtableInvite"].isHittable)
    }

    private func verifyNorthStarPickerFromFocusedGroup() {
        let draft = "Synthetic group draft beside the North Star"
        groupComposer.tap(); groupComposer.typeText(draft)
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        XCTAssertEqual(groupComposer.value as? String, draft)
        XCTAssertTrue(app.buttons["roundtableSend"].isEnabled)
        let anchor = app.buttons["northStarAnchor"]
        XCTAssertTrue(anchor.isHittable); anchor.tap()
        let navigation = app.navigationBars["本阶段北极星"]
        XCTAssertTrue(navigation.waitForExistence(timeout: 5))
        let file = app.buttons["northStarFile-Phase.md"]
        XCTAssertTrue(file.waitForExistence(timeout: 5)); file.tap()
        let paragraph = app.buttons.matching(NSPredicate(
            format: "identifier BEGINSWITH %@", "northStarParagraph-")).element(boundBy: 1)
        XCTAssertTrue(paragraph.waitForExistence(timeout: 5)); paragraph.tap()
        XCTAssertTrue(app.buttons["northStarConfirm"].waitForExistence(timeout: 5),
                      "The focused-home picker must stay presented through file selection and preview")
        attachMentorScreenshot("Focused group — North Star preview survives header changes")
        navigation.buttons["完成"].tap()
        let closed = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in !navigation.exists }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [closed], timeout: 5), .completed)
        XCTAssertEqual(groupComposer.value as? String, draft)
        XCTAssertFalse(app.staticTexts["northStarCurrentGoal"].exists,
                       "Closing the preview must not confirm an association")
        groupComposer.tap(); groupComposer.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        groupComposer.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: draft.count))
        XCTAssertFalse(app.buttons["roundtableSend"].isEnabled)
    }

    func testMentorNorthStarExternalChangeInvalidatesOpenChat() {
        app.terminate()
        for key in ["RIJI_TEST_VAULT_BROWSER", "RIJI_TEST_ORIGINAL_VAULT", "RIJI_TEST_EXTERNAL_REFRESH"] {
            app.launchEnvironment[key] = "1"
        }
        launchMobileFixture("connected")
        app.tabBars.buttons["导师"].tap(); app.buttons["mobileMentor-future_self"].tap()
        let anchor = app.buttons["northStarAnchor"]
        XCTAssertTrue(anchor.waitForExistence(timeout: 5)); anchor.tap()
        let folder = app.buttons["northStarFile-daily"]
        XCTAssertTrue(folder.waitForExistence(timeout: 5)); folder.tap()
        let file = app.buttons["northStarFile-daily/2026-09-12.md"]
        XCTAssertTrue(file.waitForExistence(timeout: 5)); file.tap()
        let paragraph = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "northStarParagraph-")).firstMatch
        XCTAssertTrue(paragraph.waitForExistence(timeout: 5)); paragraph.tap()
        let confirm = app.buttons["northStarConfirm"]
        reveal(confirm); confirm.tap()
        let current = app.staticTexts["northStarCurrentGoal"]
        XCTAssertTrue(current.waitForExistence(timeout: 5))
        app.buttons["simulateExternalEdit"].tap()
        // Stay on this destination; only the production 15-second verifier can retract the claim.
        let invalidated = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: current)
        XCTAssertEqual(XCTWaiter.wait(for: [invalidated], timeout: 25), .completed)
        XCTAssertTrue(anchor.label.contains("来源待核验"))
        anchor.tap()
        XCTAssertTrue(app.staticTexts["northStarSourceError"].waitForExistence(timeout: 5))
        attachMentorScreenshot("North Star external change — recovery")
        let unlink = app.buttons["northStarUnlink"]; reveal(unlink)
        XCTAssertTrue(unlink.isHittable); unlink.tap()
        XCTAssertFalse(app.staticTexts["northStarSourceError"].exists)
        XCTAssertFalse(unlink.exists)
        app.navigationBars.buttons["完成"].tap()
        XCTAssertTrue(anchor.label.contains("选择本阶段方向"))
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        app.tabBars.buttons["导师"].tap(); app.buttons["mobileMentor-future_self"].tap()
        XCTAssertTrue(anchor.waitForExistence(timeout: 5)); XCTAssertFalse(current.exists)
        XCTAssertTrue(anchor.label.contains("选择本阶段方向"))
    }

    private func attachMentorScreenshot(_ name: String) {
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = name; screenshot.lifetime = .keepAlways; add(screenshot)
    }

    private func captureMemoryCardEntry(_ card: XCUIElement, named name: String) {
        XCTAssertTrue(card.waitForExistence(timeout: 5))
        for _ in 0..<8 {
            let navigationBottom = app.navigationBars["记忆"].frame.maxY
            let tabTop = app.tabBars.firstMatch.frame.minY
            let top = navigationBottom + 44 + 12
            let bottom = tabTop - 12
            XCTAssertGreaterThan(bottom - top, 80)
            let frame = card.frame
            XCTAssertTrue(frame.minY.isFinite && frame.height.isFinite && !frame.isEmpty)
            if frame.maxY > top, frame.maxY < bottom { break }
            // A full-screen swipe can move the large card's entry under the
            // navigation bar. Center its bottom edge with bounded, reversible drags.
            let offset = (top + bottom) / 2 - frame.maxY
            let distance = min(120, abs(offset))
            let origin = app.coordinate(withNormalizedOffset: .zero)
            let start = origin.withOffset(CGVector(dx: app.frame.width / 2,
                dy: (navigationBottom + tabTop) / 2 - app.frame.minY))
            let end = start.withOffset(CGVector(dx: 0, dy: offset > 0 ? distance : -distance))
            start.press(forDuration: 0.05, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.2)
        }
        XCTAssertTrue(card.isHittable)
        XCTAssertLessThan(card.frame.maxY, app.tabBars.firstMatch.frame.minY)
        XCTAssertGreaterThan(card.frame.maxY, app.navigationBars.firstMatch.frame.maxY + 44)
        attachMentorScreenshot(name)
    }

    func testMobileManagedInstallationConnectsWithoutLeavingFourPeerTabs() {
        launchMobileFixture("managed")
        for name in ["日记", "导师", "记忆", "设置"] { XCTAssertTrue(app.tabBars.buttons[name].exists) }
        app.tabBars.buttons["设置"].tap()
        app.buttons["settingsDataAI"].tap(); app.buttons["settingsConnection"].tap()
        let connection = app.staticTexts["settingsAirStatus"]
        let connected = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label CONTAINS %@", "服务已连接"), object: connection)
        XCTAssertEqual(XCTWaiter.wait(for: [connected], timeout: 10), .completed)
        XCTAssertFalse(app.textFields["mobileEndpoint"].exists)
        XCTAssertFalse(app.secureTextFields["mobilePairingCode"].exists)
        XCTAssertFalse(app.buttons["mobilePair"].exists)
        let evidence = XCTAttachment(screenshot: app.screenshot())
        evidence.name = "Synthetic Air connection in the Settings tab"; evidence.lifetime = .keepAlways; add(evidence)
        app.tabBars.buttons["导师"].tap()
        XCTAssertTrue(app.buttons["mobileMentor-future_self"].waitForExistence(timeout: 5))
        app.tabBars.buttons["记忆"].tap()
        XCTAssertTrue(app.buttons["mobileRawMemories"].waitForExistence(timeout: 5)); app.buttons["mobileRawMemories"].tap()
        XCTAssertTrue(app.buttons["mobileMemory-synthetic-memory"].waitForExistence(timeout: 5))
        app.terminate(); app.launch()
        app.tabBars.buttons["导师"].tap()
        XCTAssertTrue(app.buttons["mobileMentor-future_self"].waitForExistence(timeout: 5))
        app.tabBars.buttons["日记"].tap(); XCTAssertTrue(input.waitForExistence(timeout: 5))
    }

    func testMobileMentorDraftColdRecoveryReplyAndMemoryCorrection() {
        launchMobileFixture("connected")
        app.tabBars.buttons["导师"].tap()
        let mentor = app.buttons["mobileMentor-future_self"]
        XCTAssertTrue(mentor.waitForExistence(timeout: 5)); mentor.tap()
        let draft = app.descendants(matching: .any).matching(identifier: "mobileChatDraft").firstMatch
        XCTAssertTrue(draft.waitForExistence(timeout: 5)); draft.tap(); draft.typeText("Synthetic retained draft")
        // Keyboard injection can return before the final characters reach the field.
        // Verify the input precondition before testing abrupt cold recovery.
        let entered = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", "Synthetic retained draft"), object: draft)
        XCTAssertEqual(XCTWaiter.wait(for: [entered], timeout: 5), .completed,
                       "The complete draft must reach the composer before termination")
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10)); app.tabBars.buttons["导师"].tap()
        XCTAssertTrue(mentor.waitForExistence(timeout: 5)); mentor.tap()
        XCTAssertTrue(draft.waitForExistence(timeout: 5))
        XCTAssertEqual(draft.value as? String, "Synthetic retained draft")
        app.buttons["mobileSend"].tap()
        XCTAssertTrue(app.staticTexts["Synthetic mentor reply"].waitForExistence(timeout: 10))
        XCTAssertNotEqual(draft.value as? String, "Synthetic retained draft")
        let chatEvidence = XCTAttachment(screenshot: app.screenshot())
        chatEvidence.name = "Synthetic native mentor response"; chatEvidence.lifetime = .keepAlways; add(chatEvidence)
        app.tabBars.buttons["记忆"].tap()
        XCTAssertTrue(app.buttons["mobileRawMemories"].waitForExistence(timeout: 5)); app.buttons["mobileRawMemories"].tap()
        let fact = app.staticTexts["Synthetic memory for mobile acceptance"]
        XCTAssertTrue(fact.waitForExistence(timeout: 10)); fact.tap()
        let correction = app.descendants(matching: .any).matching(identifier: "mobileMemoryCorrection").firstMatch
        XCTAssertTrue(correction.waitForExistence(timeout: 5)); correction.tap(); correction.typeText("Synthetic corrected fact")
        let save = app.buttons["确认纠正"]; revealMemoryCorrectionControl(save); save.tap()
        app.buttons["mobileSubmitCorrection"].tap()
        XCTAssertTrue(app.staticTexts["Synthetic corrected fact"].waitForExistence(timeout: 10))
        let settingsVisible = XCTNSPredicateExpectation(predicate: NSPredicate(format: "isHittable == true"), object: app.tabBars.buttons["设置"])
        XCTAssertEqual(XCTWaiter.wait(for: [settingsVisible], timeout: 5), .completed)
        XCTAssertEqual(app.keyboards.count, 0, "Submitting a correction must not restore the obscuring input keyboard")
        let memoryEvidence = XCTAttachment(screenshot: app.screenshot())
        memoryEvidence.name = "Synthetic native memory correction"; memoryEvidence.lifetime = .keepAlways; add(memoryEvidence)
        app.tabBars.buttons["设置"].tap(); app.buttons["settingsDataAI"].tap(); app.buttons["settingsConnection"].tap()
        app.buttons["资料与 AI 使用"].tap()
        XCTAssertTrue(app.staticTexts["实际来源范围"].waitForExistence(timeout: 5))
        app.tabBars.buttons["日记"].tap(); XCTAssertTrue(input.waitForExistence(timeout: 5))
    }

    private func createSyntheticGroup() {
        let create = app.buttons["mobileCreateGroup"]
        XCTAssertTrue(create.waitForExistence(timeout: 5)); create.tap()
        let confirm = app.buttons["groupConfirmCreate"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 5)); XCTAssertFalse(confirm.isEnabled)
        for actor in ["gentle_reviewer", "blunt_coach"] {
            let choice = app.buttons["groupSelect-" + actor]
            XCTAssertTrue(choice.waitForExistence(timeout: 5)); choice.tap()
        }
        XCTAssertTrue(confirm.isEnabled); confirm.tap()
        XCTAssertTrue(app.staticTexts["groupChatEmpty"].waitForExistence(timeout: 10))
        assertInlineGroup()
    }

    private func renameSyntheticGroup(_ name: String) {
        app.buttons["groupDetails"].tap()
        let entry = app.buttons["groupNameEntry"]
        XCTAssertTrue(entry.waitForExistence(timeout: 5)); entry.tap()
        let field = app.textFields["groupNameInput"]
        XCTAssertTrue(field.waitForExistence(timeout: 5)); field.tap()
        let previous = field.value as? String ?? ""
        field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: previous.count) + name)
        let save = app.buttons["groupRename"]; reveal(save); save.tap()
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        XCTAssertFalse(field.exists, "A confirmed rename returns to details without a second save action")
        app.navigationBars.buttons["完成"].tap()
        assertInlineGroup(named: name)
    }

    private func syntheticGroupRow(_ name: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@ AND label IN %@",
                                        "mobileGroup-", syntheticGroupMenuLabels(name) as NSArray)).firstMatch
    }

    private func syntheticGroupMenuLabels(_ name: String) -> [String] {
        [name, name + " · 草稿", name + " · 新回复", name + " · 草稿 · 新回复"]
    }

    private func revealRoundtableMessage(_ element: XCUIElement, towardEarlier: Bool = false) {
        let messages = app.scrollViews["roundtableMessages"]
        XCTAssertTrue(messages.waitForExistence(timeout: 5))
        for attempt in 0..<18 {
            let visible = messages.frame.insetBy(dx: 4, dy: 8)
            XCTAssertGreaterThan(visible.height, 40, "The inline chat must provide a usable message viewport")
            var earlier = attempt < 9 ? towardEarlier : !towardEarlier
            var distance = min(80, visible.height * 0.5)
            if element.exists {
                let frame = element.frame
                let center = CGPoint(x: frame.midX, y: frame.midY)
                let fits = frame.height <= visible.height ? visible.contains(frame) : visible.contains(center)
                if element.isHittable && fits { return }
                earlier = frame.midY < visible.midY
                distance = min(distance, max(18, abs(frame.midY - visible.midY)))
                print("Inline message scroll \(attempt): viewport=\(visible), target=\(frame), hittable=\(element.isHittable), earlier=\(earlier), distance=\(distance)")
            }
            let origin = messages.coordinate(withNormalizedOffset: .zero)
            let startY = visible.minY - messages.frame.minY + visible.height * (earlier ? 0.25 : 0.75)
            // The padded gutter belongs to the ScrollView, outside selectable
            // message text and card menus. Slow center drags can select text.
            let startX = visible.minX - messages.frame.minX + 4
            let start = origin.withOffset(CGVector(dx: startX, dy: startY))
            let end = start.withOffset(CGVector(dx: 0, dy: earlier ? distance : -distance))
            // Short chat viewports need controlled scrolling: XCTest's .slow
            // velocity can still fling a tall message beyond the readable area.
            start.press(forDuration: 0.05, thenDragTo: end,
                        withVelocity: XCUIGestureVelocity(rawValue: 40), thenHoldForDuration: 0.2)
        }
        attachMentorScreenshot("Inline message scroll — unresolved target")
        let hierarchy = XCTAttachment(string: app.debugDescription)
        hierarchy.name = "Inline message scroll — final hierarchy"; hierarchy.lifetime = .keepAlways; add(hierarchy)
        XCTFail("The requested message must become visible and hittable inside the inline chat")
    }

    private func openRoundtableMessageActions(last: Bool = false, longPress: Bool = false) -> String {
        let actions = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "roundtableMessageActions-"))
        XCTAssertTrue(actions.firstMatch.waitForExistence(timeout: 10))
        let actionID = (last ? actions.element(boundBy: actions.count - 1) : actions.firstMatch).identifier
        let action = app.buttons[actionID]
        revealRoundtableMessage(action, towardEarlier: !last)
        let identifier = String(actionID.dropFirst("roundtableMessageActions-".count))
        if longPress {
            let message = app.descendants(matching: .any).matching(identifier: "roundtableMessage-" + identifier).firstMatch
            revealRoundtableMessage(message, towardEarlier: !last)
            message.press(forDuration: 1.1)
        } else { action.tap() }
        return identifier
    }

    private func beginEditingRoundtableResult(append text: String = "") -> String {
        let preview = app.textViews["roundtableRenderedPreview"]
        revealRoundtableResultFormElement(preview, allowingPartialText: true, towardTop: true)
        XCTAssertTrue(preview.waitForExistence(timeout: 10))
        let edit = app.buttons["roundtableEditResult"]
        revealRoundtableResultFormElement(edit)
        XCTAssertTrue(edit.isHittable); edit.tap()
        let editor = app.textViews["roundtableResultText"]
        revealRoundtableResultFormElement(editor, towardTop: true)
        XCTAssertTrue(editor.waitForExistence(timeout: 5))
        let original = editor.value as? String ?? ""
        XCTAssertFalse(original.isEmpty)
        let confirm = app.buttons["roundtableConfirmSave"]
        XCTAssertTrue(!confirm.exists || !confirm.isEnabled, "Editing must invalidate the old confirmation")
        if !text.isEmpty { editor.tap(); editor.typeText(text) }
        return text.isEmpty ? original : (editor.value as? String ?? "")
    }

    private func finishEditingRoundtableResult() {
        let finish = app.buttons["roundtableFinishEditingResult"]
        revealRoundtableResultFormElement(finish); finish.tap()
        let preview = app.descendants(matching: .any).matching(identifier: "roundtableRenderedPreview").firstMatch
        revealRoundtableResultFormElement(preview, allowingPartialText: true, towardTop: true)
        XCTAssertTrue(preview.waitForExistence(timeout: 10))
    }

    private func editRoundtableResult(append text: String = "") -> String {
        let expected = beginEditingRoundtableResult(append: text)
        finishEditingRoundtableResult()
        return expected
    }

    private func exactRoundtableResultPreview(_ selectedText: String) -> String {
        let disclosure = app.buttons["查看确切 Markdown（含来源记录）"]
        revealRoundtableResultFormElement(disclosure); disclosure.tap()
        let preview = app.staticTexts["roundtableExactMarkdown"]
        // DisclosureGroup can expand while its lazy text row remains off-screen.
        // Mount that row by scrolling the Form before reading the entire label.
        revealRoundtableResultFormElement(preview, allowingPartialText: true)
        XCTAssertTrue(preview.waitForExistence(timeout: 5))
        let block = preview.label
        let endMarker = "<!-- /riji:ai-discussion-result -->"
        XCTAssertTrue(block.hasPrefix("<!-- riji:ai-discussion-result ")); XCTAssertTrue(block.hasSuffix(endMarker))
        let quotedSelection = selectedText.replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n").components(separatedBy: "\n").map { "> " + $0 }.joined(separator: "\n")
        XCTAssertTrue(block.contains("\n" + quotedSelection + "\n" + endMarker),
                      "The exact preview must quote every selected line and retain the AI source boundary")
        return block
    }

    private func diaryRawMarkdownFromSources(edited: Bool, screenshotName: String) -> String {
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        let readable = input.value as? String ?? ""
        XCTAssertTrue(readable.contains("AI 导师讨论结果"))
        for internalValue in ["riji:ai-discussion-result", "gentle_reviewer", "blunt_coach", "\"result_id\"", "\"discussion_id\""] {
            XCTAssertFalse(readable.contains(internalValue), "Ordinary diary reading must conceal machine fields")
        }
        input.swipeUp(); attachMentorScreenshot(screenshotName + " — readable diary without machine fields")
        app.buttons["moreMenu"].tap()
        let sources = app.buttons["diaryAISources"]
        XCTAssertTrue(sources.waitForExistence(timeout: 5)); tapVisibleGroupAction(sources, nativeButton: true)
        let dismissed = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "exists == false"), object: sources)
        XCTAssertEqual(XCTWaiter.wait(for: [dismissed], timeout: 5), .completed,
                       "Opening AI sources must dismiss the native diary menu")
        let close = app.buttons["closeDiaryAISources"]
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        let raw = app.staticTexts["diaryAIRawMarkdown"]
        XCTAssertFalse(raw.exists, "Raw Markdown must require an explicit disclosure")
        XCTAssertTrue(app.staticTexts["两周学习实验"].exists)
        let mentors = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "参与导师：")).firstMatch
        XCTAssertTrue(mentors.exists); XCTAssertTrue(mentors.label.contains("温柔回顾者")); XCTAssertTrue(mentors.label.contains("直率教练"))
        XCTAssertTrue(app.staticTexts["保存前用户编辑：" + (edited ? "是" : "否")].exists)
        XCTAssertTrue(app.staticTexts["保存日期：2026-09-12"].exists)
        attachMentorScreenshot(screenshotName + " — readable local AI source summary")
        let disclosure = app.buttons["diaryAIRawDisclosure"]
        XCTAssertTrue(disclosure.waitForExistence(timeout: 5)); reveal(disclosure); disclosure.tap()
        XCTAssertTrue(raw.waitForExistence(timeout: 5)); XCTAssertFalse(app.textViews["diaryAIRawMarkdown"].exists)
        let markdown = raw.label
        XCTAssertFalse(markdown.isEmpty)
        attachMentorScreenshot(screenshotName + " — explicitly opened read-only original Markdown")
        close.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertEqual(Data((input.value as? String ?? "").utf8), Data(readable.utf8),
                       "Reading and closing sources must not change the diary")
        return markdown
    }

    private func cancelEditedResultAndResumeDiary(before: String) -> String {
        let expected = beginEditingRoundtableResult(append: "\nSynthetic text awaiting review.")
        let close = app.buttons["roundtableCloseResult"]
        XCTAssertTrue(close.waitForExistence(timeout: 5)); close.tap()
        let keepEditing = app.buttons.matching(NSPredicate(format: "identifier == %@ OR label == %@", "roundtableContinueEditingResult", "继续编辑")).firstMatch
        XCTAssertTrue(keepEditing.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["roundtableDiscardResultEdits"].exists)
        attachMentorScreenshot("Unsaved result text — explicit continue or discard")
        keepEditing.tap()
        XCTAssertEqual(app.textViews["roundtableResultText"].value as? String, expected,
                       "Leaving an unprepared edit must not silently lose its latest text")
        finishEditingRoundtableResult()
        let cancel = app.buttons["roundtableCancelSave"]
        revealRoundtableResultFormElement(cancel)
        XCTAssertTrue(cancel.waitForExistence(timeout: 5)); XCTAssertTrue(cancel.isEnabled)
        let frame = cancel.frame
        XCTAssertGreaterThan(frame.width, 0); XCTAssertGreaterThan(frame.height, 0)
        XCTAssertTrue(app.frame.contains(frame), "The entire enabled cancel button must be visible before tapping")
        attachMentorScreenshot("Prepared result — safe cancel remains available")
        // iOS 27 can misreport this visible button's activation point. Tap its
        // observed center after checking visibility and enabled state above.
        app.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(
            dx: frame.midX - app.frame.minX, dy: frame.midY - app.frame.minY)).tap()
        XCTAssertTrue(app.buttons["groupDetails"].waitForExistence(timeout: 10))
        app.tabBars.buttons["日记"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, before,
                       "Preparing, editing and cancelling must not append any diary content")
        type("Synthetic manual diary entry after cancelling AI save")
        let edited = input.value as? String ?? ""
        XCTAssertEqual(edited, before + "Synthetic manual diary entry after cancelling AI save\n")
        attachMentorScreenshot("Cancelled AI save — diary editing unlocked without an AI append")
        app.tabBars.buttons["导师"].tap()
        XCTAssertTrue(app.buttons["groupDetails"].waitForExistence(timeout: 5))
        return edited
    }

    func testMobileGroupNameEditorCancelsWithoutChangingNameAndDisablesUnchangedSave() {
        launchMobileFixture("roundtable")
        app.tabBars.buttons["导师"].tap(); createSyntheticGroup()
        app.buttons["groupDetails"].tap()
        let entry = app.buttons["groupNameEntry"]
        XCTAssertTrue(entry.waitForExistence(timeout: 5)); entry.tap()
        let field = app.textFields["groupNameInput"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        let original = field.value as? String ?? ""
        XCTAssertFalse(original.isEmpty); XCTAssertFalse(app.buttons["groupRename"].isEnabled)
        field.tap(); field.typeText("1")
        XCTAssertTrue(app.buttons["groupRename"].isEnabled)
        attachMentorScreenshot("Group rename — explicit save and cancel")
        app.buttons["groupNameCancel"].tap()
        XCTAssertTrue(entry.waitForExistence(timeout: 5)); entry.tap()
        XCTAssertTrue(field.waitForExistence(timeout: 5)); XCTAssertEqual(field.value as? String, original)
        XCTAssertFalse(app.buttons["groupRename"].isEnabled)
        app.buttons["groupNameCancel"].tap()
        app.navigationBars.buttons["完成"].tap()
        assertInlineGroup(named: original)
        renameSyntheticGroup("Synthetic explicitly saved group")
        attachMentorScreenshot("Group rename — confirmed title in the original chat")
    }

    func testMobileGroupMessageSelectionOnlyAppearsAfterExplicitActionAndCanCancel() {
        launchMobileFixture("roundtable")
        app.tabBars.buttons["导师"].tap(); createSyntheticGroup()
        let groupName = currentGroup.label
        let composer = groupComposer
        composer.tap(); composer.typeText("Synthetic selection explanation")
        app.buttons["roundtableSend"].tap()
        let actions = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "roundtableMessageActions-"))
        XCTAssertTrue(actions.firstMatch.waitForExistence(timeout: 10))
        let firstActionID = actions.firstMatch.identifier
        composer.tap(); composer.typeText("Second synthetic selection discussion")
        app.buttons["roundtableSend"].tap()
        revealRoundtableMessage(app.staticTexts["Second synthetic selection discussion"])
        let laterIdentifier = String(actions.element(boundBy: actions.count - 1).identifier.dropFirst("roundtableMessageActions-".count))
        attachMentorScreenshot("Normal group chat — no permanent selection circles")
        let firstAction = app.buttons[firstActionID]
        revealRoundtableMessage(firstAction, towardEarlier: true); firstAction.tap()
        let identifier = String(firstActionID.dropFirst("roundtableMessageActions-".count))
        XCTAssertEqual(app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "roundtableSelect-")).count, 0)
        let multi = app.buttons["roundtableMultiSelect-" + identifier]
        XCTAssertTrue(multi.waitForExistence(timeout: 5))
        attachMentorScreenshot("AI reply actions — explicitly named save and multi-select")
        enterRoundtableSelection(from: multi)
        let selected = app.buttons["roundtableSelect-" + identifier]
        XCTAssertTrue(selected.waitForExistence(timeout: 5))
        XCTAssertEqual(selected.value as? String, "已选择")
        toggleVisibleGroupSelection(selected, expecting: "未选择")
        toggleVisibleGroupSelection(selected, expecting: "已选择")
        XCTAssertTrue(app.buttons["roundtableSaveSelection"].exists)
        attachMentorScreenshot("Multi-select — visible selected state and cancel")
        verifySelectionAndReadingAcrossGroups(groupName: groupName, selectedID: identifier, laterID: laterIdentifier)
        let cancel = app.buttons["roundtableCancelSelection"]
        XCTAssertTrue(cancel.exists); cancel.tap()
        XCTAssertEqual(app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "roundtableSelect-")).count, 0)
        XCTAssertFalse(app.buttons["roundtableSaveSelection"].exists)
        XCTAssertTrue(composer.isHittable)
    }

    private func enterRoundtableSelection(from menuAction: XCUIElement) {
        // Visible system menus can report an invalid AX hit point. Resolve the
        // stable native button once, then require dismissal and selection state.
        tapVisibleGroupAction(menuAction, nativeButton: true)
        let dismissed = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "exists == false"), object: menuAction)
        XCTAssertEqual(XCTWaiter.wait(for: [dismissed], timeout: 5), .completed,
                       "The multi-select action must dismiss its native menu")
    }

    private func toggleVisibleGroupSelection(_ element: XCUIElement, expecting value: String) {
        // Emptying the selection changes the composer height. Re-read the message
        // viewport before each tap instead of reusing the previous activation point.
        revealRoundtableMessage(element, towardEarlier: true)
        let viewport = app.scrollViews["roundtableMessages"].frame
        XCTAssertTrue(viewport.contains(element.frame), "Selection must be fully inside the message viewport")
        tapVisibleGroupAction(element)
        let changed = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", value), object: element)
        XCTAssertEqual(XCTWaiter.wait(for: [changed], timeout: 5), .completed,
                       "A single selection tap must produce the requested state")
    }

    private func tapVisibleGroupAction(_ element: XCUIElement, nativeButton: Bool = false) {
        guard let target = try? element.snapshot() else {
            XCTFail("The requested action must provide its exact accessibility identity"); return
        }
        XCTAssertTrue(!target.identifier.isEmpty || !target.label.isEmpty)
        var previousFrame: CGRect?
        var previousBounds: CGRect?
        var capturedFrame: CGRect?
        var capturedBounds: CGRect?
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            guard let visible = self.visibleGroupActionSnapshot(matching: target) else {
                previousFrame = nil; previousBounds = nil; return false
            }
            let stable = previousFrame == visible.frame && previousBounds == visible.bounds
            previousFrame = visible.frame; previousBounds = visible.bounds
            if stable { capturedFrame = visible.frame; capturedBounds = visible.bounds }
            return stable
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 5), .completed,
                       "Two snapshots must agree on one fully visible enabled action before tapping")
        guard let frame = capturedFrame, let bounds = capturedBounds else {
            XCTFail("The action must have a stable captured frame"); return
        }
        if nativeButton {
            XCTAssertEqual(target.elementType, .button)
            XCTAssertFalse(target.identifier.isEmpty)
            let matches = app.buttons.matching(NSPredicate(format: "identifier == %@ AND label == %@",
                target.identifier, target.label))
            XCTAssertEqual(matches.count, 1, "The stable native action must resolve to exactly one button")
            let evidence = XCTAttachment(string: "id=\(target.identifier); frame=\(frame); bounds=\(bounds)")
            evidence.name = "Native menu action geometry"; evidence.lifetime = .keepAlways; add(evidence)
            matches.element.tap()
            return
        }
        // Resolve the hit point once, outside the snapshot-stability wait. Multiple
        // independent AX queries inside that wait can exhaust its entire budget.
        XCTAssertTrue(element.isHittable, "The stable action must be hittable before its single tap")
        // Context-menu and selection activation points can lag behind their layout.
        // A foreground context menu may overlay the still-present keyboard.
        // Tap the captured stable center once, then let the caller assert the effect.
        app.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(
            dx: frame.midX - bounds.minX, dy: frame.midY - bounds.minY)).tap()
    }

    private func visibleGroupActionSnapshot(matching target: XCUIElementSnapshot) -> (frame: CGRect, bounds: CGRect)? {
        guard let snapshot = try? app.snapshot() else {
            XCTFail("The application must provide an accessibility snapshot for the action"); return nil
        }
        var nodes: [XCUIElementSnapshot] = [snapshot]
        var matches: [XCUIElementSnapshot] = []
        while let node = nodes.popLast() {
            let identityMatches = target.identifier.isEmpty
                ? node.label == target.label : node.identifier == target.identifier
            if node.elementType == target.elementType, identityMatches { matches.append(node) }
            nodes.append(contentsOf: node.children)
        }
        guard matches.count <= 1 else {
            XCTFail("The requested action must have exactly one matching accessibility identity"); return nil
        }
        guard let action = matches.first, action.isEnabled, isFiniteFrame(snapshot.frame),
              isFiniteFrame(action.frame), snapshot.frame.contains(action.frame) else { return nil }
        return (action.frame, snapshot.frame)
    }

    private func verifySelectionAndReadingAcrossGroups(groupName: String, selectedID: String, laterID: String) {
        XCTAssertNotEqual(selectedID, laterID, "This regression must keep an earlier message, not the end of the chat")
        let selected = app.buttons["roundtableSelect-" + selectedID]
        let later = app.buttons["roundtableSelect-" + laterID]
        revealRoundtableMessage(selected, towardEarlier: true)
        XCTAssertFalse(later.isHittable, "A later message must be outside the retained reading viewport")
        attachMentorScreenshot("Inline group A — earlier message selected before switching")
        selectLegacyRoundtable()
        XCTAssertFalse(app.buttons["roundtableCancelSelection"].exists)
        XCTAssertFalse(selected.exists, "A's selection must not be mounted in the legacy conversation")
        selectSyntheticGroup(groupName)
        let restored = XCTNSPredicateExpectation(predicate: NSPredicate(format: "isHittable == true"), object: selected)
        XCTAssertEqual(XCTWaiter.wait(for: [restored], timeout: 10), .completed)
        // Do not scroll here: switching itself must restore the earlier reading position.
        XCTAssertEqual(selected.value as? String, "已选择")
        XCTAssertTrue(app.scrollViews["roundtableMessages"].frame.contains(selected.frame))
        XCTAssertFalse(later.isHittable)
        XCTAssertTrue(app.buttons["roundtableSaveSelection"].exists)
        attachMentorScreenshot("Inline group A — reading position and selection restored without scrolling")
    }

    func testMobilePrivateAndGroupSendKeepInputFocusForFollowingDraft() {
        launchMobileFixture("roundtable")
        app.tabBars.buttons["导师"].tap(); app.buttons["mobileMentor-future_self"].tap()
        let privateDraft = app.descendants(matching: .any).matching(identifier: "mobileChatDraft").firstMatch
        XCTAssertTrue(privateDraft.waitForExistence(timeout: 5)); privateDraft.tap()
        privateDraft.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        privateDraft.typeText("Synthetic first private question"); app.buttons["mobileSend"].tap()
        XCTAssertTrue(app.staticTexts["Synthetic mentor reply"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.keyboards.firstMatch.exists)
        app.typeText("Synthetic following private draft")
        XCTAssertEqual(privateDraft.value as? String, "Synthetic following private draft")
        attachMentorScreenshot("Private chat — following draft and keyboard stay in place")
        app.navigationBars.buttons["导师"].tap(); createSyntheticGroup()
        let composer = app.descendants(matching: .any).matching(identifier: "roundtableInput").firstMatch
        composer.tap(); composer.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        composer.typeText("Synthetic first group question"); app.buttons["roundtableSend"].tap()
        let actions = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "roundtableMessageActions-"))
        XCTAssertTrue(actions.firstMatch.waitForExistence(timeout: 10))
        XCTAssertTrue(app.keyboards.firstMatch.exists)
        app.typeText("Synthetic following group draft")
        XCTAssertEqual(composer.value as? String, "Synthetic following group draft")
        attachMentorScreenshot("Group chat — following draft and keyboard stay in place")
        assertInlineGroup()
        app.buttons["mobileMentor-future_self"].tap()
        XCTAssertTrue(privateDraft.waitForExistence(timeout: 5))
        XCTAssertEqual(privateDraft.value as? String, "Synthetic following private draft")
    }

    func testMobileGroupsEmptyCreationRenameIndependentDraftsAndColdReturn() {
        launchMobileFixture("roundtable")
        app.tabBars.buttons["导师"].tap(); assertInlineGroup()
        XCTAssertEqual(app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "mobileMentor-")).count, 4)
        XCTAssertFalse(app.staticTexts["我的导师"].exists)
        XCTAssertTrue(groupComposer.isHittable)
        XCTAssertLessThan(groupComposer.frame.maxY, app.tabBars.firstMatch.frame.minY)
        attachMentorScreenshot("Inline group home — portraits, messages and composer in one viewport")
        createSyntheticGroup(); renameSyntheticGroup("Synthetic learning group A")
        groupComposer.tap(); groupComposer.typeText("Unsent draft for group A")
        expectation(for: NSPredicate(format: "value == %@", "Unsent draft for group A"), evaluatedWith: groupComposer)
        waitForExpectations(timeout: 5)
        createSyntheticGroup(); renameSyntheticGroup("Synthetic long-term group B")
        groupComposer.tap(); groupComposer.typeText("Different draft for group B")
        expectation(for: NSPredicate(format: "value == %@", "Different draft for group B"), evaluatedWith: groupComposer)
        waitForExpectations(timeout: 5)
        openGroupSwitcher()
        XCTAssertTrue(syntheticGroupRow("Synthetic learning group A").waitForExistence(timeout: 5))
        attachMentorScreenshot("Inline group switcher — choose another conversation without a push")
        chooseGroupMenuEntry(syntheticGroupRow("Synthetic learning group A"), named: "Synthetic learning group A")
        XCTAssertEqual(groupComposer.value as? String, "Unsent draft for group A")
        app.buttons["mobileMentor-future_self"].tap()
        let privateDraft = app.descendants(matching: .any).matching(identifier: "mobileChatDraft").firstMatch
        XCTAssertTrue(privateDraft.waitForExistence(timeout: 5)); privateDraft.tap(); privateDraft.typeText("Independent private draft")
        expectation(for: NSPredicate(format: "value == %@", "Independent private draft"), evaluatedWith: privateDraft)
        waitForExpectations(timeout: 5)
        app.navigationBars.buttons["导师"].tap(); assertInlineGroup(named: "Synthetic learning group A")
        XCTAssertEqual(groupComposer.value as? String, "Unsent draft for group A")
        XCUIDevice.shared.press(.home); app.activate()
        assertInlineGroup(named: "Synthetic learning group A")
        XCTAssertEqual(groupComposer.value as? String, "Unsent draft for group A")
        XCTAssertTrue(app.buttons["roundtableSend"].isEnabled)
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10)); app.tabBars.buttons["导师"].tap()
        assertInlineGroup(named: "Synthetic learning group A")
        XCTAssertEqual(groupComposer.value as? String, "Unsent draft for group A")
        selectSyntheticGroup("Synthetic long-term group B")
        XCTAssertEqual(groupComposer.value as? String, "Different draft for group B")
        app.buttons["mobileMentor-future_self"].tap()
        XCTAssertTrue(privateDraft.waitForExistence(timeout: 5))
        XCTAssertEqual(privateDraft.value as? String, "Independent private draft")
        app.navigationBars.buttons["导师"].tap(); assertInlineGroup(named: "Synthetic long-term group B")
        archiveAndRestoreSyntheticGroupDraft()
        app.tabBars.buttons["设置"].tap()
        let version = app.staticTexts["settingsAppVersion"]; reveal(version)
        XCTAssertTrue(version.label.contains("0.1.0")); XCTAssertTrue(version.label.contains("构建 2"))
    }

    private func archiveAndRestoreSyntheticGroupDraft() {
        selectSyntheticGroup("Synthetic learning group A")
        app.buttons["groupDetails"].tap()
        let archive = app.buttons["groupArchiveToggle"]; reveal(archive); archive.tap()
        let archivedState = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label == %@ AND enabled == true", "恢复群聊"), object: archive)
        XCTAssertEqual(XCTWaiter.wait(for: [archivedState], timeout: 10), .completed)
        app.navigationBars.buttons["完成"].tap()
        selectSyntheticGroup("Synthetic long-term group B")
        XCTAssertEqual(groupComposer.value as? String, "Different draft for group B")
        openGroupSwitcher()
        XCTAssertFalse(syntheticGroupRow("Synthetic learning group A").exists,
                       "An archived group must leave the active group choices")
        let archivedMenu = app.buttons["mobileArchivedGroups"]
        XCTAssertTrue(archivedMenu.waitForExistence(timeout: 5)); tapVisibleGroupAction(archivedMenu)
        // UIKit drops the SwiftUI identifier in this nested native menu.
        let archived = app.buttons.matching(NSPredicate(format: "label == %@", "Synthetic learning group A · 草稿")).firstMatch
        XCTAssertTrue(archived.waitForExistence(timeout: 5)); XCTAssertTrue(archived.isHittable)
        attachMentorScreenshot("Archived group choices — retained draft available in the nested menu")
        chooseGroupMenuEntry(archived, named: "Synthetic learning group A")
        let restore = app.buttons["groupRestore"]
        XCTAssertTrue(restore.waitForExistence(timeout: 5)); reveal(restore); restore.tap()
        let restored = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: restore)
        XCTAssertEqual(XCTWaiter.wait(for: [restored], timeout: 10), .completed)
        assertInlineGroup(named: "Synthetic learning group A")
        XCTAssertEqual(groupComposer.value as? String, "Unsent draft for group A")
        XCTAssertTrue(app.buttons["roundtableSend"].isEnabled)
        attachMentorScreenshot("Restored inline group — original draft and identity")
    }

    func testMobileInlineGroupLandscapeKeepsMessagesAndComposerReachable() {
        launchMobileFixture("roundtable")
        app.tabBars.buttons["导师"].tap(); createSyntheticGroup()
        defer { XCUIDevice.shared.orientation = .portrait }
        XCUIDevice.shared.orientation = .landscapeLeft
        let landscape = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            self.app.frame.width > self.app.frame.height
        }, object: app)
        XCTAssertEqual(XCTWaiter.wait(for: [landscape], timeout: 10), .completed)
        assertInlineGroup()
        XCTAssertEqual(app.keyboards.count, 0)
        for actor in ["gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"] {
            XCTAssertTrue(app.buttons["mobileMentor-" + actor].exists)
        }
        let messages = app.scrollViews["roundtableMessages"]
        XCTAssertTrue(messages.waitForExistence(timeout: 5))
        XCTAssertGreaterThan(messages.frame.height, 40, "Landscape must retain a usable message viewport")
        let navigation = app.navigationBars.firstMatch
        let tabs = app.tabBars.firstMatch
        XCTAssertTrue(tabs.exists)
        for control in [app.buttons["mobileGroupSwitcher"], groupComposer, app.buttons["roundtableSend"]] {
            XCTAssertTrue(control.isHittable)
            XCTAssertTrue(app.frame.contains(control.frame))
            XCTAssertGreaterThanOrEqual(control.frame.minY, navigation.frame.maxY)
            XCTAssertLessThanOrEqual(control.frame.maxY, tabs.frame.minY)
        }
        XCTAssertGreaterThanOrEqual(messages.frame.minY, app.buttons["mobileGroupSwitcher"].frame.maxY)
        XCTAssertLessThanOrEqual(messages.frame.maxY, groupComposer.frame.minY)
        XCTAssertTrue(app.staticTexts["groupChatEmpty"].isHittable)
        attachMentorScreenshot("Inline group landscape — compact portraits, readable messages and reachable composer")
    }

    func testMobileGroupMentionAndLargeTextKeepInputReachable() {
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        launchMobileFixture("roundtable")
        app.tabBars.buttons["导师"].tap(); assertInlineGroup()
        attachMentorScreenshot("Inline mentor home — accessibility XXXL before keyboard")
        createSyntheticGroup()
        let name = currentGroup.label
        let composer = groupComposer
        composer.tap(); composer.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        composer.typeText("A synthetic small next step")
        assertInlineInputReachableWithKeyboard()
        attachMentorScreenshot("Inline group home — accessibility XXXL with keyboard and switcher")
        openGroupSwitcher()
        XCTAssertTrue(legacyRoundtableEntry.waitForExistence(timeout: 5)); XCTAssertTrue(legacyRoundtableEntry.isHittable)
        attachMentorScreenshot("Inline group switcher — accessibility XXXL while composing")
        legacyRoundtableEntry.tap(); assertInlineGroup()
        XCTAssertNotEqual(composer.value as? String, "A synthetic small next step")
        selectSyntheticGroup(name)
        XCTAssertEqual(composer.value as? String, "A synthetic small next step")
        composer.tap(); composer.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        assertInlineInputReachableWithKeyboard()
        XCTAssertEqual(app.staticTexts["groupReplyScope"].label, "大家回应")
        app.buttons["roundtableSend"].tap()
        let advice = app.staticTexts.matching(NSPredicate(format: "label == %@", "这是一段合成测试回复：先选一个可以验证的小行动，再根据实际反馈调整。"))
        let firstReply = groupMentionReplies.firstMatch
        let receivedFirst = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            firstReply.exists || self.app.buttons["groupNewReply"].exists
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [receivedFirst], timeout: 10), .completed)
        assertInlineMessageViewportWithKeyboard()
        if app.buttons["groupNewReply"].exists { showLatestGroupReply() }
        XCTAssertTrue(advice.firstMatch.waitForExistence(timeout: 10))
        XCTAssertTrue(firstReply.waitForExistence(timeout: 5))
        let firstReplyID = firstReply.identifier
        revealRoundtableMessage(advice.firstMatch)
        XCTAssertTrue(advice.firstMatch.isHittable)
        attachMentorScreenshot("Large-text group — reply remains readable after viewing the new message")
        selectGroupMentionAfterMenuSettles(composer)
        XCTAssertTrue(app.staticTexts["groupReplyScope"].label.contains("仅温柔回顾者回应"))
        XCTAssertTrue(app.staticTexts["groupReplyScope"].label.contains("群内可见"))
        composer.typeText("Please add one thought")
        attachMentorScreenshot("Group mention — accessible input and visible audience")
        app.buttons["roundtableSend"].tap()
        let newReply = groupMentionReplies.matching(NSPredicate(format: "identifier != %@", firstReplyID)).firstMatch
        let latest = app.buttons["groupNewReply"]
        let received = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            newReply.exists || latest.exists
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [received], timeout: 10), .completed,
                       "The new reply must become visible or offer its real unread navigation action")
        assertInlineMessageViewportWithKeyboard()
        if !newReply.exists, latest.exists { showLatestGroupReply() }
        XCTAssertTrue(newReply.waitForExistence(timeout: 10), "The mentioned mentor must provide a new reply with the exact expected text")
        XCTAssertTrue(advice.firstMatch.waitForExistence(timeout: 10))
        revealRoundtableMessage(newReply.staticTexts.matching(NSPredicate(format: "label == %@",
            "这是一段合成测试回复：先选一个可以验证的小行动，再根据实际反馈调整。")).firstMatch)
        XCTAssertFalse(app.buttons["roundtableInvite"].exists)
    }

    private var groupMentionReplies: XCUIElementQuery {
        app.otherElements.matching(NSPredicate(format: "identifier BEGINSWITH %@", "roundtableMessage-"))
            .containing(.staticText, identifier: "温柔回顾者")
            .containing(.staticText, identifier: "这是一段合成测试回复：先选一个可以验证的小行动，再根据实际反馈调整。")
    }

    private func showLatestGroupReply() {
        let latest = app.buttons["groupNewReply"]
        XCTAssertTrue(latest.waitForExistence(timeout: 10))
        tapVisibleGroupAction(latest)
        let read = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in !latest.exists }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [read], timeout: 5), .completed,
                       "Viewing the latest reply must clear its unread affordance")
    }

    private func selectGroupMentionAfterMenuSettles(_ composer: XCUIElement) {
        app.buttons["groupMention"].tap()
        let option = app.buttons["温柔回顾者"]
        XCTAssertTrue(option.waitForExistence(timeout: 5))
        tapVisibleGroupAction(option)
        let scope = app.staticTexts["groupReplyScope"]
        let applied = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            guard !option.exists, composer.exists, scope.exists else { return false }
            return composer.value as? String == "@温柔回顾者 "
                && scope.label.contains("仅温柔回顾者回应") && scope.label.contains("群内可见")
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [applied], timeout: 5), .completed,
                       "One mention selection must close the menu, insert the complete mention and update its audience")
    }

    func testMobileGroupResultPreviewRequiresConfirmationAndKeepsExactSelection() {
        launchMobileFixture("roundtable")
        let before = input.value as? String ?? ""
        app.tabBars.buttons["导师"].tap(); createSyntheticGroup()
        let groupName = currentGroup.label
        let composer = app.descendants(matching: .any).matching(identifier: "roundtableInput").firstMatch
        composer.tap(); composer.typeText("A synthetic plan to save")
        app.buttons["roundtableSend"].tap()
        let identifier = openRoundtableMessageActions(longPress: true)
        openGroupResultFromMessageActions(identifier)
        let beforeSave = cancelEditedResultAndResumeDiary(before: before)
        XCTAssertEqual(openRoundtableMessageActions(longPress: true), identifier)
        openGroupResultFromMessageActions(identifier)
        let selectedText = editRoundtableResult(append: "\nSynthetic reviewed clarification.")
        XCTAssertFalse(app.buttons["roundtableLocalPreview"].exists)
        let exactBlock = exactRoundtableResultPreview(selectedText)
        let startMarker = "<!-- riji:ai-discussion-result "
        let endMarker = "<!-- /riji:ai-discussion-result -->"
        XCTAssertTrue(exactBlock.contains("Synthetic reviewed clarification."))
        verifyRetainedPreviewAcrossGroups(groupName: groupName, text: selectedText, exactBlock: exactBlock)
        let confirm = app.buttons["roundtableConfirmSave"]
        revealRoundtableResultFormElement(confirm); XCTAssertTrue(confirm.waitForExistence(timeout: 10))
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate(format: "enabled == true"), object: confirm)
        XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 10), .completed)
        attachMentorScreenshot("Group result — explicit local preview before confirmation")
        confirm.tap()
        let saved = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "已保存到 2026-09-12，没有重复追加。"), object: app.staticTexts["roundtableSaveStatus"])
        XCTAssertEqual(XCTWaiter.wait(for: [saved], timeout: 10), .completed)
        app.navigationBars.buttons["完成"].tap(); app.tabBars.buttons["日记"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        let bytes = diaryRawMarkdownFromSources(edited: true, screenshotName: "Saved group result")
        XCTAssertTrue(bytes.hasPrefix(beforeSave)); XCTAssertEqual(bytes.components(separatedBy: exactBlock).count, 2)
        XCTAssertEqual(bytes.components(separatedBy: startMarker).count, 2)
        XCTAssertEqual(bytes.components(separatedBy: endMarker).count, 2)
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        let reopened = diaryRawMarkdownFromSources(edited: true, screenshotName: "Cold reopened group result")
        XCTAssertEqual(Data(reopened.utf8), Data(bytes.utf8), "Cold reopen must preserve the exact AI block and manual diary entry")
    }

    private func openGroupResultFromMessageActions(_ identifier: String) {
        let save = app.buttons["roundtableSaveMessage-" + identifier]
        XCTAssertTrue(save.waitForExistence(timeout: 5))
        tapVisibleGroupAction(save)
        let dismissed = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: save)
        XCTAssertEqual(XCTWaiter.wait(for: [dismissed], timeout: 5), .completed,
                       "Saving the selected message must dismiss its context menu")
        XCTAssertTrue(app.buttons["roundtableCloseResult"].waitForExistence(timeout: 10),
                      "The selected message must open its explicit save preview")
    }

    private func revealRoundtableResultFormElement(
        _ element: XCUIElement, allowingPartialText: Bool = false, towardTop: Bool = false
    ) {
        XCTAssertTrue(app.navigationBars["保存到日记"].waitForExistence(timeout: 5))
        // The keyboard candidate bar is also a CollectionView. Restrict the
        // query to the sheet's window, without depending on a lazy preview row.
        let window = app.windows.containing(.navigationBar, identifier: "保存到日记").firstMatch
        XCTAssertTrue(window.waitForExistence(timeout: 5))
        let form = window.collectionViews.firstMatch
        XCTAssertTrue(form.waitForExistence(timeout: 5))
        for _ in 0..<10 {
            let viewport = roundtableResultFormViewport(form)
            XCTAssertGreaterThan(viewport.height, 100)
            var scrollTowardTop = towardTop
            if element.exists {
                let frame = element.frame
                if frame.minX.isFinite, frame.minY.isFinite, frame.width.isFinite,
                   frame.height.isFinite, !frame.isEmpty {
                    let visible = frame.intersection(viewport)
                    if allowingPartialText, visible.width > 0, visible.height >= min(44, frame.height) { return }
                    if viewport.contains(frame), element.isEnabled, element.isHittable { return }
                    scrollTowardTop = frame.minY < viewport.minY
                }
            }
            // Stay in the Form gutter so the gesture cannot scroll the embedded
            // Markdown preview or text editor instead of mounting the next row.
            let origin = form.coordinate(withNormalizedOffset: .zero)
            let upper = origin.withOffset(CGVector(dx: 12, dy: viewport.minY - form.frame.minY + viewport.height * 0.25))
            let lower = origin.withOffset(CGVector(dx: 12, dy: viewport.minY - form.frame.minY + viewport.height * 0.75))
            let start = scrollTowardTop ? upper : lower
            let end = scrollTowardTop ? lower : upper
            start.press(forDuration: 0.05, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.2)
        }
        XCTFail("The save Form must reveal the requested content or complete action before using it: " + element.identifier)
    }

    private func roundtableResultFormViewport(_ form: XCUIElement) -> CGRect {
        let bounds = form.frame.intersection(app.frame)
        let top = max(bounds.minY, app.navigationBars["保存到日记"].frame.maxY) + 8
        var bottom = bounds.maxY - 20
        let keyboard = app.keyboards.firstMatch
        if keyboard.exists {
            var keyboardTop = keyboard.frame.minY
            // The candidate/assistant rows can extend above Keyboard on iOS 26.
            let inputView = app.otherElements.matching(identifier: "inputView").firstMatch
            if inputView.exists, inputView.frame.intersects(keyboard.frame) {
                keyboardTop = min(keyboardTop, inputView.frame.minY)
            }
            let assistant = app.otherElements.matching(identifier: "SystemInputAssistantView").firstMatch
            if assistant.exists, assistant.frame.maxY >= keyboard.frame.minY - 1 {
                keyboardTop = min(keyboardTop, assistant.frame.minY)
            }
            bottom = min(bottom, keyboardTop - 8)
        }
        XCTAssertTrue(top.isFinite && bottom.isFinite && bounds.minX.isFinite && bounds.width.isFinite)
        XCTAssertGreaterThan(bottom - top, 100,
                             "The save Form must have a positive unobstructed height before constructing its viewport")
        return CGRect(x: bounds.minX, y: top, width: bounds.width, height: bottom - top)
    }

    private func verifyRetainedPreviewAcrossGroups(groupName: String, text: String, exactBlock: String) {
        let close = app.buttons["roundtableCloseResult"]
        XCTAssertTrue(close.isHittable); close.tap()
        assertInlineGroup(named: groupName)
        XCTAssertTrue(app.buttons["继续保存预览"].waitForExistence(timeout: 10))
        createSyntheticGroup(); renameSyntheticGroup("Synthetic separate preview group B")
        XCTAssertFalse(app.buttons["继续保存预览"].exists, "Group B must not offer group A's edited result")
        XCTAssertFalse(app.buttons["继续本地保存预览"].exists)
        selectLegacyRoundtable()
        XCTAssertFalse(app.buttons["继续保存预览"].exists)
        XCTAssertFalse(app.buttons["继续本地保存预览"].exists)
        app.buttons["roundtableMenu"].tap()
        XCTAssertTrue(app.buttons["当前条件与来源"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["继续本地保存预览"].exists, "The legacy menu must not offer another group's result")
        let messages = app.scrollViews["roundtableMessages"]
        XCTAssertTrue(messages.exists)
        XCTAssertGreaterThan(messages.frame.height, 40)
        let dismissalPoint = CGPoint(x: messages.frame.minX + messages.frame.width * 0.08,
                                     y: messages.frame.minY + messages.frame.height * 0.85)
        let menuFrame = app.buttons["当前条件与来源"].frame.union(app.buttons["归档这段讨论"].frame)
        XCTAssertFalse(menuFrame.insetBy(dx: -16, dy: -16).contains(dismissalPoint),
                       "Dismiss the native menu from the message viewport, outside its action area")
        messages.coordinate(withNormalizedOffset: CGVector(dx: 0.08, dy: 0.85)).tap()
        let closed = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: app.buttons["当前条件与来源"])
        let switcherReady = XCTNSPredicateExpectation(predicate: NSPredicate(format: "isHittable == true"), object: app.buttons["mobileGroupSwitcher"])
        XCTAssertEqual(XCTWaiter.wait(for: [closed, switcherReady], timeout: 5), .completed)
        selectSyntheticGroup(groupName)
        let retained = app.buttons["继续保存预览"]
        XCTAssertTrue(retained.waitForExistence(timeout: 10)); retained.tap()
        let target = app.staticTexts["将追加到 2026-09-12 日记 · Notes"]
        XCTAssertTrue(target.waitForExistence(timeout: 10))
        let restored = exactRoundtableResultPreview(text)
        XCTAssertEqual(Data(restored.utf8), Data(exactBlock.utf8),
                       "Switching away must preserve the edited text, target date and original group provenance exactly")
        attachMentorScreenshot("Group A retained save preview — exact text, date and provenance restored")
    }

    func testMobileRoundtableHostInvitationAndLocalSavePreview() {
        launchMobileFixture("roundtable")
        let before = input.value as? String ?? ""
        app.tabBars.buttons["导师"].tap()
        selectLegacyRoundtable()
        let composer = app.descendants(matching: .any).matching(identifier: "roundtableInput").firstMatch
        XCTAssertTrue(composer.waitForExistence(timeout: 5)); composer.tap(); composer.typeText("Synthetic learning plan")
        app.buttons["roundtableSend"].tap()
        let invite = app.buttons["roundtableInvite"]
        XCTAssertTrue(invite.waitForExistence(timeout: 10)); invite.tap()
        let preview = app.buttons["roundtablePreviewInvite"]; XCTAssertTrue(preview.isHittable); preview.tap()
        let confirm = app.buttons["roundtableConfirmInvite"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 10)); confirm.tap()
        let returnedToChat = XCTNSPredicateExpectation(predicate: NSPredicate(format: "isHittable == true"), object: invite)
        XCTAssertEqual(XCTWaiter.wait(for: [returnedToChat], timeout: 10), .completed)
        XCTAssertFalse(confirm.exists)
        let identifier = openRoundtableMessageActions(last: true)
        let multi = app.buttons["roundtableMultiSelect-" + identifier]
        XCTAssertTrue(multi.waitForExistence(timeout: 5)); enterRoundtableSelection(from: multi)
        XCTAssertTrue(app.buttons["roundtableSelect-" + identifier].waitForExistence(timeout: 5))
        let save = app.buttons["roundtableSaveSelection"]; reveal(save); save.tap()
        let selectedText = editRoundtableResult()
        XCTAssertFalse(app.buttons["roundtableLocalPreview"].exists)
        XCTAssertFalse(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "<!-- riji:ai-discussion-result")).firstMatch.exists)
        attachMentorScreenshot("Legacy roundtable — readable exact-content preview before confirmation")
        let exactBlock = exactRoundtableResultPreview(selectedText)
        let write = app.buttons["roundtableConfirmSave"]
        revealRoundtableResultFormElement(write)
        XCTAssertTrue(write.waitForExistence(timeout: 10))
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate(format: "enabled == true"), object: write)
        XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 10), .completed)
        let frame = write.frame
        XCTAssertGreaterThan(frame.width, 0); XCTAssertGreaterThan(frame.height, 0)
        XCTAssertTrue(app.frame.contains(frame), "The entire enabled confirmation button must be visible before tapping")
        attachMentorScreenshot("Legacy roundtable — visible enabled confirmation before saving")
        // iOS 27 can report an invalid activation point for this visible button,
        // as with cancellation. Tap only its checked on-screen center.
        app.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(
            dx: frame.midX - app.frame.minX, dy: frame.midY - app.frame.minY)).tap()
        let success = app.staticTexts["roundtableSaveStatus"]
        let saved = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "已保存到 2026-09-12，没有重复追加。"), object: success)
        XCTAssertEqual(XCTWaiter.wait(for: [saved], timeout: 10), .completed)
        XCTAssertFalse(write.exists)
        app.buttons["完成"].tap()
        app.tabBars.buttons["日记"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-12"))
        let bytes = diaryRawMarkdownFromSources(edited: false, screenshotName: "Saved legacy roundtable result")
        XCTAssertTrue(bytes.hasPrefix(before)); XCTAssertEqual(bytes.components(separatedBy: exactBlock).count, 2)
        XCTAssertEqual(bytes.components(separatedBy: "<!-- riji:ai-discussion-result ").count, 2)
        XCTAssertEqual(bytes.components(separatedBy: "<!-- /riji:ai-discussion-result -->").count, 2)
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-12"))
        let reopened = diaryRawMarkdownFromSources(edited: false, screenshotName: "Cold reopened legacy result")
        XCTAssertEqual(Data(reopened.utf8), Data(bytes.utf8), "Cold reopen must read the saved target file exactly")
        XCTAssertEqual(reopened.components(separatedBy: exactBlock).count, 2)
    }

    func testMobileRoundtableInvitationPinnedReviewAndTenRounds() {
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryXXXL"]
        launchMobileFixture("roundtable-long")
        app.tabBars.buttons["导师"].tap(); selectLegacyRoundtable()
        let composer = app.descendants(matching: .any).matching(identifier: "roundtableInput").firstMatch
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        composer.tap(); composer.typeText("Synthetic long-material invitation")
        app.buttons["roundtableSend"].tap()
        let invite = app.buttons["roundtableInvite"]
        XCTAssertTrue(invite.waitForExistence(timeout: 10)); invite.tap()
        let form = app.collectionViews.firstMatch
        func revealMaterial(_ element: XCUIElement) {
            for _ in 0..<30 {
                let top = max(form.frame.minY, app.navigationBars.firstMatch.frame.maxY) + 12
                let bottom = min(form.frame.maxY, app.staticTexts["roundtableShareNotice"].frame.minY - 12)
                let upper = app.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(dx: app.frame.midX, dy: (top + bottom) / 2 - 75))
                let lower = app.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(dx: app.frame.midX, dy: (top + bottom) / 2 + 75))
                if element.exists {
                    let frame = element.frame
                    if frame.minY >= top && frame.maxY <= bottom { return }
                    if frame.minY < top { upper.press(forDuration: 0.05, thenDragTo: lower); continue }
                }
                lower.press(forDuration: 0.05, thenDragTo: upper)
            }
            XCTFail("Material must be fully visible above the pinned action before interacting")
        }
        let next = app.buttons["roundtablePreviewInvite"]
        XCTAssertTrue(next.waitForExistence(timeout: 5)); XCTAssertTrue(next.isHittable)
        for name in ["未来的我", "王阳明"] {
            let toggle = app.switches[name]
            XCTAssertEqual(toggle.value as? String, "0")
            // The switch accessibility frame includes its label; tap the visible control at the trailing edge.
            toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.93, dy: 0.5)).tap()
            XCTAssertEqual(toggle.value as? String, "1", "Newly invited speaker must stay selected")
        }
        app.buttons["roundtableMode"].tap(); app.buttons["讨论分歧"].tap()
        let stepper = app.steppers["roundtableRounds"]
        reveal(stepper)
        let increase = stepper.buttons["roundtableRounds-Increment"]
        for _ in 1..<10 { increase.tap() }
        XCTAssertTrue(app.staticTexts["最多 10 轮"].exists)
        XCTAssertFalse(increase.isEnabled)
        for index in 1...10 {
            let memory = app.switches["roundtableMemory-synthetic-long-\(index)"]
            revealMaterial(memory)
            memory.coordinate(withNormalizedOffset: CGVector(dx: 0.93, dy: 0.5)).tap()
            XCTAssertEqual(memory.value as? String, "1")
        }
        let overflow = app.switches["roundtableMemory-synthetic-long-11"]
        revealMaterial(overflow); XCTAssertFalse(overflow.isEnabled)
        // No reveal(next): the primary action must stay inside the viewport.
        XCTAssertTrue(next.isHittable); next.tap()
        let confirm = app.buttons["roundtableConfirmInvite"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 10)); XCTAssertTrue(confirm.isHittable)
        XCTAssertEqual(app.staticTexts["roundtableInviteSummary"].label, "讨论分歧 · 最多 10 轮")
        for name in ["温柔回顾者", "直率教练", "未来的我", "王阳明"] {
            XCTAssertTrue(app.staticTexts["roundtableInviteSpeakers"].label.contains(name))
        }
        let firstMemory = app.staticTexts["roundtableReviewMemory-synthetic-long-1"]
        let lastMemory = app.staticTexts["roundtableReviewMemory-synthetic-long-9"]
        revealMaterial(firstMemory); XCTAssertTrue(firstMemory.exists)
        revealMaterial(lastMemory); XCTAssertTrue(lastMemory.isHittable)
        XCTAssertTrue(confirm.isHittable)
        app.buttons["roundtableEditInvite"].tap()
        XCTAssertFalse(confirm.exists); XCTAssertTrue(next.isHittable)
        let restoredMemory = app.switches["roundtableMemory-synthetic-long-9"]
        revealMaterial(restoredMemory); XCTAssertEqual(restoredMemory.value as? String, "1")
        form.swipeUp(); form.swipeUp()
        XCTAssertTrue(next.isHittable)
        next.tap(); XCTAssertTrue(confirm.waitForExistence(timeout: 10)); XCTAssertTrue(confirm.isHittable)
        for name in ["温柔回顾者", "直率教练", "未来的我", "王阳明"] {
            XCTAssertTrue(app.staticTexts["roundtableInviteSpeakers"].label.contains(name))
        }
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "Synthetic ten-round invitation confirmation in dark large text"
        screenshot.lifetime = .keepAlways; add(screenshot)
        confirm.tap()
        let returned = XCTNSPredicateExpectation(predicate: NSPredicate(format: "isHittable == true"), object: invite)
        XCTAssertEqual(XCTWaiter.wait(for: [returned], timeout: 10), .completed)
        XCTAssertFalse(confirm.exists)
    }

    func testMobileRoundtableStoppedSummaryAndColdOfflineDraft() {
        launchMobileFixture("roundtable")
        app.tabBars.buttons["导师"].tap(); selectLegacyRoundtable()
        let composer = app.descendants(matching: .any).matching(identifier: "roundtableInput").firstMatch
        XCTAssertTrue(composer.waitForExistence(timeout: 5)); composer.tap(); composer.typeText("Synthetic partial discussion")
        app.buttons["roundtableSend"].tap()
        XCTAssertTrue(app.buttons["roundtableInvite"].waitForExistence(timeout: 10))
        app.buttons["roundtableMenu"].tap(); app.buttons["归档这段讨论"].tap()
        XCTAssertTrue(app.staticTexts["已归档"].waitForExistence(timeout: 10))
        app.buttons["roundtableMenu"].tap(); app.buttons["恢复这段讨论"].tap()
        let summary = app.buttons["roundtableSummarize"]; reveal(summary); summary.tap()
        let text = "部分总结：已完成的建议供参考，未完成视角仍缺失。请结合新的条件再决定下一步。"
        revealRoundtableMessage(app.staticTexts[text])
        XCTAssertTrue(app.staticTexts[text].exists)
        composer.tap(); composer.typeText("Synthetic offline retained draft")
        expectation(for: NSPredicate(format: "value == %@", "Synthetic offline retained draft"), evaluatedWith: composer)
        waitForExpectations(timeout: 5)
        app.terminate(); app.launchEnvironment["RIJI_TEST_MOBILE"] = "roundtable-offline"; app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10)); app.tabBars.buttons["导师"].tap()
        assertInlineGroup()
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        XCTAssertEqual(composer.value as? String, "Synthetic offline retained draft")
        let offline = app.staticTexts["离线副本 · 草稿仍在本机"]
        revealRoundtableMessage(offline, towardEarlier: true)
        XCTAssertTrue(offline.exists)
        revealRoundtableMessage(app.staticTexts[text])
        XCTAssertTrue(app.staticTexts[text].exists)
        XCTAssertFalse(app.buttons["roundtableSend"].isEnabled)
        XCTAssertFalse(app.buttons["roundtableSummarize"].exists)
    }

    func testMobileMemoryOverviewThemeEvidenceCorrectionAndInvalidation() {
        launchMobileFixture("overview")
        app.tabBars.buttons["记忆"].tap()
        let theme = app.buttons["mobileSummary-synthetic-summary"]
        XCTAssertTrue(theme.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["mobileMemory-synthetic-memory"].exists)
        let title = app.navigationBars["记忆"].staticTexts["记忆"]
        XCTAssertTrue(title.waitForExistence(timeout: 5)); XCTAssertTrue(title.isHittable)
        let overviewScreenshot = XCTAttachment(screenshot: app.screenshot()); overviewScreenshot.name = "Synthetic memory overview themes"; overviewScreenshot.lifetime = .keepAlways; add(overviewScreenshot)
        reveal(theme)
        XCTAssertEqual(theme.value as? String, "首个主题")
        attachMentorScreenshot("Approved memory — first visible theme on forest")
        theme.tap()
        XCTAssertTrue(app.staticTexts["mobileSummaryText"].waitForExistence(timeout: 5))
        let evidence = app.buttons["mobileOverviewEvidence-synthetic-memory"]
        XCTAssertTrue(evidence.waitForExistence(timeout: 5))
        let screenshot = XCTAttachment(screenshot: app.screenshot()); screenshot.name = "Synthetic memory summary and evidence"; screenshot.lifetime = .keepAlways; add(screenshot)
        evidence.tap()
        let correction = app.descendants(matching: .any).matching(identifier: "mobileMemoryCorrection").firstMatch
        XCTAssertTrue(correction.waitForExistence(timeout: 5)); correction.tap(); correction.typeText("Synthetic overview correction")
        let confirm = app.buttons["确认纠正"]; revealMemoryCorrectionControl(confirm); confirm.tap()
        XCTAssertTrue(app.staticTexts["你的纠正草稿"].waitForExistence(timeout: 5))
        app.buttons["mobileSubmitCorrection"].tap()
        XCTAssertTrue(app.staticTexts["Synthetic overview correction"].waitForExistence(timeout: 5))
        app.navigationBars.buttons.element(boundBy: 0).tap()
        XCTAssertTrue(app.staticTexts["mobileSummaryInvalid"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["mobileSummaryText"].exists)
        app.navigationBars.buttons.element(boundBy: 0).tap()
        XCTAssertTrue(app.buttons["mobileThemeFilter"].waitForExistence(timeout: 5))
        let organize = app.buttons["mobileOrganize"]; reveal(organize); organize.tap()
        app.buttons["确认请求整理"].tap()
        let receipt = app.staticTexts["请求整理记忆已完成"]
        XCTAssertTrue(receipt.waitForExistence(timeout: 10)); reveal(receipt)
        let receiptScreenshot = XCTAttachment(screenshot: app.screenshot()); receiptScreenshot.name = "Synthetic memory organization receipt"; receiptScreenshot.lifetime = .keepAlways; add(receiptScreenshot)
        // A queued request intentionally disables this control. XCTest on iOS 26
        // can throw while calculating a disabled SwiftUI button's hit point.
        reveal(organize, requiringHitPoint: false)
        XCTAssertTrue(app.staticTexts["已排队或正在整理；服务会按预算分批继续，停止的历史项保持停止。"].exists)
        XCTAssertFalse(app.buttons["mobileOrganize"].isEnabled)
    }

    func testMobileCorrectionConfirmationRejectsRemoteChangeAndRetainsInput() {
        launchMobileFixture("overview-confirmation-change")
        app.tabBars.buttons["记忆"].tap()
        let theme = app.buttons["mobileSummary-synthetic-summary"]
        XCTAssertTrue(theme.waitForExistence(timeout: 5)); reveal(theme); theme.tap()
        app.buttons["mobileOverviewEvidence-synthetic-memory"].tap()
        let correction = app.descendants(matching: .any).matching(identifier: "mobileMemoryCorrection").firstMatch
        XCTAssertTrue(correction.waitForExistence(timeout: 5)); correction.tap(); correction.typeText("Synthetic confirmation candidate")
        let confirm = app.buttons["确认纠正"]; revealMemoryCorrectionControl(confirm); confirm.tap()
        let submit = app.buttons["mobileSubmitCorrection"]
        XCTAssertTrue(submit.waitForExistence(timeout: 5))
        let changed = XCTNSPredicateExpectation(predicate: NSPredicate(format: "enabled == false"), object: submit)
        XCTAssertEqual(XCTWaiter.wait(for: [changed], timeout: 40), .completed)
        XCTAssertTrue(app.staticTexts["记忆再次变化，请返回重新比较。"].exists)
        app.buttons["稍后处理"].tap()
        XCTAssertEqual(correction.value as? String, "Synthetic confirmation candidate")
        XCTAssertTrue(app.staticTexts["Synthetic changed during confirmation"].exists)
    }

    func testMobileOlderHistoryKeepsCurrentPageAndDraft() {
        launchMobileFixture("history")
        app.tabBars.buttons["导师"].tap()
        let mentor = app.buttons["mobileMentor-future_self"]
        XCTAssertTrue(mentor.waitForExistence(timeout: 5)); mentor.tap()
        XCTAssertTrue(app.staticTexts["Synthetic newest message"].waitForExistence(timeout: 5))
        let draft = app.descendants(matching: .any).matching(identifier: "mobileChatDraft").firstMatch
        XCTAssertTrue(draft.waitForExistence(timeout: 5)); draft.tap(); draft.typeText("Synthetic draft survives pagination")
        let older = app.buttons["mobileOlderMessages"]
        XCTAssertTrue(older.waitForExistence(timeout: 5)); reveal(older); older.tap()
        XCTAssertTrue(app.staticTexts["Synthetic older message"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Synthetic newest message"].exists)
        XCTAssertFalse(older.exists, "The exhausted cursor must not offer another page")
        XCTAssertEqual(draft.value as? String, "Synthetic draft survives pagination")
        let evidence = XCTAttachment(screenshot: app.screenshot())
        evidence.name = "Synthetic older and current mentor history"; evidence.lifetime = .keepAlways; add(evidence)
    }

    func testMobileCorrectionRequiresVisibleComparisonAfterRemoteChange() {
        launchMobileFixture("rebase")
        app.tabBars.buttons["记忆"].tap()
        XCTAssertTrue(app.buttons["mobileRawMemories"].waitForExistence(timeout: 5)); app.buttons["mobileRawMemories"].tap()
        let fact = app.buttons["mobileMemory-synthetic-memory"]
        XCTAssertTrue(fact.waitForExistence(timeout: 5)); fact.tap()
        let correction = app.descendants(matching: .any).matching(identifier: "mobileMemoryCorrection").firstMatch
        XCTAssertTrue(correction.waitForExistence(timeout: 5)); correction.tap(); correction.typeText("Synthetic version-bound correction")
        // Verify that every injected character reached the field before cold recovery.
        let entered = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", "Synthetic version-bound correction"), object: correction)
        XCTAssertEqual(XCTWaiter.wait(for: [entered], timeout: 5), .completed,
                       "The complete correction draft must reach the field before termination")
        app.terminate()
        app.launchEnvironment["RIJI_TEST_MOBILE"] = "rebase-updated"
        app.launch()
        app.tabBars.buttons["记忆"].tap()
        XCTAssertTrue(app.buttons["mobileRawMemories"].waitForExistence(timeout: 5)); app.buttons["mobileRawMemories"].tap()
        XCTAssertTrue(fact.waitForExistence(timeout: 5)); fact.tap()
        let compare = app.buttons["mobileCompareCorrection"]
        XCTAssertTrue(compare.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["确认纠正"].isEnabled)
        reveal(compare); compare.tap()
        XCTAssertTrue(app.navigationBars["比较纠正草稿"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Synthetic remotely updated memory"].exists)
        XCTAssertTrue(app.staticTexts["Synthetic version-bound correction"].exists)
        app.buttons["mobileRebaseCorrection"].tap()
        XCTAssertTrue(correction.waitForExistence(timeout: 5))
        XCTAssertEqual(correction.value as? String, "Synthetic version-bound correction")
        XCTAssertTrue(app.buttons["确认纠正"].isEnabled)
    }

    func testMobileRefusalRequiresConfirmedStopBeforeSendingAdjustedDraft() {
        launchMobileFixture("refused")
        app.tabBars.buttons["导师"].tap()
        let mentor = app.buttons["mobileMentor-future_self"]
        XCTAssertTrue(mentor.waitForExistence(timeout: 5)); mentor.tap()
        let draft = app.descendants(matching: .any).matching(identifier: "mobileChatDraft").firstMatch
        XCTAssertTrue(draft.waitForExistence(timeout: 5)); draft.tap(); draft.typeText("Adjusted synthetic question")
        let stop = app.buttons["mobileStopConversation"]
        let send = app.buttons["mobileSend"]
        XCTAssertTrue(stop.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["继续"].exists)
        XCTAssertFalse(send.isEnabled, "A refused conversation requires an explicit stop first")
        stop.tap()
        let enabled = XCTNSPredicateExpectation(predicate: NSPredicate(format: "enabled == true"), object: send)
        XCTAssertEqual(XCTWaiter.wait(for: [enabled], timeout: 10), .completed)
        XCTAssertEqual(draft.value as? String, "Adjusted synthetic question")
        send.tap()
        XCTAssertTrue(app.staticTexts["Synthetic mentor reply"].waitForExistence(timeout: 10))
        app.navigationBars.buttons.element(boundBy: 0).tap()
        let other = app.buttons["mobileMentor-blunt_coach"]
        XCTAssertTrue(other.waitForExistence(timeout: 5)); other.tap()
        XCTAssertTrue(stop.waitForExistence(timeout: 5), "Stopping one mentor must not stop another")
        XCTAssertTrue(draft.waitForExistence(timeout: 5)); draft.tap(); draft.typeText("Another synthetic draft")
        XCTAssertFalse(send.isEnabled)
        XCTAssertFalse(app.buttons["继续"].exists)
    }

    func testMobileCorruptArchiveWarningPreservesHealthyContentAndNeverClaimsEmpty() {
        for mode in ["archive-mixed", "archive-corrupt"] {
            app.terminate()
            app.launchEnvironment["RIJI_TEST_CONTAINER"] = UUID().uuidString
            launchMobileFixture(mode)
            app.tabBars.buttons["设置"].tap(); app.buttons["settingsDataAI"].tap(); app.buttons["settingsConnection"].tap()
            let archive = app.buttons["未发草稿与原操作"]; reveal(archive); archive.tap()
            let warning = app.descendants(matching: .any).matching(identifier: "mobileArchiveReadWarning").firstMatch
            XCTAssertTrue(warning.waitForExistence(timeout: 5))
            XCTAssertFalse(app.staticTexts["没有保留的草稿或操作记录。"].exists)
            if mode == "archive-mixed" {
                XCTAssertTrue(app.staticTexts["Synthetic readable archive"].exists)
                XCTAssertTrue(app.buttons["复制或导出文字"].exists)
            } else {
                XCTAssertFalse(app.staticTexts["Synthetic readable archive"].exists)
            }
        }
    }

    func testMobileUnsavedRecoveryCanOpenExportAndRequiresExplicitDiscard() {
        launchMobileFixture("recovery")
        app.tabBars.buttons["设置"].tap(); app.buttons["settingsDataAI"].tap(); app.buttons["settingsConnection"].tap()
        let archive = app.buttons["未发草稿与原操作"]; reveal(archive); archive.tap()
        XCTAssertTrue(app.staticTexts["Synthetic recovery text"].waitForExistence(timeout: 5))
        let export = app.buttons["复制或导出文字"].firstMatch
        XCTAssertTrue(export.exists); export.tap()
        let copy = app.descendants(matching: .any).matching(NSPredicate(format: "label IN %@", ["拷贝", "复制", "Copy"])).firstMatch
        XCTAssertTrue(copy.waitForExistence(timeout: 5), "The native text export sheet must offer copying")
        let shareTree = XCTAttachment(string: app.debugDescription)
        shareTree.name = "System text export accessibility"; shareTree.lifetime = .keepAlways; add(shareTree)
        let saveFile = app.descendants(matching: .any).matching(NSPredicate(format: "label IN %@", ["存储到文件", "储存到文件", "Save to Files"])).firstMatch
        XCTAssertTrue(saveFile.exists, "The native text export panel must offer saving the same text")
        XCTAssertEqual(app.otherElements["LP.CaptionBar.BottomCaption"].label, "Synthetic recovery text",
                       "The system export preview must contain the exact recovery text")
        let evidence = XCTAttachment(screenshot: app.screenshot())
        evidence.name = "Synthetic recovery text in the system export panel"; evidence.lifetime = .keepAlways; add(evidence)
        copy.tap()
        expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: copy)
        waitForExpectations(timeout: 5)
        let retry = app.buttons["mobileRetryRecoverySave"].firstMatch
        XCTAssertTrue(retry.waitForExistence(timeout: 5)); retry.tap()
        XCTAssertTrue(app.staticTexts["Synthetic recovery text"].exists, "Failed retry must retain the text")
        let discard = app.buttons["mobileDiscardRecovery"].firstMatch
        reveal(discard); discard.tap()
        let confirm = app.buttons["mobileConfirmDiscardRecovery"].firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Synthetic recovery text"].exists, "Opening confirmation must not discard")
        confirm.tap()
        let removed = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"),
            object: app.staticTexts["Synthetic recovery text"])
        XCTAssertEqual(XCTWaiter.wait(for: [removed], timeout: 5), .completed, app.debugDescription)
    }

    func testCreatedBlankDayAppearsAfterBackInRootAndAppToolsLists() {
        for tree in [false, true] {
            app.terminate()
            app.launchEnvironment["RIJI_TEST_CONTAINER"] = UUID().uuidString
            app.launchEnvironment["RIJI_TEST_VAULT_BROWSER"] = tree ? "1" : nil
            app.launch()
            XCTAssertTrue(input.waitForExistence(timeout: 10))
            app.buttons["diaryDirectory"].tap()
            if tree {
                let tools = app.buttons["appDiaryTools"]
                XCTAssertTrue(tools.waitForExistence(timeout: 10)); tools.tap()
            }
            let created = app.buttons["directory-2026-09-12"]
            XCTAssertFalse(created.exists)
            app.buttons["blankDayFlow"].tap()
            app.buttons["previewBlankDay"].tap()
            XCTAssertTrue(app.staticTexts["blankDayPreview"].waitForExistence(timeout: 5))
            let confirm = app.buttons["confirmBlankDay"]; reveal(confirm); confirm.tap()
            expectation(for: NSPredicate(format: "label CONTAINS %@", "已保存在手机"),
                evaluatedWith: app.staticTexts["workflowStatus"])
            waitForExpectations(timeout: 10)
            app.navigationBars.buttons.element(boundBy: 0).tap()
            XCTAssertTrue(created.waitForExistence(timeout: 5), "Returning from creation must refresh this list")
            reveal(created)
            let evidence = XCTAttachment(screenshot: app.screenshot())
            evidence.name = tree ? "Created date in nested App diary list" : "Created date in root diary list"
            evidence.lifetime = .keepAlways; add(evidence)
            created.tap()
            XCTAssertTrue(input.waitForExistence(timeout: 5))
            XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-12"))
            waitForStatus("已保存在手机")
        }
    }

    func testTodayDatePickerAndBlankPreviewKeepSkippedLocalDateLabels() {
        let cases = [
            (zone: "America/Sao_Paulo", now: "2018-11-05T12:00:00Z", start: "2018-11-05", skipped: "2018-11-04"),
            (zone: "Pacific/Apia", now: "2011-12-31T08:00:00Z", start: "2011-12-31", skipped: "2011-12-30")
        ]
        for value in cases {
            app.terminate()
            app.launchEnvironment["RIJI_TEST_CONTAINER"] = UUID().uuidString
            app.launchEnvironment["RIJI_TEST_DATE"] = value.now
            app.launchEnvironment["RIJI_TEST_NAVIGATION_ZONE"] = value.zone
            launchNavigationFixture()
            XCTAssertTrue(app.buttons["selectedDate"].label.contains(value.start))
            input.swipeRight()
            XCTAssertTrue(app.buttons["selectedDate"].label.contains(value.skipped))
            app.buttons["selectedDate"].tap()
            XCTAssertTrue(app.navigationBars["选择日期"].waitForExistence(timeout: 5))
            app.buttons["打开"].tap()
            XCTAssertTrue(input.waitForExistence(timeout: 5))
            XCTAssertTrue(app.buttons["selectedDate"].label.contains(value.skipped))
            app.tabBars.buttons["设置"].tap(); app.buttons["settingsFiles"].tap()
            let zone = app.descendants(matching: .any).matching(identifier: "settingsTimeZone").firstMatch
            XCTAssertTrue(zone.waitForExistence(timeout: 5))
            XCTAssertTrue((zone.label + " " + (zone.value as? String ?? "")).contains(value.zone))
            app.tabBars.buttons["日记"].tap()
            app.buttons["diaryDirectory"].tap()
            app.buttons["blankDayFlow"].tap()
            app.buttons["previewBlankDay"].tap()
            let preview = app.staticTexts["blankDayPreview"]
            XCTAssertTrue(preview.waitForExistence(timeout: 5))
            XCTAssertTrue(preview.label.contains(value.skipped), preview.label)
            XCTAssertTrue(app.staticTexts[value.skipped + " · 已固定的日记预览"].exists)
            let evidence = XCTAttachment(screenshot: app.screenshot())
            evidence.name = "Skipped local date preview " + value.skipped
            evidence.lifetime = .keepAlways; add(evidence)
        }
    }

    func testNavigationGesturesAndDestinationRoundTripsPreserveActualReadingPosition() {
        launchNavigationFixture()
        let saved = input.value as? String
        // The fixture's second plain line is table-like; a horizontal drag belongs to it.
        let origin = input.coordinate(withNormalizedOffset: .zero)
        origin.withOffset(CGVector(dx: 280, dy: 60)).press(forDuration: 0.01,
            thenDragTo: origin.withOffset(CGVector(dx: 60, dy: 60)))
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-12"))
        input.swipeUp()
        let scrolled = viewport("y"); XCTAssertGreaterThan(scrolled, 100)
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-12"))
        dragBody(from: CGVector(dx: 0.6, dy: 0.5), to: CGVector(dx: 0.52, dy: 0.5))
        assertSelectedDiary("2026-09-12", content: saved)
        dragBody(from: CGVector(dx: 0.4, dy: 0.5), to: CGVector(dx: 0.48, dy: 0.5))
        assertSelectedDiary("2026-09-12", content: saved)
        dragBody(from: CGVector(dx: 0.8, dy: 0.3), to: CGVector(dx: 0.25, dy: 0.7))
        assertSelectedDiary("2026-09-12", content: saved)
        // A short drag may put the native caret into the text. Explicitly finish
        // editing through the existing date picker before testing a reading swipe.
        app.buttons["selectedDate"].tap()
        app.buttons["取消"].tap()
        let position = viewport("y"), caret = viewport("caret")
        input.swipeRight()
        let gestureEvidence = XCTAttachment(screenshot: app.screenshot()); gestureEvidence.name = "Actual previous-day swipe"; gestureEvidence.lifetime = .keepAlways; add(gestureEvidence)
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-11"), input.label)
        XCTAssertEqual(input.value as? String, "Previous synthetic day\n")
        XCTAssertEqual(viewport("y"), 0)
        input.swipeLeft()
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-12"))
        XCTAssertEqual(input.value as? String, saved)
        XCTAssertEqual(viewport("y"), position, accuracy: 2)
        XCTAssertEqual(viewport("caret"), caret)
        app.tabBars.buttons["导师"].tap()
        assertDisconnectedDestination("导师")
        app.tabBars.buttons["记忆"].tap()
        assertDisconnectedDestination("记忆")
        app.tabBars.buttons["设置"].tap()
        XCTAssertTrue(app.navigationBars["设置"].waitForExistence(timeout: 5))
        app.tabBars.buttons["日记"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, saved); XCTAssertEqual(viewport("y"), position, accuracy: 2)
        app.buttons["diaryDirectory"].tap()
        app.buttons["directory-2026-09-12"].tap()
        XCTAssertEqual(input.value as? String, saved); XCTAssertEqual(viewport("y"), position, accuracy: 2)
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Diary position after date, destination and directory round trips"; attachment.lifetime = .keepAlways; add(attachment)
    }

    private func assertDisconnectedDestination(_ title: String) {
        let identifier = "unavailable-" + title
        let status = app.staticTexts.matching(identifier: identifier)
            .matching(NSPredicate(format: "label == %@", "尚未连接")).firstMatch
        XCTAssertTrue(status.waitForExistence(timeout: 5)); XCTAssertTrue(status.isHittable)
        let guidance = app.staticTexts.matching(identifier: identifier)
            .matching(NSPredicate(format: "label == %@", "尚未启用 AI 服务，请更新个人安装。本地日记可继续使用。")).firstMatch
        XCTAssertTrue(guidance.exists)
        let retry = app.buttons.matching(identifier: identifier)
            .matching(NSPredicate(format: "label == %@", "重试连接")).firstMatch
        XCTAssertTrue(retry.isHittable); XCTAssertTrue(retry.isEnabled)
        XCTAssertTrue(app.tabBars.buttons[title].isSelected)
        XCTAssertFalse(input.exists)
    }

    func testApprovedSettingsGroupsKeepDiaryAndRecoveryRoutesReachable() {
        let original = input.value as? String
        app.tabBars.buttons["设置"].tap()
        let settingsTitle = app.navigationBars["设置"].staticTexts["设置"]
        XCTAssertTrue(settingsTitle.waitForExistence(timeout: 5)); XCTAssertTrue(settingsTitle.isHittable)
        attachMentorScreenshot("Approved settings root — visible inline title")
        for (id, title) in [("settingsFiles", "文件与模板"), ("settingsDataAI", "资料与 AI"),
                            ("settingsTasks", "任务与处理"), ("settingsExportRecovery", "导出与恢复")] {
            let row = app.buttons[id]; reveal(row); tapVisibleGroupAction(row)
            XCTAssertTrue(app.navigationBars[title].waitForExistence(timeout: 5),
                          "The selected settings destination must finish opening before inspecting its controls")
            XCTAssertEqual(app.tabBars.buttons.allElementsBoundByIndex.map(\.label), ["日记", "导师", "记忆", "设置"])
            if id == "settingsFiles" {
                XCTAssertTrue(app.buttons["settingsChooseVault"].exists)
                XCTAssertTrue(app.buttons["settingsTemplates"].exists)
            } else if id == "settingsDataAI" {
                XCTAssertTrue(app.buttons["settingsPrivacy"].exists)
                XCTAssertTrue(app.buttons["settingsConnection"].exists)
            } else if id == "settingsTasks" {
                XCTAssertTrue(app.descendants(matching: .any)["settingsTasksUnavailable"].exists)
            } else {
                let drafts = app.buttons["settingsRetainedDrafts"]; reveal(drafts); drafts.tap()
                XCTAssertTrue(app.navigationBars["本机保留内容"].waitForExistence(timeout: 5))
                app.navigationBars.buttons.element(boundBy: 0).tap()
                let recordings = app.buttons["settingsRecordings"]; reveal(recordings); recordings.tap()
                XCTAssertTrue(app.navigationBars["语音记录"].waitForExistence(timeout: 5))
                XCTAssertFalse(app.buttons["recordVoice"].exists)
                app.buttons["closeVoice"].tap()
            }
            attachMentorScreenshot("Approved settings destination — " + id)
            app.navigationBars.buttons.element(boundBy: 0).tap()
        }
        app.tabBars.buttons["日记"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5)); XCTAssertEqual(input.value as? String, original)
        input.swipeRight()
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-11"))
        app.buttons["selectedDate"].tap()
        let today = app.buttons["dateSheetToday"]
        XCTAssertTrue(today.waitForExistence(timeout: 5)); reveal(today); today.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-12"))
        XCTAssertEqual(input.value as? String, original)
    }

    func testApprovedMemoryFiltersAndUnavailableReportWithDarkLargeText() {
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launchEnvironment["RIJI_TEST_APPEARANCE"] = "dark"
        launchMobileFixture("overview-multiple")
        attachMentorScreenshot("Approved diary — dark largest text")
        app.tabBars.buttons["设置"].tap()
        let settingsTitle = app.navigationBars["设置"].staticTexts["设置"]
        XCTAssertTrue(settingsTitle.waitForExistence(timeout: 5)); XCTAssertTrue(settingsTitle.isHittable)
        let files = app.buttons["settingsFiles"]; reveal(files)
        attachMentorScreenshot("Approved settings — dark largest text")
        app.tabBars.buttons["记忆"].tap()
        let filter = app.buttons["mobileThemeFilter"]; reveal(filter)
        let summary = app.buttons["mobileSummary-synthetic-summary"]; reveal(summary)
        XCTAssertEqual(summary.value as? String, "首个主题")
        captureMemoryCardEntry(summary, named: "Approved memory all themes — first card, dark largest text")
        let goal = app.buttons["mobileSummary-synthetic-goal"]; reveal(goal)
        XCTAssertEqual(goal.value as? String, "主题")
        let nextGoal = app.buttons["mobileSummary-synthetic-goal-next"]; reveal(nextGoal)
        XCTAssertEqual(nextGoal.value as? String, "主题")
        captureMemoryCardEntry(nextGoal, named: "Approved memory all themes — ordinary remaining card, dark largest text")
        selectMemoryTheme("工作与学习")
        for card in [summary, goal, nextGoal] { XCTAssertFalse(card.exists) }
        selectMemoryTheme("目标与计划")
        XCTAssertFalse(summary.exists)
        reveal(goal); XCTAssertEqual(goal.value as? String, "首个主题")
        captureMemoryCardEntry(goal, named: "Approved memory filtered goals — new first card, dark largest text")
        goal.tap()
        XCTAssertTrue(app.staticTexts["mobileSummaryText"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.staticTexts["mobileSummaryText"].label, "A synthetic plan supported by the selected evidence.")
        let evidence = app.buttons["mobileOverviewEvidence-synthetic-memory"]; reveal(evidence)
        attachMentorScreenshot("Approved memory evidence — dark largest text")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        reveal(nextGoal); XCTAssertEqual(nextGoal.value as? String, "主题")
        captureMemoryCardEntry(nextGoal, named: "Approved memory filtered goals — ordinary remaining card, dark largest text")
        let weekly = app.buttons["mobileWeeklyMemory"]; reveal(weekly); weekly.tap()
        XCTAssertTrue(app.descendants(matching: .any)["mobileWeeklyMemoryUnavailable"].waitForExistence(timeout: 5))
        attachMentorScreenshot("Approved weekly report unavailable — dark largest text")
    }

    private func selectMemoryTheme(_ title: String) {
        let filter = app.buttons["mobileThemeFilter"]
        revealMemoryThemeFilter(filter); filter.tap()
        let option = app.buttons[title]
        XCTAssertTrue(option.waitForExistence(timeout: 5))
        var previousFrame = CGRect.null
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            guard option.exists else { return false }
            let frame = option.frame
            guard self.isFiniteFrame(frame), self.app.frame.insetBy(dx: 8, dy: 44).contains(frame) else { return false }
            let stable = previousFrame == frame
            previousFrame = frame
            return stable && option.isEnabled && option.isHittable
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 5), .completed,
                       "The native picker option must have a stable visible hit target before selection")
        option.tap()
        let selected = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            filter.exists && filter.label.contains("分类筛选") && filter.label.hasSuffix(title)
        }, object: filter)
        let dismissed = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: option)
        XCTAssertEqual(XCTWaiter.wait(for: [selected, dismissed], timeout: 5), .completed,
                       "Filtering must commit the selected category and dismiss the native menu before inspecting cards")
    }

    private func revealMemoryThemeFilter(_ filter: XCUIElement) {
        let list = app.collectionViews.firstMatch
        XCTAssertTrue(app.navigationBars["记忆"].waitForExistence(timeout: 5))
        XCTAssertTrue(list.waitForExistence(timeout: 5))
        for _ in 0..<16 {
            let bounds = list.frame.intersection(app.frame)
            let top = max(bounds.minY, app.navigationBars["记忆"].frame.maxY) + 12
            let bottom = min(bounds.maxY, app.tabBars.firstMatch.frame.minY) - 12
            XCTAssertGreaterThan(bottom - top, 200)
            let viewport = CGRect(x: bounds.minX, y: top, width: bounds.width, height: bottom - top)
            var offset = min(360, viewport.height * 0.6)
            if filter.exists, isFiniteFrame(filter.frame) {
                let frame = filter.frame
                offset = viewport.midY - frame.midY
                if viewport.contains(frame), abs(offset) < 24, filter.isHittable { return }
                // Once mounted, approach the center in small steps. Full-screen
                // swipes overshoot this large-text picker and can oscillate.
                offset = (offset < 0 ? -1 : 1) * min(120, max(24, abs(offset) + 10))
            }
            let origin = list.coordinate(withNormalizedOffset: .zero)
            let startY = viewport.midY - list.frame.minY - offset / 2
            let start = origin.withOffset(CGVector(dx: 12, dy: startY))
            start.press(forDuration: 0.05, thenDragTo: start.withOffset(CGVector(dx: 0, dy: offset)),
                        withVelocity: .slow, thenHoldForDuration: 0.2)
        }
        XCTFail("The memory picker must be fully visible near the list center before opening its menu")
    }

    func testSettingsMainTabSavesInputAndKeepsSubpagesAndRecordingsReachable() {
        let pending = appendSettingsDiaryInput("Pending settings edit")
        app.buttons["收起键盘"].tap()
        XCTAssertTrue(app.tabBars.buttons["设置"].isHittable)
        app.tabBars.buttons["设置"].tap()
        XCTAssertTrue(app.navigationBars["设置"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.tabBars.buttons.allElementsBoundByIndex.map(\.label), ["日记", "导师", "记忆", "设置"])
        XCTAssertTrue(app.tabBars.buttons["设置"].isSelected)
        XCTAssertFalse(input.exists)
        XCTAssertFalse(app.buttons["moreMenu"].isHittable)
        XCTAssertFalse(app.buttons["directorySettings"].exists)
        XCTAssertFalse(app.buttons["openVaultFolder"].exists)
        XCTAssertEqual(app.navigationBars["设置"].buttons.count, 0)
        app.buttons["settingsDataAI"].tap(); app.buttons["settingsConnection"].tap()
        let airStatus = app.staticTexts.matching(identifier: "settingsAirStatus")
            .matching(NSPredicate(format: "label CONTAINS %@", "尚未连接")).firstMatch
        XCTAssertTrue(airStatus.waitForExistence(timeout: 5), app.debugDescription)
        XCTAssertTrue(airStatus.isHittable)
        let retry = app.buttons["mobileRetryConnection"]
        XCTAssertTrue(retry.isHittable); XCTAssertTrue(retry.isEnabled)
        XCTAssertTrue(app.staticTexts["尚未启用 AI 服务，请更新个人安装。本地日记可继续使用。"].exists)
        XCTAssertFalse(app.buttons["moreMenu"].isHittable)
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.buttons["settingsFiles"].tap(); app.buttons["settingsTemplates"].tap()
        XCTAssertTrue(app.buttons["weekdayLanguage"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["moreMenu"].isHittable)
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.navigationBars.buttons.element(boundBy: 0).tap()
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Settings as fourth peer tab"; attachment.lifetime = .keepAlways; add(attachment)
        app.tabBars.buttons["日记"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5)); XCTAssertEqual(input.value as? String, pending)
        app.terminate(); app.launch(); XCTAssertTrue(input.waitForExistence(timeout: 10))
        XCTAssertEqual(input.value as? String, pending)
        app.buttons["moreMenu"].tap()
        let library = app.buttons["voiceLibrary"]
        XCTAssertTrue(library.waitForExistence(timeout: 5)); XCTAssertEqual(library.label, "录音记录")
        library.tap()
        XCTAssertTrue(app.navigationBars["语音记录"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["查看本机保存的录音和未完成的文字记录。"].exists)
        XCTAssertTrue(app.buttons["prepareVoiceRecord"].exists)
        XCTAssertFalse(app.buttons["recordVoice"].exists, "Opening the library must not create or start a recording")
        app.buttons["closeVoice"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5)); XCTAssertEqual(input.value as? String, pending)
    }

    func testDirectoryWithoutSettingsFooterKeepsExpandedLongTreeDuringPreview() {
        launchNavigationFixture(tree: true)
        app.buttons["diaryDirectory"].tap()
        let daily = app.buttons["vault-folder-daily"]
        XCTAssertTrue(daily.waitForExistence(timeout: 10)); daily.tap()
        let row = app.buttons["vault-file-daily/Row-040.md"]
        for _ in 0..<15 { if row.isHittable { break }; app.swipeUp() }
        XCTAssertTrue(row.isHittable)
        let frame = row.frame
        XCTAssertFalse(app.buttons["directorySettings"].exists)
        row.tap()
        XCTAssertTrue(app.textViews["vaultFileReader"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.textViews["vaultFileReader"].value as? String, "Synthetic tree row 40\n")
        app.buttons["closeVaultPreview"].tap()
        XCTAssertTrue(row.waitForExistence(timeout: 5)); XCTAssertTrue(row.isHittable)
        XCTAssertEqual(row.frame.minY, frame.minY, accuracy: 3)
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Expanded long directory without Settings footer"; attachment.lifetime = .keepAlways; add(attachment)
        app.buttons["完成"].tap()
        app.tabBars.buttons["设置"].tap()
        XCTAssertTrue(app.navigationBars["设置"].waitForExistence(timeout: 5))
    }

    func testSettingsTemplateChangesRefreshOnlyUnwrittenDiaryAcrossTabs() {
        app.tabBars.buttons["设置"].tap()
        app.buttons["settingsFiles"].tap(); app.buttons["settingsTemplates"].tap()
        let sample = app.buttons["previewSampleTemplate"]; reveal(sample); sample.tap()
        app.buttons["confirmTemplateImport"].tap()
        let imported = app.buttons["template-imported"]; revealAboveToolbar(imported); imported.tap()
        app.buttons["selectTemplate"].tap()
        let language = app.buttons["weekdayLanguage"]
        language.tap(); app.buttons["English (Monday)"].tap()
        XCTAssertTrue(language.label.contains("English"))
        app.tabBars.buttons["导师"].tap()
        app.tabBars.buttons["日记"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertTrue((input.value as? String)?.contains("fixture: synthetic-manual-acceptance") == true)
        XCTAssertTrue((input.value as? String)?.contains("weekday: 'Saturday'") == true)
        let english = input.value as? String ?? ""
        app.tabBars.buttons["设置"].tap()
        XCTAssertTrue(language.waitForExistence(timeout: 5))
        language.tap(); app.buttons["中文（星期一）"].tap()
        XCTAssertTrue(language.label.contains("中文"))
        app.tabBars.buttons["日记"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, english.replacingOccurrences(of: "weekday: 'Saturday'", with: "weekday: '星期六'"))
        type("Synthetic fixed content")
        let saved = input.value as? String
        app.tabBars.buttons["设置"].tap()
        app.buttons["template-initial"].tap(); app.buttons["selectTemplate"].tap()
        language.tap(); app.buttons["English (Monday)"].tap()
        app.tabBars.buttons["日记"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5)); XCTAssertEqual(input.value as? String, saved)
        app.terminate(); app.launch(); XCTAssertTrue(input.waitForExistence(timeout: 10))
        XCTAssertEqual(input.value as? String, saved)
    }

    func testBothSwipesPreviewMissingFutureOriginalDaysAndReturnWithoutCreatingFiles() {
        app.terminate()
        app.launchEnvironment["RIJI_TEST_ORIGINAL_VAULT"] = "1"
        app.launchEnvironment["RIJI_TEST_VAULT_BROWSER"] = "1"
        app.launchEnvironment["RIJI_TEST_NAVIGATION"] = "1"
        app.launch()
        waitForOriginalEditor(containing: "After synthetic table")
        let original = input.value as? String
        // Begin above the native table so the gesture belongs to date navigation.
        let origin = input.coordinate(withNormalizedOffset: .zero)
        origin.withOffset(CGVector(dx: 290, dy: 34)).press(forDuration: 0.01,
            thenDragTo: origin.withOffset(CGVector(dx: 60, dy: 34)))
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-13"))
        XCTAssertEqual(input.value as? String, "# Original template\n")
        XCTAssertFalse(status.label.contains("已保存在此设备"))
        input.swipeLeft()
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-14"))
        XCTAssertEqual(input.value as? String, "# Original template\n")
        input.swipeRight()
        assertSelectedDiary("2026-09-13", content: "# Original template\n")
        XCTAssertFalse(status.label.contains("已保存在此设备"))
        input.swipeRight()
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-12"))
        XCTAssertEqual(input.value as? String, original)
        app.buttons["diaryDirectory"].tap()
        app.buttons["vault-folder-daily"].tap()
        XCTAssertFalse(app.buttons["vault-file-daily/2026-09-13.md"].exists)
        XCTAssertFalse(app.buttons["vault-file-daily/2026-09-14.md"].exists)
        XCTAssertTrue(app.buttons["vault-file-daily/2026-09-12.md"].exists)
        let evidence = XCTAttachment(screenshot: app.screenshot())
        evidence.name = "Original directory after missing future-day round trip"
        evidence.lifetime = .keepAlways; add(evidence)
    }

    func testMissingFutureDayFirstEditSavesAndColdReopensWithoutChangingSource() {
        app.terminate()
        app.launchEnvironment["RIJI_TEST_ORIGINAL_VAULT"] = "1"
        app.launchEnvironment["RIJI_TEST_VAULT_BROWSER"] = "1"
        app.launchEnvironment["RIJI_TEST_NAVIGATION"] = "1"
        app.launch()
        waitForOriginalEditor(containing: "After synthetic table")
        let original = input.value as? String
        func swipeHeadingToNextDay() {
            let origin = input.coordinate(withNormalizedOffset: .zero)
            origin.withOffset(CGVector(dx: 290, dy: 34)).press(forDuration: 0.01,
                thenDragTo: origin.withOffset(CGVector(dx: 60, dy: 34)))
        }
        swipeHeadingToNextDay()
        waitForSelectedDiary("2026-09-13", content: "# Original template\n")
        XCTAssertFalse(status.label.contains("已保存在此设备"))
        type("Future first edit", savedStatus: "已保存在此设备")
        let edited = "# Original template\nFuture first edit\n"
        assertSelectedDiary("2026-09-13", content: edited)
        input.swipeRight()
        waitForSelectedDiary("2026-09-12", content: original)
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        assertSelectedDiary("2026-09-12", content: original)
        swipeHeadingToNextDay()
        waitForSelectedDiary("2026-09-13", content: edited)
        waitForStatus("已保存在此设备")
        XCTAssertFalse(app.staticTexts["storageError"].exists)
    }

    private func waitForSelectedDiary(_ day: String, content: String?, file: StaticString = #filePath, line: UInt = #line) {
        // A completed native gesture can precede the asynchronous store load and
        // its UI update. Observe completion without repeating the navigation.
        guard let content else { XCTFail("The expected diary source must be available", file: file, line: line); return }
        let completed = NSPredicate { [self] _, _ in
            app.buttons["selectedDate"].label.contains(day) && (input.value as? String) == content
        }
        XCTAssertEqual(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: completed, object: nil)], timeout: 5),
                       .completed, "The requested day and complete source must finish loading", file: file, line: line)
        assertSelectedDiary(day, content: content, file: file, line: line)
    }

    private func assertSelectedDiary(_ day: String, content: String?, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(app.buttons["selectedDate"].label.contains(day), file: file, line: line)
        XCTAssertEqual(input.value as? String, content, file: file, line: line)
        XCTAssertTrue(app.tabBars.buttons["日记"].isSelected, file: file, line: line)
    }

    func testFailedSaveAndEditingGestureCannotLeaveDiaryOrLoseInput() {
        launchNavigationFixture(failing: true)
        let pending = appendRecoveryText(input, "Pending navigation edit")
        input.swipeLeft()
        assertSelectedDiary("2026-09-12", content: pending)
        XCTAssertTrue(app.keyboards.firstMatch.exists)
        input.swipeRight()
        assertSelectedDiary("2026-09-12", content: pending)
        XCTAssertTrue(app.keyboards.firstMatch.exists)
        app.buttons["收起键盘"].tap()
        XCTAssertTrue(app.tabBars.buttons["导师"].isHittable)
        app.tabBars.buttons["导师"].tap()
        XCTAssertTrue(app.staticTexts["storageError"].waitForExistence(timeout: 5))
        XCTAssertTrue(input.exists); XCTAssertEqual(input.value as? String, pending)
        XCTAssertTrue(app.tabBars.buttons["日记"].isSelected)
        input.swipeLeft()
        assertSelectedDiary("2026-09-12", content: pending)
        XCTAssertTrue(app.staticTexts["storageError"].exists); XCTAssertTrue(app.buttons["retryBodySave"].isHittable)
        input.swipeRight()
        assertSelectedDiary("2026-09-12", content: pending)
        XCTAssertTrue(app.staticTexts["storageError"].exists); XCTAssertTrue(app.buttons["retryBodySave"].isHittable)
        app.tabBars.buttons["记忆"].tap()
        XCTAssertTrue(input.exists); XCTAssertEqual(input.value as? String, pending)
        XCTAssertTrue(app.buttons["retryBodySave"].isHittable)
        XCTAssertTrue(app.tabBars.buttons["设置"].isHittable)
        app.tabBars.buttons["设置"].tap()
        XCTAssertTrue(input.exists); XCTAssertEqual(input.value as? String, pending)
        XCTAssertTrue(app.tabBars.buttons["日记"].isSelected)
        XCTAssertFalse(app.navigationBars["设置"].exists)
        app.terminate(); app.launch(); XCTAssertTrue(input.waitForExistence(timeout: 10))
        XCTAssertEqual(input.value as? String, pending)
    }

    private func appendRecoveryText(_ editor: XCUIElement, _ suffix: String) -> String {
        let before = editor.value as? String ?? ""
        editor.tap(); editor.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        editor.typeKey(XCUIKeyboardKey.downArrow, modifierFlags: .command)
        editor.typeText(suffix)
        XCTAssertEqual(editor.value as? String, before + suffix)
        return before + suffix
    }

    private func appendSettingsDiaryInput(_ suffix: String) -> String {
        let before = input.value as? String ?? ""
        input.tap(); input.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        let keyboard = app.keyboards.firstMatch
        XCTAssertTrue(keyboard.waitForExistence(timeout: 5))
        // Use the real editor's end action rather than relying on keyboard-shortcut
        // routing to establish this pending-input precondition.
        let end = app.buttons["文末"]
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            guard end.exists, self.hasVisibleFrame(end) else { return false }
            return end.frame.maxY <= keyboard.frame.minY + 1 && end.isHittable
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 5), .completed)
        XCTAssertEqual(input.value as? String, before, "Preparing the caret must preserve the original document")
        end.tap(); input.typeText(suffix)
        let entered = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", before + suffix), object: input)
        XCTAssertEqual(XCTWaiter.wait(for: [entered], timeout: 5), .completed)
        return before + suffix
    }

    func testCorruptRecoveryShowsReadOnlySavedDocumentAndExportsOnlyItsExactBytes() {
        launchRecoveryFixture("corrupt")
        let expected = "# Synthetic recovery\n## Notes\nBase\n"
        XCTAssertEqual(input.value as? String, expected)
        let error = app.staticTexts["storageError"]
        XCTAssertTrue(error.waitForExistence(timeout: 10)); XCTAssertTrue(error.label.contains("只读"))
        XCTAssertTrue(status.label.contains("已保存版本"))
        XCTAssertFalse(app.buttons["openVoice"].exists)
        XCTAssertFalse(app.buttons["recoverBodyMerge"].isEnabled)
        input.tap(); XCTAssertFalse(app.keyboards.firstMatch.exists)
        XCTAssertEqual(input.value as? String, expected)
        app.buttons["retryBodyRecoveryRead"].tap()
        XCTAssertTrue(error.exists); XCTAssertEqual(input.value as? String, expected)
        app.buttons["moreMenu"].tap()
        let export = app.buttons["exportMarkdown"]
        XCTAssertTrue(export.label.contains("已保存版本")); export.tap()
        let filename = app.textFields["DOCPicker.filenameTextField"]
        XCTAssertTrue(filename.waitForExistence(timeout: 10))
        let keyboard = app.keyboards.firstMatch
        let ready = NSPredicate { _, _ in filename.isHittable && keyboard.exists && filename.frame.maxY <= keyboard.frame.minY }
        var focused = false
        for _ in 0..<3 {
            if filename.isHittable { filename.tap() }
            if XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: ready, object: filename)], timeout: 2) == .completed { focused = true; break }
        }
        XCTAssertTrue(focused)
        let oldName = filename.value as? String ?? ""
        filename.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: oldName.count))
        filename.typeText("Riji-Saved-Recovery-" + container)
        app.buttons["保存"].tap(); waitForStatus("不含待恢复修改")
        let digest = SHA256.hash(data: Data(expected.utf8)).map { String(format: "%02x", $0) }.joined()
        XCTAssertEqual(status.value as? String, digest, "The system-exported file must contain only the verified saved version")
        XCTAssertTrue(error.exists)
        app.buttons["diaryDirectory"].tap()
        let otherDay = app.buttons["directory-2026-09-11"]
        XCTAssertTrue(otherDay.waitForExistence(timeout: 10)); reveal(otherDay); otherDay.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 10)); XCTAssertEqual(input.value as? String, "# Another healthy day\n")
        XCTAssertFalse(app.staticTexts["storageError"].exists)
        app.buttons["diaryDirectory"].tap()
        let originalDay = app.buttons["directory-2026-09-12"]
        XCTAssertTrue(originalDay.waitForExistence(timeout: 10)); reveal(originalDay); originalDay.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 10)); XCTAssertEqual(input.value as? String, expected)
        XCTAssertTrue(app.staticTexts["storageError"].exists)
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10)); XCTAssertEqual(input.value as? String, expected)
        XCTAssertTrue(app.staticTexts["storageError"].exists)
    }

    func testRejectedProtectedEditShowsAccessibleExplanationAndKeepsSavedBytes() {
        launchRecoveryFixture("protected")
        let original = input.value as? String ?? ""
        XCTAssertTrue(original.hasPrefix("---\r\nunknown: keep\r\n---"))
        input.tap(); input.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        input.typeKey(XCUIKeyboardKey.upArrow, modifierFlags: .command)
        for _ in 0..<3 { input.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: .shift) }
        input.typeText("X")
        let feedback = app.staticTexts["protectedBodyEditFeedback"]
        XCTAssertTrue(feedback.waitForExistence(timeout: 5)); XCTAssertTrue(feedback.label.contains("来源标识"))
        XCTAssertEqual(input.value as? String, original); XCTAssertFalse(app.staticTexts["storageError"].exists)
        XCTAssertTrue(status.label.contains("已保存在手机"))
        app.buttons["文末"].tap(); input.typeText("Allowed body edit\n"); waitForStatus("已保存在手机")
        XCTAssertEqual(input.value as? String, original + "Allowed body edit\n")
        expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: feedback); waitForExpectations(timeout: 5)
        app.terminate()
        app.launchEnvironment["RIJI_TEST_DATE"] = "2026-09-13T08:00:00Z"
        app.launch(); XCTAssertTrue(input.waitForExistence(timeout: 10))
        app.buttons["diaryDirectory"].tap()
        let priorDay = app.buttons["directory-2026-09-12"]
        XCTAssertTrue(priorDay.waitForExistence(timeout: 10)); reveal(priorDay); priorDay.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        input.tap(); input.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        input.typeKey(XCUIKeyboardKey.upArrow, modifierFlags: .command)
        for _ in 0..<3 { input.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: .shift) }
        input.typeText("X"); XCTAssertTrue(feedback.waitForExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, original + "Allowed body edit\n")
        app.buttons["收起键盘"].tap(); app.buttons["returnToday"].tap()
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-13"))
        expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: feedback); waitForExpectations(timeout: 5)
    }

    func testActualMergeRecoveryControlsDisableHeldInputThenRetryExactBytes() {
        launchRecoveryFixture("merge")
        app.buttons["recoverBodyMerge"].tap()
        let fullVersions = app.buttons["bodyFullVersions"]; reveal(fullVersions); fullVersions.tap()
        let editor = app.textViews["bodyConflictMerge"]
        XCTAssertTrue(editor.waitForExistence(timeout: 10))
        let expected = appendRecoveryText(editor, "Accepted merged input 中 e\u{301}\n")
        let confirm = app.buttons["confirmBodyMerge"]; revealBodyMergeControl(confirm); confirm.tap()
        let gate = app.staticTexts["recoveryGateState"]
        expectation(for: NSPredicate(format: "label == 'held'"), evaluatedWith: gate); waitForExpectations(timeout: 10)
        XCTAssertFalse(editor.isEnabled); XCTAssertFalse(confirm.isEnabled)
        XCTAssertEqual(editor.value as? String, expected)
        let refresh = app.buttons["refreshBodyMergeSource"]
        revealBodyMergeControl(refresh, towardEarlier: true, enabled: false)
        XCTAssertFalse(refresh.isEnabled); XCTAssertFalse(app.buttons["closeBodyMerge"].isEnabled)
        let release = app.buttons["releaseRecoveryGate"]; revealBodyMergeControl(release); release.tap()
        expectation(for: NSPredicate(format: "enabled == true"), evaluatedWith: editor); waitForExpectations(timeout: 10)
        XCTAssertTrue(app.staticTexts["bodyMergeError"].exists)
        let reviewedSource = app.staticTexts["bodyConflictSource"]
        revealBodyMergeControl(reviewedSource, towardEarlier: true)
        XCTAssertTrue(reviewedSource.label.contains("External source"))
        XCTAssertEqual(editor.value as? String, expected)
        revealBodyMergeControl(confirm); confirm.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 10)); waitForStatus("已保存在手机")
        XCTAssertEqual(input.value as? String, expected)
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10)); XCTAssertEqual(input.value as? String, expected)
    }

    func testActualLegacyRecoveryControlsOwnHeldBodyFlushAndShowFailure() {
        launchRecoveryFixture("legacy")
        let body = input.value as? String ?? ""
        app.buttons["recoverLegacyDraft"].tap()
        let editor = app.textViews["legacyDraftEditor"]
        XCTAssertTrue(editor.waitForExistence(timeout: 10))
        let expected = appendRecoveryText(editor, " latest accepted 中 e\u{301}")
        let confirm = app.buttons["confirmLegacyDraft"]; reveal(confirm); confirm.tap()
        let gate = app.staticTexts["recoveryGateState"]
        expectation(for: NSPredicate(format: "label == 'held'"), evaluatedWith: gate); waitForExpectations(timeout: 10)
        XCTAssertFalse(editor.isEnabled); XCTAssertFalse(confirm.isEnabled)
        XCTAssertFalse(app.buttons["legacySectionPicker"].isEnabled)
        XCTAssertFalse(app.buttons["refreshLegacySource"].isEnabled); XCTAssertFalse(app.buttons["closeLegacyRecovery"].isEnabled)
        XCTAssertEqual(editor.value as? String, expected)
        let release = app.buttons["releaseRecoveryGate"]; reveal(release); release.tap()
        expectation(for: NSPredicate(format: "enabled == true"), evaluatedWith: editor); waitForExpectations(timeout: 10)
        XCTAssertTrue(app.staticTexts["legacyRecoveryError"].exists)
        XCTAssertEqual(editor.value as? String, expected)
        let refresh = app.buttons["refreshLegacySource"]; revealAboveToolbar(refresh); refresh.tap()
        expectation(for: NSPredicate(format: "enabled == true"), evaluatedWith: confirm); waitForExpectations(timeout: 10)
        reveal(confirm); confirm.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 10)); waitForStatus("已保存在手机")
        let final = input.value as? String ?? ""
        XCTAssertTrue(final.hasPrefix(body)); XCTAssertTrue(final.contains(expected))
        XCTAssertEqual(final.components(separatedBy: expected).count, 2)
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10)); XCTAssertEqual(input.value as? String, final)
        XCTAssertFalse(app.buttons["recoverLegacyDraft"].exists)
    }

    func testSyntheticVoiceFilesReplayCorrectionInsertionSaveAndRelaunch() {
        type("Existing before voice")
        let beforeVoice = input.value as? String ?? ""
        app.buttons["moreMenu"].tap(); app.buttons["voiceLibrary"].tap()
        app.buttons["prepareVoiceRecord"].tap()
        let export = app.buttons["exportSyntheticVoice"]; reveal(export); export.tap()
        let sampleName = "Voice-" + String(container.prefix(8))
        saveVoiceFile(named: sampleName)
        let importer = app.buttons["importSyntheticVoice"]
        reveal(importer); importer.tap()
        let file = app.cells.matching(NSPredicate(format: "identifier BEGINSWITH %@", sampleName)).firstMatch
        if !file.waitForExistence(timeout: 5), app.buttons["最近项目"].exists { app.buttons["最近项目"].tap() }
        XCTAssertTrue(file.waitForExistence(timeout: 10))
        file.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.25)).tap()
        let play = app.buttons["playVoice-0"]
        for _ in 0..<8 { if play.isHittable { break }; app.swipeDown() }
        XCTAssertTrue(play.waitForExistence(timeout: 10)); revealAboveToolbar(play); play.tap()
        let voiceStatus = app.descendants(matching: .any).matching(identifier: "voiceStatus").firstMatch
        for _ in 0..<8 { if voiceStatus.isHittable { break }; app.swipeDown() }
        XCTAssertEqual(voiceStatus.value as? String, "playback-verified")
        expectation(for: NSPredicate(format: "label CONTAINS %@", "原录音回放完成"), evaluatedWith: voiceStatus)
        waitForExpectations(timeout: 8)
        XCTAssertFalse(app.buttons["stopVoicePlayback"].exists)
        let audioAttachment = XCTAttachment(screenshot: app.screenshot()); audioAttachment.name = "Synthetic WAV replay and correction"; audioAttachment.lifetime = .keepAlways; add(audioAttachment)
        let correction = app.textViews["voiceCorrection"]
        reveal(correction); correction.tap(); correction.typeText("Exact synthetic voice correction")
        app.swipeUp()
        let preview = app.buttons["previewVoiceInsertion"]; reveal(preview); preview.tap()
        let previewText = app.staticTexts["voiceInsertionPreview"]
        XCTAssertTrue(previewText.waitForExistence(timeout: 10))
        let previewAttachment = XCTAttachment(screenshot: app.screenshot()); previewAttachment.name = "Exact voice insertion preview"; previewAttachment.lifetime = .keepAlways; add(previewAttachment)
        let exactPreview = previewText.label
        XCTAssertTrue(exactPreview.contains("Exact synthetic voice correction"))
        XCTAssertTrue(exactPreview.contains("../Attachments/Audio/"))
        let confirm = app.buttons["confirmVoiceInsertion"]; reveal(confirm); confirm.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        XCTAssertTrue((input.value as? String)?.contains("Existing before voice") == true)
        XCTAssertTrue((input.value as? String)?.contains(exactPreview) == true)
        waitForStatus("已保存在手机")
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        app.buttons["moreMenu"].tap(); app.buttons["voiceLibrary"].tap()
        let retained = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "voice-library-")).firstMatch
        XCTAssertTrue(retained.waitForExistence(timeout: 10)); retained.tap()
        XCTAssertEqual(app.textViews["voiceCorrection"].value as? String, "Exact synthetic voice correction")
        XCTAssertTrue(app.staticTexts["voiceAlreadyInserted"].exists)
        reveal(app.buttons["transcribeVoice"])
        XCTAssertFalse(app.buttons["transcribeVoice"].isEnabled)
        let originalExport = app.buttons["exportVoice-0"]
        for _ in 0..<8 { if originalExport.isHittable { break }; app.swipeDown() }
        revealAboveToolbar(originalExport)
        XCTAssertTrue(originalExport.isHittable); originalExport.tap()
        saveVoiceFile(named: "Riji-Original-Voice-" + container)
        for _ in 0..<8 { if voiceStatus.isHittable { break }; app.swipeDown() }
        XCTAssertTrue(voiceStatus.waitForExistence(timeout: 5))
        XCTAssertEqual(voiceStatus.value as? String, syntheticVoiceDigest())
        app.buttons["closeVoice"].tap()
        app.buttons["moreMenu"].tap(); app.buttons["exportMarkdown"].tap()
        saveVoiceFile(named: "Riji-Voice-Diary-" + container)
        waitForStatus("Markdown 已导出")
        let expected = beforeVoice + exactPreview + "\n"
        let digest = SHA256.hash(data: Data(expected.utf8)).map { String(format: "%02x", $0) }.joined()
        XCTAssertEqual(status.value as? String, digest)
        app.buttons["moreMenu"].tap(); app.buttons["voiceLibrary"].tap()
        reveal(retained)
        XCTAssertTrue(retained.waitForExistence(timeout: 10)); retained.tap()
        let newRecording = app.buttons["newVoiceRecording"]
        for _ in 0..<8 { if newRecording.isHittable { break }; app.swipeDown() }
        revealAboveToolbar(newRecording)
        XCTAssertTrue(newRecording.isHittable); newRecording.tap()
        XCTAssertEqual(app.textViews["voiceCorrection"].value as? String, "")
        XCTAssertTrue(app.buttons["recordVoice"].isEnabled)
        XCTAssertFalse(app.staticTexts["voiceAlreadyInserted"].exists)
        app.buttons["closeVoice"].tap()
    }

    func testProductionPauseAndEndRowDispatchesIndependentSyntheticActions() {
        app.terminate()
        app.launchEnvironment["RIJI_TEST_VOICE_ACTIONS"] = "1"
        app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        app.buttons["moreMenu"].tap(); app.buttons["voiceLibrary"].tap()
        app.buttons["prepareVoiceRecord"].tap()
        let pause = app.buttons["pauseVoice"]; reveal(pause); pause.tap()
        XCTAssertEqual(app.staticTexts["voiceActionCounts"].label, "pause=1,end=0")
        app.buttons["endVoice"].tap()
        XCTAssertEqual(app.staticTexts["voiceActionCounts"].label, "pause=1,end=1")
        let record = app.buttons["recordVoice"]
        for _ in 0..<8 { if record.isHittable { break }; app.swipeDown() }
        revealAboveToolbar(record)
        XCTAssertTrue(record.isEnabled)
        app.buttons["closeVoice"].tap()
    }

    private func saveVoiceFile(named name: String) {
        let filename = app.textFields["DOCPicker.filenameTextField"]
        let save = app.buttons["DOCPicker.actionButton"]
        XCTAssertTrue(save.waitForExistence(timeout: 10))
        // The file-provider view can arrive before its filename accessory.
        // Wait for both, and keep unique names so repeat runs never overwrite files.
        XCTAssertTrue(filename.waitForExistence(timeout: 20))
        filename.tap()
        let old = filename.value as? String ?? ""
        filename.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: old.count))
        filename.typeText(name)
        save.tap()
    }

    private func syntheticVoiceDigest() -> String {
        var data = Data()
        func word<T: FixedWidthInteger>(_ value: T) { var value = value.littleEndian; withUnsafeBytes(of: &value) { data.append(contentsOf: $0) } }
        data.append(Data("RIFF".utf8)); word(UInt32(32036)); data.append(Data("WAVEfmt ".utf8))
        word(UInt32(16)); word(UInt16(1)); word(UInt16(1)); word(UInt32(16000)); word(UInt32(32000))
        word(UInt16(2)); word(UInt16(16)); data.append(Data("data".utf8)); word(UInt32(32000))
        for sample in 0..<16000 { word(Int16(sin(Double(sample) * 2 * .pi * 440 / 16000) * 3000)) }
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
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
        XCTAssertFalse(app.buttons["sectionPicker"].exists)
        XCTAssertTrue((input.value as? String)?.contains("## 🧠  **Notes**") == true)
        type("Synthetic styled capture")
        waitForStatus("已保存在手机")
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
        XCTAssertTrue(filename.waitForExistence(timeout: 10))
        let previousName = filename.value as? String ?? ""
        let keyboard = app.keyboards.firstMatch
        let ready = NSPredicate { _, _ in
            filename.isHittable && keyboard.exists && filename.frame.maxY <= keyboard.frame.minY
        }
        var focused = false
        for _ in 0..<3 {
            if filename.isHittable { filename.tap() }
            if XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: ready, object: filename)], timeout: 2) == .completed {
                focused = true; break
            }
        }
        XCTAssertTrue(focused, "The export filename must be active above the keyboard before typing")
        XCTAssertEqual(filename.value as? String, previousName, "Preparing filename focus must not change it")
        filename.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: previousName.count))
        filename.typeText("Riji-Frozen-Preview-" + container)
        XCTAssertEqual(filename.value as? String, "Riji-Frozen-Preview-" + container)
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
        XCTAssertTrue(app.textViews["savedReview"].waitForExistence(timeout: 5))
        XCTAssertTrue((app.textViews["savedReview"].value as? String)?.contains("Synthetic native weekly reflection") == true)
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        app.buttons["diaryDirectory"].tap(); app.buttons["reviewsFlow"].tap(); app.buttons["openReviewPeriod"].tap()
        XCTAssertTrue(app.textViews["savedReview"].waitForExistence(timeout: 5))
        XCTAssertTrue((app.textViews["savedReview"].value as? String)?.contains("Synthetic native weekly reflection") == true)
    }

    func testApprovedDirectionHierarchyKeepsGoalVersionsAndMITConfirmation() {
        app.buttons["diaryDirectory"].tap()
        let direction = app.buttons["goalsFlow"]; reveal(direction); direction.tap()
        app.buttons["northStarDefinitions"].tap()
        let fields = [("name", "Synthetic practice direction"), ("definition", "Complete one small practice and reflect on what changed."),
                      ("unit", "practice"), ("target", "4"), ("period", "This month"), ("stage", "Building a rhythm")]
        for (key, value) in fields {
            let field = app.descendants(matching: .any).matching(identifier: "goal-" + key).firstMatch
            revealFormControl(field, in: app.collectionViews["northStarDefinitionForm"], below: app.navigationBars["北极星指标"])
            field.tap(); field.typeText(value)
        }
        let preview = app.buttons["previewNorthStar"]
        revealFormControl(preview, in: app.collectionViews["northStarDefinitionForm"], below: app.navigationBars["北极星指标"]); preview.tap()
        let confirm = app.buttons["confirmNorthStar"]; reveal(confirm); confirm.tap()
        expectation(for: NSPredicate { _, _ in !self.app.buttons["confirmNorthStar"].exists }, evaluatedWith: nil)
        waitForExpectations(timeout: 5)
        app.navigationBars.buttons.element(boundBy: 0).tap()
        let title = app.staticTexts["directionGoalTitle"]
        XCTAssertTrue(title.waitForExistence(timeout: 5)); XCTAssertEqual(title.label, fields[0].1)
        XCTAssertEqual(app.staticTexts["directionGoalDefinition"].label, fields[1].1)
        attachMentorScreenshot("Approved direction — stage, star and continuous definition")
        let mit = app.buttons["directionMIT"]; reveal(mit); mit.tap()
        let action = app.textFields["mit-action"]
        XCTAssertTrue(action.waitForExistence(timeout: 5))
        // Select the destination before opening the keyboard: iOS 26 can expose
        // visible context-menu items with zero AX frames beside its candidate window.
        let section = app.buttons["mitSection"]
        revealDirectionMITControl(section); section.tap()
        let morning = app.buttons["🌅 Morning"]
        XCTAssertTrue(morning.waitForExistence(timeout: 5)); tapVisibleGroupAction(morning)
        let chosen = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            !morning.exists && section.label.contains("🌅 Morning")
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [chosen], timeout: 5), .completed,
                       "The native section menu must close with the requested destination selected")
        XCTAssertTrue(section.label.contains("🌅 Morning"))
        action.tap(); action.typeText("One synthetic practice")
        app.textFields["mit-criterion"].tap(); app.textFields["mit-criterion"].typeText("One saved reflection")
        revealDirectionMITControl(app.buttons["previewMIT"]); app.buttons["previewMIT"].tap()
        let confirmMIT = app.buttons["confirmMIT"]
        XCTAssertTrue(confirmMIT.waitForExistence(timeout: 5)); confirmMIT.tap()
        XCTAssertTrue(app.staticTexts["currentMIT"].waitForExistence(timeout: 5))
        app.navigationBars.buttons.element(boundBy: 0).tap()
        let confirmed = app.staticTexts["directionMITAction"]; reveal(confirmed)
        XCTAssertEqual(confirmed.label, "One synthetic practice")
        reveal(mit); XCTAssertTrue(mit.isHittable)
        attachMentorScreenshot("Approved direction — confirmed MIT and original action")
        app.terminate()
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launch(); XCTAssertTrue(input.waitForExistence(timeout: 10))
        app.buttons["diaryDirectory"].tap(); reveal(direction); direction.tap()
        XCTAssertTrue(title.waitForExistence(timeout: 5)); reveal(title)
        attachMentorScreenshot("Approved direction — accessibility XXXL definition")
        reveal(mit); XCTAssertTrue(mit.isHittable)
        attachMentorScreenshot("Approved direction — accessibility XXXL MIT action")
    }

    private func revealDirectionMITControl(_ element: XCUIElement) {
        let form = app.collectionViews.containing(.textField, identifier: "mit-criterion").firstMatch
        revealFormControl(element, in: form, below: app.navigationBars["最重要的一件事"])
    }

    private func revealMemoryCorrectionControl(_ element: XCUIElement) {
        revealFormControl(element, in: app.collectionViews["mobileMemoryDetailForm"], below: app.navigationBars["记忆详情"])
    }

    private func revealBodyMergeControl(_ element: XCUIElement, towardEarlier: Bool = false, enabled: Bool = true) {
        revealFormControl(element, in: app.collectionViews["bodyConflictReviewForm"],
                          below: app.navigationBars["核对正文版本"],
                          scroll: (towardEarlier, towardEarlier ? 8 : 7), enabled: enabled)
    }

    private func revealFormControl(_ element: XCUIElement, in form: XCUIElement, below navigation: XCUIElement,
                                   scroll: (earlier: Bool, attempts: Int) = (false, 8), enabled: Bool = true) {
        XCTAssertTrue(form.waitForExistence(timeout: 5))
        for _ in 0..<scroll.attempts {
            let bounds = form.frame.intersection(app.frame)
            guard isFiniteFrame(bounds) else { XCTFail("The Form must have a finite visible viewport"); return }
            let top = max(bounds.minY, navigation.frame.maxY) + 8
            let keyboard = app.keyboards.firstMatch
            var keyboardTop = keyboard.exists ? keyboard.frame.minY : app.frame.maxY
            if keyboard.exists {
                // iOS 26 exposes the candidate row separately from Keyboard. Its
                // inputView contains both, so the keys' minY is not a safe boundary.
                let inputView = app.otherElements.matching(identifier: "inputView").firstMatch
                if inputView.exists, inputView.frame.intersects(keyboard.frame) {
                    keyboardTop = min(keyboardTop, inputView.frame.minY)
                }
                let assistant = app.otherElements.matching(identifier: "SystemInputAssistantView").firstMatch
                if assistant.exists, assistant.frame.maxY >= keyboard.frame.minY - 1 {
                    keyboardTop = min(keyboardTop, assistant.frame.minY)
                }
            }
            let bottom = min(bounds.maxY, keyboardTop) - 8
            let height = bottom - top
            XCTAssertGreaterThan(height, 60)
            let frame = element.exists ? element.frame : .null
            if isFiniteFrame(frame), bounds.contains(frame), frame.minY >= top, frame.maxY <= bottom,
               element.isEnabled == enabled, element.isHittable { return }
            // Start inside the Form gutter, above the entire keyboard and candidate bar.
            // An application-wide swipe can start on the keyboard on smaller phones.
            let towardEarlier = isFiniteFrame(frame) ? frame.minY < top : scroll.earlier
            let origin = form.coordinate(withNormalizedOffset: .zero)
            let start = origin.withOffset(CGVector(dx: 12, dy: top - form.frame.minY + height * (towardEarlier ? 0.25 : 0.75)))
            let end = origin.withOffset(CGVector(dx: 12, dy: top - form.frame.minY + height * (towardEarlier ? 0.75 : 0.25)))
            start.press(forDuration: 0.05, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.2)
        }
        XCTFail("The Form control must be fully visible above the keyboard before interaction")
    }

    func testDirectMITBodyEditRequiresExplicitPlanReconciliation() {
        app.buttons["diaryDirectory"].tap(); app.buttons["mitFlow"].tap()
        let action = app.textFields["mit-action"]
        XCTAssertTrue(action.waitForExistence(timeout: 5)); action.tap(); action.typeText("Original action")
        app.textFields["mit-criterion"].tap(); app.textFields["mit-criterion"].typeText("One original result")
        reveal(app.buttons["mitSection"]); app.buttons["mitSection"].tap(); app.buttons["🌅 Morning"].tap()
        reveal(app.buttons["previewMIT"]); app.buttons["previewMIT"].tap()
        XCTAssertTrue(app.buttons["confirmMIT"].waitForExistence(timeout: 5)); app.buttons["confirmMIT"].tap()
        XCTAssertTrue(app.staticTexts["currentMIT"].waitForExistence(timeout: 5))
        app.terminate(); app.launch(); XCTAssertTrue(input.waitForExistence(timeout: 10))
        let original = input.value as? String ?? ""
        guard let range = original.range(of: "Original action") else { XCTFail("Saved plan must be in the document"); return }
        input.tap(); input.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        let keyboard = app.keyboards.firstMatch
        XCTAssertTrue(keyboard.waitForExistence(timeout: 5))
        let end = app.buttons["文末"]
        expectation(for: NSPredicate { _, _ in end.isHittable && end.frame.maxY <= keyboard.frame.minY + 1 }, evaluatedWith: end)
        waitForExpectations(timeout: 5)
        XCTAssertEqual(input.value as? String, original)
        input.typeKey(XCUIKeyboardKey.upArrow, modifierFlags: .command)
        for _ in original[..<range.lowerBound] { input.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: []) }
        for _ in "Original action" { input.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: .shift) }
        input.typeText("Manual changed action")
        waitForStatus("已保存在手机")
        let manual = original.replacingOccurrences(of: "Original action", with: "Manual changed action")
        XCTAssertEqual(input.value as? String, manual, "Editing the rendered action must preserve all other document bytes")
        app.buttons["收起键盘"].tap()
        app.buttons["diaryDirectory"].tap(); app.buttons["goalsFlow"].tap()
        let mismatch = app.staticTexts["directionMITBodyMismatch"]; reveal(mismatch)
        XCTAssertTrue(mismatch.waitForExistence(timeout: 5))
        let reconcile = app.buttons["directionMIT"]; reveal(reconcile)
        attachMentorScreenshot("Approved direction — manual MIT mismatch remains explicit")
        XCTAssertEqual(reconcile.label, "核对修改并确认新计划"); reconcile.tap()
        XCTAssertTrue(app.staticTexts["mitBodyMismatch"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.staticTexts["currentMIT"].label, "Original action")
        XCTAssertFalse(app.switches["mitFeedbackMode"].isEnabled)
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Edited MIT body is distinguished from its recorded plan"; attachment.lifetime = .keepAlways; add(attachment)
        app.buttons["reconcileMITBody"].tap()
        let nextAction = app.textFields["mit-action"]; reveal(nextAction); nextAction.tap(); nextAction.typeText("Explicit replacement action")
        app.textFields["mit-criterion"].tap(); app.textFields["mit-criterion"].typeText("One replacement result")
        reveal(app.buttons["mitSection"]); app.buttons["mitSection"].tap(); app.buttons["🌅 Morning"].tap()
        reveal(app.buttons["previewMIT"]); app.buttons["previewMIT"].tap()
        XCTAssertTrue(app.buttons["confirmMIT"].waitForExistence(timeout: 5)); app.buttons["confirmMIT"].tap()
        expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: app.buttons["confirmMIT"])
        waitForExpectations(timeout: 5)
        let form = app.collectionViews.containing(.textField, identifier: "mit-criterion").firstMatch
        XCTAssertTrue(form.waitForExistence(timeout: 5))
        let current = app.staticTexts["currentMIT"]
        for _ in 0..<6 {
            if current.isHittable { break }
            let visibleBottom = keyboard.exists ? min(form.frame.maxY, keyboard.frame.minY) : form.frame.maxY
            let height = visibleBottom - form.frame.minY
            let origin = form.coordinate(withNormalizedOffset: .zero)
            origin.withOffset(CGVector(dx: form.frame.width / 2, dy: height * 0.25))
                .press(forDuration: 0.05, thenDragTo: origin.withOffset(CGVector(dx: form.frame.width / 2, dy: height * 0.85)))
        }
        XCTAssertTrue(app.staticTexts["currentMIT"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.staticTexts["currentMIT"].label, "Explicit replacement action")
        XCTAssertFalse(app.staticTexts["mitBodyMismatch"].exists)
        app.terminate(); app.launch(); XCTAssertTrue(input.waitForExistence(timeout: 10))
        let final = input.value as? String ?? ""
        XCTAssertTrue(final.contains("Manual changed action")); XCTAssertTrue(final.contains("Explicit replacement action"))
    }

    func testMarkdownLivePreviewPersistsAndExportsTheUnchangedSource() { assertMarkdownPreviewExport(fromSettings: false) }

    func testSettingsExportsTheExactCurrentMarkdownThroughSystemFiles() { assertMarkdownPreviewExport(fromSettings: true) }

    private func assertMarkdownPreviewExport(fromSettings: Bool) {
        let raw = "# Live preview\n### **Morning**\n- [ ] Important task\n- [x] Completed task\n- A list item\n> A thought\n**Bold** and *italic* and ~~old~~\n`code` and [Link](https://example.com)\n---\n-\n"
        let initial = input.value as? String ?? ""
        prepareSystemFilesLocalLocation()
        app.activate()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        XCTAssertEqual(input.value as? String, initial, "Preparing the system export destination must not change the diary")
        input.tap(); input.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        // Use the native Select All command; arrow navigation can stop after a
        // rendered heading marker. Verify the selection cleared every source byte.
        input.typeKey("a", modifierFlags: .command)
        XCTAssertEqual(input.value as? String, initial, "Selecting the fixture must not change its source")
        input.typeText(XCUIKeyboardKey.delete.rawValue)
        XCTAssertEqual(input.value as? String, "", "Select All must clear the complete original fixture before replacement")
        input.typeText(raw)
        waitForStatus("已保存在手机"); XCTAssertEqual(input.value as? String, raw)
        input.typeKey(XCUIKeyboardKey.upArrow, modifierFlags: .command)
        app.buttons["收起键盘"].tap()
        let preview = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        preview.name = "Formatted Markdown in the actual diary"; preview.lifetime = .keepAlways; add(preview)
        app.terminate(); app.launch(); XCTAssertTrue(input.waitForExistence(timeout: 10))
        XCTAssertEqual(input.value as? String, raw)
        // The fixed synthetic fixture puts its first task below the two single-line headings.
        // Tap its visible box, not an editor-only hook, on the dedicated portrait iPhone.
        input.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(dx: 28, dy: 94)).tap()
        waitForStatus("已保存在手机")
        let checked = raw.replacingOccurrences(of: "- [ ] Important", with: "- [x] Important")
        XCTAssertEqual(input.value as? String, checked)
        XCTAssertFalse(app.keyboards.firstMatch.exists, "A task tap must not open a separate editor")
        app.terminate(); app.launch(); XCTAssertTrue(input.waitForExistence(timeout: 10))
        XCTAssertEqual(input.value as? String, checked)
        var exportedBody = checked
        if fromSettings {
            openSettingsMarkdownExporter()
            exportedBody = cancelSettingsExportAndEdit(checked)
            openSettingsMarkdownExporter()
        } else { app.buttons["moreMenu"].tap(); app.buttons["exportMarkdown"].tap() }
        let exportName = "Riji-Markdown-Preview-" + container
        if fromSettings { saveSettingsMarkdownFile(named: exportName) }
        else { saveVoiceFile(named: exportName) }
        let receipt = fromSettings ? app.staticTexts["settingsExportStatus"] : status
        let digest = SHA256.hash(data: Data(exportedBody.utf8)).map { String(format: "%02x", $0) }.joined()
        // A disappearing remote Files view must not block the local success receipt.
        let exportCompleted = NSPredicate { _, _ in
            if fromSettings { return self.settingsExportReceiptMatches(digest) }
            guard receipt.exists else { return false }
            return receipt.label.contains("Markdown 已导出") && receipt.value as? String == digest
        }
        expectation(for: exportCompleted, evaluatedWith: nil)
        waitForExpectations(timeout: 10)
        if fromSettings {
            let closed = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
                !self.app.navigationBars["FullDocumentManagerViewControllerNavigationBar"].exists
            }, object: nil)
            XCTAssertEqual(XCTWaiter.wait(for: [closed], timeout: 5), .completed)
        }
        XCTAssertEqual(receipt.value as? String, digest, "Rendering must never become a lossy Markdown serialization")
    }

    private func settingsExportReceiptMatches(_ digest: String) -> Bool {
        guard let snapshot = try? app.snapshot() else { return false }
        var nodes = snapshot.children
        var receipts: [XCUIElementSnapshot] = []
        var exports: [XCUIElementSnapshot] = []
        while let node = nodes.popLast() {
            if node.elementType == .staticText, node.identifier == "settingsExportStatus" { receipts.append(node) }
            if node.elementType == .button, node.identifier == "settingsExportMarkdown" { exports.append(node) }
            nodes.append(contentsOf: node.children)
        }
        guard receipts.count == 1, exports.count == 1, let receipt = receipts.first,
              let export = exports.first else { return false }
        return receipt.label.contains("Markdown 已导出") && receipt.value as? String == digest && export.isEnabled
    }

    private func openSettingsMarkdownExporter() {
        let settings = app.tabBars.buttons["设置"]
        settings.tap()
        let selected = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in settings.isSelected }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [selected], timeout: 5), .completed)
        let destination = app.navigationBars["导出与恢复"]
        if !destination.exists {
            let row = app.buttons["settingsExportRecovery"]
            reveal(row); tapVisibleGroupAction(row)
        }
        XCTAssertTrue(destination.waitForExistence(timeout: 5))
        let export = app.buttons["settingsExportMarkdown"]
        XCTAssertTrue(export.waitForExistence(timeout: 5)); tapVisibleGroupAction(export)
    }

    private func cancelSettingsExportAndEdit(_ original: String) -> String {
        // The first presentation must be complete before cancellation; reopening
        // must not hide an empty or malformed first exporter.
        _ = settingsExportFilename()
        let picker = app.navigationBars["FullDocumentManagerViewControllerNavigationBar"]
        guard var state = stableFilesNavigation(picker) else { return original }
        if state.title == "我的iPhone" {
            tapFilesNavigationAction(state, in: picker)
            guard let root = stableFilesNavigation(picker, requiringBrowse: true) else { return original }
            state = root
        }
        XCTAssertEqual(state.title, "浏览")
        tapFilesNavigationAction(state, in: picker)
        let closed = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in !picker.exists }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [closed], timeout: 5), .completed)
        XCTAssertTrue(app.buttons["settingsExportMarkdown"].isEnabled)
        XCTAssertFalse(app.staticTexts["settingsExportStatus"].exists, "Cancellation must not claim an exported file")
        app.tabBars.buttons["日记"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 10)); XCTAssertEqual(input.value as? String, original)
        input.tap(); input.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        let end = app.buttons["文末"]
        XCTAssertTrue(end.waitForExistence(timeout: 5)); XCTAssertTrue(end.isHittable); end.tap()
        let addition = "Fresh export after cancellation " + container + "\n"
        input.typeText(addition)
        let expected = original + addition
        XCTAssertEqual(input.value as? String, expected); waitForStatus("已保存在手机")
        app.buttons["收起键盘"].tap()
        return expected
    }

    private struct FilesNavigationState: Equatable {
        let title: String
        let identifier: String
        let label: String
        let actionFrame: CGRect
        let navigationFrame: CGRect
    }

    private func stableFilesNavigation(
        _ navigation: XCUIElement, requiringBrowse: Bool = false
    ) -> FilesNavigationState? {
        let bounds = app.frame
        var previous: FilesNavigationState?
        var captured: FilesNavigationState?
        // Files restores its previous directory asynchronously. An initial
        // Browse/Cancel snapshot must not decide the route during that transition.
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            guard let snapshot = try? navigation.snapshot(),
                  let state = self.filesNavigationState(snapshot, within: bounds),
                  !requiringBrowse || state.title == "浏览" else {
                previous = nil; return false
            }
            let stable = previous == state
            previous = state
            if stable { captured = state }
            return stable
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 10), .completed,
                       "Files must expose the same complete navigation state in two consecutive snapshots")
        return captured
    }

    private func filesNavigationState(
        _ snapshot: XCUIElementSnapshot, within bounds: CGRect
    ) -> FilesNavigationState? {
        var nodes = snapshot.children
        var titles: [String] = []
        var back: [XCUIElementSnapshot] = []
        var cancel: [XCUIElementSnapshot] = []
        while let node = nodes.popLast() {
            if node.elementType == .staticText, ["浏览", "我的iPhone"].contains(node.label) { titles.append(node.label) }
            if node.elementType == .button {
                if node.identifier == "BackButton" { back.append(node) }
                if ["取消", "Cancel"].contains(node.label) { cancel.append(node) }
            }
            nodes.append(contentsOf: node.children)
        }
        guard titles.count == 1, let title = titles.first else { return nil }
        let actions = title == "浏览" ? cancel : back
        let other = title == "浏览" ? back : cancel
        guard actions.count == 1, other.isEmpty, let action = actions.first,
              action.isEnabled, isFiniteFrame(snapshot.frame), isFiniteFrame(action.frame),
              bounds.contains(action.frame), snapshot.frame.contains(action.frame) else { return nil }
        return FilesNavigationState(title: title, identifier: action.identifier, label: action.label,
                                    actionFrame: action.frame, navigationFrame: snapshot.frame)
    }

    private func tapFilesNavigationAction(_ state: FilesNavigationState, in navigation: XCUIElement) {
        // Keep the action inside the remote system navigation bar. Never retry
        // a tap or substitute a same-label element from the app-owned tree.
        let matches = navigation.buttons.matching(NSPredicate(
            format: "identifier == %@ AND label == %@", state.identifier, state.label))
        guard matches.count == 1 else { XCTFail("Files must retain exactly one captured navigation action"); return }
        let action = matches.element
        XCTAssertTrue(action.isEnabled); XCTAssertTrue(action.isHittable)
        action.tap()
    }

    private func prepareSystemFilesLocalLocation() {
        // A fresh Simulator may open the export picker with no destinations.
        // Establish the real local location in Files through its own UI first.
        let files = XCUIApplication(bundleIdentifier: "com.apple.DocumentsApp")
        files.launchArguments = ["-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        files.launch()
        XCTAssertTrue(files.wait(for: .runningForeground, timeout: 10))
        let browse = files.tabBars.buttons["浏览"]
        if browse.waitForExistence(timeout: 5) { browse.tap() }
        let localLocation = files.cells.matching(NSPredicate(format: "label == %@", "我的iPhone")).firstMatch
        let localTitle = files.navigationBars.staticTexts["我的iPhone"]
        let available = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            localTitle.exists || localLocation.exists
        }, object: files)
        guard XCTWaiter.wait(for: [available], timeout: 10) == .completed else {
            attachSystemFilesFailure(files, "System Files must expose its real local destination before the export test")
            return
        }
        if !localTitle.exists {
            XCTAssertTrue(localLocation.isHittable)
            localLocation.tap()
        }
        guard localTitle.waitForExistence(timeout: 5) else {
            attachSystemFilesFailure(files, "System Files must enter My iPhone before returning to the diary")
            return
        }
    }

    private func attachSystemFilesFailure(_ files: XCUIApplication, _ reason: String) {
        let screenshot = XCTAttachment(screenshot: files.screenshot())
        screenshot.name = "System Files local destination unavailable"; screenshot.lifetime = .keepAlways; add(screenshot)
        let hierarchy = XCTAttachment(string: files.debugDescription)
        hierarchy.name = "System Files local destination hierarchy"; hierarchy.lifetime = .keepAlways; add(hierarchy)
        XCTFail(reason)
    }

    private func saveSettingsMarkdownFile(named name: String) {
        // The system save action has no DOCPicker.actionButton identifier on the CI runtime.
        let picker = app.navigationBars["FullDocumentManagerViewControllerNavigationBar"]
        let save = picker.buttons["保存"]
        XCTAssertTrue(save.waitForExistence(timeout: 10))
        let filename = settingsExportFilename()
        filename.tap()
        let previous = filename.value as? String ?? ""
        filename.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: previous.count))
        filename.typeText(name)
        XCTAssertEqual(filename.value as? String, name)
        XCTAssertTrue(save.isEnabled); XCTAssertTrue(save.isHittable); save.tap()
    }

    private func settingsExportFilename() -> XCUIElement {
        let filename = app.textFields["DOCPicker.filenameTextField"]
        guard filename.waitForExistence(timeout: 20) else {
            attachMentorScreenshot("Settings Markdown export — missing system filename field")
            let hierarchy = XCTAttachment(string: app.debugDescription)
            hierarchy.name = "Settings Markdown export — system picker hierarchy"
            hierarchy.lifetime = .keepAlways; add(hierarchy)
            XCTFail("The system exporter must expose its filename field before saving a unique file"); return filename
        }
        return filename
    }

    func testLocalVaultTreeOpensOriginalFilesReadOnlyAndRestoresItsBookmark() {
        app.terminate(); app.launchEnvironment["RIJI_TEST_VAULT_BROWSER"] = "1"; app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        let today = input.value as? String
        app.buttons["diaryDirectory"].tap()
        let root = app.buttons["vaultRoot"]
        XCTAssertTrue(root.waitForExistence(timeout: 10)); XCTAssertTrue(root.label.contains("riji"))
        for name in ["assets", "daily", "templates", "weekly"] { XCTAssertTrue(app.buttons["vault-folder-" + name].exists) }
        let search = app.buttons["searchFlow"]
        XCTAssertTrue(search.label.contains("App 本地日记")); search.tap()
        XCTAssertTrue(app.staticTexts["searchActiveScope"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["searchActiveScope"].label.contains("App 本地日记"))
        app.navigationBars.buttons.element(boundBy: 0).tap()
        let daily = app.buttons["vault-folder-daily"]
        daily.tap()
        let note = app.buttons["vault-file-daily/Synthetic.md"]
        XCTAssertTrue(note.waitForExistence(timeout: 10))
        let tree = XCTAttachment(screenshot: app.screenshot()); tree.name = "Local vault folder tree"; tree.lifetime = .keepAlways; add(tree)
        note.tap()
        let reader = app.textViews["vaultFileReader"]
        XCTAssertTrue(reader.waitForExistence(timeout: 10))
        let expected = "# Synthetic local note\r\n**Original body** e\u{301} 🧠\r\n"
        XCTAssertEqual(reader.value as? String, expected)
        reader.tap(); XCTAssertFalse(app.keyboards.firstMatch.exists)
        let preview = XCTAttachment(screenshot: app.screenshot()); preview.name = "Read original local Markdown"; preview.lifetime = .keepAlways; add(preview)
        app.buttons["closeVaultPreview"].tap()
        XCTAssertTrue(note.waitForExistence(timeout: 5)); note.tap()
        XCTAssertTrue(reader.waitForExistence(timeout: 10)); XCTAssertEqual(reader.value as? String, expected)
        app.buttons["closeVaultPreview"].tap()
        app.buttons["vault-folder-daily/nested"].tap()
        let nested = app.buttons["vault-file-daily/nested/Readme.txt"]
        XCTAssertTrue(nested.waitForExistence(timeout: 10)); nested.tap()
        XCTAssertTrue(reader.waitForExistence(timeout: 10)); XCTAssertEqual(reader.value as? String, "Nested text\n")
        app.buttons["closeVaultPreview"].tap()
        app.buttons["openVaultFolder"].tap()
        let cancel = app.buttons["取消"]
        XCTAssertTrue(cancel.waitForExistence(timeout: 10)); cancel.tap()
        XCTAssertTrue(root.waitForExistence(timeout: 5)); XCTAssertFalse(app.staticTexts["vaultError"].exists)
        root.tap(); XCTAssertFalse(daily.exists); root.tap(); XCTAssertTrue(daily.exists)
        app.buttons["完成"].tap(); XCTAssertTrue(input.waitForExistence(timeout: 10)); XCTAssertEqual(input.value as? String, today)
        app.terminate(); app.launchEnvironment.removeValue(forKey: "RIJI_TEST_VAULT_BROWSER"); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10)); app.buttons["diaryDirectory"].tap()
        XCTAssertTrue(root.waitForExistence(timeout: 10)); daily.tap()
        XCTAssertTrue(note.waitForExistence(timeout: 10)); note.tap()
        XCTAssertTrue(reader.waitForExistence(timeout: 10)); XCTAssertEqual(reader.value as? String, expected)
        app.buttons["closeVaultPreview"].tap()
        app.buttons["appDiaryTools"].tap()
        XCTAssertTrue(app.buttons["templatesFlow"].waitForExistence(timeout: 5))
    }

    func testFirstLaunchShowsTodayWithoutSetup() {
        XCTAssertTrue(app.staticTexts["templateLabel"].exists)
        XCTAssertEqual(app.staticTexts["templateLabel"].label, "产品初始模板")
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-12"))
        XCTAssertTrue(input.isEnabled)
        XCTAssertFalse(app.buttons["saveEntry"].exists)
        XCTAssertFalse(app.buttons["登录"].exists)
    }

    func testBodySavedOnOriginalDateReopensFromDirectoryAfterMidnight() {
        type("Synthetic evening body")
        let saved = input.value as? String
        app.terminate()
        app.launchEnvironment["RIJI_TEST_DATE"] = "2026-09-13T08:00:00Z"
        app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-13"))
        app.buttons["diaryDirectory"].tap()
        let oldDay = app.buttons["directory-2026-09-12"]
        XCTAssertTrue(oldDay.waitForExistence(timeout: 5)); oldDay.tap()
        XCTAssertEqual(input.value as? String, saved)
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-12"))
        XCTAssertTrue(app.buttons["returnToday"].exists)
        app.buttons["returnToday"].tap()
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-13"))
    }

    func testSingleSaveStaysOnDayAndSurvivesRelaunch() {
        type("Synthetic confirmed text")
        waitForStatus("已保存在手机")
        XCTAssertTrue(app.buttons["selectedDate"].label.contains("2026-09-12"))
        XCTAssertFalse(app.buttons["saveEntry"].exists)
        app.terminate()
        app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        waitForStatus("已保存在手机")
        app.swipeUp()
        XCTAssertTrue((input.value as? String)?.contains("Synthetic confirmed text") == true)
        XCTAssertFalse(app.buttons["saveEntry"].exists)
    }

    func testCaretReplacementSurvivesAutosaveAndRelaunch() {
        type("Synthetic original middle")
        input.tap(); app.buttons["文末"].tap()
        input.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: "middle\n".count))
        input.typeText("replacement\n")
        waitForStatus("已保存在手机")
        let expected = input.value as? String
        XCTAssertTrue(expected?.contains("Synthetic original replacement") == true)
        XCTAssertFalse(expected?.contains("middle") == true)
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Edit existing document at the caret"; attachment.lifetime = .keepAlways; add(attachment)
        app.buttons["收起键盘"].tap()
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        XCTAssertEqual(input.value as? String, expected)
    }

    func testIndependentRepeatedTextAndExportSheet() {
        for _ in 0..<2 {
            type("Repeated deliberate record")
                waitForStatus("已保存在手机")
        }
        app.swipeUp()
        XCTAssertEqual((input.value as? String)?.components(separatedBy: "Repeated deliberate record").count, 3)
        type("Latest body edit is included in export")
        let expected = input.value as? String ?? ""
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
        let digest = SHA256.hash(data: Data(expected.utf8)).map { String(format: "%02x", $0) }.joined()
        XCTAssertEqual(status.value as? String, digest, "Read back the actual system destination bytes")
        XCTAssertEqual(input.value as? String, expected)
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

extension DiaryFlowTests {
    func testHistoricalProposalBeyondHydrationShowsItsOwnNoticesBeforeAdoption() {
        app.terminate()
        app.launchEnvironment["RIJI_TEST_VOICE_HISTORY"] = "1"
        app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        app.buttons["moreMenu"].tap(); app.buttons["voiceLibrary"].tap()
        app.buttons["prepareVoiceRecord"].tap()
        let prepare = app.buttons["prepareSyntheticVoiceHistory"]; reveal(prepare); prepare.tap()
        let history = app.buttons["所有转写版本与逐段原稿"]
        for _ in 0..<10 { if history.isHittable { break }; app.swipeDown() }
        XCTAssertTrue(history.waitForExistence(timeout: 10)); revealAboveToolbar(history); history.tap()
        let firstPage = app.buttons["转写历史第一页"]; reveal(firstPage); firstPage.tap()
        let old = app.buttons["读取原稿 F10500"]; reveal(old); old.tap()
        let notice = app.staticTexts["voiceProposalNotice"].firstMatch
        reveal(notice)
        XCTAssertTrue(notice.label.contains("第 2 段已选择略过"))
        XCTAssertTrue(notice.label.contains("第 3 段尚不可用"))
        XCTAssertTrue(notice.label.contains("拒收 7 帧"))
        XCTAssertTrue(notice.label.contains("采集证据不可用"))
        let original = app.staticTexts["voiceProposalText"].firstMatch
        reveal(original)
        XCTAssertEqual(original.label, "Historical synthetic original e\u{301}\n仅为测试原稿")
        let adoption = app.buttons["采用这份原稿"].firstMatch
        reveal(adoption); XCTAssertTrue(adoption.isEnabled)
        let attachment = XCTAttachment(screenshot: app.screenshot()); attachment.name = "Historical source notices before adoption"; attachment.lifetime = .keepAlways; add(attachment)
        adoption.tap()
        let origin = app.staticTexts["voiceCorrectionOrigin"]
        for _ in 0..<12 { if origin.isHittable { break }; app.swipeDown() }
        XCTAssertTrue(origin.waitForExistence(timeout: 10)); XCTAssertTrue(origin.label.contains("历史原稿说明"))
        XCTAssertTrue(origin.label.contains("第 2 段已选择略过"))
        let correction = app.textViews["voiceCorrection"]
        XCTAssertEqual(correction.value as? String, "Historical synthetic original e\u{301}\n仅为测试原稿")
    }
}


extension DiaryFlowTests {
    func testRenderedTableCellTapUsesNativeEditorAndPreservesSource() {
        app.terminate(); app.launchEnvironment["RIJI_TEST_NAVIGATION"] = "1"; app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        let table = "\n| A | B | C | D | E | F |\n| --- | --- | --- | --- | --- | --- |\n" +
            (0..<12).map { "| row \($0) | value | | 中文 | fifth | sixth |\n" }.joined() +
            "| Tap target | value | | 中文 | fifth | sixth |\n"
        type(table)
        let source = input.value as? String ?? ""
        let target = app.staticTexts["Tap target"]
        for _ in 0..<6 { if target.isHittable { break }; input.swipeUp() }
        XCTAssertTrue(target.isHittable)
        target.tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, source, "Tapping a cell keeps the grid and exact source")
        let editor = app.textViews["markdownTableCellEditor"]
        XCTAssertTrue(editor.waitForExistence(timeout: 5))
        XCTAssertTrue(app.descendants(matching: .any).matching(identifier: "markdownTable").firstMatch.exists)
        let expected = NSMaxRange((source as NSString).range(of: "Tap target"))
        editor.typeText("Edited")
        waitForStatus("已保存在手机")
        XCTAssertEqual(input.value as? String, (source as NSString).replacingCharacters(in: NSRange(location: expected, length: 0), with: "Edited"))
        app.buttons["收起键盘"].tap()
    }

    func testOriginalTableHorizontalAndVerticalGesturesStayInSameDiary() {
        app.terminate()
        app.launchEnvironment["RIJI_TEST_ORIGINAL_VAULT"] = "1"
        app.launchEnvironment["RIJI_TEST_VAULT_BROWSER"] = "1"
        app.launchEnvironment["RIJI_TEST_NAVIGATION"] = "1"
        app.launch()
        waitForOriginalEditor(containing: "After synthetic table")
        let source = input.value as? String
        let lastColumn = app.staticTexts["Sixth header"]
        XCTAssertFalse(lastColumn.isHittable)
        let start = input.coordinate(withNormalizedOffset: CGVector(dx: 0.8, dy: 0.35))
        let end = input.coordinate(withNormalizedOffset: CGVector(dx: 0.15, dy: 0.35))
        start.press(forDuration: 0.05, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.2)
        XCTAssertTrue(lastColumn.isHittable, "The wide table must scroll horizontally")
        XCTAssertEqual(input.value as? String, source)
        end.press(forDuration: 0.05, thenDragTo: start, withVelocity: .slow, thenHoldForDuration: 0.2)
        XCTAssertFalse(lastColumn.isHittable, "The table must scroll back without changing dates")
        XCTAssertEqual(input.value as? String, source)
        let viewport = input.label
        input.swipeUp()
        XCTAssertNotEqual(input.label, viewport, "Vertical drag on a table must scroll the diary")
        XCTAssertEqual(input.value as? String, source, "Table swipe must not navigate to another date")
        XCTAssertTrue(status.label.contains("已保存在此设备"))
        XCTAssertFalse(app.keyboards.firstMatch.exists)
    }

    func testOriginalHomeRestoresBookmarkAndOpensNestedHistoryWithTable() {
        app.terminate()
        app.launchEnvironment["RIJI_TEST_ORIGINAL_VAULT"] = "1"
        app.launchEnvironment["RIJI_TEST_VAULT_BROWSER"] = "1"
        app.launch()
        XCTAssertTrue(status.waitForExistence(timeout: 10))
        let loaded = NSPredicate(format: "value CONTAINS %@", "Original home synthetic")
        expectation(for: loaded, evaluatedWith: input); waitForExpectations(timeout: 10)
        XCTAssertTrue(app.descendants(matching: .any).matching(identifier: "markdownTable").firstMatch.exists)
        XCTAssertTrue(status.label.contains("已保存在此设备"))
        app.buttons["diaryDirectory"].tap()
        app.buttons["vault-folder-daily"].tap()
        app.buttons["vault-folder-daily/nested"].tap()
        let nested = app.buttons["vault-file-daily/nested/2026-09-11.md"]
        XCTAssertTrue(nested.waitForExistence(timeout: 5)); nested.tap()
        expectation(for: NSPredicate(format: "value CONTAINS %@", "Nested original synthetic"), evaluatedWith: input)
        waitForExpectations(timeout: 10)
        app.terminate(); app.launch()
        expectation(for: loaded, evaluatedWith: input); waitForExpectations(timeout: 10)
        XCTAssertTrue(status.label.contains("已保存在此设备"))
    }

    func testCloudConflictColdOpenDirectoryChoiceAndBackupRemainReachable() {
        app.terminate()
        app.launchEnvironment["RIJI_TEST_ORIGINAL_VAULT"] = "1"
        app.launchEnvironment["RIJI_TEST_VAULT_BROWSER"] = "1"
        app.launchEnvironment["RIJI_TEST_CLOUD_CONFLICT"] = "1"
        app.launch()
        waitForOriginalEditor(containing: "Original home synthetic")
        XCTAssertTrue(app.buttons["reviewCloudVersions"].waitForExistence(timeout: 5))
        app.buttons["diaryDirectory"].tap()
        app.buttons["vault-folder-daily"].tap()
        app.buttons["vault-folder-daily/nested"].tap()
        app.buttons["vault-file-daily/nested/2026-09-11.md"].tap()
        expectation(for: NSPredicate(format: "value CONTAINS %@", "Nested original synthetic"), evaluatedWith: input)
        waitForExpectations(timeout: 10)
        app.buttons["reviewCloudVersions"].tap()
        XCTAssertTrue(app.navigationBars["查看并处理版本"].waitForExistence(timeout: 5))
        let originals = app.buttons["完整原文与导出"]; reveal(originals); originals.tap()
        app.buttons["冲突副本 1"].tap()
        XCTAssertTrue(app.staticTexts["cloudRaw-version-0"].label.contains("version: alternate"))
        XCTAssertTrue(app.buttons["exportCloud-version-0"].exists)
        let select = app.buttons["chooseCloud-version-0"]; reveal(select); select.tap()
        let fullResult = app.buttons["查看完整结果（原文）"]; reveal(fullResult); fullResult.tap()
        XCTAssertTrue(app.staticTexts["cloudChosenFullSource"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["cloudChosenFullSource"].label.contains("version: alternate"))
        app.buttons["confirmCloudResolution"].tap()
        let chosen = "---\r\nversion: alternate\r\n---\r\n# Synthetic cloud alternate\r\nFull source 🧠 e\u{301}\r\n"
        expectation(for: NSPredicate(format: "value == %@", chosen), evaluatedWith: input)
        waitForExpectations(timeout: 10)
        XCTAssertFalse(app.buttons["reviewCloudVersions"].exists)
        XCTAssertFalse(app.staticTexts["storageError"].exists)
        app.buttons["moreMenu"].tap(); app.buttons["cloudVersionBackups"].tap()
        XCTAssertTrue(app.navigationBars["查看并处理版本"].waitForExistence(timeout: 5))
        reveal(app.staticTexts["本机版本备份"])
        app.buttons["closeCloudReview"].tap()
        app.terminate(); app.launch()
        app.buttons["diaryDirectory"].tap()
        app.buttons["vault-folder-daily"].tap()
        app.buttons["vault-folder-daily/nested"].tap()
        app.buttons["vault-file-daily/nested/2026-09-11.md"].tap()
        expectation(for: NSPredicate(format: "value == %@", chosen), evaluatedWith: input)
        waitForExpectations(timeout: 10)
        XCTAssertFalse(app.buttons["reviewCloudVersions"].exists)
    }

    func testCloudManualMergeClosesAndColdReopensWithoutChangingCurrentFile() {
        app.terminate()
        app.launchEnvironment["RIJI_TEST_ORIGINAL_VAULT"] = "1"
        app.launchEnvironment["RIJI_TEST_VAULT_BROWSER"] = "1"
        app.launchEnvironment["RIJI_TEST_CLOUD_CONFLICT"] = "1"
        app.launch()
        XCTAssertTrue(app.buttons["reviewCloudVersions"].waitForExistence(timeout: 10))
        let before = input.value as? String
        app.buttons["reviewCloudVersions"].tap()
        let fullEditor = app.buttons["手动编辑整篇"]; reveal(fullEditor); fullEditor.tap()
        let merge = app.textViews["cloudMergeEditor"]
        XCTAssertTrue(merge.waitForExistence(timeout: 5)); reveal(merge); merge.tap()
        let initialMerge = merge.value as? String ?? ""
        XCTAssertEqual(initialMerge, before)
        let insertion = "Unique manual cloud merge 94827"
        merge.typeText(insertion)
        let expectedMerge = merge.value as? String ?? ""
        XCTAssertEqual(expectedMerge.components(separatedBy: insertion).count, 2)
        XCTAssertEqual(expectedMerge.replacingOccurrences(of: insertion, with: ""), initialMerge,
                       "Typing a merge insertion must preserve every pre-existing byte")
        app.buttons["closeCloudReview"].tap()
        expectation(for: NSPredicate(format: "value == %@", before ?? ""), evaluatedWith: input)
        waitForExpectations(timeout: 10)
        app.terminate(); app.launch()
        XCTAssertTrue(app.buttons["reviewCloudVersions"].waitForExistence(timeout: 10))
        XCTAssertEqual(input.value as? String, before)
        app.buttons["reviewCloudVersions"].tap()
        reveal(fullEditor); fullEditor.tap()
        XCTAssertTrue(merge.waitForExistence(timeout: 5))
        XCTAssertEqual(merge.value as? String, expectedMerge)
        let review = app.buttons["reviewCloudMerge"]; reveal(review); review.tap()
        let fullResult = app.buttons["查看完整结果（原文）"]; reveal(fullResult); fullResult.tap()
        let confirmation = app.staticTexts["cloudChosenFullSource"]
        XCTAssertTrue(confirmation.waitForExistence(timeout: 5)); XCTAssertEqual(confirmation.label, expectedMerge)
        app.buttons["confirmCloudResolution"].tap()
        expectation(for: NSPredicate(format: "value == %@", expectedMerge), evaluatedWith: input)
        waitForExpectations(timeout: 10)
        app.terminate(); app.launch()
        expectation(for: NSPredicate(format: "value == %@", expectedMerge), evaluatedWith: input)
        waitForExpectations(timeout: 10)
        XCTAssertFalse(app.buttons["reviewCloudVersions"].exists)
    }

    func testWholeCurrentDiaryChoiceNeedsNoPerFragmentDecisionsAndResumesEditing() {
        app.terminate()
        for key in ["RIJI_TEST_ORIGINAL_VAULT", "RIJI_TEST_VAULT_BROWSER", "RIJI_TEST_CLOUD_CONFLICT", "RIJI_TEST_CLOUD_DIFF"] { app.launchEnvironment[key] = "1" }
        app.launch()
        XCTAssertTrue(app.buttons["reviewCloudVersions"].waitForExistence(timeout: 10))
        let before = input.value as? String ?? ""
        app.buttons["reviewCloudVersions"].tap()
        XCTAssertTrue(app.buttons["keepEntireCurrentDiary"].waitForExistence(timeout: 10))
        app.buttons["keepEntireCurrentDiary"].tap()
        XCTAssertTrue(app.staticTexts["cloudResultDiffSummary"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["cloudResultDiffSummary"].label.contains("内容一致"))
        app.buttons["confirmCloudResolution"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        XCTAssertEqual(input.value as? String, before)
        XCTAssertFalse(app.buttons["reviewCloudVersions"].exists)
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10)); XCTAssertEqual(input.value as? String, before)
        XCTAssertFalse(app.buttons["reviewCloudVersions"].exists)
    }

    func testCloudDifferencesShowOnlyChangesAndPerHunkChoicesPersistThenConfirm() {
        app.terminate()
        for key in ["RIJI_TEST_ORIGINAL_VAULT", "RIJI_TEST_VAULT_BROWSER", "RIJI_TEST_CLOUD_CONFLICT", "RIJI_TEST_CLOUD_DIFF"] { app.launchEnvironment[key] = "1" }
        app.launch()
        XCTAssertTrue(app.buttons["reviewCloudVersions"].waitForExistence(timeout: 10))
        let before = input.value as? String ?? ""
        app.buttons["reviewCloudVersions"].tap()
        let summary = app.staticTexts["cloudDiffSummary"]
        XCTAssertTrue(summary.waitForExistence(timeout: 10)); XCTAssertTrue(summary.label.contains("3 处差异"))
        XCTAssertFalse(app.staticTexts["cloudRaw-current"].exists)
        XCTAssertFalse(app.textViews["cloudMergeEditor"].exists)
        let initial = XCTAttachment(screenshot: app.screenshot()); initial.name = "Cloud differences by diary section"; initial.lifetime = .keepAlways; add(initial)
        app.buttons["keepDiff-0"].tap()
        let row = app.buttons["useDiff-1"]; reveal(row); row.tap()
        XCTAssertTrue(row.label.contains("已采用"))
        let note = app.buttons["useDiff-2"]; reveal(note); note.tap()
        let chosen = before.replacingOccurrences(of: "Bench | 20", with: "Bench | 25").replacingOccurrences(of: "Original thought.", with: "Another thought.")
        app.buttons["closeCloudReview"].tap()
        XCTAssertEqual(input.value as? String, before)
        app.terminate(); app.launch()
        XCTAssertTrue(app.buttons["reviewCloudVersions"].waitForExistence(timeout: 10)); app.buttons["reviewCloudVersions"].tap()
        XCTAssertTrue(summary.waitForExistence(timeout: 10)); XCTAssertTrue(summary.label.contains("1 处差异"))
        app.buttons["reviewCloudMerge"].tap()
        XCTAssertTrue(app.navigationBars["核对保存结果"].waitForExistence(timeout: 5))
        let finalSummary = app.staticTexts["cloudResultDiffSummary"]
        XCTAssertTrue(finalSummary.waitForExistence(timeout: 10)); XCTAssertTrue(finalSummary.label.contains("2 处差异"))
        let confirmation = XCTAttachment(screenshot: app.screenshot()); confirmation.name = "Only selected changes before saving"; confirmation.lifetime = .keepAlways; add(confirmation)
        app.buttons["confirmCloudResolution"].tap()
        expectation(for: NSPredicate(format: "value == %@", chosen), evaluatedWith: input); waitForExpectations(timeout: 10)
        app.terminate(); app.launch()
        expectation(for: NSPredicate(format: "value == %@", chosen), evaluatedWith: input); waitForExpectations(timeout: 10)
        XCTAssertFalse(app.buttons["reviewCloudVersions"].exists)
    }

    func testCloudDifferenceFragmentEditingKeepsOtherParagraphsAndColdReopensExactResult() {
        app.terminate()
        for key in ["RIJI_TEST_ORIGINAL_VAULT", "RIJI_TEST_VAULT_BROWSER", "RIJI_TEST_CLOUD_CONFLICT", "RIJI_TEST_CLOUD_DIFF"] { app.launchEnvironment[key] = "1" }
        app.launch()
        XCTAssertTrue(app.buttons["reviewCloudVersions"].waitForExistence(timeout: 10))
        let before = input.value as? String ?? ""
        app.buttons["reviewCloudVersions"].tap()
        XCTAssertTrue(app.staticTexts["cloudDiffSummary"].waitForExistence(timeout: 10))
        let edit = app.buttons["只编辑这一处"].firstMatch; reveal(edit); edit.tap()
        let fragment = app.textViews["editDiff-0"]; reveal(fragment); fragment.tap()
        let initial = fragment.value as? String ?? ""
        XCTAssertEqual(initial, "- [ ] Plan today\r\n")
        let insertion = " Manual fragment 73915"
        fragment.typeText(insertion)
        let changed = fragment.value as? String ?? ""
        XCTAssertEqual(changed.components(separatedBy: insertion).count, 2)
        XCTAssertEqual(changed.replacingOccurrences(of: insertion, with: ""), initial)
        let result = before.replacingOccurrences(of: initial, with: changed)
        app.buttons["closeCloudReview"].tap()
        XCTAssertEqual(input.value as? String, before)
        app.terminate(); app.launch()
        XCTAssertTrue(app.buttons["reviewCloudVersions"].waitForExistence(timeout: 10)); app.buttons["reviewCloudVersions"].tap()
        XCTAssertTrue(app.staticTexts["cloudDiffSummary"].waitForExistence(timeout: 10))
        app.buttons["reviewCloudMerge"].tap()
        let summary = app.staticTexts["cloudResultDiffSummary"]
        XCTAssertTrue(summary.waitForExistence(timeout: 10)); XCTAssertTrue(summary.label.contains("1 处差异"))
        app.buttons["confirmCloudResolution"].tap()
        expectation(for: NSPredicate(format: "value == %@", result), evaluatedWith: input); waitForExpectations(timeout: 10)
        app.terminate(); app.launch()
        expectation(for: NSPredicate(format: "value == %@", result), evaluatedWith: input); waitForExpectations(timeout: 10)
        XCTAssertFalse(app.buttons["reviewCloudVersions"].exists)
    }

    private func tapStableDirectoryFile(_ identifier: String) {
        var previous: GroupMenuTarget?
        var captured: GroupMenuTarget?
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            guard let visible = self.uniqueVisibleButton(matching: { $0.identifier == identifier }) else {
                previous = nil; return false
            }
            let stable = previous == visible
            previous = visible
            if stable { captured = visible }
            return stable
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 5), .completed,
                       "The exact directory file must be unique, enabled and fully visible in two stable snapshots")
        guard let target = captured else { XCTFail("The directory file must have a stable target before tapping"); return }
        let rows = app.buttons.matching(NSPredicate(format: "identifier == %@ AND label == %@", target.identifier, target.label))
        XCTAssertEqual(rows.count, 1, "The stable directory file must still resolve uniquely")
        XCTAssertTrue(rows.element.isEnabled)
        XCTAssertTrue(rows.element.isHittable, "The stable file must have a native hit target before its single tap")
        rows.element.tap()
    }

    func testOriginalHomeManualEditingSavesAndColdReopensExactSource() {
        app.terminate()
        app.launchEnvironment["RIJI_TEST_ORIGINAL_VAULT"] = "1"
        app.launchEnvironment["RIJI_TEST_VAULT_BROWSER"] = "1"
        app.launch()
        waitForOriginalEditor(containing: "Original home synthetic")
        let before = input.value as? String ?? ""
        XCTAssertTrue(before.contains("| 中文 🧠 0 |")); XCTAssertTrue(before.contains("\r\n"))
        XCTAssertFalse(app.buttons["openVoice"].exists)
        type("Original manual edit", savedStatus: "已保存在此设备")
        let edited = before + "Original manual edit\n"
        XCTAssertEqual(input.value as? String, edited)
        XCTAssertFalse(app.staticTexts["saveStatus"].exists)
        XCTAssertFalse(app.staticTexts["iCloud 由系统同步，其他设备是否收到仍需核对"].exists)
        let cleanDiary = XCTAttachment(screenshot: app.screenshot())
        cleanDiary.name = "Original diary without a persistent save footer"
        cleanDiary.lifetime = .keepAlways; add(cleanDiary)
        for label in ["导师", "记忆", "设置", "日记"] { app.tabBars.buttons[label].tap() }
        XCTAssertEqual(input.value as? String, edited)
        app.terminate(); app.launch()
        expectation(for: NSPredicate(format: "value == %@", edited), evaluatedWith: input)
        waitForExpectations(timeout: 10)
        waitForStatus("已保存在此设备")
        app.buttons["diaryDirectory"].tap()
        app.buttons["vault-folder-daily"].tap()
        app.buttons["vault-folder-daily/nested"].tap()
        let nestedIdentifier = "vault-file-daily/nested/2026-09-11.md"
        tapStableDirectoryFile(nestedIdentifier)
        expectation(for: NSPredicate(format: "value CONTAINS %@", "Nested original synthetic"), evaluatedWith: input)
        waitForExpectations(timeout: 10)
        let nestedBefore = input.value as? String ?? ""
        type("Nested manual edit", savedStatus: "已保存在此设备")
        app.buttons["returnToday"].tap()
        expectation(for: NSPredicate(format: "value == %@", edited), evaluatedWith: input)
        waitForExpectations(timeout: 10)
        app.terminate(); app.launch()
        app.buttons["diaryDirectory"].tap()
        app.buttons["vault-folder-daily"].tap()
        app.buttons["vault-folder-daily/nested"].tap()
        tapStableDirectoryFile(nestedIdentifier)
        expectation(for: NSPredicate(format: "value == %@", nestedBefore + "Nested manual edit\n"), evaluatedWith: input)
        waitForExpectations(timeout: 10)
        XCTAssertFalse(app.staticTexts["storageError"].exists)
    }

    func testOriginalDiaryConfirmationAndAutosaveKeepScrolledWritingPosition() {
        app.terminate()
        app.launchEnvironment["RIJI_TEST_ORIGINAL_VAULT"] = "1"
        app.launchEnvironment["RIJI_TEST_VAULT_BROWSER"] = "1"
        app.launchEnvironment["RIJI_TEST_NAVIGATION"] = "1"
        app.launch()
        waitForOriginalEditor(containing: "After synthetic table")
        let original = input.value as? String ?? ""
        input.tap(); input.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        let keyboard = app.keyboards.firstMatch, end = app.buttons["文末"]
        XCTAssertTrue(keyboard.waitForExistence(timeout: 5))
        expectation(for: NSPredicate { _, _ in end.isHittable && end.frame.maxY <= keyboard.frame.minY + 1 }, evaluatedWith: end)
        waitForExpectations(timeout: 5)
        end.tap()
        let writingPosition = viewport("y")
        XCTAssertGreaterThan(writingPosition, 300)
        var expected = original
        for addition in ["Confirmed input", "\nContinued at the same position"] {
            input.typeText(addition); expected += addition
            waitForStatus("已保存在此设备")
            XCTAssertEqual(input.value as? String, expected)
            XCTAssertEqual(viewport("caret"), expected.utf16.count)
            XCTAssertGreaterThan(viewport("y"), writingPosition - 100, "Saving confirmed input must keep the bottom of the diary visible")
        }
        let evidence = XCTAttachment(screenshot: app.screenshot())
        evidence.name = "Original diary stays at confirmed input after autosave"; evidence.lifetime = .keepAlways; add(evidence)
        app.buttons["收起键盘"].tap()
        XCTAssertGreaterThan(viewport("y"), 100)
        let readingPosition = viewport("y")
        app.tabBars.buttons["设置"].tap(); app.tabBars.buttons["日记"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertEqual(viewport("y"), readingPosition, accuracy: 2)
        XCTAssertEqual(input.value as? String, expected)
    }

    func testOriginalTableInPlaceEditUndoAndColdReopenPreservesExactSource() {
        app.terminate()
        app.launchEnvironment["RIJI_TEST_ORIGINAL_VAULT"] = "1"
        app.launchEnvironment["RIJI_TEST_VAULT_BROWSER"] = "1"
        app.launchEnvironment["RIJI_TEST_NAVIGATION"] = "1"
        app.launch()
        expectation(for: NSPredicate(format: "value CONTAINS %@", "After synthetic table"), evaluatedWith: input)
        waitForExpectations(timeout: 10)
        let source = input.value as? String ?? ""
        let cell = app.staticTexts["中文 🧠 0"]
        XCTAssertTrue(cell.waitForExistence(timeout: 5)); XCTAssertTrue(cell.isHittable)
        cell.tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        let editor = app.textViews["markdownTableCellEditor"]
        XCTAssertTrue(editor.waitForExistence(timeout: 5))
        let position = NSMaxRange((source as NSString).range(of: "中文 🧠 0"))
        XCTAssertEqual(input.value as? String, source)
        editor.typeText("Changed")
        let first = (source as NSString).replacingCharacters(in: NSRange(location: position, length: 0), with: "Changed")
        expectation(for: NSPredicate(format: "value == %@", first), evaluatedWith: input)
        waitForExpectations(timeout: 10); waitForStatus("已保存在此设备")
        let undo = app.buttons["撤销"]
        XCTAssertTrue(undo.waitForExistence(timeout: 5)); XCTAssertTrue(undo.isHittable)
        for _ in 0..<8 {
            if input.value as? String == source { break }
            undo.tap()
        }
        expectation(for: NSPredicate(format: "value == %@", source), evaluatedWith: input)
        waitForExpectations(timeout: 10); waitForStatus("已保存在此设备")
        editor.typeText("Reviewed")
        let edited = (source as NSString).replacingCharacters(in: NSRange(location: position, length: 0), with: "Reviewed")
        expectation(for: NSPredicate(format: "value == %@", edited), evaluatedWith: input)
        waitForExpectations(timeout: 10); waitForStatus("已保存在此设备")
        app.buttons["收起键盘"].tap()
        app.terminate(); app.launch()
        expectation(for: NSPredicate(format: "value == %@", edited), evaluatedWith: input)
        waitForExpectations(timeout: 10); waitForStatus("已保存在此设备")
        XCTAssertFalse(app.staticTexts["storageError"].exists)
    }

    func testUnselectedOriginalHomeShowsFolderActionAndFourMainTabsWork() {
        app.terminate(); app.launchEnvironment["RIJI_TEST_ORIGINAL_VAULT"] = "1"; app.launch()
        XCTAssertTrue(app.buttons["chooseHomeVaultFolder"].waitForExistence(timeout: 10))
        XCTAssertEqual(input.value as? String ?? "", "")
        for label in ["导师", "记忆", "设置", "日记"] { app.tabBars.buttons[label].tap() }
        XCTAssertTrue(app.buttons["chooseHomeVaultFolder"].exists)
        app.buttons["openOldLocalDiaries"].tap()
        XCTAssertTrue(app.staticTexts["templateLabel"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["chooseHomeVaultFolder"].exists)
    }
}


extension DiaryFlowTests {
    func testInlineTableReturnAddRowAddColumnAndColdReopenKeepExactSource() {
        app.terminate()
        app.launchEnvironment["RIJI_TEST_ORIGINAL_VAULT"] = "1"
        app.launchEnvironment["RIJI_TEST_VAULT_BROWSER"] = "1"
        app.launchEnvironment["RIJI_TEST_NAVIGATION"] = "1"
        app.launch()
        waitForOriginalEditor(containing: "After synthetic table")
        let source = input.value as? String ?? ""
        app.staticTexts["中文 🧠 0"].tap()
        let editor = app.textViews["markdownTableCellEditor"]
        XCTAssertTrue(editor.waitForExistence(timeout: 5))
        editor.typeText("Typed\nNew row")
        let newline = source.contains("\r\n") ? "\r\n" : "\n"
        let sourceLine = source.components(separatedBy: newline).first { $0.contains("中文 🧠 0") }!
        let columns = sourceLine.filter { $0 == "|" }.count - 1
        let newLine = "| New row |" + String(repeating: "  |", count: columns - 1)
        let expected = source.replacingOccurrences(of: sourceLine + newline, with: sourceLine.replacingOccurrences(of: "中文 🧠 0", with: "中文 🧠 0Typed") + newline + newLine + newline)
        expectation(for: NSPredicate(format: "value == %@", expected), evaluatedWith: input)
        waitForExpectations(timeout: 10); waitForStatus("已保存在此设备")
        let typing = XCTAttachment(screenshot: app.screenshot()); typing.name = "Inline table typing and Return"; typing.lifetime = .keepAlways; add(typing)
        app.buttons["收起键盘"].tap()
        let addRow = app.buttons["tableAddRow"].firstMatch
        for _ in 0..<25 { if addRow.isHittable { break }; input.swipeUp() }
        XCTAssertTrue(addRow.isHittable); addRow.tap()
        XCTAssertTrue(editor.waitForExistence(timeout: 5))
        let keyboard = app.keyboards.firstMatch, end = app.buttons["文末"]
        XCTAssertTrue(keyboard.waitForExistence(timeout: 5))
        expectation(for: NSPredicate { _, _ in
            let visibleBottom = min(self.input.frame.maxY, keyboard.frame.minY, end.frame.minY)
            return editor.isHittable && keyboard.exists && end.isHittable
                && editor.frame.minY >= self.input.frame.minY && editor.frame.maxY <= visibleBottom
        }, evaluatedWith: editor)
        waitForExpectations(timeout: 10)
        let bottomFocus = XCTAttachment(screenshot: app.screenshot()); bottomFocus.name = "Bottom row above keyboard"; bottomFocus.lifetime = .keepAlways; add(bottomFocus)
        editor.typeText("Bottom row")
        let bottomTyping = XCTAttachment(screenshot: app.screenshot()); bottomTyping.name = "Bottom row typing"; bottomTyping.lifetime = .keepAlways; add(bottomTyping)
        app.buttons["收起键盘"].tap()
        input.swipeDown(velocity: .fast)
        for _ in 0..<25 { if app.staticTexts["名称"].isHittable { break }; input.swipeDown() }
        let grid = app.descendants(matching: .any).matching(identifier: "markdownTable").firstMatch
        let addColumn = app.buttons["tableAddColumn"].firstMatch
        for _ in 0..<12 { if addColumn.isHittable { break }; grid.swipeLeft() }
        XCTAssertTrue(addColumn.isHittable); addColumn.tap()
        XCTAssertTrue(editor.waitForExistence(timeout: 5)); editor.typeText("New column")
        waitForStatus("已保存在此设备")
        let structure = XCTAttachment(screenshot: app.screenshot()); structure.name = "Inline table new column"; structure.lifetime = .keepAlways; add(structure)
        let lastRow = expected.components(separatedBy: newline).last { $0.hasPrefix("|") }!
        let bottom = "| Bottom row |" + String(repeating: "  |", count: columns - 1)
        let withRow = expected.replacingOccurrences(of: lastRow + newline, with: lastRow + newline + bottom + newline)
        var tableLine = 0
        let final = withRow.components(separatedBy: newline).map { line -> String in
            guard line.hasPrefix("|") else { return line }
            defer { tableLine += 1 }
            return line + (tableLine == 0 ? " New column |" : tableLine == 1 ? " --- |" : "  |")
        }.joined(separator: newline)
        XCTAssertEqual(input.value as? String, final)
        app.buttons["收起键盘"].tap()
        app.terminate(); app.launch()
        expectation(for: NSPredicate(format: "value == %@", final), evaluatedWith: input)
        waitForExpectations(timeout: 10); waitForStatus("已保存在此设备")
        XCTAssertFalse(app.staticTexts["storageError"].exists)
    }
}

extension DiaryFlowTests {
    private func waitForOriginalEditor(containing text: String) {
        // Loading the original source replaces the initial empty editor.
        // Filter the current hierarchy instead of retaining that transient element.
        let loaded = app.textViews.matching(identifier: "diaryBodyEditor")
            .matching(NSPredicate(format: "value CONTAINS %@", text)).firstMatch
        XCTAssertTrue(loaded.waitForExistence(timeout: 10),
                      "The current original-source editor must contain the expected fixture text")
    }

    private func launchExternalRefreshFixture() {
        app.terminate()
        app.launchEnvironment["RIJI_TEST_ORIGINAL_VAULT"] = "1"
        app.launchEnvironment["RIJI_TEST_VAULT_BROWSER"] = "1"
        app.launchEnvironment["RIJI_TEST_EXTERNAL_REFRESH"] = "1"
        app.launch()
        waitForOriginalEditor(containing: "Original home synthetic")
    }

    func testActiveExternalFileRefreshAllowsContinuedEditingAndColdReopen() {
        launchExternalRefreshFixture()
        let before = input.value as? String ?? ""
        app.buttons["moreMenu"].tap(); app.buttons["simulateExternalEdit"].tap()
        let refreshed = before + "External simulated edit\r\n"
        expectation(for: NSPredicate(format: "value == %@", refreshed), evaluatedWith: input)
        waitForExpectations(timeout: 10)
        XCTAssertFalse(app.staticTexts["storageError"].exists)
        XCTAssertFalse(app.staticTexts["saveStatus"].exists)
        let evidence = XCTAttachment(screenshot: app.screenshot())
        evidence.name = "Foreground external file refresh without a permanent save footer"; evidence.lifetime = .keepAlways; add(evidence)
        type("Continued after external refresh", savedStatus: "已保存在此设备")
        let saved = refreshed + "Continued after external refresh\n"
        app.terminate(); app.launch()
        expectation(for: NSPredicate(format: "value == %@", saved), evaluatedWith: input)
        waitForExpectations(timeout: 10)
        XCTAssertFalse(app.staticTexts["storageError"].exists)
    }

    func testOrdinaryBodyConflictFragmentChoiceEditRecoveryAndSaveExactBytes() {
        app.launchEnvironment["RIJI_TEST_PREPARE_BODY_CONFLICT"] = "1"
        launchExternalRefreshFixture()
        app.launchEnvironment.removeValue(forKey: "RIJI_TEST_PREPARE_BODY_CONFLICT")
        let before = input.value as? String ?? ""
        let prepare = app.buttons["prepareSyntheticBodyConflict"]
        XCTAssertTrue(prepare.waitForExistence(timeout: 5))
        XCTAssertTrue(prepare.isEnabled)
        prepare.tap()
        XCTAssertTrue(app.buttons["reviewBodyConflict"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["prepareSyntheticBodyConflict"].exists,
                       "The one-shot fixture control must disappear after preparing the conflict")
        app.buttons["reviewBodyConflict"].tap()
        XCTAssertTrue(app.staticTexts["bodyDiffSummary"].waitForExistence(timeout: 5))
        let use = app.buttons["useDiff-0"]; reveal(use); use.tap()
        let edit = app.buttons["只编辑这一处"].firstMatch; reveal(edit); edit.tap()
        let fragment = app.textViews["editDiff-0"]; reveal(fragment)
        let fragmentBefore = fragment.value as? String ?? ""
        XCTAssertEqual(Data(fragmentBefore.utf8), Data("External simulated edit\r\n".utf8))
        let suffix = "Reviewed fragment e\u{301} 🧠"
        appendBodyConflictFragment(fragment, suffix)
        let close = app.buttons["closeBodyMerge"]; close.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        app.terminate(); app.launch(); XCTAssertTrue(input.waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["prepareSyntheticBodyConflict"].exists)
        XCTAssertTrue(app.buttons["recoverBodyMerge"].waitForExistence(timeout: 10))
        app.buttons["recoverBodyMerge"].tap()
        openRecoveredBodyFullVersions()
        let merged = app.textViews["bodyConflictMerge"]; reveal(merged)
        let expected = before + "External simulated edit\r\n" + suffix
        XCTAssertEqual(Data((merged.value as? String ?? "").utf8), Data(expected.utf8), "Untouched headings, tables, Unicode and CRLF must remain byte-for-byte unchanged")
        let finalDiff = app.buttons["bodyFinalDiff"]; reveal(finalDiff); finalDiff.tap()
        XCTAssertTrue(app.staticTexts["bodyFinalDiffSummary"].waitForExistence(timeout: 5))
        performBodyReviewAction()
        expectation(for: NSPredicate(format: "value == %@", expected), evaluatedWith: input); waitForExpectations(timeout: 10)
        app.terminate(); app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        XCTAssertEqual(Data((input.value as? String ?? "").utf8), Data(expected.utf8))
    }

    private func openRecoveredBodyFullVersions() {
        let navigation = app.navigationBars["核对正文版本"]
        XCTAssertTrue(navigation.waitForExistence(timeout: 5))
        let window = app.windows.containing(.navigationBar, identifier: "核对正文版本").firstMatch
        let form = window.collectionViews.firstMatch
        XCTAssertTrue(form.waitForExistence(timeout: 5))
        let full = form.buttons["bodyFullVersions"]
        for attempt in 0...7 {
            // The recovered Form can discard a lazy row between exists and frame.
            // Search one captured tree instead of resolving the missing row itself.
            guard let snapshot = try? form.snapshot() else {
                XCTFail("The recovered merge Form must provide an accessibility snapshot"); return
            }
            let bounds = snapshot.frame.intersection(app.frame)
            let top = max(bounds.minY, navigation.frame.maxY) + 8
            let bottom = bounds.maxY - 20
            guard isFiniteFrame(bounds), bottom > top + 100 else {
                XCTFail("The recovered merge Form must have a usable scroll viewport"); return
            }
            var nodes: [XCUIElementSnapshot] = [snapshot]
            var disclosureFrame: CGRect?
            while let node = nodes.popLast() {
                if node.identifier == "bodyFullVersions", node.elementType == .button {
                    disclosureFrame = node.frame; break
                }
                nodes.append(contentsOf: node.children)
            }
            if let frame = disclosureFrame, isFiniteFrame(frame),
               frame.minY >= top, frame.maxY <= bottom, bounds.contains(frame), full.isHittable {
                full.tap(); return
            }
            guard attempt < 7 else {
                XCTFail("The recovered merge Form must reveal its full-versions disclosure before opening it"); return
            }
            let towardTop = disclosureFrame.map { isFiniteFrame($0) && $0.minY < top } ?? false
            let origin = form.coordinate(withNormalizedOffset: .zero)
            let upper = origin.withOffset(CGVector(dx: 12, dy: top - snapshot.frame.minY + (bottom - top) * 0.25))
            let lower = origin.withOffset(CGVector(dx: 12, dy: top - snapshot.frame.minY + (bottom - top) * 0.75))
            let start = towardTop ? upper : lower, end = towardTop ? lower : upper
            start.press(forDuration: 0.05, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.2)
        }
    }

    private func performBodyReviewAction(_ identifier: String = "confirmBodyMerge", towardEarlier: Bool = false, evidenceName: String? = nil) {
        bodyReviewGeometry = []
        defer { attachBodyReviewGeometry() }
        let navigation = app.navigationBars["核对正文版本"]
        XCTAssertTrue(navigation.waitForExistence(timeout: 5))
        let window = app.windows.containing(.navigationBar, identifier: "核对正文版本").firstMatch
        let form = window.collectionViews.firstMatch
        XCTAssertTrue(form.waitForExistence(timeout: 5))
        var previousFrame: CGRect?
        let maximumScrolls = towardEarlier ? 8 : 7
        for attempt in 0...maximumScrolls {
            previousFrame = bodyReviewActionFrame(identifier, in: form, navigation: navigation)
            if previousFrame != nil { break }
            guard attempt < maximumScrolls else {
                // XCTest can stop the case before defer runs on a failure.
                attachBodyReviewGeometry()
                XCTFail("The body review action must become fully visible and enabled"); return
            }
            let bounds = form.frame.intersection(app.frame)
            let top = max(bounds.minY, navigation.frame.maxY) + 8, bottom = bounds.maxY - 20
            guard isFiniteFrame(bounds), top.isFinite, bottom.isFinite, bottom > top + 100 else {
                XCTFail("The recovered merge Form must have a usable scroll viewport"); return
            }
            let origin = form.coordinate(withNormalizedOffset: .zero)
            let upper = origin.withOffset(CGVector(dx: 12, dy: top - form.frame.minY + (bottom - top) * 0.25))
            let lower = origin.withOffset(CGVector(dx: 12, dy: top - form.frame.minY + (bottom - top) * 0.75))
            let start = towardEarlier ? upper : lower, end = towardEarlier ? lower : upper
            start.press(forDuration: 0.05, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.2)
        }
        var capturedFrame: CGRect?
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            guard let frame = self.bodyReviewActionFrame(identifier, in: form, navigation: navigation) else {
                previousFrame = nil; return false
            }
            let stable = previousFrame == frame
            previousFrame = frame
            if stable { capturedFrame = frame }
            return stable
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 5), .completed,
                       "Two Form snapshots must agree on the visible enabled action before one tap")
        guard let frame = capturedFrame else { XCTFail("The body review action must have a stable captured frame"); return }
        if let evidenceName { attachMentorScreenshot(evidenceName) }
        // iOS 26 can throw while resolving this visible button's AX hit point.
        // Use its observed stable center once, then require the real save to finish.
        app.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(
            dx: frame.midX - app.frame.minX, dy: frame.midY - app.frame.minY)).tap()
        let closed = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in !navigation.exists }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [closed], timeout: 10), .completed,
                       "The body review action must save and close the review sheet")
    }

    private func attachBodyReviewGeometry() {
        let geometry = XCTAttachment(string: bodyReviewGeometry.joined(separator: "\n"))
        geometry.name = "Body review scroll geometry"; geometry.lifetime = .keepAlways; add(geometry)
    }

    private func bodyReviewActionFrame(_ identifier: String, in form: XCUIElement, navigation: XCUIElement) -> CGRect? {
        guard let snapshot = try? form.snapshot() else {
            XCTFail("The recovered merge Form must provide an accessibility snapshot"); return nil
        }
        let bounds = snapshot.frame.intersection(app.frame)
        let top = max(bounds.minY, navigation.frame.maxY) + 8, bottom = bounds.maxY - 20
        guard isFiniteFrame(bounds), top.isFinite, bottom.isFinite, bottom > top + 100 else {
            XCTFail("The recovered merge Form must have a usable confirmation viewport"); return nil
        }
        var nodes: [XCUIElementSnapshot] = [snapshot]
        var buttons: [XCUIElementSnapshot] = []
        var geometry = ["time=\(Date().timeIntervalSince1970); form=\(snapshot.frame)"]
        while let node = nodes.popLast() {
            if node.identifier == identifier, node.elementType == .button { buttons.append(node) }
            if [identifier, "bodyFullVersions", "bodyConflictSource", "bodyConflictMerge"].contains(node.identifier) {
                geometry.append("\(node.identifier)=\(node.frame); enabled=\(node.isEnabled)")
            }
            nodes.append(contentsOf: node.children)
        }
        bodyReviewGeometry.append(geometry.joined(separator: "; "))
        guard buttons.count <= 1 else { XCTFail("The body review must expose only one button for the requested action"); return nil }
        guard let button = buttons.first, button.isEnabled else { return nil }
        let frame = button.frame
        guard isFiniteFrame(frame), bounds.contains(frame), frame.minY >= top, frame.maxY <= bottom else { return nil }
        return frame
    }

    private func appendBodyConflictFragment(_ editor: XCUIElement, _ suffix: String) {
        let before = editor.value as? String ?? ""
        editor.tap(); editor.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        // Command-down does not move this native fragment editor on the CI runtime.
        // Unmodified arrows saturate at the end; UTF-16 length bounds all caret steps.
        for _ in 0..<before.utf16.count { editor.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: []) }
        XCTAssertEqual(Data((editor.value as? String ?? "").utf8), Data(before.utf8), "Positioning the caret must not change the fragment")
        editor.typeText(suffix)
        XCTAssertEqual(Data((editor.value as? String ?? "").utf8), Data((before + suffix).utf8))
    }

    func testCompetingExternalEditsOfferLatestAndLocalChoicesWithoutReauthorization() {
        launchExternalRefreshFixture()
        for choice in ["useLatestBodyVersion", "keepLocalBodyVersion"] {
            let before = input.value as? String ?? ""
            app.buttons["moreMenu"].tap(); app.buttons["simulateCompetingEdits"].tap()
            XCTAssertTrue(app.buttons["reviewBodyConflict"].waitForExistence(timeout: 10))
            XCTAssertFalse(app.buttons["reauthorizeOriginal"].exists)
            XCTAssertEqual(input.value as? String, before + "Local simulated edit\r\n")
            app.buttons["reviewBodyConflict"].tap()
            XCTAssertTrue(app.buttons["useLatestBodyVersion"].waitForExistence(timeout: 5))
            XCTAssertTrue(app.buttons["keepLocalBodyVersion"].exists)
            XCTAssertTrue(app.staticTexts["bodyDiffSummary"].waitForExistence(timeout: 5))
            let fullVersions = app.buttons["bodyFullVersions"]; reveal(fullVersions); fullVersions.tap()
            let merge = app.textViews["bodyConflictMerge"]
            reveal(merge)
            XCTAssertTrue(merge.exists)
            performBodyReviewAction(choice, towardEarlier: true,
                                    evidenceName: "Explicit current file, local edit and manual merge choices")
            let selected = before + (choice == "useLatestBodyVersion" ? "External simulated edit\r\n" : "Local simulated edit\r\n")
            expectation(for: NSPredicate(format: "value == %@", selected), evaluatedWith: input)
            waitForExpectations(timeout: 10)
            XCTAssertFalse(app.staticTexts["storageError"].exists)
            app.terminate(); app.launch()
            expectation(for: NSPredicate(format: "value == %@", selected), evaluatedWith: input)
            waitForExpectations(timeout: 10)
        }
    }

    func testHomeAndActivatePreservePendingBodyAndTableInput() {
        app.launchEnvironment["RIJI_TEST_LIFECYCLE_REFRESH"] = "1"
        launchExternalRefreshFixture()
        let original = input.value as? String ?? ""
        app.staticTexts["中文 🧠 0"].tap()
        let cell = app.textViews["markdownTableCellEditor"]
        XCTAssertTrue(cell.waitForExistence(timeout: 5))
        cell.typeText("Lifecycle table")
        let tableEdited = original.replacingOccurrences(of: "中文 🧠 0", with: "中文 🧠 0Lifecycle table")
        expectation(for: NSPredicate(format: "value == %@", tableEdited), evaluatedWith: input)
        waitForExpectations(timeout: 10)
        XCUIDevice.shared.press(.home)
        XCTAssertTrue(app.wait(for: .runningBackground, timeout: 5) || app.state == .runningBackgroundSuspended)
        app.activate()
        expectation(for: NSPredicate(format: "value == %@", tableEdited), evaluatedWith: input)
        waitForExpectations(timeout: 10); waitForStatus("已保存在此设备")
        XCTAssertFalse(app.staticTexts["storageError"].exists)

        input.tap(); input.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        let keyboard = app.keyboards.firstMatch, end = app.buttons["文末"]
        XCTAssertTrue(keyboard.waitForExistence(timeout: 5))
        expectation(for: NSPredicate { _, _ in end.isHittable && end.frame.maxY <= keyboard.frame.minY + 1 }, evaluatedWith: end)
        waitForExpectations(timeout: 5); end.tap()
        input.typeText("Pending body before Home\n")
        let final = tableEdited + "Pending body before Home\n"
        XCTAssertEqual(input.value as? String, final)
        XCUIDevice.shared.press(.home)
        XCTAssertTrue(app.wait(for: .runningBackground, timeout: 5) || app.state == .runningBackgroundSuspended)
        app.activate()
        expectation(for: NSPredicate(format: "value == %@", final), evaluatedWith: input)
        waitForExpectations(timeout: 10); waitForStatus("已保存在此设备")
        XCTAssertFalse(app.staticTexts["storageError"].exists)
        let evidence = XCTAttachment(screenshot: app.screenshot())
        evidence.name = "Real Home and activate retain table and body input"; evidence.lifetime = .keepAlways; add(evidence)
        app.terminate(); app.launch()
        expectation(for: NSPredicate(format: "value == %@", final), evaluatedWith: input)
        waitForExpectations(timeout: 10); waitForStatus("已保存在此设备")
        XCTAssertFalse(app.staticTexts["storageError"].exists)
    }
}
