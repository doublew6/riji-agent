import XCTest
import Foundation
@testable import RijiCore
@testable import RijiState

private func roundtableJSON(version: String = "one", id: String = "roundtable-one", message: String = "message-one", cursor: String? = nil, status: String = "completed", ttl: Double = 899) throws -> String {
    let body: [String: Any] = ["conversation_id": id, "status": status, "revision": 1, "state_revision": 1,
        "summary_version": 1, "correction_version": 0, "run_id": "run-one", "run_kind": "followup", "run_personas": ["host"],
        "mode": "reference", "rounds": 1, "suspended_run_id": "", "messages": [["id": message, "actor": "host", "kind": "followup", "text": "Synthetic advice", "created_at": 1, "input_revision": 1, "run_id": "run-one", "origin_kind": "ai_discussion", "superseded": false, "uncertainties": [], "next_steps": [], "source_ids": []]],
        "current_summary": NSNull(), "runs": [], "audience": ["host", "gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"],
        "history_version": version, "next_cursor": cursor as Any? ?? NSNull(), "selected_artifact_ids": [], "cache_expires_at": Date().timeIntervalSince1970 + ttl]
    return String(decoding: try JSONSerialization.data(withJSONObject: body), as: UTF8.self)
}
private let roundtableList = "{\"items\":[],\"current_id\":\"roundtable-one\",\"truncated\":false}"

