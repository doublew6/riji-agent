import XCTest
import RijiCore

#if !DEBUG
#error("The physical probe requires the Debug host namespace isolation.")
#endif

final class RijiICloudProbeTests: XCTestCase {
    func testPhysicalProbe() async throws {
        let environment = ProcessInfo.processInfo.environment
        let requested: UUID?
        do { requested = try ProbeRunRequest.parse(environment["RIJI_ICLOUD_PROBE_RUN"]) }
        catch { XCTFail("Invalid physical iCloud probe run UUID."); return }
        guard let run = requested else {
            throw XCTSkip("Physical iCloud probe not requested; explicit run UUID required.")
        }
        #if targetEnvironment(simulator)
        throw XCTSkip("Simulator compilation only; physical iCloud probe was not executed.")
        #else
        guard environment["RIJI_TEST_CONTAINER"] == ProbeFixture.namespace,
              environment["RIJI_TEST_ORIGINAL_VAULT"] == "0" else {
            XCTFail("Probe namespace was not injected before host launch."); return
        }
        guard let phase = environment["RIJI_ICLOUD_PROBE_PHASE"], ["initial", "verify"].contains(phase) else {
            XCTFail("Explicit initial or verify phase required."); return
        }
        let fixture = ProbeFixture(run: run)
        let session: ProbePhoneSession
        do { session = try ProbePhoneSession(fixture: fixture) }
        catch { XCTFail("Probe state unavailable."); return }
        do {
            if phase == "initial" {
                try await session.prepareInitial()
                let picker = await ProbeFolderPicker()
                let root = try await picker.select(folderName: fixture.folderName)
                try await session.initial(root: root)
            } else { try await session.verify() }
            attach(try await session.record(phase: phase, outcome: "passed"), phase: phase)
        } catch {
            let code = ProbeFailure.code(error)
            if let data = try? await session.record(phase: phase, outcome: "failed", failure: code) { attach(data, phase: phase) }
            XCTFail("Physical iCloud probe failed: \(code). See private JSON evidence; original writing remains read-only.")
        }
        #endif
    }

    private func attach(_ data: Data, phase: String) {
        let attachment = XCTAttachment(data: data, uniformTypeIdentifier: "public.json")
        attachment.name = "synthetic-icloud-probe-\(phase)"; attachment.lifetime = .keepAlways
        add(attachment)
    }
}

/// Pure local guards: these tests never present a picker or open a cloud URL.
final class ProbePhoneGuardTests: XCTestCase {
    private func state() throws -> (ProbeFixture, ProbePhoneState) {
        let fixture = ProbeFixture(run: UUID()), identity = String(repeating: "a", count: 64)
        let paths = [ProbeFixture.savedPath, ProbeFixture.modifiedPath, ProbeFixture.deletedPath, ProbeFixture.createdPath]
        let edits = try paths.enumerated().map { index, path in
            BodyEdit(day: try JournalDay(URL(fileURLWithPath: path).deletingPathExtension().lastPathComponent),
                     before: fixture.files(.pristine)[path],
                     content: fixture.document(index == 0 ? "Phone saved day" : "Retained phone draft \(index)"),
                     sourceID: identity + ":" + path)
        }
        return (fixture, ProbePhoneState(run: fixture.run, process: UUID(), identity: identity, saved: edits[0], drafts: Array(edits.dropFirst())))
    }

    private func initialChecks() -> [String: String] {
        Dictionary(uniqueKeysWithValues: ["physicalDevice", "iCloudMetadata", "read", "localSave", "idempotency", "retainedDrafts", "durableState"].map { ($0, "passed") })
    }

