import XCTest
import RijiCore
@testable import RijiState

@MainActor
final class NorthStarAnchorModelTests: XCTestCase {
    private func goal(_ name: String = "Synthetic direction", previous: UUID? = nil) -> NorthStarDefinition {
        NorthStarDefinition(previousID: previous, name: name, definition: "Practice every week", unit: "sessions",
                            target: "4", period: "This phase", stage: "Explore", day: JournalDay(date: Date(), timeZone: .current))
    }
    private func directory() -> URL { FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString) }
    private func readers(_ goals: @escaping () async throws -> [NorthStarDefinition]) -> NorthStarReaders {
        NorthStarReaders(source: "local", account: "synthetic", original: false, goals: goals,
                         entries: { _ in [] }, text: { _ in throw NorthStarAnchorError.unavailable })
    }

    func testExplicitChoiceReopensAcrossMentorsAndSupersededGoalRequiresReselection() async throws {
        let directory = directory(); defer { try? FileManager.default.removeItem(at: directory) }
        let store = NorthStarAnchorStore(directory: directory), first = goal("First")
        var second = goal("Second")
        second.measurement = "2 sessions"; second.measurementSource = "Synthetic log through this week"
        var goals = [first, second]
        let model = NorthStarAnchorModel(store: store)
        model.activate(readers { goals }); await model.refresh(); await model.browse()
        XCTAssertNil(model.current); XCTAssertEqual(model.goals.count, 2)
        await model.prepare(second); XCTAssertNil(model.current)
        XCTAssertTrue(model.preview?.display.detail.contains("当前测量：2 sessions") == true)
        XCTAssertTrue(model.preview?.display.detail.contains("测量来源及截止时间：Synthetic log through this week") == true)
        let confirmed = await model.confirm(); XCTAssertTrue(confirmed); XCTAssertEqual(model.current?.title, "Second")
        let reopened = NorthStarAnchorModel(store: store)
        reopened.activate(readers { goals }); await reopened.refresh()
        XCTAssertEqual(reopened.current?.title, "Second")
        goals = [first, goal("Second updated", previous: second.id)]
        await reopened.refresh(); XCTAssertNil(reopened.current); XCTAssertNotNil(reopened.sourceError)
        let persisted = try await store.read(scope: model.scope!); XCTAssertNotNil(persisted)
    }

    func testChangedDefinitionOrReadFailureHidesClaimAndFailedConfirmationKeepsPreview() async throws {
        let directory = directory(); defer { try? FileManager.default.removeItem(at: directory) }
        var value = goal(), fail = false
        let model = NorthStarAnchorModel(store: NorthStarAnchorStore(directory: directory))
        model.activate(readers { if fail { throw NorthStarAnchorError.unavailable }; return [value] })
        await model.prepare(value)
        value.stage = "Changed"
        let stale = await model.confirm(); XCTAssertFalse(stale); XCTAssertNil(model.current); XCTAssertNotNil(model.preview)
        await model.prepare(value)
        let saved = await model.confirm(); XCTAssertTrue(saved)
        fail = true; await model.refresh(); XCTAssertNil(model.current); XCTAssertNotNil(model.sourceError)
    }

    func testFailedPersistenceNeverClaimsAssociationWasSaved() async throws {
        let directory = directory(); defer { try? FileManager.default.removeItem(at: directory) }
        let value = goal(), store = NorthStarAnchorStore(directory: directory, failure: { throw NorthStarAnchorError.storage })
        let model = NorthStarAnchorModel(store: store)
        model.activate(readers { [value] }); await model.prepare(value)
        let result = await model.confirm()
        XCTAssertFalse(result); XCTAssertNil(model.current); XCTAssertNotNil(model.preview)
        XCTAssertEqual(model.error, NorthStarAnchorError.storage.localizedDescription)
        let missing = try await store.read(scope: model.scope!); XCTAssertNil(missing)
    }

    func testLateSourceReadCannotLeakIntoNewSourceOrAccount() async throws {
        let directory = directory(); defer { try? FileManager.default.removeItem(at: directory) }
        let store = NorthStarAnchorStore(directory: directory), value = goal()
        let model = NorthStarAnchorModel(store: store)
        model.activate(readers { [value] }); await model.prepare(value); _ = await model.confirm()
        var continuation: CheckedContinuation<[NorthStarDefinition], Never>?
        model.activate(readers { await withCheckedContinuation { continuation = $0 } })
        let pending = Task { await model.refresh() }
        while continuation == nil { await Task.yield() }
        model.activate(NorthStarReaders(source: "other-vault", account: "other-account", original: false,
                                       goals: { [] }, entries: { _ in [] }, text: { _ in Data() }))
        await model.refresh()
        continuation?.resume(returning: [value]); await pending.value
        XCTAssertNil(model.current); XCTAssertNil(model.error); XCTAssertFalse(model.busy)
        XCTAssertEqual(model.scope, NorthStarAnchor.scope(source: "other-vault", account: "other-account"))
    }

    func testPickerWaitsForRefreshAndReloadsAfterSourceReactivation() async throws {
        let directory = directory(); defer { try? FileManager.default.removeItem(at: directory) }
        let store = NorthStarAnchorStore(directory: directory), value = goal()
        let model = NorthStarAnchorModel(store: store)
        model.activate(readers { [value] }); await model.prepare(value); _ = await model.confirm()
        var continuation: CheckedContinuation<[NorthStarDefinition], Never>?
        var waiting = true
        let source = readers {
            if waiting { return await withCheckedContinuation { continuation = $0 } }
            return [value]
        }
        model.activate(source)
        let refresh = Task { await model.refresh() }
        while continuation == nil { await Task.yield() }
        let picker = Task { await model.browseWhenReady() }
        waiting = false; continuation?.resume(returning: [value])
        await refresh.value; await picker.value
        XCTAssertEqual(model.goals, [value])
        let generation = model.activationID
        model.invalidate(); model.activate(source)
        XCTAssertNotEqual(model.activationID, generation)
        await model.browseWhenReady(); XCTAssertEqual(model.goals, [value])
    }

    func testOriginalFileRequiresExactPreviewAndFreshConfirmationWithoutChangingSource() async throws {
        let directory = directory(); defer { try? FileManager.default.removeItem(at: directory) }
        var bytes = Data("# Synthetic direction\r\n\r\nPractice e\u{301} 🧠\r\n".utf8)
        let entry = VaultEntry(path: ["Goal.md"], kind: .file)
        let model = NorthStarAnchorModel(store: NorthStarAnchorStore(directory: directory))
        model.activate(NorthStarReaders(source: "vault", account: "synthetic", original: true,
            goals: { [] }, entries: { _ in [entry] }, text: { _ in bytes }))
        await model.browse(); await model.open(entry)
        await model.prepare(try XCTUnwrap(model.paragraphs.last)); XCTAssertNil(model.current)
        bytes.append(Data("\n".utf8))
        let stale = await model.confirm(); XCTAssertFalse(stale); XCTAssertNil(model.current)
        await model.open(entry); await model.prepare(try XCTUnwrap(model.paragraphs.last))
        let before = bytes, saved = await model.confirm()
        XCTAssertTrue(saved); XCTAssertEqual(model.current?.title, "Practice e\u{301} 🧠"); XCTAssertEqual(bytes, before)
    }

    func testRefreshPreservesVerifiedDisplayDuringSlowReadAndFailedOperationFeedback() async throws {
        let directory = directory(); defer { try? FileManager.default.removeItem(at: directory) }
        let value = goal(), next = goal("Next"), store = NorthStarAnchorStore(directory: directory)
        let source = readers { [value, next] }
        let anchor = try NorthStarAnchor(scope: source.scope, source: .definition(value.id, hash: NorthStarAnchor.definitionHash(value)))
        try await store.save(anchor)
        let failing = NorthStarAnchorStore(directory: directory, failure: { throw NorthStarAnchorError.storage })
        let model = NorthStarAnchorModel(store: failing)
        var slow = false
        var continuation: CheckedContinuation<[NorthStarDefinition], Never>?
        model.activate(readers {
            if slow { return await withCheckedContinuation { continuation = $0 } }
            return [value, next]
        })
        await model.refresh(); await model.prepare(next)
        let saved = await model.confirm(); XCTAssertFalse(saved)
        let saveError = try XCTUnwrap(model.error)
        await model.refresh()
        XCTAssertEqual(model.current?.title, value.name); XCTAssertEqual(model.error, saveError)
        await model.unlink()
        let unlinkError = try XCTUnwrap(model.error)
        slow = true
        let pending = Task { await model.refresh() }
        while continuation == nil { await Task.yield() }
        XCTAssertEqual(model.current?.title, value.name); XCTAssertEqual(model.error, unlinkError)
        continuation?.resume(returning: [value, next]); await pending.value
        XCTAssertEqual(model.current?.title, value.name); XCTAssertEqual(model.error, unlinkError)
        try await store.remove(scope: source.scope)
        await model.refresh()
        XCTAssertNil(model.current); XCTAssertFalse(model.hasAssociation); XCTAssertEqual(model.error, unlinkError)
    }

    func testConfirmationRejectsSourceChangedAfterPersistence() async throws {
        for failRead in [false, true] {
            let directory = directory(); defer { try? FileManager.default.removeItem(at: directory) }
            let store = NorthStarAnchorStore(directory: directory), value = goal()
            var changed = value; changed.stage = "Changed after persistence"
            var reads = 0
            let model = NorthStarAnchorModel(store: store)
            let source = readers {
                reads += 1
                if reads == 3 {
                    if failRead { throw NorthStarAnchorError.unavailable }
                    return [changed]
                }
                return [value]
            }
            model.activate(source); await model.prepare(value)
            let confirmed = await model.confirm()
            XCTAssertFalse(confirmed); XCTAssertEqual(reads, 3)
            XCTAssertNil(model.current); XCTAssertNotNil(model.preview); XCTAssertNotNil(model.error)
            let persisted = try await store.read(scope: source.scope)
            XCTAssertNotNil(persisted); XCTAssertTrue(model.hasAssociation)
        }
    }

    func testRecoveryReloadsSelectedFileDiscardsPreviewAndUnlinksUnresolvedAssociation() async throws {
        let directory = directory(); defer { try? FileManager.default.removeItem(at: directory) }
        let store = NorthStarAnchorStore(directory: directory), entry = VaultEntry(path: ["Goal.md"], kind: .file)
        var bytes = Data("Original synthetic direction".utf8)
        let source = NorthStarReaders(source: "vault", account: "synthetic", original: true,
                                      goals: { [] }, entries: { _ in [entry] }, text: { _ in bytes })
        let model = NorthStarAnchorModel(store: store)
        model.activate(source); await model.browse(); await model.open(entry)
        await model.prepare(try XCTUnwrap(model.paragraphs.first))
        bytes = Data("Updated synthetic direction".utf8)
        let stale = await model.confirm(); XCTAssertFalse(stale); XCTAssertNotNil(model.preview)
        await model.recover()
        XCTAssertNil(model.preview); XCTAssertEqual(model.paragraphs.map(\.text), ["Updated synthetic direction"])
        await model.prepare(try XCTUnwrap(model.paragraphs.first))
        let saved = await model.confirm(); XCTAssertTrue(saved)
        bytes.append(Data(" changed again".utf8)); await model.refresh()
        XCTAssertNil(model.current); XCTAssertTrue(model.hasAssociation); XCTAssertNotNil(model.sourceError)
        await model.unlink()
        XCTAssertFalse(model.hasAssociation); XCTAssertNil(model.sourceError)
        let persisted = try await store.read(scope: source.scope); XCTAssertNil(persisted)
    }

    func testOversizedSelectedFileExplainsNorthStarLimit() async throws {
        let directory = directory(); defer { try? FileManager.default.removeItem(at: directory) }
        let model = NorthStarAnchorModel(store: NorthStarAnchorStore(directory: directory))
        model.activate(NorthStarReaders(source: "vault", account: "synthetic", original: true,
            goals: { [] }, entries: { _ in [] }, text: { _ in throw VaultReadError.tooLarge }))
        await model.open(VaultEntry(path: ["Large.md"], kind: .file))
        XCTAssertEqual(model.error, NorthStarAnchorError.tooLarge.localizedDescription)
        XCTAssertEqual(model.selectedFile, ["Large.md"])
    }

    func testVaultReaderRejectsDifferentIdentityConflictMissingAndOversizedFiles() async throws {
        let directory = directory(); defer { try? FileManager.default.removeItem(at: directory) }
        let root = directory.appendingPathComponent("vault")
        try FileManager.default.createDirectory(at: root.appendingPathComponent("daily"), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: root.appendingPathComponent("templates"), withIntermediateDirectories: true)
        try Data("# Synthetic template\n".utf8).write(to: root.appendingPathComponent("templates/daily.md"))
        let url = root.appendingPathComponent("Goal.md"), bytes = Data("Synthetic phase direction".utf8)
        try bytes.write(to: url)
        let vault = VaultBrowserStore(stateDirectory: directory.appendingPathComponent("state"), appRoot: directory)
        let candidate = try await vault.connectOriginal(root); try await vault.commitOriginal(candidate)
        let identity = candidate.original.identity
        let actual = try await vault.northStarText(path: ["Goal.md"], identity: identity); XCTAssertEqual(actual, bytes)
        do { _ = try await vault.northStarText(path: ["Goal.md"], identity: "another"); XCTFail() } catch {}
        do { _ = try await vault.northStarText(path: ["Missing.md"], identity: identity); XCTFail() } catch {}
        let conflicting = VaultBrowserStore(stateDirectory: directory.appendingPathComponent("conflict"), appRoot: directory,
                                           versions: AnchorConflictVersions())
        let other = try await conflicting.connectOriginal(root); try await conflicting.commitOriginal(other)
        do { _ = try await conflicting.northStarText(path: ["Goal.md"], identity: identity); XCTFail() } catch {}
        try Data(repeating: 65, count: NorthStarParagraph.maximumFileBytes + 1).write(to: url)
        do { _ = try await vault.northStarText(path: ["Goal.md"], identity: identity); XCTFail() } catch {}
    }
}

private struct AnchorConflictVersions: CloudVersionProvider {
    func inventory(at url: URL) throws -> CloudVersionInventory { CloudVersionInventory(versions: [], hasConflict: true) }
    func resolve(_ reviewed: [CloudVersion], at url: URL) throws { throw NorthStarAnchorError.unavailable }
}