@MainActor
final class MobileRoundtableStateTests: XCTestCase {
    private func start(_ f: MobileStateFixture) async throws {
        await f.model.start()
        await f.wire.queue("/mobile/v1/roundtables", .init(json: roundtableList))
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: try roundtableJSON()))
        await f.model.openRoundtables()
    }
    func testThreeRelaunchesRestoreDraftAndSelectedHistoryWithoutSending() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        await f.model.selectRoundtable(nil)
        f.model.setDraft("roundtable:new", text: "Retained new draft")
        for _ in 0..<3 {
            let model = MobileClientModel(credentials: f.credentials, persistence: f.store, transport: f.wire)
            await model.start(); await f.wire.queue("/mobile/v1/roundtables", .init(json: roundtableList))
            await model.openRoundtables()
            XCTAssertNil(model.roundtableID); XCTAssertEqual(model.draft(model.roundtableDraftKey), "Retained new draft")
        }
        let requests = await f.wire.requests
        XCTAssertFalse(requests.contains { $0.httpMethod == "POST" })
    }
    func testChangedHistoryDoesNotMixPagesAndDatedOfflineCacheCannotWrite() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: try roundtableJSON(cursor: "next")))
        await f.model.refreshRoundtable()
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: try roundtableJSON(version: "changed", message: "changed-page")))
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: try roundtableJSON(version: "latest", message: "latest")))
        await f.model.refreshRoundtable(older: true)
        XCTAssertEqual(f.model.roundtable?.messages.map(\.id), ["latest"])
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: "", timeout: true))
        await f.model.refreshRoundtable()
        XCTAssertTrue(f.model.roundtableOffline); XCTAssertFalse(f.model.canMutateRoundtable)
        f.model.invalidateRoundtable()
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: "", timeout: true))
        await f.model.refreshRoundtable()
        XCTAssertNil(f.model.roundtable)
    }
    func testUnknownMessageCanStopOnIndependentReceiptTarget() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        f.model.setDraft(f.model.roundtableDraftKey, text: "Retained pending question")
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one/messages", .init(json: "", timeout: true))
        await f.wire.queue("/mobile/v1/roundtables", .init(json: roundtableList))
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: try roundtableJSON()))
        await f.model.sendRoundtable()
        XCTAssertTrue(f.model.hasUnresolved("roundtable:roundtable-one"))
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one/commands", .init(json: "{\"operation_id\":\"$OP\",\"kind\":\"roundtable_command\",\"state\":\"completed\",\"result\":{\"conversation_id\":\"roundtable-one\"}}"))
        await f.wire.queue("/mobile/v1/roundtables", .init(json: roundtableList))
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: try roundtableJSON()))
        await f.model.roundtableCommand("stop")
        XCTAssertEqual(f.model.operation("roundtable-stop:roundtable-one")?.state, "completed")
        XCTAssertTrue(f.model.hasUnresolved("roundtable:roundtable-one"))
        XCTAssertEqual(f.model.draft("roundtable:roundtable-one"), "Retained pending question")
    }
    func testCreationReplyMovesOnlyNewerComposerTextIntoCreatedConversation() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; await f.model.start()
        await f.wire.queue("/mobile/v1/roundtables", .init(json: "{\"items\":[],\"current_id\":null,\"truncated\":false}"))
        await f.model.openRoundtables()
        f.model.setDraft("roundtable:new", text: "First submitted question")
        await f.wire.hold("/mobile/v1/roundtables")
        let task = Task { await f.model.sendRoundtable() }
        for _ in 0..<100 { if await f.wire.isHeld { break }; try await Task.sleep(nanoseconds: 10_000_000) }
        f.model.setDraft("roundtable:new", text: "Newer unsent input")
        await f.wire.queue("/mobile/v1/roundtables", .init(json: roundtableList))
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: try roundtableJSON()))
        await f.wire.release(.init(json: "{\"operation_id\":\"$OP\",\"kind\":\"roundtable_create\",\"state\":\"completed\",\"result\":{\"conversation_id\":\"roundtable-one\"}}"))
        await task.value
        XCTAssertEqual(f.model.roundtableID, "roundtable-one")
        XCTAssertEqual(f.model.draft(f.model.roundtableDraftKey), "Newer unsent input")
        XCTAssertEqual(f.model.draft("roundtable:new"), "")
        XCTAssertEqual(try f.store.load(f.credential).roundtableSelection, "roundtable-one")
    }
    func testKnownSourcePrivacyAndDeviceRevocationInvalidateCacheAcrossRelaunch() async throws {
        for mode in ["source", "privacy", "device", "account"] {
            let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
            XCTAssertNotNil(try f.store.roundtableCache.load(f.credential))
            switch mode {
            case "source":
                await f.wire.queue("/mobile/v1/memories/m1", .init(status: 404, json: "{\"detail\":\"memory_not_found\"}")); await f.model.refreshMemory("m1")
            case "privacy": await f.model.refreshPrivacy()
            case "device":
                await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(status: 401, json: "{}")); await f.model.refreshRoundtable()
                f.credentials.write(f.credential)
            default:
                f.credentials.write(MobileCredential(endpoint: "https://different.example", deviceId: "other-device", token: "example-other-token"))
                f.model.retryCredentialRead(); f.credentials.write(f.credential)
            }
            XCTAssertNil(try f.store.roundtableCache.load(f.credential), mode)
            let restarted = MobileClientModel(credentials: f.credentials, persistence: f.store, transport: f.wire)
            restarted.retryCredentialRead()
            await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: "", timeout: true)); await restarted.refreshRoundtable()
            XCTAssertNil(restarted.roundtable, mode)
        }
    }
    func testColdDiaryRecoveryOffersRetainedPreviewAndCancellationWithoutRemotePermission() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("RoundtableDiary-\(UUID())")
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"))
        let day = try JournalDay("2026-09-20"), now = ISO8601DateFormatter().date(from: "2026-09-20T08:00:00Z")!
        let value = MobileResultValidation(resultId: "result", text: "Selected AI text", provenance: DiscussionProvenance(resultId: "result", problemId: "problem", discussionId: "discussion", artifactIds: ["artifact"], inputRevision: 1, summaryVersion: 1, correctionVersion: 0, recordedAt: "2026-09-20T00:00:00Z", mentors: ["host"], topic: "Synthetic", savedDate: "", editedByUser: false), binding: String(repeating: "a", count: 64), validatedAt: Date().timeIntervalSince1970)
        let preview = try await store.prepareAIResult(value, account: "account", day: day, text: value.text)
        let model = DiaryViewModel(now: { now }, storageFactory: { store }); await model.start()
        XCTAssertFalse(model.canEditBody)
        await model.restorePendingAIResults(account: "other"); XCTAssertNil(model.aiResultPreview)
        await model.restorePendingAIResults(account: "account"); XCTAssertEqual(model.aiResultPreview, preview)
        await model.cancelAIResultPreview()
        XCTAssertNil(model.aiResultPreview); XCTAssertTrue(model.canEditBody)
        let receipt = try await store.aiResultReceipt(account: "account", resultID: "result"); XCTAssertNil(receipt)
    }

    func testReadingSelectionAndScrollAnchorRemainScopedToConversationAcrossRelaunch() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        f.model.saveRoundtableReadingState(selected: ["message-one"], anchor: "message-one")
        let restarted = MobileClientModel(credentials: f.credentials, persistence: f.store, transport: f.wire)
        await restarted.start()
        await f.wire.queue("/mobile/v1/roundtables", .init(json: roundtableList))
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: try roundtableJSON()))
        await restarted.openRoundtables()
        XCTAssertEqual(restarted.roundtableReadingState?.selectedIDs, ["message-one"])
        XCTAssertEqual(restarted.roundtableReadingState?.anchor, "message-one")
        await restarted.selectRoundtable(nil)
        XCTAssertNil(restarted.roundtableReadingState)
    }

    private func validationJSON() throws -> String {
        let value = MobileResultValidation(resultId: "result-one", text: "Selected AI advice", provenance: DiscussionProvenance(resultId: "result-one", problemId: "roundtable-one", discussionId: "run-one", artifactIds: ["message-one"], inputRevision: 1, summaryVersion: 1, correctionVersion: 0, recordedAt: "2026-09-20T00:00:00Z", mentors: ["host"], topic: "Synthetic", savedDate: "", editedByUser: false), binding: String(repeating: "a", count: 64), validatedAt: Date().timeIntervalSince1970)
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        return String(decoding: try encoder.encode(value), as: UTF8.self)
    }
    private func waitHeld(_ wire: MobileStateWire) async throws {
        for _ in 0..<100 { if await wire.isHeld { return }; try await Task.sleep(nanoseconds: 10_000_000) }
        XCTFail("The request must be held before changing state")
    }
    func testNewResultPreviewCannotOpenOldValidationWhenLocalPersistenceFails() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        f.model.resultValidation = try decoder.decode(MobileResultValidation.self, from: Data(validationJSON().utf8))
        try FileManager.default.removeItem(at: f.root)
        try Data("Synthetic storage obstacle".utf8).write(to: f.root)
        let before = (await f.wire.requests).filter { $0.httpMethod == "POST" }.count
        await f.model.previewRoundtableResult(["message-one"], conversationID: "roundtable-one")
        XCTAssertNil(f.model.resultValidation)
        XCTAssertEqual(f.model.error, MobileError.storage.localizedDescription)
        let after = (await f.wire.requests).filter { $0.httpMethod == "POST" }.count
        XCTAssertEqual(before, after)
    }
    func testCancelledResultValidationDoesNotReportConnectionOffline() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        await f.wire.hold("/mobile/v1/roundtables/roundtable-one/result-validation")
        let task = Task { await f.model.validateRoundtableResult("result-one", conversationID: "roundtable-one") }
        try await waitHeld(f.wire)
        task.cancel()
        await f.wire.release(.init(json: "", timeout: true))
        let result = await task.value
        XCTAssertNil(result); XCTAssertNil(f.model.error)
        XCTAssertEqual(f.model.connectionState, .connected)
    }
    func testResultReceiptCompactionReloadAndFreshValidationRetainOnlyIdentifiers() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one/result-preview", .init(json: "{\"operation_id\":\"$OP\",\"kind\":\"roundtable_result\",\"state\":\"completed\",\"result\":{\"result_id\":\"result-one\",\"text\":\"Must not persist\"}}"))
        await f.wire.queue("/mobile/v1/roundtables", .init(json: roundtableList))
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: try roundtableJSON()))
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one/result-validation", .init(json: try validationJSON()))
        let validation = await f.model.previewRoundtableResult(["message-one"])
        XCTAssertEqual(validation?.resultId, "result-one")
        XCTAssertEqual(f.model.resultValidation?.text, "Selected AI advice")
        let operation = try XCTUnwrap(f.store.load(f.credential).operations.last)
        XCTAssertTrue(operation.body.isEmpty)
        XCTAssertEqual(operation.result, ["result_id": .string("result-one")])
        let restarted = MobileClientModel(credentials: f.credentials, persistence: f.store, transport: f.wire)
        await restarted.start()
        await f.wire.queue("/mobile/v1/roundtables", .init(json: roundtableList))
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: try roundtableJSON()))
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one/result-validation", .init(json: try validationJSON()))
        await restarted.refreshAfterRoundtable(operation)
        XCTAssertEqual(restarted.resultValidation?.resultId, "result-one")
        let requests = await f.wire.requests
        XCTAssertEqual(requests.filter { $0.url?.path.hasSuffix("/result-preview") == true }.count, 1)
        XCTAssertEqual(requests.filter { $0.url?.path.hasSuffix("/result-validation") == true }.count, 2)
    }
    func testConcurrentPreviewCannotReturnAnotherAttemptValidation() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        await f.wire.hold("/mobile/v1/roundtables/roundtable-one/result-preview")
        let first = Task { await f.model.previewRoundtableResult(["message-one"], conversationID: "roundtable-one") }
        try await waitHeld(f.wire)
        let second = await f.model.previewRoundtableResult(["another-message"], conversationID: "roundtable-one")
        XCTAssertNil(second)
        await f.wire.queue("/mobile/v1/roundtables", .init(json: roundtableList))
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: try roundtableJSON()))
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one/result-validation", .init(json: try validationJSON()))
        await f.wire.release(.init(json: "{\"operation_id\":\"$OP\",\"kind\":\"roundtable_result\",\"state\":\"completed\",\"result\":{\"result_id\":\"result-one\"}}"))
        let actual = await first.value
        XCTAssertEqual(actual?.provenance.artifactIds, ["message-one"])
        let requests = await f.wire.requests
        XCTAssertEqual(requests.filter { $0.url?.path.hasSuffix("/result-preview") == true }.count, 1)
    }
    func testLateHistoryAndResultResponsesCannotRestoreRevokedContent() async throws {
        for result in [false, true] {
            let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
            let path = "/mobile/v1/roundtables/roundtable-one" + (result ? "/result-validation" : "")
            await f.wire.hold(path)
            let pending = Task {
                if result { _ = await f.model.validateRoundtableResult("result-one", conversationID: "roundtable-one") }
                else { await f.model.refreshRoundtable() }
            }
            try await waitHeld(f.wire)
            await f.model.refreshPrivacy()
            await f.wire.release(.init(json: try result ? validationJSON() : roundtableJSON()))
            await pending.value
            XCTAssertNil(f.model.roundtable); XCTAssertNil(f.model.resultValidation)
            XCTAssertNil(try f.store.roundtableCache.load(f.credential))
        }
    }
    func testChangedInvitationSelectionRevokesHeldPreviewAndRejectsMismatchedSnapshot() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        let path = "/mobile/v1/roundtables/roundtable-one/materials/preview"
        let preview = "{\"preview_hash\":\"hash\",\"artifact_ids\":[\"message-one\"],\"memories\":[],\"audience\":[\"host\",\"gentle_reviewer\",\"blunt_coach\",\"future_self\",\"wang_yangming\"],\"personas\":[\"gentle_reviewer\",\"blunt_coach\"],\"mode\":\"reference\",\"rounds\":1,\"revision\":1}"
        await f.wire.hold(path)
        let pending = Task { await f.model.previewMaterials(artifacts: ["message-one"], memories: [], personas: ["gentle_reviewer", "blunt_coach"], mode: "reference", rounds: 1) }
        try await waitHeld(f.wire)
        f.model.invalidateMaterialPreview()
        await f.wire.release(.init(json: preview)); await pending.value
        XCTAssertNil(f.model.materialPreview)
        await f.wire.queue(path, .init(json: preview))
        await f.model.previewMaterials(artifacts: ["changed-selection"], memories: [], personas: ["gentle_reviewer", "blunt_coach"], mode: "reference", rounds: 1)
        XCTAssertNil(f.model.materialPreview)
        XCTAssertNotNil(f.model.materialPreviewError)
        f.model.show(MobileError.storage)
        let storageError = f.model.error
        await f.wire.queue(path, .init(json: preview))
        await f.model.previewMaterials(artifacts: ["message-one"], memories: [], personas: ["gentle_reviewer", "blunt_coach"], mode: "reference", rounds: 1)
        XCTAssertEqual(f.model.materialPreview?.artifactIds, ["message-one"])
        XCTAssertNil(f.model.materialPreviewError)
        XCTAssertEqual(f.model.error, storageError)
    }
    func testCompletionReasonIsScopedToAnIncompleteRunAndNeverEchoesRawCodes() throws {
        for status in ["partial", "interrupted", "running", "completed", "archived"] {
            var body = try JSONSerialization.jsonObject(with: Data(roundtableJSON(status: status).utf8)) as! [String: Any]
            for code in ["source_budget_exhausted", "budget_exhausted", "synthetic_internal_details"] {
                body["notice"] = code
                let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
                let value = try decoder.decode(MobileRoundtable.self, from: JSONSerialization.data(withJSONObject: body))
                if ["partial", "interrupted"].contains(status) {
                    XCTAssertNotNil(value.completionNotice)
                    XCTAssertFalse(value.completionNotice?.contains(code) ?? true)
                } else { XCTAssertNil(value.completionNotice, "A successful later run must not display a stale failure") }
            }
        }
    }
    func testColdOfflineEntryRestoresBoundedCacheAndDraftButNeverKnownRevokedContent() async throws {
        for revoked in [false, true] {
            let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
            f.model.setDraft("roundtable:roundtable-one", text: "Offline unsent draft")
            f.model.saveRoundtableSelection("roundtable-one")
            if revoked { await f.model.refreshPrivacy() }
            let restarted = MobileClientModel(credentials: f.credentials, persistence: f.store, transport: f.wire)
            await f.wire.queue("/mobile/v1/capabilities", .init(json: "", timeout: true)); await restarted.start()
            XCTAssertNil(restarted.capabilities); XCTAssertTrue(restarted.canOpenRoundtable)
            await f.wire.queue("/mobile/v1/roundtables", .init(json: "", timeout: true)); await restarted.openRoundtables()
            XCTAssertEqual(restarted.draft(restarted.roundtableDraftKey), "Offline unsent draft")
            XCTAssertTrue(restarted.roundtableOffline); XCTAssertFalse(restarted.canMutateRoundtable)
            if revoked { XCTAssertNil(restarted.roundtable) } else { XCTAssertEqual(restarted.roundtable?.messages.first?.text, "Synthetic advice") }
            await restarted.sendRoundtable()
            let requests = await f.wire.requests; XCTAssertFalse(requests.contains { $0.httpMethod == "POST" })
        }
    }
    func testIdleRefreshAndCacheWriteFailureKeepLiveContentWhileOfflineExpiryClearsIt() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: try roundtableJSON(ttl: 5))); await f.model.refreshRoundtable()
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: try roundtableJSON(version: "fresh", message: "fresh")))
        await f.model.maintainRoundtable()
        XCTAssertEqual(f.model.roundtable?.messages.first?.id, "fresh")
        let cache = f.root.appendingPathComponent("roundtable-cache")
        try FileManager.default.removeItem(at: cache); try Data("Synthetic obstacle".utf8).write(to: cache)
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: try roundtableJSON(version: "live", message: "live"))); await f.model.refreshRoundtable()
        XCTAssertEqual(f.model.roundtable?.messages.first?.id, "live"); XCTAssertFalse(f.model.roundtableOffline)
        XCTAssertNotNil(f.model.roundtableCacheNotice)
        f.model.roundtableOffline = true
        await f.model.maintainRoundtable(now: Date().addingTimeInterval(901))
        XCTAssertNil(f.model.roundtable); XCTAssertTrue(f.model.roundtableOffline)
    }
    func testStoppedPartialSummaryIsReachableAndUsesItsOwnCommand() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: try roundtableJSON(status: "stopped"))); await f.model.refreshRoundtable()
        XCTAssertTrue(f.model.canSummarizeRoundtable)
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one/commands", .init(json: "{\"operation_id\":\"$OP\",\"kind\":\"roundtable_command\",\"state\":\"completed\",\"result\":{\"conversation_id\":\"roundtable-one\"}}"))
        await f.wire.queue("/mobile/v1/roundtables", .init(json: roundtableList))
        await f.wire.queue("/mobile/v1/roundtables/roundtable-one", .init(json: try roundtableJSON(status: "partial")))
        await f.model.roundtableCommand("summarize")
        XCTAssertEqual(f.model.roundtable?.status, "partial")
        let requests = await f.wire.requests
        let sent = try XCTUnwrap(requests.last { $0.httpMethod == "POST" }?.httpBody)
        XCTAssertEqual(try JSONDecoder().decode([String: MobileJSON].self, from: sent)["action"], .string("summarize"))
    }
    func testHistoryListPaginationAppendsAndChangedVersionNeverMixesPages() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; await f.model.start()
        func page(_ indices: Range<Int>, version: String, more: Bool) throws -> String {
            let items = indices.map { ["id": "room-\($0)", "title": "Synthetic", "status": "completed", "updated_at": 1, "revision": 1] as [String: Any] }
            return String(decoding: try JSONSerialization.data(withJSONObject: ["items": items, "current_id": NSNull(), "truncated": more, "next_cursor": more ? "next" as Any : NSNull(), "history_version": version]), as: UTF8.self)
        }
        await f.wire.queue("/mobile/v1/roundtables", .init(json: try page(0..<100, version: "one", more: true))); await f.model.openRoundtables()
        await f.wire.queue("/mobile/v1/roundtables", .init(json: try page(100..<105, version: "one", more: true))); await f.model.openRoundtables(more: true)
        XCTAssertEqual(f.model.roundtableList?.items.count, 105)
        XCTAssertEqual(f.model.roundtableList?.items.last?.id, "room-104")
        await f.wire.queue("/mobile/v1/roundtables", .init(status: 409, json: "{\"detail\":\"roundtable_list_changed\"}"))
        await f.wire.queue("/mobile/v1/roundtables", .init(json: try page(106..<107, version: "new", more: false))); await f.model.openRoundtables(more: true)
        XCTAssertEqual(f.model.roundtableList?.items.map(\.id), ["room-106"])
        let requests = await f.wire.requests
        XCTAssertTrue(requests.contains { $0.url?.query == "cursor=next" })
    }

    func testLocalRecoverySurvivesMissingOrChangedAccountAndLocksOnlyItsTargetDay() async throws {
        for account in [String?.none, "switched-account", "revoked-account"] {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent("LocalAIRecovery-\(UUID())")
            defer { try? FileManager.default.removeItem(at: root) }
            let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"))
            let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
            let value = try decoder.decode(MobileResultValidation.self, from: Data(try validationJSON().utf8))
            let target = try JournalDay("2026-09-20"), other = try JournalDay("2026-09-21")
            let preview = try await store.prepareAIResult(value, account: "original-account", day: target, text: value.text)
            let model = DiaryViewModel(now: { other.date(in: TimeZone.current) }, storageFactory: { store }); await model.start()
            await model.restorePendingAIResults(account: "original-account")
            XCTAssertEqual(model.aiResultPreview?.id, preview.id); XCTAssertTrue(model.canEditBody)
            let moved = await model.navigate(to: target); XCTAssertTrue(moved)
            await model.refreshLocalAIRecovery()
            XCTAssertFalse(model.canEditBody); XCTAssertEqual(model.localAIResultPreviews, [preview])
            if let account {
                await model.confirmAIResult(account: account, validation: value)
                let receipt = try await store.aiResultReceipt(account: "original-account", resultID: value.resultId)
                XCTAssertNil(receipt)
            }
            await model.cancelLocalAIResult(preview)
            XCTAssertTrue(model.localAIResultPreviews.isEmpty); XCTAssertTrue(model.canEditBody)
            let source = root.appendingPathComponent("Journals/" + target.value + ".md")
            XCTAssertFalse(FileManager.default.fileExists(atPath: source.path))
        }
    }
    func testFirstMessageClassificationTravelsThroughNativeCreateAndFixtureHistory() async throws {
        for kind in ["user_plan", "user_feedback"] {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent("RoundtableClassification-\(UUID())")
            defer { try? FileManager.default.removeItem(at: root) }
            let fixture = MobileRoundtableFixture(file: root.appendingPathComponent("history.json"))
            struct Transport: MobileTransport {
                let fixture: MobileRoundtableFixture
                func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
                    if let value = try await fixture.response(request) { return value }
                    return (Data(mobileCapabilities.utf8), HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
                }
            }
            let credential = MobileCredential(endpoint: "https://example.test", deviceId: "test-device", token: "example-synthetic")
            let model = MobileClientModel(credentials: MobileVolatileCredentials(credential), persistence: MobileLocalStore(directory: root.appendingPathComponent("client")), transport: Transport(fixture: fixture))
            await model.start(); await model.openRoundtables()
            model.setDraft(model.roundtableDraftKey, text: "Synthetic first message")
            await model.sendRoundtable(statement: kind)
            XCTAssertEqual(model.roundtable?.messages.first?.originKind, kind)
            XCTAssertEqual(model.roundtable?.messages.first?.text, "Synthetic first message")
        }
    }

}