    func testStateRequiresCompleteOrderedUniqueSyntheticScenarios() throws {
        let (fixture, state) = try state()
        try state.validate(fixture: fixture)
        for drafts in [[], Array(state.drafts.prefix(2)), Array(state.drafts.reversed()), state.drafts + [state.drafts[0]]] {
            let changed = ProbePhoneState(run: state.run, process: state.process, identity: state.identity, saved: state.saved, drafts: drafts)
            XCTAssertThrowsError(try changed.validate(fixture: fixture))
        }
        let first = state.drafts[0]
        var duplicate = try JSONSerialization.jsonObject(with: JSONEncoder().encode(first)) as! [String: Any]
        duplicate["id"] = state.saved.id.uuidString
        let duplicated = try JSONDecoder().decode(BodyEdit.self, from: JSONSerialization.data(withJSONObject: duplicate))
        let invalidEdits = [duplicated,
                            BodyEdit(day: first.day, before: first.before, content: Data("Unknown".utf8), sourceID: first.sourceID),
                            BodyEdit(day: first.day, before: nil, content: first.content, sourceID: first.sourceID),
                            BodyEdit(day: first.day, before: first.before, content: first.content, sourceID: "other")]
        for edit in invalidEdits {
            let changed = ProbePhoneState(run: state.run, process: state.process, identity: state.identity, saved: state.saved,
                                          drafts: [edit] + Array(state.drafts.dropFirst()))
            XCTAssertThrowsError(try changed.validate(fixture: fixture))
        }
        let wrongSaved = ProbePhoneState(run: state.run, process: state.process, identity: state.identity, saved: first, drafts: state.drafts)
        XCTAssertThrowsError(try wrongSaved.validate(fixture: fixture))
    }

    func testPassedEvidenceRequiresEveryPhaseCheck() throws {
        let initial = initialChecks()
        try ProbePhoneSession.requireCompletedChecks(initial, phase: "initial")
        for key in initial.keys {
            var missing = initial; missing[key] = "pending"
            XCTAssertThrowsError(try ProbePhoneSession.requireCompletedChecks(missing, phase: "initial"))
        }
        var verified = initial
        verified["bookmarkNewProcess"] = "passed"; verified["macToPhonePropagation"] = "passed"
        verified["phoneToMacPropagation"] = "passed-via-mac-ready-receipt"
        for key in ["staleModify", "staleDelete", "staleCreate"] { verified[key] = "refused-changedSource" }
        try ProbePhoneSession.requireCompletedChecks(verified, phase: "verify")
        for key in verified.keys {
            var missing = verified; missing[key] = "pending"
            XCTAssertThrowsError(try ProbePhoneSession.requireCompletedChecks(missing, phase: "verify"))
        }
        verified["staleDelete"] = "refused-other-error"
        XCTAssertThrowsError(try ProbePhoneSession.requireCompletedChecks(verified, phase: "verify"))
    }

    func testRepeatedInitialFailurePreservesSuccessfulEvidence() async throws {
        let (fixture, _) = try state()
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("ProbeEvidence-\(UUID())")
        defer { try? FileManager.default.removeItem(at: base) }
        let session = try ProbePhoneSession(fixture: fixture, localTestDirectory: base)
        var summary = initialChecks(); summary["outcome"] = "passed"; summary["run"] = fixture.run.uuidString; summary["phase"] = "initial"
        let prior = try JSONEncoder().encode(summary), success = base.appendingPathComponent("summary-initial.json")
        try prior.write(to: success, options: .withoutOverwriting)
        for _ in 0..<2 {
            do { try await session.prepareInitial(); XCTFail("Repeated preparation must fail before source access") } catch {}
            try await session.record(phase: "initial", outcome: "failed", failure: "state")
        }
        XCTAssertEqual(try Data(contentsOf: success), prior)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: base.path).filter { $0.hasPrefix("attempt-initial-") }.count, 2)
        XCTAssertFalse(FileManager.default.fileExists(atPath: base.appendingPathComponent("phase.json").path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: base.appendingPathComponent("folder.bookmark").path))
        do { try await session.record(phase: "initial", outcome: "passed"); XCTFail("Unfinished checks cannot publish success") } catch {}
        XCTAssertEqual(try Data(contentsOf: success), prior)
    }

    func testOnlyMissingRunMaySkip() throws {
        XCTAssertNil(try ProbeRunRequest.parse(nil))
        let run = UUID(); XCTAssertEqual(try ProbeRunRequest.parse(run.uuidString), run)
        for value in ["", "not-a-uuid", "../private"] { XCTAssertThrowsError(try ProbeRunRequest.parse(value)) }
    }
}