private func groupJSON(id: String = "group-one", title: String = "Synthetic group", empty: Bool = false) throws -> String {
    var body = try JSONSerialization.jsonObject(with: Data(roundtableJSON(id: id).utf8)) as! [String: Any]
    let members = ["future_self", "blunt_coach"]
    body["chat_kind"] = "group"; body["title"] = title; body["personas"] = members
    body["audience"] = members; body["run_personas"] = members; body["run_kind"] = "group"
    if empty { body["messages"] = [] }
    else {
        var messages = body["messages"] as! [[String: Any]]
        messages[0]["actor"] = "future_self"; body["messages"] = messages
    }
    return String(decoding: try JSONSerialization.data(withJSONObject: body), as: UTF8.self)
}
private let groupListJSON = """
{"items":[{"id":"group-one","title":"Synthetic group","chat_kind":"group","personas":["future_self","blunt_coach"],"status":"completed","updated_at":1,"revision":1},{"id":"group-two","title":"Another group","chat_kind":"group","personas":["future_self","blunt_coach"],"status":"completed","updated_at":1,"revision":1}],"current_id":"group-one","truncated":false}
"""

@MainActor
final class MobileGroupChatStateTests: XCTestCase {
    private func start(_ f: MobileStateFixture) async throws {
        await f.wire.queue("/mobile/v1/capabilities", .init(json: mobileCapabilities.replacingOccurrences(of: "\"roundtable\":false", with: "\"roundtable\":true,\"group_chat\":true")))
        if f.model.connected { await f.model.refreshCapabilities() } else { await f.model.start() }
        await f.wire.queue("/mobile/v1/roundtables", .init(json: groupListJSON))
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: try groupJSON()))
        await f.model.openRoundtables()
    }
    func testConfirmedMissingGroupFallsBackWithoutDiscardingItsDraftOrReadingState() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        f.model.setDraft("roundtable:group-one", text: "First retained draft")
        f.model.setDraft("roundtable:group-two", text: "Second retained draft")
        f.model.saveRoundtableReadingState(selected: ["message-one"], anchor: "message-one")
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(status: 404, json: "{\"detail\":\"roundtable_not_found\"}"))
        await f.model.refreshRoundtable()
        XCTAssertEqual(f.model.unavailableRoundtableIDs, ["group-one"])
        XCTAssertEqual(f.model.roundtableID, "group-two")
        XCTAssertEqual(f.model.preferredRoundtableSelection, "group-two")
        let retained = try f.store.load(f.credential)
        XCTAssertEqual(retained.roundtableSelection, "group-two")
        XCTAssertEqual(retained.drafts["roundtable:group-one"], "First retained draft")
        XCTAssertEqual(retained.drafts["roundtable:group-two"], "Second retained draft")
        XCTAssertEqual(retained.roundtableReading?["group-one"]?.selectedIDs, ["message-one"])
        let restarted = MobileClientModel(credentials: f.credentials, persistence: f.store, transport: f.wire)
        await restarted.start()
        await f.wire.queue("/mobile/v1/roundtables", .init(json: groupListJSON))
        await f.wire.queue("/mobile/v1/roundtables/group-two", .init(json: try groupJSON(id: "group-two")))
        await restarted.openRoundtables()
        XCTAssertEqual(restarted.roundtableID, "group-two")
        XCTAssertEqual(restarted.draft("roundtable:group-one"), "First retained draft")
    }
    func testConfirmedMissingLastGroupLeavesEmptySelectionAndKeepsItsDraft() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        f.model.roundtableList?.items.removeAll { $0.id == "group-two" }
        f.model.setDraft("roundtable:group-one", text: "Recoverable draft")
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(status: 404, json: "{}"))
        await f.model.refreshRoundtable()
        XCTAssertNil(f.model.roundtableID); XCTAssertNil(f.model.preferredRoundtableSelection)
        XCTAssertTrue(f.model.roundtableLoaded)
        XCTAssertEqual(f.model.local?.roundtableSelection, "")
        XCTAssertEqual(f.model.draft("roundtable:group-one"), "Recoverable draft")
        let requests = await f.wire.requests
        XCTAssertFalse(requests.contains { $0.httpMethod == "POST" })
    }
    func testMissingListPageTransportAndMissingCacheDoNotDeclareGroupUnavailable() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        let page = "{\"items\":[],\"current_id\":null,\"truncated\":true,\"next_cursor\":\"next\"}"
        await f.wire.queue("/mobile/v1/roundtables", .init(json: page))
        await f.model.openRoundtables(refreshSelection: false)
        XCTAssertEqual(f.model.preferredRoundtableSelection, "group-one")
        XCTAssertTrue(f.model.unavailableRoundtableIDs.isEmpty)
        f.model.invalidateRoundtable()
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: "", timeout: true))
        await f.model.refreshRoundtable()
        XCTAssertNil(f.model.roundtable); XCTAssertTrue(f.model.roundtableOffline)
        XCTAssertEqual(f.model.roundtableID, "group-one")
        XCTAssertEqual(f.model.preferredRoundtableSelection, "group-one")
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(status: 503, json: "{}"))
        await f.model.refreshRoundtable()
        XCTAssertEqual(f.model.roundtableID, "group-one")
        XCTAssertTrue(f.model.unavailableRoundtableIDs.isEmpty)
    }
    func testOldPageOrStaleGroup404CannotClearCurrentGroup() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: try roundtableJSON(id: "group-one", cursor: "next")))
        await f.model.refreshRoundtable()
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(status: 404, json: "{}"))
        await f.model.refreshRoundtable(older: true)
        XCTAssertEqual(f.model.roundtableID, "group-one")
        XCTAssertTrue(f.model.unavailableRoundtableIDs.isEmpty)
        await f.wire.hold("/mobile/v1/roundtables/group-one")
        let original = Task { await f.model.refreshRoundtable() }
        for _ in 0..<100 {
            if await f.wire.isHeld { break }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        let held = await f.wire.isHeld; XCTAssertTrue(held)
        await f.wire.queue("/mobile/v1/roundtables/group-two", .init(json: try groupJSON(id: "group-two")))
        await f.model.selectRoundtable("group-two")
        await f.wire.release(.init(status: 404, json: "{}")); await original.value
        XCTAssertEqual(f.model.roundtableID, "group-two")
        XCTAssertEqual(f.model.roundtable?.conversationId, "group-two")
        XCTAssertTrue(f.model.unavailableRoundtableIDs.isEmpty)
    }
    func testEmptyGroupCreationIsExplicitAndCapabilityGatedWithoutCopyingPrivateDraft() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; await f.model.start()
        let denied = await f.model.createGroup(personas: ["future_self", "blunt_coach"])
        XCTAssertNil(denied)
        let deniedRequests = await f.wire.requests
        XCTAssertFalse(deniedRequests.contains { $0.httpMethod == "POST" })
        try await start(f)
        f.model.setDraft("mentor:future_self", text: "Unshared private text")
        f.model.saveGroupCreationSelection(["future_self", "blunt_coach"])
        await f.wire.queue("/mobile/v1/roundtables", .init(json: "{\"operation_id\":\"$OP\",\"kind\":\"roundtable_create\",\"state\":\"completed\",\"result\":{\"conversation_id\":\"new-group\"}}"))
        await f.wire.queue("/mobile/v1/roundtables", .init(json: groupListJSON))
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: try groupJSON()))
        let created = await f.model.createGroup(personas: ["future_self", "blunt_coach"])
        XCTAssertEqual(created, "new-group")
        XCTAssertEqual(f.model.draft("mentor:future_self"), "Unshared private text")
        let requests = await f.wire.requests
        let post = try XCTUnwrap(requests.last(where: { $0.httpMethod == "POST" }))
        let body = try JSONSerialization.jsonObject(with: post.httpBody!) as! [String: Any]
        XCTAssertEqual(body["chat_kind"] as? String, "group"); XCTAssertEqual(body["text"] as? String, "")
        XCTAssertEqual(body["personas"] as? [String], ["future_self", "blunt_coach"])
        XCTAssertNil(body["background"]); XCTAssertNil(body["artifact_ids"])
    }
    func testMentionTargetsOnlyMembersAndKeepsAudienceWhileOrdinaryMessagesTargetAll() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        XCTAssertEqual(f.model.groupResponders("@未来的我 你怎么看？", members: ["future_self", "blunt_coach"]), ["future_self"])
        XCTAssertEqual(f.model.groupResponders("＠直率教练，给我一个行动。", members: ["future_self", "blunt_coach"]), ["blunt_coach"])
        XCTAssertEqual(f.model.groupResponders("@王阳明 你好", members: ["future_self", "blunt_coach"]), [])
        f.model.setDraft("roundtable:group-one", text: "@未来的我 你怎么看？")
        await f.wire.queue("/mobile/v1/roundtables/group-one/messages", .init(json: "{\"operation_id\":\"$OP\",\"kind\":\"roundtable_message\",\"state\":\"completed\",\"result\":{\"conversation_id\":\"group-one\"}}"))
        await f.wire.queue("/mobile/v1/roundtables", .init(json: groupListJSON))
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: try groupJSON()))
        await f.model.sendRoundtable()
        let requests = await f.wire.requests
        let post = try XCTUnwrap(requests.last(where: { $0.httpMethod == "POST" }))
        let body = try JSONSerialization.jsonObject(with: post.httpBody!) as! [String: Any]
        XCTAssertEqual(body["reply_personas"] as? [String], ["future_self"])
        XCTAssertEqual(f.model.roundtable?.audience, ["future_self", "blunt_coach"])
        XCTAssertNil(body["personas"])
    }
    func testLateSendCannotSwitchGroupOrMoveDraftAndReadingState() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        f.model.setDraft("roundtable:group-one", text: "Original first group message")
        f.model.saveRoundtableReadingState(selected: ["message-one"], anchor: "message-one")
        await f.wire.hold("/mobile/v1/roundtables/group-one/messages")
        let send = Task { await f.model.sendRoundtable() }
        for _ in 0..<100 { if await f.wire.isHeld { break }; try await Task.sleep(nanoseconds: 10_000_000) }
        let held = await f.wire.isHeld
        XCTAssertTrue(held)
        await f.wire.queue("/mobile/v1/roundtables/group-two", .init(json: try groupJSON(id: "group-two", title: "Another group")))
        await f.model.selectRoundtable("group-two")
        f.model.setDraft("roundtable:group-two", text: "Second group draft")
        f.model.saveRoundtableReadingState(selected: [], anchor: "second-anchor")
        await f.wire.queue("/mobile/v1/roundtables", .init(json: groupListJSON))
        await f.wire.queue("/mobile/v1/roundtables/group-two", .init(json: try groupJSON(id: "group-two", title: "Another group")))
        await f.wire.release(.init(json: "{\"operation_id\":\"$OP\",\"kind\":\"roundtable_message\",\"state\":\"completed\",\"result\":{\"conversation_id\":\"group-one\"}}"))
        await send.value
        XCTAssertEqual(f.model.roundtableID, "group-two")
        XCTAssertEqual(f.model.draft(f.model.roundtableDraftKey), "Second group draft")
        XCTAssertEqual(f.model.roundtableReadingState?.anchor, "second-anchor")
        XCTAssertEqual(f.model.local?.roundtableReading?["group-one"]?.anchor, "message-one")
        XCTAssertEqual(f.model.draft("roundtable:group-one"), "")
    }
    func testUnknownCreationRetainsChoicesAndDoesNotCreateAgain() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        f.model.saveGroupCreationSelection(["future_self", "blunt_coach"])
        await f.wire.queue("/mobile/v1/roundtables", .init(json: "", timeout: true))
        await f.wire.queue("/mobile/v1/roundtables", .init(json: groupListJSON))
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: try groupJSON()))
        let result = await f.model.createGroup(personas: ["future_self", "blunt_coach"])
        XCTAssertNil(result); XCTAssertTrue(f.model.hasUnresolved(f.model.groupCreationTarget))
        let duplicate = await f.model.createGroup(personas: ["future_self", "blunt_coach"])
        XCTAssertNil(duplicate)
        let requests = await f.wire.requests
        XCTAssertEqual(requests.filter { $0.httpMethod == "POST" }.count, 1)
        XCTAssertEqual(try f.store.load(f.credential).groupCreationPersonas, ["future_self", "blunt_coach"])
    }
    func testGroupCacheAndDraftsStaySeparateAndPrivacyRevocationRemovesAllCachedGroups() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        f.model.setDraft("roundtable:group-one", text: "First draft")
        await f.wire.queue("/mobile/v1/roundtables/group-two", .init(json: try groupJSON(id: "group-two")))
        await f.model.selectRoundtable("group-two")
        f.model.setDraft("roundtable:group-two", text: "Second draft")
        f.model.saveMentorReadingAnchor("future_self", anchor: "private-anchor")
        XCTAssertNotNil(try f.store.roundtableCache.load(f.credential, conversationID: "group-one"))
        XCTAssertNotNil(try f.store.roundtableCache.load(f.credential, conversationID: "group-two"))
        XCTAssertEqual(try f.store.roundtableCache.cachedList(f.credential)?.items.count, 2)
        let restarted = MobileClientModel(credentials: f.credentials, persistence: f.store, transport: f.wire)
        await restarted.start()
        XCTAssertEqual(restarted.draft("roundtable:group-one"), "First draft")
        XCTAssertEqual(restarted.draft("roundtable:group-two"), "Second draft")
        XCTAssertEqual(restarted.mentorReadingAnchor("future_self"), "private-anchor")
        await f.model.refreshPrivacy()
        XCTAssertNil(try f.store.roundtableCache.load(f.credential, conversationID: "group-one"))
        XCTAssertNil(try f.store.roundtableCache.load(f.credential, conversationID: "group-two"))
    }
    func testGroupDecodeRejectsHiddenHostOrMessagesFromNonmembers() throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let valid = try decoder.decode(MobileRoundtable.self, from: Data(groupJSON().utf8))
        XCTAssertNoThrow(try valid.validated())
        for field in ["audience", "actor"] {
            var body = try JSONSerialization.jsonObject(with: Data(groupJSON().utf8)) as! [String: Any]
            if field == "audience" { body["audience"] = ["host", "future_self", "blunt_coach"] }
            else { var messages = body["messages"] as! [[String: Any]]; messages[0]["actor"] = "host"; body["messages"] = messages }
            let value = try decoder.decode(MobileRoundtable.self, from: JSONSerialization.data(withJSONObject: body))
            XCTAssertThrowsError(try value.validated())
        }
    }
    func testOfflineListShowsOtherCachedGroupsWhenLastSelectedGroupHasNoValidCache() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        f.model.saveRoundtableSelection("missing-group")
        f.model.setDraft("roundtable:missing-group", text: "Uncached group draft")
        let restarted = MobileClientModel(credentials: f.credentials, persistence: f.store, transport: f.wire)
        await restarted.start()
        await f.wire.queue("/mobile/v1/roundtables", .init(json: "", timeout: true))
        await restarted.openRoundtables()
        XCTAssertEqual(restarted.roundtableList?.items.map(\.id), ["group-one"])
        XCTAssertEqual(restarted.roundtableID, "missing-group")
        XCTAssertEqual(restarted.draft(restarted.roundtableDraftKey), "Uncached group draft")
        XCTAssertNil(restarted.roundtable); XCTAssertTrue(restarted.roundtableOffline)
    }
}

extension MobileGroupChatStateTests {
    func testExpectedConversationRejectsActionsFromAStaleGroupView() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        f.model.setDraft("roundtable:group-one", text: "Original group draft")
        f.model.saveRoundtableReadingState(selected: ["message-one"], anchor: "message-one", conversationID: "group-one")
        await f.wire.queue("/mobile/v1/roundtables/group-two", .init(json: try groupJSON(id: "group-two")))
        await f.model.selectRoundtable("group-two")
        f.model.setDraft("roundtable:group-two", text: "Current group draft")
        f.model.saveRoundtableReadingState(selected: [], anchor: "second-anchor", conversationID: "group-two")
        // A disappearing inline view can still emit viewport or selection changes.
        f.model.saveRoundtableReadingState(selected: ["stale-selection"], anchor: "stale-anchor", conversationID: "group-one")
        await f.model.sendRoundtable(conversationID: "group-one")
        await f.model.roundtableCommand("stop", conversationID: "group-one")
        await f.model.previewRoundtableResult(["message-one"], conversationID: "group-one")
        let requests = await f.wire.requests
        XCTAssertFalse(requests.contains { $0.httpMethod == "POST" })
        XCTAssertEqual(f.model.draft("roundtable:group-two"), "Current group draft")
        XCTAssertEqual(f.model.draft("roundtable:group-one"), "Original group draft")
        XCTAssertEqual(f.model.roundtableID, "group-two")
        XCTAssertEqual(f.model.local?.roundtableReading?["group-one"]?.selectedIDs, ["message-one"])
        XCTAssertEqual(f.model.local?.roundtableReading?["group-one"]?.anchor, "message-one")
        XCTAssertEqual(f.model.roundtableReadingState?.selectedIDs, [])
        XCTAssertEqual(f.model.roundtableReadingState?.anchor, "second-anchor")
        let restarted = MobileClientModel(credentials: f.credentials, persistence: f.store, transport: f.wire)
        await restarted.start()
        XCTAssertEqual(restarted.local?.roundtableReading?["group-one"]?.anchor, "message-one")
        XCTAssertEqual(restarted.local?.roundtableReading?["group-two"]?.anchor, "second-anchor")
    }
    func testInterruptedGroupCannotSendANewMessageAndCanExplicitlyResume() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        var interrupted = try JSONSerialization.jsonObject(with: Data(groupJSON().utf8)) as! [String: Any]
        interrupted["status"] = "interrupted"
        let json = String(decoding: try JSONSerialization.data(withJSONObject: interrupted), as: UTF8.self)
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: json))
        await f.model.refreshRoundtable()
        f.model.setDraft("roundtable:group-one", text: "Retained next message")
        await f.model.sendRoundtable(conversationID: "group-one")
        let beforeResume = await f.wire.requests
        XCTAssertFalse(beforeResume.contains { $0.httpMethod == "POST" })
        await f.wire.queue("/mobile/v1/roundtables/group-one/commands", .init(json: "{\"operation_id\":\"$OP\",\"kind\":\"roundtable_command\",\"state\":\"completed\",\"result\":{\"conversation_id\":\"group-one\"}}"))
        await f.wire.queue("/mobile/v1/roundtables", .init(json: groupListJSON))
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: try groupJSON()))
        await f.model.roundtableCommand("resume", conversationID: "group-one")
        XCTAssertEqual(f.model.roundtable?.status, "completed")
        XCTAssertEqual(f.model.draft("roundtable:group-one"), "Retained next message")
        let requests = await f.wire.requests
        XCTAssertEqual(requests.filter { $0.httpMethod == "POST" }.count, 1)
    }
    func testPrivateReplyCannotInvalidateTheGroupOpenedWhileItWasPending() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        await f.model.refreshConversation("future_self")
        f.model.setDraft("mentor:future_self", text: "Private pending question")
        await f.wire.hold("/mobile/v1/mentors/future_self/messages")
        let send = Task { await f.model.send("future_self") }
        try await waitForHeldRequest(f.wire)
        await f.wire.queue("/mobile/v1/roundtables/group-two", .init(json: try groupJSON(id: "group-two")))
        await f.model.selectRoundtable("group-two")
        f.model.setDraft("roundtable:group-two", text: "Group draft")
        await f.wire.release(.init(json: "{\"operation_id\":\"$OP\",\"kind\":\"mentor_message\",\"state\":\"completed\",\"result\":{}}"))
        await send.value
        XCTAssertEqual(f.model.roundtableID, "group-two")
        XCTAssertEqual(f.model.roundtable?.conversationId, "group-two")
        XCTAssertEqual(f.model.draft("roundtable:group-two"), "Group draft")
    }
    func testGroupReplyCannotInvalidateThePrivateChatOpenedWhileItWasPending() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        f.model.setDraft("roundtable:group-one", text: "Pending group question")
        await f.wire.hold("/mobile/v1/roundtables/group-one/messages")
        let send = Task { await f.model.sendRoundtable(conversationID: "group-one") }
        try await waitForHeldRequest(f.wire)
        await f.model.refreshConversation("future_self")
        f.model.setDraft("mentor:future_self", text: "Private unsent text")
        XCTAssertNotNil(f.model.conversations["future_self"])
        await f.wire.queue("/mobile/v1/roundtables", .init(json: groupListJSON))
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: try groupJSON()))
        await f.wire.release(.init(json: "{\"operation_id\":\"$OP\",\"kind\":\"roundtable_message\",\"state\":\"completed\",\"result\":{\"conversation_id\":\"group-one\"}}"))
        await send.value
        XCTAssertNotNil(f.model.conversations["future_self"])
        XCTAssertEqual(f.model.draft("mentor:future_self"), "Private unsent text")
    }
    private func waitForHeldRequest(_ wire: MobileStateWire) async throws {
        for _ in 0..<100 {
            if await wire.isHeld { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("The request must be held before switching chats")
    }
}

extension MobileGroupChatStateTests {
    private func readingPage(_ identifier: String, version: String, cursor: String? = nil, text: String = "Current version text") throws -> String {
        var body = try JSONSerialization.jsonObject(with: Data(groupJSON().utf8)) as! [String: Any]
        var items = body["messages"] as! [[String: Any]]
        items[0]["id"] = identifier; items[0]["text"] = text
        body["messages"] = items; body["history_version"] = version
        body["next_cursor"] = cursor as Any? ?? NSNull()
        return String(decoding: try JSONSerialization.data(withJSONObject: body), as: UTF8.self)
    }
    func testReadingAnchorRepagesCurrentVersionWithoutKeepingStaleOldText() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        f.model.saveRoundtableReadingState(selected: [], anchor: "older-anchor")
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: try readingPage("latest-one", version: "new", cursor: "older")))
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: try readingPage("older-anchor", version: "new", text: "Version one")))
        await f.model.refreshRoundtable()
        XCTAssertEqual(f.model.roundtable?.messages.map(\.id), ["older-anchor", "latest-one"])
        XCTAssertNil(f.model.roundtableReadingNotice)
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: try readingPage("latest-two", version: "next", cursor: "older-next")))
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: try readingPage("older-anchor", version: "next", text: "Version two")))
        await f.model.refreshRoundtable()
        XCTAssertEqual(f.model.roundtable?.messages.first?.text, "Version two")
        XCTAssertFalse(f.model.roundtable?.messages.contains(where: { $0.text == "Version one" }) ?? true)
        XCTAssertEqual(f.model.roundtableReadingState?.anchor, "older-anchor")
    }
    func testReadingRestorationCanContinueAnotherBatchAndNilViewportDoesNotEraseAnchor() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        f.model.saveRoundtableReadingState(selected: [], anchor: "deep-anchor")
        f.model.saveRoundtableReadingState(selected: [], anchor: nil)
        XCTAssertEqual(f.model.roundtableReadingState?.anchor, "deep-anchor")
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: try readingPage("latest", version: "one", cursor: "page-one")))
        await f.model.refreshRoundtable(restoreReading: false)
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: try readingPage("middle", version: "one", cursor: "page-two")))
        await f.model.restoreRoundtableReadingPosition("group-one", maxPages: 1)
        XCTAssertNotNil(f.model.roundtableReadingNotice); XCTAssertEqual(f.model.roundtable?.nextCursor, "page-two")
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: try readingPage("deep-anchor", version: "one")))
        await f.model.restoreRoundtableReadingPosition("group-one", maxPages: 1)
        XCTAssertNil(f.model.roundtableReadingNotice)
        XCTAssertEqual(f.model.roundtable?.messages.map(\.id), ["deep-anchor", "middle", "latest"])
    }
    func testUnseenRepliesRequireActualMessageViewingAndPersistWithoutRenameNoise() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        let key = "roundtable:group-one"
        XCTAssertTrue(f.model.hasUnreadReply(key))
        f.model.markReplyRead(key, visibleMessageID: "missing")
        XCTAssertTrue(f.model.hasUnreadReply(key))
        f.model.markReplyRead(key, visibleMessageID: "message-one")
        XCTAssertFalse(f.model.hasUnreadReply(key))
        f.model.recordLatestReplies([key: "message-one"])
        XCTAssertFalse(f.model.hasUnreadReply(key), "Renaming or refetching the same reply must not mark it unread")
        f.model.recordLatestReplies([key: "new-reply"])
        f.model.markReplyRead(key, visibleMessageID: "message-one")
        XCTAssertTrue(f.model.hasUnreadReply(key))
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: try readingPage("new-reply", version: "next")))
        await f.model.refreshRoundtable()
        f.model.markReplyRead(key, visibleMessageID: "new-reply")
        XCTAssertFalse(f.model.hasUnreadReply(key))
        let restarted = MobileClientModel(credentials: f.credentials, persistence: f.store, transport: f.wire)
        await restarted.start()
        XCTAssertFalse(restarted.hasUnreadReply(key))
        XCTAssertEqual(restarted.local?.lastReadReplies?[key], "new-reply")
    }
    func testInboxOnlyReadsMetadataAndReportsPrivateAndGroupReplies() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        var caps = try JSONSerialization.jsonObject(with: Data(mobileCapabilities.utf8)) as! [String: Any]
        var mentors = caps["mentors"] as! [[String: Any]]
        for index in mentors.indices { mentors[index]["last_reply_id"] = mentors[index]["id"] as? String == "future_self" ? "private-reply" : "" }
        caps["mentors"] = mentors; caps["capabilities"] = ["mentors": true, "roundtable": true, "group_chat": true]
        var list = try JSONSerialization.jsonObject(with: Data(groupListJSON.utf8)) as! [String: Any]
        var rooms = list["items"] as! [[String: Any]]; rooms[0]["last_reply_id"] = "group-reply"; list["items"] = rooms
        await f.wire.queue("/mobile/v1/capabilities", .init(json: String(decoding: try JSONSerialization.data(withJSONObject: caps), as: UTF8.self)))
        await f.wire.queue("/mobile/v1/roundtables", .init(json: String(decoding: try JSONSerialization.data(withJSONObject: list), as: UTF8.self)))
        let before = (await f.wire.requests).count
        await f.model.refreshChatInbox()
        let requests = Array((await f.wire.requests).dropFirst(before))
        XCTAssertEqual(requests.map { $0.url!.path }, ["/mobile/v1/capabilities", "/mobile/v1/roundtables"])
        XCTAssertTrue(requests.allSatisfy { $0.httpMethod == "GET" })
        XCTAssertTrue(f.model.hasUnreadReply("mentor:future_self"))
        XCTAssertTrue(f.model.hasUnreadReply("roundtable:group-one"))
    }
    func testCrossRunResultSelectionPreservesOriginalRunAndCannotSubmitMixedArtifacts() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        var body = try JSONSerialization.jsonObject(with: Data(groupJSON().utf8)) as! [String: Any]
        var messages = body["messages"] as! [[String: Any]], second = messages[0]
        second["id"] = "second-run-result"; second["run_id"] = "second-run"
        messages.append(second); body["messages"] = messages
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: String(decoding: try JSONSerialization.data(withJSONObject: body), as: UTF8.self)))
        await f.model.refreshRoundtable()
        XCTAssertFalse(f.model.canSaveGroupResults(["message-one", "second-run-result"]))
        XCTAssertEqual(f.model.groupResultSelection(toggling: "second-run-result", selected: ["message-one"]), ["message-one"])
        XCTAssertTrue(f.model.canSaveGroupResults(["second-run-result"]))
        let before = (await f.wire.requests).filter { $0.httpMethod == "POST" }.count
        await f.model.previewRoundtableResult(["message-one", "second-run-result"], conversationID: "group-one")
        let after = (await f.wire.requests).filter { $0.httpMethod == "POST" }.count
        XCTAssertEqual(before, after)
    }
    func testSixthResultCannotReplaceExistingFiveSelections() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        var body = try JSONSerialization.jsonObject(with: Data(groupJSON().utf8)) as! [String: Any]
        let original = (body["messages"] as! [[String: Any]])[0]
        body["messages"] = (1...6).map { index -> [String: Any] in
            var message = original; message["id"] = "same-run-\(index)"; return message
        }
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: String(decoding: try JSONSerialization.data(withJSONObject: body), as: UTF8.self)))
        await f.model.refreshRoundtable()
        let selected = Set((1...5).map { "same-run-\($0)" })
        XCTAssertTrue(f.model.canSaveGroupResults(Array(selected)))
        XCTAssertEqual(f.model.groupResultSelection(toggling: "same-run-6", selected: selected), selected)
        XCTAssertFalse(f.model.canSaveGroupResults(Array(selected.union(["same-run-6"]))))
        XCTAssertEqual(f.model.groupResultSelection(toggling: "same-run-1", selected: selected), selected.subtracting(["same-run-1"]))
    }
    func testTypedMentionsNeedNoSpaceAndOnlyActualMembersCanRespond() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        XCTAssertEqual(f.model.groupResponders("@王阳明你怎么看", members: ["wang_yangming", "future_self"]), ["wang_yangming"])
        XCTAssertEqual(f.model.groupResponders("@王阳明（补充条件）", members: ["wang_yangming", "future_self"]), ["wang_yangming"])
        XCTAssertEqual(f.model.groupResponders("@王阳明你怎么看", members: ["blunt_coach", "future_self"]), [])
    }
}

extension MobileGroupChatStateTests {
    private func privateReadingPage(_ prefix: String, version: String, cursor: String? = nil) throws -> String {
        let identifiers = cursor == nil ? ["private-anchor"] : (0..<200).map { prefix + String($0) }
        let messages = identifiers.map { ["id": $0, "actor": "future_self", "text": version + " text", "created_at": 1, "source_ids": []] as [String: Any] }
        let value: [String: Any] = ["conversation_id": "private-one", "status": "completed", "revision": 1,
            "messages": messages, "history_version": version, "next_cursor": cursor as Any? ?? NSNull(), "history_has_more": cursor != nil]
        return String(decoding: try JSONSerialization.data(withJSONObject: value), as: UTF8.self)
    }
    func testPrivateAnchorBeyondLatestTwoHundredRepagesAfterHistoryChanges() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        f.model.saveMentorReadingAnchor("future_self", anchor: "private-anchor")
        f.model.saveMentorReadingAnchor("future_self", anchor: nil)
        await f.wire.queue("/mobile/v1/mentors/future_self/conversation", .init(json: try privateReadingPage("latest-", version: "old", cursor: "older")))
        await f.wire.queue("/mobile/v1/mentors/future_self/conversation", .init(json: try privateReadingPage("", version: "old")))
        await f.model.refreshConversation("future_self")
        XCTAssertEqual(f.model.messages("future_self").count, 201)
        XCTAssertNil(f.model.mentorReadingNotices["future_self"])
        await f.wire.queue("/mobile/v1/mentors/future_self/conversation", .init(json: try privateReadingPage("changed-", version: "new", cursor: "older-new")))
        await f.wire.queue("/mobile/v1/mentors/future_self/conversation", .init(json: try privateReadingPage("", version: "new")))
        await f.model.refreshConversation("future_self")
        XCTAssertEqual(f.model.messages("future_self").count, 201)
        XCTAssertEqual(f.model.messages("future_self").first?.text, "new text")
        XCTAssertFalse(f.model.messages("future_self").contains { $0.id == "latest-0" })
        XCTAssertEqual(f.model.mentorReadingAnchor("future_self"), "private-anchor")
    }
    func testEndingUnknownTurnUsesIndependentReceiptAndPreservesNextMessage() async throws {
        let f = MobileStateFixture(); defer { f.cleanup() }; try await start(f)
        f.model.setDraft("roundtable:group-one", text: "Next message retained")
        await f.wire.queue("/mobile/v1/roundtables/group-one/commands", .init(json: "{\"operation_id\":\"$OP\",\"kind\":\"roundtable_command\",\"state\":\"completed\",\"result\":{\"conversation_id\":\"group-one\"}}"))
        await f.wire.queue("/mobile/v1/roundtables", .init(json: groupListJSON))
        var ended = try JSONSerialization.jsonObject(with: Data(groupJSON().utf8)) as! [String: Any]
        ended["turn_ended"] = true; ended["status"] = "stopped"
        await f.wire.queue("/mobile/v1/roundtables/group-one", .init(json: String(decoding: try JSONSerialization.data(withJSONObject: ended), as: UTF8.self)))
        await f.model.roundtableCommand("end_turn", conversationID: "group-one")
        XCTAssertEqual(f.model.operation("roundtable-end:group-one")?.state, "completed")
        XCTAssertEqual(f.model.roundtable?.turnEnded, true)
        XCTAssertEqual(f.model.roundtable?.status, "stopped")
        XCTAssertEqual(f.model.draft("roundtable:group-one"), "Next message retained")
    }
}
