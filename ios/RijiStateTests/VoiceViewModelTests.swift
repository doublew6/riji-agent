import XCTest
import RijiCore
@testable import RijiState

@MainActor
private final class ControlledVoiceServices: VoiceServices {
    var event: ((VoiceCaptureToken, VoiceHardwareEvent) -> Void)?
    var playbackEvent: ((VoicePlaybackToken, VoicePlaybackEvent) -> Void)?
    var plays: [VoicePlaybackToken] = []
    var playedData: Data?
    var failStart = false
    var holdRotation = false
    var rotation: CheckedContinuation<Void, Never>?
    var holdValidation = false
    var validation: CheckedContinuation<Double, Error>?
    var captureLoss = false
    var failPlay = false
    var speechURLs: [URL] = []
    var speechSources: [Data] = []
    var starts: [VoiceCaptureToken] = []
    var stopCount = 0
    var releaseCount = 0
    var permissionRequests = 0
    var granted = true
    var holdPermission = false
    var permission: CheckedContinuation<Bool, Never>?
    var speech: CheckedContinuation<String, Error>?
    var interruptOnStart = false
    var interruptOnStop = false
    var lastURL: URL?
    func microphonePermission() async -> Bool {
        permissionRequests += 1
        if holdPermission { return await withCheckedContinuation { permission = $0 } }
        return granted
    }
    func start(url: URL, token: VoiceCaptureToken) throws {
        if failStart { throw JournalError.storage }
        starts.append(token); lastURL = url
        try Data("controlled synthetic audio".utf8).write(to: url)
        if interruptOnStart { event?(token, .interrupted) }
    }
    func rotate(url: URL, token: VoiceCaptureToken) async throws -> Double {
        if interruptOnStop {
            if let previous = starts.last { event?(previous, .interrupted) }
            throw VoiceError.interrupted
        }
        let stops = stopCount
        try start(url: url, token: token)
        if holdRotation { await withCheckedContinuation { rotation = $0 } }
        guard stops == stopCount else { throw VoiceError.interrupted }
        return 2.75
    }
    func stop() -> Task<Double, Error> {
        stopCount += 1
        if interruptOnStop, let latest = starts.last { event?(latest, .interrupted) }
        return Task { 2.75 }
    }
    func duration(of url: URL) throws -> Double { 2.75 }
    func verifiedDuration(of url: URL) async throws -> Double {
        if holdValidation && validation == nil { return try await withCheckedThrowingContinuation { validation = $0 } }
        return 2.75
    }
    func evidence(for token: VoiceCaptureToken) -> VoiceCaptureEvidence? {
        guard captureLoss, starts.contains(token) else { return nil }
        var evidence = VoiceCaptureEvidence(firstFrame: 0, frameCount: 132000, sampleRate: 48000, streamID: token.generation)
        evidence.rejectedFrames = 137; evidence.unassignedFrames = 29
        return evidence
    }
    func play(_ data: Data, token: VoicePlaybackToken) throws {
        if failPlay { throw VoiceError.changedMedia }
        playedData = data; plays.append(token)
    }
    func stopPlayback() { releaseCount += 1 }
    func transcribe(_ source: VoiceRecognitionSource) async throws -> String { speechURLs.append(URL(fileURLWithPath: source.media.relativeLink)); speechSources.append(source.data); return try await withCheckedThrowingContinuation { speech = $0 } }
    func cancelTranscription() {}
}

private final class StateVoiceFault: @unchecked Sendable {
    let lock = NSLock()
    var values: [(FilePurpose, FileStage)] = []
    func arm(_ purpose: FilePurpose, _ stage: FileStage) { lock.lock(); values.append((purpose, stage)); lock.unlock() }
    func check(_ purpose: FilePurpose, _ stage: FileStage) throws {
        lock.lock(); defer { lock.unlock() }
        if let value = values.first, value.0 == purpose, value.1 == stage { values.removeFirst(); throw JournalError.storage }
    }
}

private final class VoicePageReadGate: @unchecked Sendable {
    let entered = XCTestExpectation(description: "Store executor is held before page read")
    private let release = DispatchSemaphore(value: 0)
    func hold() -> Bool {
        entered.fulfill()
        return release.wait(timeout: .now() + 5) == .success
    }
    func open() { release.signal() }
}

private extension LocalJournalStore {
    // Test-only synchronous actor work holds page reads without a production injection surface.
    func holdVoicePageReads(_ gate: VoicePageReadGate) -> Bool { gate.hold() }
}

@MainActor
private final class VoiceStateFixture {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("VoiceState-\(UUID())")
    let fault = StateVoiceFault()
    let service = ControlledVoiceServices()
    let store: LocalJournalStore
    let day = try! JournalDay("2026-09-12")
    lazy var diary = DiaryViewModel(now: { ISO8601DateFormatter().date(from: "2026-09-12T08:00:00Z")! },
                                    storageFactory: { [store] in store }, voiceServices: service)
    init() throws {
        let fault = self.fault
        store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"),
            stateDirectory: root.appendingPathComponent("State"), failure: { try fault.check($0, $1) })
    }
    func seed() async throws -> VoiceViewModel {
        await diary.start(); diary.updateText("Existing typed text"); await diary.flushDraft()
        let attached = await diary.captureVoice(); XCTAssertTrue(attached)
        return diary.voice!
    }
    func recordPiece(_ voice: VoiceViewModel) async {
        await voice.record(); await voice.pause(); voice.updateCorrection("Exact corrected transcript")
        _ = await voice.flushCorrection()
    }
    func cleanup() { try? FileManager.default.removeItem(at: root) }
}

@MainActor
final class VoiceViewModelTests: XCTestCase {
    func testConfirmedVoiceBridgeSavesVisibleDocumentAndRetainsReceiptAfterBodyEdit() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        await f.diary.start()
        f.diary.updateBody(f.diary.markdown + "Manual before voice\n")
        let saved = await f.diary.flushBody(); XCTAssertTrue(saved)
        let attached = await f.diary.captureVoice(); XCTAssertTrue(attached)
        let voice = f.diary.voice!; await f.recordPiece(voice)
        await f.diary.prepareVoiceInsertion(); let candidate = try XCTUnwrap(voice.preview)
        let inserted = await f.diary.insertVoiceIntoDocument(candidate); XCTAssertTrue(inserted)
        XCTAssertTrue(f.diary.text.isEmpty); XCTAssertTrue(f.diary.markdown.contains(candidate.preview))
        let stamp = try await f.store.voiceInsertion(for: candidate.recordingID); XCTAssertNotNil(stamp)
        f.diary.updateBody(f.diary.markdown.replacingOccurrences(of: "Manual before voice", with: "Manual edited after voice"))
        let edited = await f.diary.flushBody(); XCTAssertTrue(edited)
        let retained = try await f.store.voiceInsertion(for: candidate.recordingID); XCTAssertEqual(retained, stamp)
        let duplicate = try await f.store.insertVoice(candidate); XCTAssertEqual(duplicate, .alreadyInserted)
        let bytes = try await f.store.exportMarkdown(f.day)
        XCTAssertEqual(String(decoding: bytes, as: UTF8.self).components(separatedBy: candidate.preview).count, 2)
    }

    func testConfirmedInsertionWithFailedDiaryCommitRemainsRetryableAfterRecordingLocks() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        await f.diary.start()
        let attached = await f.diary.captureVoice(); XCTAssertTrue(attached)
        let voice = try XCTUnwrap(f.diary.voice)
        await f.recordPiece(voice); await f.diary.prepareVoiceInsertion()
        let candidate = try XCTUnwrap(voice.preview)
        f.fault.arm(.diary, .beforeReplace)
        let inserted = await f.diary.insertVoiceIntoDocument(candidate)
        XCTAssertFalse(inserted); XCTAssertFalse(voice.canEditRecording)
        XCTAssertNotNil(voice.insertion); XCTAssertNotNil(f.diary.errorMessage)
        XCTAssertTrue(f.diary.hasVoiceCommitRecovery)
        let retained = try await f.store.open(f.day).draft
        let operation = try XCTUnwrap(retained?.operationID)
        let retry = await f.diary.retryVoiceDocumentCommit(); XCTAssertTrue(retry)
        XCTAssertFalse(f.diary.hasVoiceCommitRecovery); XCTAssertNil(f.diary.errorMessage)
        let receiptExists = try await f.store.hasOperation(operation); XCTAssertTrue(receiptExists)
        let stamp = try await f.store.voiceInsertion(for: candidate.recordingID); XCTAssertNotNil(stamp)
        let saved = try await f.store.exportMarkdown(f.day)
        XCTAssertEqual(String(decoding: saved, as: UTF8.self).components(separatedBy: candidate.preview).count, 2)
    }

    func testPermissionOnlyAfterRecordAndLateCancelledGrantNeverStartsMicrophone() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed()
        XCTAssertEqual(f.service.permissionRequests, 0)
        let captured = voice.recording!.target
        f.service.holdPermission = true
        let task = Task { await voice.record() }
        await spin { f.service.permission != nil }
        voice.cancelPermission()
        f.service.permission?.resume(returning: true); f.service.permission = nil
        await task.value
        XCTAssertEqual(f.service.starts.count, 0)
        XCTAssertEqual(voice.recording?.target, captured)
        f.service.holdPermission = false; f.service.granted = false
        await voice.record()
        XCTAssertEqual(f.service.starts.count, 0)
        XCTAssertEqual(voice.phase, .paused)
        f.diary.updateText("Typing still available")
        XCTAssertEqual(f.diary.text, "Typing still available")
    }

    func testRotationUniqueSegmentsInterruptionAndOldGenerationCannotResume() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed()
        await voice.record()
        let first = f.service.starts[0]
        let releases = f.service.releaseCount
        f.service.event?(first, .rotate)
        await spin { f.service.starts.count == 2 && !voice.busy }
        let second = f.service.starts[1]
        XCTAssertNotEqual(first.segmentID, second.segmentID)
        XCTAssertEqual(f.service.releaseCount, releases, "Rotation keeps the session active")
        f.service.event?(second, .interrupted)
        await spin { voice.phase == .paused && !voice.busy }
        f.service.event?(first, .rotate)
        await Task.yield()
        XCTAssertEqual(f.service.starts.count, 2)
        XCTAssertEqual(voice.recording?.segments.count, 2)
        XCTAssertEqual(voice.recording?.duration, 5.5)
        XCTAssertEqual(voice.phase, .paused)
        XCTAssertGreaterThan(f.service.releaseCount, releases)
    }

    func testInterruptionWhileStartReloadIsBusyStopsAndPersistsPiece() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed()
        f.service.interruptOnStart = true
        await voice.record()
        await spin { voice.phase == .paused && !voice.busy && voice.recording?.segments.first?.finalized == true }
        XCTAssertEqual(f.service.starts.count, 1)
        XCTAssertEqual(f.service.stopCount, 1)
        XCTAssertEqual(voice.recording?.duration, 2.75)
    }

    func testRecoveryReadsExistingPiecesWithoutPermissionOrMicrophone() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed()
        await f.recordPiece(voice)
        let value = voice.recording!
        let recoveredService = ControlledVoiceServices()
        let recovered = VoiceViewModel(store: f.store, services: recoveredService)
        await recovered.select(value)
        XCTAssertEqual(recoveredService.permissionRequests, 0)
        XCTAssertTrue(recoveredService.starts.isEmpty)
        XCTAssertEqual(recovered.correction, "Exact corrected transcript")
        XCTAssertEqual(recovered.phase, .paused)
        XCTAssertEqual(recovered.recording?.target, value.target)
    }

    func testSpeechProposalLateResultPreservesCorrectionAndCancelledResultIsIgnored() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        voice.transcribe(); await spin { f.service.speech != nil }
        voice.updateCorrection("Later manual correction")
        _ = await voice.flushCorrection()
        f.service.speech?.resume(returning: "Original recognized proposal"); f.service.speech = nil
        await spin { !voice.transcribing }
        XCTAssertEqual(voice.correction, "Later manual correction")
        XCTAssertEqual(voice.recording?.transcripts.last?.text, "Original recognized proposal")
        await voice.record(); await voice.pause()
        voice.transcribe(); await spin { f.service.speech != nil }
        voice.cancelTranscription()
        f.service.speech?.resume(returning: "Obsolete result"); f.service.speech = nil
        await Task.yield(); await Task.yield()
        let stored = try await f.store.recording(voice.recording!.id)
        XCTAssertEqual(stored.transcripts.count, 1)
        XCTAssertEqual(stored.correctedText, "Later manual correction")
    }

    func testCorrectionFailedAutosaveRetriesBeforeSwitchAndRetainsVisibleText() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        let original = voice.recording!
        f.fault.arm(.voice, .beforeWrite)
        voice.updateCorrection("Must survive failed autosave")
        _ = await voice.flushCorrection()
        f.fault.arm(.voice, .beforeWrite)
        voice.updateCorrection("Newest visible correction")
        await Task.yield()
        await voice.select(original)
        XCTAssertEqual(voice.correction, "Newest visible correction")
        let stored = try await f.store.recording(original.id)
        XCTAssertEqual(stored.correctedText, "Newest visible correction")
    }

    func testAfterReplaceUnknownFreezesEditsNavigationAndRelaunchLoadsActualDraft() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        await f.diary.prepareVoiceInsertion()
        let candidate = voice.preview!
        f.fault.arm(.draft, .afterReplace)
        let inserted = await f.diary.insertVoice(candidate)
        XCTAssertFalse(inserted)
        XCTAssertTrue(f.diary.isInsertionUnknown)
        let actualText = f.diary.text
        XCTAssertTrue(actualText.contains(candidate.preview))
        f.diary.updateText("obsolete autosave")
        XCTAssertEqual(f.diary.text, actualText)
        let moved = await f.diary.navigate(to: try JournalDay("2026-09-13"))
        XCTAssertFalse(moved)
        let relaunched = DiaryViewModel(now: { Date() }, storageFactory: { [store = f.store] in store })
        await relaunched.start()
        XCTAssertFalse(relaunched.isInsertionUnknown)
        XCTAssertEqual(relaunched.selectedDay, f.day)
        XCTAssertEqual(relaunched.text, actualText)
        await relaunched.save()
        let bytes = try await f.store.exportMarkdown(f.day)
        XCTAssertEqual(String(decoding: bytes, as: UTF8.self).components(separatedBy: candidate.preview).count, 2)
    }

    func testResumeInvalidatesPreviewAndActiveRecorderRejectsOldInsertion() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        await f.diary.prepareVoiceInsertion()
        let candidate = voice.preview!
        await voice.record()
        XCTAssertNil(voice.preview)
        let result = await f.diary.insertVoice(candidate)
        XCTAssertFalse(result)
        XCTAssertEqual(f.diary.text, "Existing typed text")
        await voice.pause()
    }

    func testUnselectedSentinelAndFailedDraftFlushDoNotRequestPermission() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        await f.diary.start()
        f.diary.updateSection(DiaryViewModel.unselectedSection)
        let rejected = await f.diary.captureVoice()
        XCTAssertFalse(rejected)
        f.diary.updateSection("🧠 Notes"); f.diary.updateText("latest unsaved")
        await f.diary.flushDraft()
        f.fault.arm(.draft, .beforeWrite)
        let failed = await f.diary.captureVoice()
        XCTAssertFalse(failed)
        XCTAssertEqual(f.diary.text, "latest unsaved")
        XCTAssertEqual(f.service.permissionRequests, 0)
    }

    func testEmptyAndUnavailableSpeechKeepAudioAndCorrection() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        voice.transcribe(); await spin { f.service.speech != nil }
        f.service.speech?.resume(returning: ""); f.service.speech = nil
        await spin { !voice.transcribing }
        XCTAssertNotNil(voice.errorMessage)
        XCTAssertEqual(voice.correction, "Exact corrected transcript")
        voice.transcribe(); await spin { f.service.speech != nil }
        f.service.speech?.resume(throwing: VoiceError.unavailable); f.service.speech = nil
        await spin { !voice.transcribing }
        XCTAssertTrue(voice.errorMessage?.contains("不支持离线中文转写") == true)
        XCTAssertEqual(voice.recording?.transcripts.count, 0)
        XCTAssertEqual(voice.recording?.segments.count, 1)
    }

    func testImportWriteUnknownRetriesSameOriginalAndFinalize() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed()
        let bytes = Data("controlled synthetic imported audio".utf8)
        f.fault.arm(.audio, .afterReplace)
        await voice.importAudio(bytes)
        XCTAssertNotNil(voice.errorMessage)
        await voice.importAudio(bytes)
        XCTAssertEqual(voice.recording?.segments.count, 1)
        XCTAssertTrue(voice.recording?.segments[0].finalized == true)
        let exported = try await f.store.exportVoiceAudio(voice.recording!.id, segmentID: voice.recording!.segments[0].id)
        XCTAssertEqual(exported, bytes)
    }

    func testAnotherDayCannotReceiveRecordingAndOriginalTargetSurvivesReturn() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        let captured = voice.recording!.target
        let nextDay = try JournalDay("2026-09-13")
        let navigated = await f.diary.navigate(to: nextDay)
        XCTAssertTrue(navigated)
        f.diary.updateText("Other day input"); await f.diary.flushDraft()
        await f.diary.prepareVoiceInsertion()
        XCTAssertNil(voice.preview)
        XCTAssertEqual(voice.recording?.target, captured)
        XCTAssertEqual(f.diary.text, "Other day input")
        let returned = await f.diary.navigate(to: captured.day)
        XCTAssertTrue(returned)
        await f.diary.prepareVoiceInsertion()
        XCTAssertEqual(voice.preview?.target, captured)
    }

    func testInterruptionInRotationFinalizeGapRequiresExplicitResume() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed()
        await voice.record()
        f.service.interruptOnStop = true
        f.service.event?(f.service.starts[0], .rotate)
        await spin { voice.phase == .paused && !voice.busy }
        XCTAssertEqual(f.service.starts.count, 1)
        XCTAssertEqual(voice.recording?.segments.first?.duration, 2.75)
        f.service.interruptOnStop = false
        await voice.record()
        XCTAssertEqual(f.service.starts.count, 2)
        await voice.pause()
    }


    func testFailedReservationStartAndFinalizeThenNewCaptureResetRuntime() async throws {
        for stage in [FileStage.beforeWrite, .afterReplace] {
            let f = try VoiceStateFixture(); defer { f.cleanup() }
            let voice = try await f.seed()
            f.fault.arm(.voice, stage)
            await voice.record()
            XCTAssertEqual(voice.phase, .paused)
            XCTAssertNotNil(voice.errorMessage)
            await voice.record()
            XCTAssertEqual(voice.phase, .recording)
            await voice.pause()
            f.service.failStart = true
            await voice.record()
            XCTAssertEqual(voice.phase, .paused)
            XCTAssertNotNil(voice.recording?.segments.last)
            f.service.failStart = false
            await voice.record()
            f.fault.arm(.voice, .beforeWrite)
            await voice.pause()
            let previousID = voice.recording!.id
            let attached = await f.diary.captureVoice()
            XCTAssertTrue(attached); XCTAssertNotEqual(voice.recording?.id, previousID)
            XCTAssertEqual(voice.liveDuration, 0)
            await voice.record()
            XCTAssertEqual(voice.phase, .recording)
            XCTAssertEqual(voice.recording?.segments.count, 1)
            await voice.pause()
            let retained = try await f.store.recording(previousID)
            XCTAssertGreaterThan(retained.segments.count, 0)
        }
    }

    func testDelayedPlaybackCannotStartAfterRecordSelectDismissDeleteOrStop() async throws {
        for action in 0..<5 {
            let f = try VoiceStateFixture(); defer { f.cleanup() }
            let initial = try await f.seed(); await f.recordPiece(initial)
            let value = initial.recording!
            var waiting: CheckedContinuation<Void, Never>?
            let voice = VoiceViewModel(store: f.store, services: f.service, verifyPlayback: { id, piece in
                let data = try await f.store.verifiedVoicePlayback(id, segmentID: piece)
                await withCheckedContinuation { waiting = $0 }
                return data
            })
            await voice.select(value)
            let play = Task { await voice.play(value.segments[0]) }
            await spin { waiting != nil }
            switch action {
            case 0: await voice.record()
            case 1: await voice.select(value)
            case 2: let dismissed = await voice.dismiss(); XCTAssertTrue(dismissed)
            case 3: await voice.deleteAudio()
            default: voice.stopPlayback()
            }
            waiting?.resume(); await play.value
            XCTAssertTrue(f.service.plays.isEmpty)
            XCTAssertFalse(voice.playbackVerified)
            if voice.phase == .recording { await voice.pause() }
        }
    }

    func testPlaybackCompletionFailureAndObsoleteCallbacksAreRequestBound() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        let segment = voice.recording!.segments[0]
        await voice.play(segment)
        let first = f.service.plays.last!
        XCTAssertEqual(voice.playbackState, .playing)
        XCTAssertTrue(voice.playbackVerified)
        f.service.playbackEvent?(first, .finished)
        XCTAssertEqual(voice.playbackState, .finished)
        XCTAssertTrue(voice.playbackVerified)
        await voice.play(segment)
        let next = f.service.plays.last!
        f.service.playbackEvent?(first, .failed)
        XCTAssertEqual(voice.playbackState, .playing)
        f.service.playbackEvent?(next, .failed)
        XCTAssertEqual(voice.playbackState, .failed)
        XCTAssertFalse(voice.playbackVerified)
        f.service.failPlay = true
        await voice.play(segment)
        XCTAssertEqual(voice.playbackState, .failed)
        XCTAssertFalse(voice.playbackVerified)
        f.service.failPlay = false
        await voice.play(segment); voice.stopPlayback()
        XCTAssertEqual(voice.playbackState, .stopped)
    }

    func testInsertedRecordingIsReadOnlyAcrossSaveRelaunchDeletionAndNewCapture() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        await f.diary.prepareVoiceInsertion()
        let candidate = voice.preview!
        let success = await f.diary.insertVoice(candidate)
        XCTAssertTrue(success); XCTAssertEqual(voice.insertion?.insertionID, candidate.id)
        XCTAssertFalse(voice.canRecord)
        voice.updateCorrection("Must not change"); voice.transcribe(); await voice.record()
        XCTAssertEqual(voice.correction, "Exact corrected transcript")
        XCTAssertFalse(voice.transcribing); XCTAssertEqual(f.service.starts.count, 1)
        await f.diary.save()
        let recovered = VoiceViewModel(store: f.store, services: ControlledVoiceServices())
        await recovered.select(voice.recording!)
        XCTAssertEqual(recovered.insertion, voice.insertion)
        XCTAssertFalse(recovered.canEditRecording)
        await recovered.deleteAudio()
        XCTAssertEqual(recovered.insertion?.insertionID, candidate.id)
        XCTAssertTrue(recovered.audioRemovalComplete)
        let captured = await f.diary.captureVoice()
        XCTAssertTrue(captured); XCTAssertNotEqual(voice.recording?.id, candidate.recordingID)
        XCTAssertNil(voice.insertion); XCTAssertTrue(voice.canRecord)
        XCTAssertEqual(voice.correction, "")
        XCTAssertEqual(voice.recording?.target.day, f.diary.selectedDay)
    }

    func testPartialRecognitionErrorCancellationAndRelaunchRetryRetainExactOrder() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        await voice.record(); await voice.pause()
        voice.transcribe(); await spin { f.service.speech != nil }
        f.service.speech?.resume(returning: "第一段原文"); f.service.speech = nil
        await spin { f.service.speech != nil && voice.retainedPieceCount == 1 }
        f.service.speech?.resume(throwing: VoiceError.unavailable); f.service.speech = nil
        await spin { !voice.transcribing }
        XCTAssertEqual(voice.retainedPieceCount, 1)
        XCTAssertEqual(voice.recording?.transcripts.count, 0)
        voice.transcribe(); await spin { f.service.speech != nil }
        XCTAssertEqual(f.service.speechURLs.count, 3)
        XCTAssertEqual(f.service.speechURLs[1], f.service.speechURLs[2])
        voice.cancelTranscription()
        f.service.speech?.resume(returning: "Cancelled second result"); f.service.speech = nil
        await Task.yield(); await Task.yield()
        let service = ControlledVoiceServices()
        let restored = VoiceViewModel(store: f.store, services: service)
        await restored.select(voice.recording!)
        restored.transcribe(); await spin { service.speech != nil }
        XCTAssertEqual(service.speechURLs.count, 1)
        XCTAssertEqual(service.speechURLs[0], f.service.speechURLs[1])
        service.speech?.resume(returning: "第二段原文"); service.speech = nil
        await spin { !restored.transcribing }
        XCTAssertEqual(restored.recording?.transcripts.last?.text, "第一段原文\n第二段原文")
        XCTAssertEqual(restored.retainedPieceCount, 2)
        XCTAssertEqual(restored.correction, "Exact corrected transcript")
        restored.transcribe(); await spin { !restored.transcribing }
        XCTAssertEqual(service.speechURLs.count, 1)
        XCTAssertEqual(restored.recording?.transcripts.count, 1)
    }

    func testChangedMediaAndDeletedSourceNeverReuseOrResurrectRecognition() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        await voice.record(); await voice.pause()
        let value = voice.recording!
        voice.transcribe(); await spin { f.service.speech != nil }
        f.service.speech?.resume(returning: "Retained first"); f.service.speech = nil
        await spin { f.service.speech != nil && voice.retainedPieceCount == 1 }
        voice.cancelTranscription()
        f.service.speech?.resume(returning: "Cancelled"); f.service.speech = nil
        await Task.yield()
        let first = try await f.store.verifiedVoiceURL(value.id, segmentID: value.segments[0].id)
        let bytes = try Data(contentsOf: first)
        try Data("Changed source".utf8).write(to: first)
        let requestCount = f.service.speechURLs.count
        voice.transcribe(); await spin { !voice.transcribing }
        XCTAssertEqual(f.service.speechURLs.count, requestCount)
        XCTAssertEqual(voice.retainedPieceCount, 1)
        try bytes.write(to: first)
        voice.transcribe(); await spin { f.service.speech != nil }
        await voice.deleteAudio()
        f.service.speech?.resume(returning: "Late after delete"); f.service.speech = nil
        await Task.yield(); await Task.yield()
        let retained = try await f.store.recording(value.id)
        XCTAssertTrue(retained.deletionRequested)
        XCTAssertEqual(retained.segmentTranscripts?.count, 1)
        XCTAssertEqual(retained.transcripts.count, 0)
        XCTAssertFalse(FileManager.default.fileExists(atPath: first.path))
        XCTAssertEqual(retained.correctedText, "Exact corrected transcript")
    }

    func testVisibleDestinationRejectsStalePreviewAndPreflightKeepsEditor() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        await f.diary.prepareVoiceInsertion()
        let candidate = voice.preview!
        let dismissed = await voice.dismiss(); XCTAssertTrue(dismissed)
        let moved = await f.diary.navigate(to: try JournalDay("2026-09-13")); XCTAssertTrue(moved)
        f.diary.updateText("Other visible text"); await f.diary.flushDraft()
        let inserted = await f.diary.insertVoice(candidate)
        XCTAssertFalse(inserted)
        XCTAssertEqual(f.diary.errorMessage, VoiceError.invalidTarget.localizedDescription)
        XCTAssertEqual(f.diary.text, "Other visible text")
        let original = try await f.store.open(f.day).draft!
        XCTAssertEqual(original.text, "Existing typed text")
        _ = await f.diary.navigate(to: f.day)
        voice.updateCorrection("<!-- riji-record:invalid -->")
        await f.diary.prepareVoiceInsertion()
        let invalid = voice.preview!
        let rejected = await f.diary.insertVoice(invalid)
        XCTAssertFalse(rejected); XCTAssertFalse(f.diary.isInsertionUnknown)
        XCTAssertEqual(f.diary.text, "Existing typed text")
        XCTAssertEqual(f.diary.errorMessage, JournalError.invalidMarkdown.localizedDescription)
        let pending = try await f.store.pendingVoiceInsertion(f.day)
        XCTAssertNil(pending)
    }

    func testSourceRefreshCarriesStampsThroughOrdinarySaveAndOriginalCandidateRetry() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        await f.diary.prepareVoiceInsertion(); let candidate = voice.preview!
        let inserted = await f.diary.insertVoice(candidate); XCTAssertTrue(inserted)
        let merged = try await f.store.open(f.day).draft!
        let source = Data((merged.effectiveTemplate + "\nExternal preserved bytes\n").utf8)
        try source.write(to: f.root.appendingPathComponent("Journals/\(f.day.value).md"))
        await f.diary.reviewCurrentSource()
        let refreshed = try await f.store.open(f.day).draft!
        XCTAssertEqual(refreshed.voiceInsertions, merged.voiceInsertions)
        XCTAssertNotEqual(refreshed.operationID, merged.operationID)
        await f.diary.save()
        XCTAssertNil(f.diary.errorMessage)
        let expected = try MarkdownSectionAppender.append(text: merged.text, to: merged.section, in: source)
        let after = try await f.store.exportMarkdown(f.day)
        XCTAssertEqual(after, expected)
        let result = try await f.store.insertVoice(candidate)
        XCTAssertEqual(result, .alreadyInserted)
        let unchanged = try await f.store.exportMarkdown(f.day)
        XCTAssertEqual(unchanged, after)
        let stamp = try await f.store.voiceInsertion(for: candidate.recordingID)
        XCTAssertEqual(stamp?.insertionID, candidate.id)
    }

    func testSourceRefreshBusyLockPreventsConcurrentLibrarySelection() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        let original = voice.recording!
        let other = try await f.store.createRecording(id: UUID(), target: original.target)
        let draft = try await f.store.open(f.day).draft!
        let task = Task { await voice.refreshSource(draft: draft) }
        await spin { voice.busy }
        await voice.select(other)
        await task.value
        XCTAssertEqual(voice.recording?.id, original.id)
        XCTAssertEqual(voice.recording?.insertionTarget, original.target)
        XCTAssertFalse(voice.busy)
    }

    func testLibraryFailureWithoutSelectionAndPendingDeletionHaveVisibleState() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let bad = f.root.appendingPathComponent("State/voice/\(UUID()).json")
        try Data("unreadable synthetic manifest".utf8).write(to: bad)
        let library = VoiceViewModel(store: f.store, services: ControlledVoiceServices())
        await library.loadLibrary()
        XCTAssertNil(library.recording)
        XCTAssertEqual(library.libraryUnreadableCount, 1)
        XCTAssertTrue(FileManager.default.fileExists(atPath: bad.path))
        let voice = try await f.seed(); await f.recordPiece(voice)
        f.fault.arm(.audio, .beforeRemove)
        await voice.deleteAudio()
        XCTAssertTrue(voice.recording?.deletionRequested == true)
        XCTAssertFalse(voice.audioRemovalComplete)
        XCTAssertNotNil(voice.errorMessage)
        await voice.deleteAudio()
        XCTAssertTrue(voice.audioRemovalComplete)
    }

    private func spin(_ condition: () -> Bool) async {
        for _ in 0..<200 where !condition() { try? await Task.sleep(nanoseconds: 1_000_000) }
        XCTAssertTrue(condition())
    }
}

extension VoiceViewModelTests {
    func testLoopTwoEmptyMiddlePieceNeedsExplicitSkipAndRelaunchRetainsPartialOriginals() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        await voice.record(); await voice.pause(); await voice.record(); await voice.pause()
        voice.transcribe(); await spin { f.service.speech != nil }
        f.service.speech?.resume(returning: "First exact original"); f.service.speech = nil
        await spin { f.service.speech != nil }
        f.service.speech?.resume(returning: ""); f.service.speech = nil
        await spin { !voice.transcribing }
        XCTAssertEqual(voice.recording?.segmentTranscripts?.first?.text, "First exact original")
        XCTAssertEqual(voice.emptyPiece?.segmentID, voice.recording?.segments[1].id)
        XCTAssertEqual(voice.correction, "Exact corrected transcript")
        let empty = voice.emptyPiece!
        voice.cancelTranscription(); await voice.skipEmptyPiece()
        XCTAssertNil(voice.recording?.skippedTranscripts)
        voice.transcribe(); await spin { f.service.speech != nil }
        f.service.speech?.resume(throwing: VoiceError.emptyTranscript); f.service.speech = nil
        await spin { !voice.transcribing }
        await voice.skipEmptyPiece()
        XCTAssertEqual(voice.recording?.skippedTranscripts, [empty])
        let service = ControlledVoiceServices()
        let reopened = VoiceViewModel(store: f.store, services: service)
        await reopened.select(voice.recording!)
        reopened.transcribe(); await spin { service.speech != nil }
        XCTAssertTrue(service.speechURLs[0].lastPathComponent.contains(voice.recording!.segments[2].id.uuidString))
        service.speech?.resume(returning: "Third exact original"); service.speech = nil
        await spin { !reopened.transcribing }
        let proposal = reopened.recording!.transcripts.last!
        XCTAssertEqual(proposal.text, "First exact original\nThird exact original")
        XCTAssertEqual(proposal.skipped, [empty])
        XCTAssertEqual(proposal.media.count, 3)
        XCTAssertTrue(reopened.status.contains("不完整"))
        XCTAssertEqual(reopened.correction, "Exact corrected transcript")
        reopened.adopt(proposal)
        XCTAssertEqual(reopened.correction, proposal.text)
    }

    func testLoopTwoChangedAndDeletedEmptyPieceCannotBeSkippedOrResurrected() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        voice.transcribe(); await spin { f.service.speech != nil }
        f.service.speech?.resume(returning: ""); f.service.speech = nil
        await spin { !voice.transcribing }
        let value = voice.recording!, piece = value.segments[0]
        let url = try await f.store.verifiedVoiceURL(value.id, segmentID: piece.id)
        try Data("changed source".utf8).write(to: url)
        await voice.skipEmptyPiece()
        XCTAssertNil(voice.recording?.skippedTranscripts)
        XCTAssertNotNil(voice.errorMessage)
        await voice.deleteAudio(); await voice.skipEmptyPiece()
        XCTAssertNil(voice.emptyPiece)
        XCTAssertNil(voice.recording?.skippedTranscripts)
        XCTAssertTrue(voice.recording!.deletionRequested)
    }

    func testLoopTwoPagedLibraryFailureKeepsRowsAndLaterPageSelection() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        for index in 0..<22 {
            _ = try await f.store.createRecording(id: UUID(uuidString: String(format: "00000000-0000-0000-0000-%012d", index))!, target: voice.recording!.target)
        }
        await voice.loadLibrary()
        let first = voice.library.map(\.id)
        XCTAssertEqual(first.count, 20)
        let gate = VoicePageReadGate(), store = f.store
        defer { gate.open() }
        let heldStore = Task.detached { await store.holdVoicePageReads(gate) }
        await fulfillment(of: [gate.entered], timeout: 2)
        let firstAdvance = Task { await voice.nextLibraryPage() }
        await spin { voice.libraryLoading }
        XCTAssertTrue(voice.libraryLoading)
        XCTAssertEqual(voice.libraryPageNumber, 1)
        // This action must return while the first actor read is still blocked. Without the
        // loading guard it waits for the gate timeout and then duplicates the same cursor.
        await voice.nextLibraryPage()
        XCTAssertTrue(voice.libraryLoading)
        XCTAssertEqual(voice.library.map(\.id), first)
        gate.open()
        let releasedBeforeTimeout = await heldStore.value
        XCTAssertTrue(releasedBeforeTimeout)
        await firstAdvance.value
        XCTAssertEqual(voice.libraryPageNumber, 2)
        XCTAssertLessThanOrEqual(voice.library.count, 20)
        let second = voice.library.map(\.id)
        let folder = f.root.appendingPathComponent("State/voice"), held = f.root.appendingPathComponent("held-manifests")
        try FileManager.default.moveItem(at: folder, to: held)
        await voice.loadLibrary()
        XCTAssertEqual(voice.library.map(\.id), second)
        XCTAssertNotNil(voice.libraryError)
        try FileManager.default.moveItem(at: held, to: folder)
        await voice.loadLibrary()
        XCTAssertNil(voice.libraryError)
        let row = voice.library.first { $0.id == voice.recording!.id }!
        await voice.selectSummary(row)
        let exported = await voice.export(voice.recording!.segments[0])
        XCTAssertEqual(exported, Data("controlled synthetic audio".utf8))
        await voice.previousLibraryPage()
        XCTAssertEqual(voice.library.map(\.id), first)
    }

    func testLoopTwoExplicitAbandonmentUnblocksOriginalEditorAndNewSourcePreview() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        await f.diary.prepareVoiceInsertion()
        let candidate = voice.preview!
        f.fault.arm(.draft, .beforeWrite)
        let result = await f.diary.insertVoice(candidate)
        XCTAssertFalse(result); XCTAssertTrue(f.diary.isInsertionUnknown)
        let snapshot = try await f.store.open(f.day)
        try Data((snapshot.markdown + "\nExternal source change\n").utf8).write(to: f.root.appendingPathComponent("Journals/\(f.day.value).md"))
        await f.diary.retryVoiceInsertion()
        XCTAssertEqual(f.diary.abandonableInsertion, candidate)
        await f.diary.abandonVoiceInsertion()
        XCTAssertFalse(f.diary.isInsertionUnknown)
        XCTAssertEqual(f.diary.text, "Existing typed text")
        XCTAssertEqual(voice.correction, "Exact corrected transcript")
        await f.diary.refreshVoiceInsertionSource()
        await f.diary.prepareVoiceInsertion()
        XCTAssertNotNil(voice.preview)
        XCTAssertNotEqual(voice.preview?.id, candidate.id)
        let inserted = await f.diary.insertVoice(voice.preview!)
        XCTAssertTrue(inserted)
        do { _ = try await f.store.insertVoice(candidate); XCTFail("Old abandoned identity must be rejected") }
        catch { XCTAssertEqual(error as? JournalError, .operationMismatch) }
    }

    func testLoopTwoFailedRotationCanResumeOnlyWithFreshInactiveToken() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed()
        await voice.record()
        let first = f.service.starts[0]
        f.service.failStart = true; f.service.event?(first, .rotate)
        await spin { !voice.busy && voice.phase == .paused }
        XCTAssertEqual(voice.recording?.segments.count, 2)
        XCTAssertTrue(voice.recording!.segments[0].finalized)
        XCTAssertFalse(voice.recording!.segments[1].finalized)
        f.service.failStart = false
        await voice.record()
        XCTAssertEqual(voice.phase, .recording)
        XCTAssertNotEqual(f.service.starts.last?.segmentID, first.segmentID)
        await voice.pause(ending: true)
        XCTAssertEqual(voice.phase, .ended)
        XCTAssertTrue(voice.status.contains("已结束"))
    }
}

extension VoiceViewModelTests {
    func testLoopTwoActualSkipAndContinueActionCannotRestartAfterCancellationChangeOrDeletion() async throws {
        for outcome in 0..<3 {
            let f = try VoiceStateFixture(); defer { f.cleanup() }
            let initial = try await f.seed(); await f.recordPiece(initial)
            let value = initial.recording!
            var waiting: CheckedContinuation<Void, Never>?
            let voice = VoiceViewModel(store: f.store, services: f.service, verifySkip: { id, segment in
                let url = try await f.store.verifiedVoiceURL(id, segmentID: segment)
                await withCheckedContinuation { waiting = $0 }
                return url
            })
            await voice.select(value)
            voice.transcribe(); await spin { f.service.speech != nil }
            f.service.speech?.resume(returning: ""); f.service.speech = nil
            await spin { !voice.transcribing }
            let operation = Task { await voice.skipAndContinueTranscription() }
            await spin { waiting != nil }
            if outcome == 0 { voice.cancelTranscription() }
            else if outcome == 1 {
                let url = try await f.store.verifiedVoiceURL(value.id, segmentID: value.segments[0].id)
                try Data("changed synthetic original".utf8).write(to: url)
            } else { try await f.store.deleteVoiceAudio(value.id) }
            waiting?.resume(); await operation.value
            XCTAssertEqual(f.service.speechURLs.count, 1)
            XCTAssertFalse(voice.transcribing)
            XCTAssertEqual(voice.correction, "Exact corrected transcript")
            let retained = try await f.store.recording(value.id)
            XCTAssertNil(retained.skippedTranscripts)
        }
    }
}

extension VoiceViewModelTests {
    func testLoopTwoColdUnknownInsertionHasNoSelectionButCanAbandonAndCloseSafely() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        await f.diary.prepareVoiceInsertion()
        let candidate = voice.preview!
        f.fault.arm(.draft, .beforeWrite)
        let first = await f.diary.insertVoice(candidate)
        XCTAssertFalse(first)
        let url = try await f.store.verifiedVoiceURL(candidate.recordingID, segmentID: candidate.media[0].segmentID)
        try Data("changed original before relaunch".utf8).write(to: url)
        let reopened = DiaryViewModel(storageFactory: { f.store }, voiceServices: ControlledVoiceServices())
        await reopened.start()
        XCTAssertTrue(reopened.isInsertionUnknown)
        XCTAssertNil(reopened.voice?.recording)
        XCTAssertEqual(reopened.abandonableInsertion, candidate)
        let canClose = await reopened.voice!.dismiss()
        XCTAssertTrue(canClose)
        XCTAssertTrue(reopened.isInsertionUnknown)
        await reopened.abandonVoiceInsertion()
        XCTAssertFalse(reopened.isInsertionUnknown)
        XCTAssertEqual(reopened.text, "Existing typed text")
        XCTAssertNil(reopened.voice?.recording)
        reopened.updateText("New retained input")
        await reopened.flushDraft()
        XCTAssertEqual(reopened.text, "New retained input")
    }
}

extension VoiceViewModelTests {
    func testLoopThreeImmediateStopDuringHeldRotationKeepsEndIntentAndNeverRestarts() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed()
        await voice.record()
        let first = f.service.starts.last!
        f.service.holdValidation = true
        f.service.event?(first, .rotate)
        await spin { f.service.validation != nil }
        XCTAssertTrue(voice.busy)
        XCTAssertTrue(voice.hardwareStopAvailable)
        XCTAssertEqual(f.service.starts.count, 2)
        let before = f.service.stopCount
        await voice.pause()
        XCTAssertEqual(f.service.stopCount, before + 1)
        XCTAssertFalse(voice.hardwareStopAvailable)
        XCTAssertTrue(voice.captureStopPending)
        await voice.pause(ending: true)
        XCTAssertEqual(f.service.stopCount, before + 1)
        f.service.event?(first, .rotate)
        f.service.event?(f.service.starts.last!, .interrupted)
        f.service.holdValidation = false
        f.service.validation?.resume(returning: 2.75); f.service.validation = nil
        await spin { !voice.busy && voice.phase == .ended }
        XCTAssertEqual(voice.phase, .ended)
        XCTAssertTrue(voice.recording?.ended == true)
        XCTAssertEqual(voice.recording?.segments.filter(\.finalized).count, 2)
        XCTAssertEqual(f.service.starts.count, 2)
        XCTAssertFalse(voice.captureStopPending)
        XCTAssertTrue(voice.status.contains("结束"))
    }

    func testLoopThreeStopSurvivesUnverifiedInsertionMetadataAndBlockedStore() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await voice.record()
        let gate = VoicePageReadGate(); defer { gate.open() }
        let held = Task { await f.store.holdVoicePageReads(gate) }
        await fulfillment(of: [gate.entered], timeout: 2)
        let refresh = Task { await voice.refreshInsertionState() }
        await spin { !voice.insertionKnown }
        XCTAssertFalse(voice.canEditRecording)
        XCTAssertTrue(voice.hardwareStopAvailable)
        let stops = f.service.stopCount
        let stopping = Task { await voice.pause(ending: true) }
        await spin { f.service.stopCount > stops }
        XCTAssertEqual(f.service.stopCount, stops + 1)
        XCTAssertFalse(voice.hardwareStopAvailable)
        XCTAssertTrue(voice.busy)
        gate.open()
        _ = await held.value; await refresh.value; await stopping.value
        XCTAssertEqual(voice.phase, .ended)
        XCTAssertEqual(voice.recording?.segments.filter(\.finalized).count, 1)
    }

    func testLoopThreeCorrectionAndPieceAdoptionFreezeDuringUnknownInsertion() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        await f.diary.prepareVoiceInsertion()
        let candidate = voice.preview!
        f.fault.arm(.draft, .beforeWrite)
        let inserted = await f.diary.insertVoice(candidate)
        XCTAssertFalse(inserted)
        XCTAssertTrue(f.diary.isInsertionUnknown)
        let text = voice.correction
        voice.updateCorrection("Individual retained piece must not replace correction")
        voice.adopt(VoiceTranscript(id: UUID(), media: candidate.media, text: "Whole proposal also frozen"))
        XCTAssertEqual(voice.correction, text)
        let retained = try await f.store.recording(candidate.recordingID)
        XCTAssertEqual(retained.correctedText, text)
        do {
            try await f.store.retainVoiceCorrection(candidate.recordingID, correction: VoiceCorrection(text: "Invalid overwrite"))
            XCTFail("Pending voice intent accepted correction")
        } catch { XCTAssertEqual(error as? JournalError, .pendingOperation) }
        await f.diary.retryVoiceInsertion()
        XCTAssertFalse(f.diary.isInsertionUnknown)
        XCTAssertTrue(f.diary.text.contains(candidate.preview))
    }

    func testLoopThreeVerifiedPlaybackSnapshotCannotPlayReplacementPathBytes() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let original = try await f.seed(); await f.recordPiece(original)
        let value = original.recording!, segment = value.segments[0]
        let url = try await f.store.verifiedVoiceURL(value.id, segmentID: segment.id)
        let bytes = try Data(contentsOf: url)
        var waiting: CheckedContinuation<Void, Never>?
        let voice = VoiceViewModel(store: f.store, services: f.service, verifyPlayback: { id, segment in
            let data = try await f.store.verifiedVoicePlayback(id, segmentID: segment)
            await withCheckedContinuation { waiting = $0 }
            return data
        })
        await voice.select(value)
        let playing = Task { await voice.play(segment) }
        await spin { waiting != nil }
        try Data("replacement bytes".utf8).write(to: url, options: .atomic)
        waiting?.resume(); await playing.value
        XCTAssertEqual(f.service.playedData, bytes)
        XCTAssertEqual(voice.playbackState, .playing)
        voice.stopPlayback()
        await voice.play(segment)
        XCTAssertEqual(voice.playbackState, .failed)
        XCTAssertFalse(voice.playbackVerified)
    }

    func testLoopThreeSkippedPieceRetryRetainsOldIncompleteProposalAndCompletesInOrder() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed()
        for _ in 0..<3 { await voice.record(); await voice.pause() }
        let refs = try await f.store.voiceReferences(voice.recording!.id)
        try await f.store.retainVoiceSegmentTranscript(voice.recording!.id, piece: VoiceSegmentTranscript(media: refs[0], text: "First"))
        try await f.store.skipVoiceTranscript(voice.recording!.id, media: refs[1])
        try await f.store.retainVoiceSegmentTranscript(voice.recording!.id, piece: VoiceSegmentTranscript(media: refs[2], text: "Third"))
        voice.transcribe(); await spin { !voice.transcribing }
        let old = voice.recording!.transcripts.last!
        XCTAssertEqual(old.text, "First\nThird")
        XCTAssertEqual(old.skipped, [refs[1]])
        await voice.retrySkippedPiece(refs[1])
        voice.transcribe(); await spin { f.service.speech != nil }
        f.service.speech?.resume(returning: "Second"); f.service.speech = nil
        await spin { !voice.transcribing }
        XCTAssertEqual(voice.recording?.transcripts.last?.text, "First\nSecond\nThird")
        XCTAssertEqual(voice.recording?.transcripts.first, old)
        XCTAssertEqual(f.service.speechURLs.count, 1)
        XCTAssertEqual(voice.correction, "")
    }

    func testLoopThreeReopeningUnusedSheetReusesRecordAcrossRelaunchAndCurrentDamageKeepsAudio() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(), id = voice.recording!.id
        for _ in 0..<5 {
            let dismissed = await voice.dismiss(); XCTAssertTrue(dismissed)
            let attached = await f.diary.captureVoice(); XCTAssertTrue(attached)
            XCTAssertEqual(voice.recording?.id, id)
        }
        let restored = VoiceViewModel(store: f.store, services: f.service)
        let target = voice.recording!.target
        let attached = await restored.attach(target: target)
        XCTAssertTrue(attached); XCTAssertEqual(restored.recording?.id, id)
        await restored.record(); await restored.pause()
        restored.updateCorrection("Current version")
        _ = await restored.flushCorrection()
        let value = try await f.store.recording(id), reference = value.currentCorrectionReference!
        let url = f.root.appendingPathComponent("State/voice-corrections/\(id)/\(reference.id).json")
        try Data("corrupt checkpoint".utf8).write(to: url)
        let cold = VoiceViewModel(store: f.store, services: f.service)
        await cold.select(value)
        XCTAssertTrue(cold.recording?.currentCorrectionUnavailable == true)
        XCTAssertFalse(cold.canEditRecording)
        cold.updateCorrection("")
        let exported = await cold.export(value.segments[0])
        XCTAssertEqual(exported, Data("controlled synthetic audio".utf8))
        let closed = await cold.dismiss()
        XCTAssertTrue(closed)
        let stillDamaged = try Data(contentsOf: url)
        XCTAssertEqual(stillDamaged, Data("corrupt checkpoint".utf8))
    }

    func testLoopThreeFinalizationRetryPreservesNonzeroLossAndShowsUnknownAfterColdRecovery() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed()
        f.service.captureLoss = true
        await voice.record()
        f.fault.arm(.voice, .beforeReplace)
        await voice.pause(ending: true)
        XCTAssertTrue(voice.stopNeedsRetry)
        await voice.retryRecovery()
        await spin { voice.phase == .ended && !voice.busy }
        XCTAssertEqual(voice.recording?.segments.last?.captureEvidence?.rejectedFrames, 137)
        XCTAssertEqual(voice.recording?.segments.last?.captureEvidence?.unassignedFrames, 29)
        XCTAssertTrue(voice.integrityWarning?.contains("未写入") == true)
        let draft = try await f.store.open(f.day).draft!
        let next = try await f.store.createRecording(id: UUID(), target: f.store.voiceTarget(draft))
        let id = UUID(), url = try await f.store.reserveVoiceSegment(next.id, id: id, captureExpected: true)
        try Data("recoverable original with unknown integrity".utf8).write(to: url)
        let cold = VoiceViewModel(store: f.store, services: f.service)
        await cold.select(next)
        XCTAssertEqual(cold.recording?.segments.last?.captureEvidenceUnknown, true)
        XCTAssertTrue(cold.integrityWarning?.contains("证据不可用") == true)
    }
}

extension VoiceViewModelTests {
    func testLoopThreeRetrySkippedDecisionCannotMutateAfterCancellationChangeOrDeletion() async throws {
        for outcome in 0..<3 {
            let f = try VoiceStateFixture(); defer { f.cleanup() }
            let initial = try await f.seed(); await f.recordPiece(initial)
            let value = initial.recording!, refs = try await f.store.voiceReferences(initial.recording!.id)
            try await f.store.skipVoiceTranscript(value.id, media: refs[0])
            var waiting: CheckedContinuation<Void, Never>?
            let voice = VoiceViewModel(store: f.store, services: f.service, verifySkip: { id, segment in
                let url = try await f.store.verifiedVoiceURL(id, segmentID: segment)
                await withCheckedContinuation { waiting = $0 }
                return url
            })
            await voice.select(value)
            let operation = Task { await voice.retrySkippedPiece(refs[0]) }
            await spin { waiting != nil }
            if outcome == 0 { voice.cancelTranscription() }
            else if outcome == 1 {
                let url = try await f.store.verifiedVoiceURL(value.id, segmentID: refs[0].segmentID)
                try Data("changed source".utf8).write(to: url)
            } else { try await f.store.deleteVoiceAudio(value.id) }
            waiting?.resume(); await operation.value
            XCTAssertEqual(voice.correction, "Exact corrected transcript")
            XCTAssertEqual(f.service.speechURLs.count, 0)
            let retained = try await f.store.voiceManifest(value.id)
            XCTAssertEqual(retained.skippedTranscripts, refs)
        }
    }

    func testLoopThreeColdSelectionErrorAndSelectedHistoryFailureRemainVisible() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let original = try await f.seed(); await f.recordPiece(original)
        let value = original.recording!
        let cold = VoiceViewModel(store: f.store, services: f.service)
        await cold.loadLibrary()
        let summary = cold.library.first!
        let url = f.root.appendingPathComponent("State/voice/\(value.id).json")
        let bytes = try Data(contentsOf: url)
        try Data("broken manifest".utf8).write(to: url)
        await cold.selectSummary(summary)
        XCTAssertNil(cold.recording)
        XCTAssertNotNil(cold.errorMessage)
        try bytes.write(to: url)
        await cold.selectSummary(summary)
        XCTAssertEqual(cold.recording?.id, value.id)
        await cold.loadHistory()
        let version = cold.history.first!
        let historyURL = f.root.appendingPathComponent("State/voice-corrections/\(value.id)/\(version.id).json")
        try Data("broken historical version".utf8).write(to: historyURL)
        await cold.showHistory(version)
        XCTAssertNotNil(cold.historyError)
        XCTAssertNil(cold.selectedHistory)
        let audio = await cold.export(value.segments[0])
        XCTAssertEqual(audio, Data("controlled synthetic audio".utf8))
    }
}

extension VoiceViewModelTests {
    func testLoopThreeEndIsDurableBeforeHeldValidationFinishesAndColdRecoveryCannotResume() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); f.service.captureLoss = true
        await voice.record()
        f.service.holdValidation = true
        let stopped = Task { await voice.pause(ending: true) }
        await spin { f.service.validation != nil }
        XCTAssertEqual(f.service.stopCount, 1)
        XCTAssertFalse(voice.hardwareStopAvailable)
        let retained = try await f.store.voiceManifest(voice.recording!.id)
        XCTAssertTrue(retained.ended)
        let cold = VoiceViewModel(store: f.store, services: f.service)
        await cold.select(retained)
        XCTAssertEqual(cold.phase, .ended)
        XCTAssertFalse(cold.canRecord)
        f.service.holdValidation = false
        f.service.validation?.resume(returning: 2.75); f.service.validation = nil
        await stopped.value
        XCTAssertEqual(voice.phase, .ended)
        let finished = try await f.store.voiceManifest(retained.id)
        // A cold recovery could have finalized with unknown evidence before the original task resumes.
        XCTAssertTrue(finished.ended)
        XCTAssertTrue(finished.segments[0].finalized)
        XCTAssertEqual(finished.segments[0].captureEvidence?.rejectedFrames, 137)
        XCTAssertEqual(finished.segments[0].captureEvidence?.unassignedFrames, 29)
        XCTAssertNil(finished.segments[0].captureEvidenceUnknown)
        XCTAssertEqual(f.service.starts.count, 1)
    }

    func testLoopThreeImmediateStopDuringNativeTransitionRetainsBothReceivedPieces() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); f.service.captureLoss = true
        await voice.record()
        f.service.holdRotation = true
        f.service.event?(f.service.starts[0], .rotate)
        await spin { f.service.rotation != nil }
        XCTAssertEqual(f.service.starts.count, 2)
        await voice.pause(ending: true)
        XCTAssertEqual(f.service.stopCount, 1)
        XCTAssertFalse(voice.hardwareStopAvailable)
        await spin { voice.endRequestRetained }
        let manifest = try await f.store.voiceManifest(voice.recording!.id)
        XCTAssertTrue(manifest.ended)
        f.service.rotation?.resume(); f.service.rotation = nil
        await spin { voice.phase == .ended && !voice.busy }
        XCTAssertEqual(voice.phase, .ended)
        XCTAssertEqual(voice.recording?.segments.filter(\.finalized).count, 2)
        XCTAssertTrue(voice.recording!.segments.allSatisfy { $0.captureEvidence?.rejectedFrames == 137 })
        XCTAssertEqual(f.service.starts.count, 2)
    }

    func testLoopThreeActiveCaptureRejectsHeldSkipRetryAndPauseStillFinishes() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let initial = try await f.seed(); await f.recordPiece(initial)
        let value = initial.recording!, reference = try await f.store.voiceReferences(initial.recording!.id)[0]
        try await f.store.skipVoiceTranscript(value.id, media: reference)
        var waiting: CheckedContinuation<Void, Never>?, completed = false
        let voice = VoiceViewModel(store: f.store, services: f.service, verifySkip: { id, segment in
            let url = try await f.store.verifiedVoiceURL(id, segmentID: segment)
            await withCheckedContinuation { waiting = $0 }
            return url
        })
        await voice.select(value); await voice.record()
        let retry = Task { await voice.retrySkippedPiece(reference); completed = true }
        await spin { waiting != nil || completed }
        let verificationWasReached = waiting != nil
        await voice.pause()
        waiting?.resume(); await retry.value
        await spin { !voice.busy }
        XCTAssertFalse(verificationWasReached)
        XCTAssertEqual(voice.phase, .paused)
        XCTAssertEqual(voice.recording?.segments.filter(\.finalized).count, 2)
        XCTAssertFalse(voice.captureStopPending)
        let manifest = try await f.store.voiceManifest(value.id)
        XCTAssertEqual(manifest.skippedTranscripts, [reference])
    }

    func testLoopThreeTodaySingleActionPersistsTargetPresentsAndRequestsPermissionOnce() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        await f.diary.start(); f.diary.updateText("Latest typed draft before microphone")
        f.service.holdPermission = true
        var presentations = 0
        let start = Task { await f.diary.beginVoiceCapture { presentations += 1 } }
        await spin { f.service.permission != nil }
        XCTAssertEqual(presentations, 1)
        XCTAssertEqual(f.service.permissionRequests, 1)
        let voice = f.diary.voice!, target = voice.recording!.target
        let stored = try await f.store.open(target.day).draft!
        XCTAssertEqual(stored.text, "Latest typed draft before microphone")
        XCTAssertEqual(target.day, f.day)
        XCTAssertEqual(Data(target.section.utf8), Data(f.diary.section.utf8))
        XCTAssertEqual(voice.phase, .permission)
        f.service.permission?.resume(returning: true); f.service.permission = nil
        await start.value
        XCTAssertEqual(f.service.starts.count, 1)
        XCTAssertEqual(voice.phase, .recording)
        await voice.pause(ending: true)
    }

    func testLoopThreeTodayCancelledLatePermissionAndFailedStartKeepCapturedInput() async throws {
        for outcome in 0..<3 {
            let f = try VoiceStateFixture(); defer { f.cleanup() }
            await f.diary.start(); f.diary.updateText("Retain target and input")
            f.service.holdPermission = outcome == 0
            f.service.granted = outcome != 1
            f.service.failStart = outcome == 2
            var presented = false
            let action = Task { await f.diary.beginVoiceCapture { presented = true } }
            if outcome == 0 {
                await spin { f.service.permission != nil }
                f.diary.voice?.cancelPermission()
                f.service.permission?.resume(returning: true); f.service.permission = nil
            }
            await action.value
            XCTAssertTrue(presented)
            XCTAssertEqual(f.service.permissionRequests, 1)
            XCTAssertTrue(f.service.starts.isEmpty)
            XCTAssertEqual(f.diary.voice?.recording?.target.day, f.day)
            XCTAssertEqual(f.diary.text, "Retain target and input")
            XCTAssertEqual(f.diary.voice?.phase, .paused)
            if outcome > 0 { XCTAssertNotNil(f.diary.voice?.errorMessage) }
        }
    }
}

extension VoiceViewModelTests {
    func testLoopThreeUnknownInsertionRejectsNewSkipRecognitionAndCaptureWithoutChangingEvidence() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        let id = voice.recording!.id, refs = try await f.store.voiceReferences(voice.recording!.id)
        voice.transcribe(); await spin { f.service.speech != nil }
        f.service.speech?.resume(returning: ""); f.service.speech = nil
        await spin { !voice.transcribing }
        XCTAssertNotNil(voice.emptyPiece)
        await f.diary.prepareVoiceInsertion()
        let candidate = voice.preview!
        f.fault.arm(.draft, .beforeWrite)
        _ = await f.diary.insertVoice(candidate)
        XCTAssertTrue(f.diary.isInsertionUnknown)
        let original = try await f.store.voiceManifest(id)
        let speechCount = f.service.speechURLs.count, starts = f.service.starts.count
        await voice.skipAndContinueTranscription()
        voice.transcribe(); await voice.record()
        XCTAssertEqual(f.service.speechURLs.count, speechCount)
        XCTAssertEqual(f.service.starts.count, starts)
        do { try await f.store.skipVoiceTranscript(id, media: refs[0]); XCTFail("Unknown insertion accepted skip mutation") }
        catch { XCTAssertEqual(error as? JournalError, .pendingOperation) }
        let after = try await f.store.voiceManifest(id)
        XCTAssertEqual(after, original)
        await f.diary.retryVoiceInsertion()
        XCTAssertFalse(f.diary.isInsertionUnknown)
        XCTAssertTrue(f.diary.text.contains(candidate.preview))
    }
}

extension VoiceViewModelTests {
    func testLoopFourPermissionOnlyManualCorrectionSurvivesNormalSaveAndRelaunch() async throws {
        for startFailure in [false, true] {
            let f = try VoiceStateFixture(); defer { f.cleanup() }
            let voice = try await f.seed(), target = voice.recording!.target
            f.service.granted = startFailure; f.service.failStart = startFailure
            await voice.record()
            XCTAssertEqual(f.service.permissionRequests, 1); XCTAssertEqual(f.service.starts.count, 0)
            XCTAssertEqual(voice.recording?.target, target)
            voice.updateCorrection("手动保留的原文 e\u{301}")
            await f.diary.prepareVoiceInsertion()
            let candidate = try XCTUnwrap(voice.preview)
            XCTAssertTrue(candidate.media.isEmpty); XCTAssertTrue(candidate.preview.contains("录音资料不完整"))
            let inserted = await f.diary.insertVoice(candidate)
            XCTAssertTrue(inserted)
            await f.diary.save()
            let reopened = DiaryViewModel(storageFactory: { f.store }, voiceServices: ControlledVoiceServices())
            await reopened.start()
            let bytes = try await f.store.exportMarkdown(f.day)
            XCTAssertTrue(String(decoding: bytes, as: UTF8.self).contains(candidate.preview))
            let status = try await f.store.voiceInsertion(for: candidate.recordingID)
            XCTAssertEqual(status?.insertionID, candidate.id)
        }
    }

    func testLoopFourRelaunchRecoversOrphanPieceWithoutInvokingDifferentSecondRecognition() async throws {
        for stage in [FileStage.beforeReplace, .afterReplace] {
            let f = try VoiceStateFixture(); defer { f.cleanup() }
            let voice = try await f.seed(); await f.recordPiece(voice)
            voice.transcribe(); await spin { f.service.speech != nil }
            f.fault.arm(.voice, stage)
            f.service.speech?.resume(returning: "Immutable original first result"); f.service.speech = nil
            await spin { !voice.transcribing }
            let coldService = ControlledVoiceServices(), cold = VoiceViewModel(store: f.store, services: coldService)
            await cold.select(voice.recording!)
            cold.transcribe(); await spin { !cold.transcribing }
            XCTAssertEqual(coldService.speechURLs.count, 0)
            XCTAssertNil(coldService.speech)
            XCTAssertEqual(cold.recording?.transcripts.last?.text, "Immutable original first result")
            XCTAssertEqual(cold.correction, "Exact corrected transcript")
            XCTAssertEqual(cold.retainedPieceCount, 1)
        }
    }

    func testLoopFourRecognitionReceivesImmutableOriginalAcrossReplaceRestoreAndCancellation() async throws {
        for cancel in [false, true] {
            let f = try VoiceStateFixture(); defer { f.cleanup() }
            let voice = try await f.seed(); await f.recordPiece(voice)
            let record = voice.recording!, segment = record.segments[0]
            let original = try await f.store.verifiedVoiceURL(record.id, segmentID: segment.id)
            let bytes = try Data(contentsOf: original)
            voice.transcribe(); await spin { f.service.speech != nil }
            try Data("replacement while recognition callback is held".utf8).write(to: original)
            XCTAssertEqual(f.service.speechSources, [bytes])
            try bytes.write(to: original)
            if cancel { voice.cancelTranscription() }
            f.service.speech?.resume(returning: "Synthetic verified-source result"); f.service.speech = nil
            await spin { !voice.transcribing }; await Task.yield()
            let retained = try await f.store.recording(record.id)
            XCTAssertEqual(retained.segmentTranscriptReferences?.count ?? 0, cancel ? 0 : 1)
            XCTAssertEqual(retained.correctedText, "Exact corrected transcript")
            XCTAssertEqual(try Data(contentsOf: original), bytes)
        }
    }

    func testLoopFourFailedEndDeletionReleasesOnlyDurablyTombstonedOwnership() async throws {
        for removalFailure in [false, true] {
            let f = try VoiceStateFixture(); defer { f.cleanup() }
            let voice = try await f.seed()
            voice.updateCorrection("Text survives failed End and deletion")
            _ = await voice.flushCorrection(); await voice.record()
            f.fault.arm(.voice, .beforeReplace)
            await voice.pause(ending: true)
            XCTAssertTrue(voice.stopNeedsRetry)
            let id = voice.recording!.id
            if removalFailure { f.fault.arm(.audio, .beforeRemove) }
            await voice.deleteAudio()
            XCTAssertTrue(voice.recording?.deletionRequested == true)
            XCTAssertFalse(voice.stopNeedsRetry); XCTAssertFalse(voice.captureStopPending)
            XCTAssertEqual(voice.audioRemovalComplete, !removalFailure)
            if removalFailure { XCTAssertTrue(voice.status.contains("待重试")) }
            let close = await voice.dismiss(); XCTAssertTrue(close)
            let new = await f.diary.captureVoice(); XCTAssertTrue(new)
            XCTAssertNotEqual(voice.recording?.id, id)
            let previous = try await f.store.recording(id)
            XCTAssertEqual(previous.correctedText, "Text survives failed End and deletion")
            XCTAssertTrue(previous.ended)
        }
    }

    func testLoopFourPersistentCleanupFailureKeepsSavedCorrectionUsableForCloseAndInsertion() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        for _ in 0..<8 { f.fault.arm(.voiceCorrection, .beforeRemove) }
        voice.updateCorrection("Verified current while obsolete draft cleanup fails")
        let retained = await voice.flushCorrection()
        XCTAssertTrue(retained); XCTAssertTrue(voice.correctionCleanupPending)
        let close = await voice.dismiss(); XCTAssertTrue(close)
        await f.diary.prepareVoiceInsertion()
        let candidate = try XCTUnwrap(voice.preview)
        XCTAssertEqual(candidate.correction.text, "Verified current while obsolete draft cleanup fails")
        let current = try await f.store.recording(candidate.recordingID)
        XCTAssertEqual(current.correctedText, candidate.correction.text)
    }
}

extension VoiceViewModelTests {
    func testLoopFourUnavailableRetainedPieceNeedsExplicitIncompleteRetryAndKeepsIntactOriginalUsable() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        await voice.record(); await voice.pause()
        voice.transcribe(); await spin { f.service.speech != nil }
        f.service.speech?.resume(returning: "First retained source"); f.service.speech = nil
        await spin { f.service.speech != nil }
        f.service.speech?.resume(returning: "Second retained source"); f.service.speech = nil
        await spin { !voice.transcribing }
        let record = voice.recording!, first = record.segments[0], second = record.segments[1]
        let firstURL = try await f.store.verifiedVoiceURL(record.id, segmentID: first.id)
        let original = try Data(contentsOf: firstURL)
        try FileManager.default.removeItem(at: firstURL)
        voice.transcribe(); await spin { !voice.transcribing }
        XCTAssertNotNil(voice.errorMessage)
        voice.transcribe(allowingUnavailable: true); await spin { !voice.transcribing }
        XCTAssertEqual(f.service.speechURLs.count, 2)
        let partial = try XCTUnwrap(voice.recording?.transcripts.last)
        XCTAssertEqual(partial.text, "Second retained source")
        XCTAssertEqual(partial.media.map(\.segmentID), [second.id])
        XCTAssertEqual(partial.inventory?.map(\.segmentID), [first.id, second.id])
        XCTAssertTrue(partial.inventory?.first?.unavailable == true)
        XCTAssertTrue(VoiceSourceNotices.text(partial.inventory!).contains("第 1 段尚不可用"))
        let stillPlayable = await voice.export(second)
        XCTAssertEqual(stillPlayable, Data("controlled synthetic audio".utf8))
        voice.adopt(partial); _ = await voice.flushCorrection()
        await f.diary.prepareVoiceInsertion()
        let candidate = try XCTUnwrap(voice.preview)
        XCTAssertTrue(candidate.preview.contains("第 1 段尚不可用"))
        try original.write(to: firstURL)
        let stale = await f.diary.insertVoice(candidate)
        XCTAssertFalse(stale)
        XCTAssertEqual(try Data(contentsOf: firstURL), original)
    }

    func testLoopFourPagedHistoricalProposalRetainsWarningsBeforeAdoption() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        let id = voice.recording!.id, media = try await f.store.voiceReferences(id)
        let missing = UUID()
        _ = try await f.store.reserveVoiceSegment(id, id: missing)
        let inventory = try await f.store.voiceSourceInventory(id)
        var originals: [VoiceTranscript] = []
        for index in 0..<5 {
            var proposal = VoiceTranscript(id: UUID(), media: media, text: String(repeating: "original \(index) ", count: 28000))
            proposal.inventory = inventory
            try await f.store.retainVoiceTranscript(id, proposal: proposal); originals.append(proposal)
        }
        await voice.select(voice.recording!)
        XCTAssertEqual(voice.recording?.transcripts.count, 1)
        await voice.loadTranscriptHistory()
        await voice.showTranscript(voice.transcriptHistory[0])
        XCTAssertEqual(voice.selectedTranscript, originals[0])
        let selected = try XCTUnwrap(voice.selectedTranscript)
        XCTAssertTrue(VoiceSourceNotices.text(selected.inventory!).contains("第 2 段尚不可用"))
        voice.adopt(selected)
        XCTAssertEqual(voice.correction, originals[0].text)
        await f.diary.prepareVoiceInsertion()
        XCTAssertTrue(voice.preview?.preview.contains("第 2 段尚不可用") == true)
    }
}

extension VoiceViewModelTests {
    func testLoopFourPartialLibraryRetryKeepsExistingRowsAndContinuesBoundedColdRebuild() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        await voice.loadLibrary()
        let initial = voice.library.map(\.id)
        for _ in 0..<24 {
            var value = try await f.store.createRecording(id: UUID(), target: voice.recording!.target)
            value.corrections = (0..<3).map { _ in VoiceCorrection(text: String(repeating: "z", count: 490000)) }
            try JSONEncoder().encode(value).write(to: f.root.appendingPathComponent("State/voice/\(value.id).json"))
        }
        await voice.restartLibrary()
        XCTAssertGreaterThan(voice.libraryRebuildingCount, 0)
        XCTAssertEqual(voice.library.map(\.id), initial)
        f.fault.arm(.voiceSummary, .beforeRead)
        await voice.loadLibrary()
        XCTAssertNotNil(voice.libraryError)
        XCTAssertEqual(voice.library.map(\.id), initial)
        XCTAssertEqual(voice.libraryPageNumber, 1)
        for _ in 0..<24 where voice.libraryRebuildingCount > 0 { await voice.loadLibrary() }
        XCTAssertEqual(voice.libraryRebuildingCount, 0)
        XCTAssertNil(voice.libraryError); XCTAssertEqual(voice.library.count, 20)
        await voice.nextLibraryPage()
        XCTAssertEqual(voice.libraryPageNumber, 2)
        XCTAssertTrue(voice.library.contains { initial.contains($0.id) })
        let summary = try XCTUnwrap(voice.library.first { initial.contains($0.id) })
        await voice.selectSummary(summary)
        let exported = await voice.export(voice.recording!.segments[0])
        XCTAssertEqual(exported, Data("controlled synthetic audio".utf8))
    }
}

extension VoiceViewModelTests {
    func testLoopFourCaptureEvidenceKeepsItsTokenAcrossCancelledRecognitionGeneration() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); f.service.captureLoss = true
        await voice.record()
        f.fault.arm(.audio, .beforeRead)
        await voice.pause(ending: true)
        XCTAssertTrue(voice.stopNeedsRetry)
        let originalToken = f.service.starts[0]
        voice.cancelTranscription()
        await voice.retryRecovery(); await spin { voice.phase == .ended && !voice.busy }
        let evidence = voice.recording?.segments[0].captureEvidence
        XCTAssertEqual(evidence?.streamID, originalToken.generation)
        XCTAssertEqual(evidence?.rejectedFrames, 137); XCTAssertEqual(evidence?.unassignedFrames, 29)
        XCTAssertFalse(voice.recording?.segments[0].captureEvidenceUnknown == true)
        XCTAssertEqual(f.service.starts.count, 1)
    }
}

extension VoiceViewModelTests {
    func testLoopFiveNextTokenInterruptionBeforeRotationReturnsStopsOwnedStream() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed()
        await voice.record()
        f.service.captureLoss = true; f.service.holdRotation = true
        let previous = f.service.starts[0]
        f.service.event?(previous, .rotate)
        await spin { f.service.rotation != nil }
        let next = f.service.starts[1]
        f.service.event?(VoiceCaptureToken(recordingID: UUID(), segmentID: next.segmentID, generation: next.generation), .interrupted)
        await Task.yield(); XCTAssertEqual(f.service.stopCount, 0)
        f.service.event?(next, .interrupted)
        await spin { f.service.stopCount == 1 }
        XCTAssertEqual(voice.phase, .paused)
        f.service.rotation?.resume(); f.service.rotation = nil
        await spin { !voice.busy && !voice.captureStopPending }
        XCTAssertEqual(f.service.starts.count, 2); XCTAssertEqual(f.service.stopCount, 1)
        let recovered = try await f.store.recording(previous.recordingID)
        XCTAssertEqual(recovered.segments.filter(\.finalized).count, 2)
        XCTAssertEqual(recovered.segments.map { $0.captureEvidence?.rejectedFrames }, [137, 137])
        XCTAssertTrue(recovered.segments.allSatisfy { $0.captureEvidence?.streamID == previous.generation })
        f.service.holdRotation = false
        await voice.record(); XCTAssertEqual(f.service.starts.count, 3)
        await voice.pause()
    }

    func testLoopFivePermissionIsCancelledBeforeHeldCloseSelectAttachFlushEvenWhenFlushFails() async throws {
        for action in 0..<3 {
            for failure in [false, true] {
                let f = try VoiceStateFixture(); defer { f.cleanup() }
                let voice = try await f.seed()
                voice.updateCorrection("Retained input before permission"); _ = await voice.flushCorrection()
                let target = voice.recording!.target, value = voice.recording!
                f.service.holdPermission = true
                let record = Task { await voice.record() }
                await spin { f.service.permission != nil }
                let gate = VoicePageReadGate(); defer { gate.open() }
                let held = Task { await f.store.holdVoicePageReads(gate) }
                await fulfillment(of: [gate.entered], timeout: 2)
                if failure { f.fault.arm(.voice, .beforeWrite) }
                let transition = Task {
                    if action == 0 { return await voice.dismiss() }
                    if action == 1 { await voice.select(value); return !voice.busy }
                    return await voice.attach(target: target)
                }
                await spin { voice.busy && voice.phase == .paused }
                f.service.permission?.resume(returning: true); f.service.permission = nil
                await record.value
                XCTAssertTrue(f.service.starts.isEmpty)
                gate.open(); _ = await held.value; _ = await transition.value
                XCTAssertTrue(f.service.starts.isEmpty); XCTAssertFalse(voice.hardwareStopAvailable)
                let original = try await f.store.recording(value.id)
                XCTAssertEqual(original.correctedText, "Retained input before permission")
                if action == 2 && !failure { XCTAssertNotEqual(voice.recording?.id, value.id); XCTAssertEqual(voice.correction, "") }
                else { XCTAssertEqual(voice.correction, "Retained input before permission") }
                XCTAssertEqual(voice.recording?.target, target)
                if failure { XCTAssertNotNil(voice.errorMessage) }
                f.service.holdPermission = false
                await voice.record(); XCTAssertEqual(f.service.starts.count, 1)
                await voice.pause()
            }
        }
    }

    func testLoopFiveAdoptionKeepsHistoricalProvenanceAcrossManualEditAndRelaunch() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await voice.importAudio(Data("intact synthetic imported audio".utf8))
        let id = voice.recording!.id, media = try await f.store.voiceReferences(id)
        var proposal = VoiceTranscript(id: UUID(), media: media, text: "Exact original e\u{301}")
        proposal.inventory = try await f.store.voiceSourceInventory(id)
        try await f.store.retainVoiceTranscript(id, proposal: proposal)
        await voice.select(voice.recording!)
        voice.adopt(proposal)
        XCTAssertEqual(Data(voice.correction.utf8), Data(proposal.text.utf8)); XCTAssertEqual(voice.correctionOrigin?.versionID, proposal.id)
        voice.updateCorrection("Later manual edit")
        let flushed = await voice.flushCorrection(); XCTAssertTrue(flushed)
        let restored = VoiceViewModel(store: f.store, services: ControlledVoiceServices())
        await restored.select(try await f.store.recording(id))
        XCTAssertEqual(restored.correction, "Later manual edit"); XCTAssertEqual(restored.correctionOrigin, voice.correctionOrigin)
        let preview = await restored.prepareInsertion(draft: try await f.store.open(f.day).draft!)
        XCTAssertTrue(preview?.preview.contains("校正文字的历史来源") == true)
        XCTAssertFalse(preview?.preview.contains("历史原稿说明：") == true)
    }

    func testLoopFiveMalformedTemporaryObjectRemainsVisibleAfterCleanupRetry() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        let id = voice.recording!.id
        let folder = f.root.appendingPathComponent("State/voice-correction-drafts/\(id)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let url = folder.appendingPathComponent("\(UUID()).json")
        let bytes = Data("malformed retained old temporary object".utf8); try bytes.write(to: url)
        await voice.retryCorrectionCleanup()
        XCTAssertTrue(voice.correctionCleanupPending); XCTAssertEqual(try Data(contentsOf: url), bytes)
        let canClose = await voice.dismiss(); XCTAssertTrue(canClose)
        XCTAssertEqual(voice.correction, "Exact corrected transcript")
        await f.diary.prepareVoiceInsertion(); XCTAssertNotNil(voice.preview)
    }

    func testLoopFiveLargeIntegrityCountersAndPendingPlaybackRemainUsable() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        let id = voice.recording!.id, url = f.root.appendingPathComponent("State/voice/\(id).json")
        var object = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [String: Any]
        var segments = object["segments"] as! [[String: Any]]
        segments[0]["captureEvidence"] = ["firstFrame": 0, "frameCount": 1, "sampleRate": 48000, "rejectedFrames": Int64.max, "unassignedFrames": Int64.max]
        object["segments"] = segments
        try JSONSerialization.data(withJSONObject: object).write(to: url)
        var release: CheckedContinuation<Void, Never>?
        let delayed = VoiceViewModel(store: f.store, services: f.service, verifyPlayback: { id, segment in
            let data = try await f.store.verifiedVoicePlayback(id, segmentID: segment)
            await withCheckedContinuation { release = $0 }; return data
        })
        await delayed.select(try await f.store.recording(id))
        XCTAssertNotNil(delayed.integrityWarning)
        let play = Task { await delayed.play(delayed.recording!.segments[0]) }
        await spin { release != nil }
        XCTAssertEqual(delayed.playbackState, .verifying)
        delayed.stopPlayback(); XCTAssertEqual(delayed.playbackState, .stopped)
        release?.resume(); await play.value
        XCTAssertTrue(f.service.plays.isEmpty)
    }

    func testLoopFiveHealthyLibraryExplicitRefreshUpdatesSameSheetSummaries() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed()
        await voice.loadLibrary(); XCTAssertEqual(voice.library.first?.segmentCount, 0)
        await f.recordPiece(voice)
        await voice.restartLibrary(); XCTAssertEqual(voice.library.first?.segmentCount, 1)
        await voice.deleteAudio(); await voice.restartLibrary()
        XCTAssertTrue(voice.library.first?.deletionRequested == true)
    }
}

extension VoiceViewModelTests {
    func testLoopFiveActiveDerivedCopyKeepsDeletionPendingUntilLeaseFinishes() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let voice = try await f.seed(); await f.recordPiece(voice)
        let value = voice.recording!, cache = f.store.derivedVoiceCache
        let lease = try cache.snapshot(data: Data("synthetic active decoder bytes".utf8), recordingID: value.id,
            segmentID: value.segments[0].id, fileExtension: "wav")
        await voice.deleteAudio()
        XCTAssertTrue(voice.recording?.deletionRequested == true); XCTAssertFalse(voice.audioRemovalComplete)
        XCTAssertTrue(voice.status.contains("部分移除待重试")); XCTAssertEqual(voice.correction, "Exact corrected transcript")
        try lease.finish(); await voice.deleteAudio()
        XCTAssertTrue(voice.audioRemovalComplete); XCTAssertTrue(voice.status.contains("原音频已删除"))
    }
}


extension VoiceViewModelTests {
    func testLoopFiveDamagedAdoptedHistoryAllowsCurrentFlushEditDismissAndRelaunch() async throws {
        for missing in [false, true] {
            let f = try VoiceStateFixture(); defer { f.cleanup() }
            let voice = try await f.seed(); await voice.importAudio(Data("intact synthetic imported audio".utf8))
            let id = voice.recording!.id, media = try await f.store.voiceReferences(id)
            var proposal = VoiceTranscript(id: UUID(), media: media, text: "Exact retained e\u{301}\n历史原文")
            proposal.inventory = try await f.store.voiceSourceInventory(id)
            try await f.store.retainVoiceTranscript(id, proposal: proposal)
            await voice.select(voice.recording!)
            voice.adopt(proposal)
            let initial = await voice.flushCorrection(); XCTAssertTrue(initial)
            let sourceURL = f.root.appendingPathComponent("State/voice-transcripts/\(id)/proposals/\(proposal.id).json")
            if missing { try FileManager.default.removeItem(at: sourceURL) }
            else { try Data("damaged historical proposal".utf8).write(to: sourceURL) }
            let flush = await voice.flushCorrection(); XCTAssertTrue(flush)
            voice.updateCorrection(proposal.text + "\nManual continuation")
            let edited = await voice.flushCorrection(); XCTAssertTrue(edited)
            XCTAssertTrue(voice.correctionOriginNotice!.contains(VoiceCorrectionOrigin.unavailableNotice))
            let dismissed = await voice.dismiss(); XCTAssertTrue(dismissed)
            let cold = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"))
            let restored = VoiceViewModel(store: cold, services: ControlledVoiceServices())
            await restored.select(try await cold.recording(id))
            XCTAssertEqual(restored.correction, proposal.text + "\nManual continuation")
            XCTAssertEqual(restored.correctionOrigin, voice.correctionOrigin)
            XCTAssertTrue(restored.correctionOriginNotice!.contains(VoiceCorrectionOrigin.unavailableNotice))
            let preview = await restored.prepareInsertion(draft: try await cold.open(f.day).draft!)
            XCTAssertTrue(preview?.preview.contains(VoiceCorrectionOrigin.unavailableNotice) == true)
            XCTAssertTrue(restored.canEditRecording)
            // The still-loaded pre-damage proposal cannot become a newly adopted current version.
            voice.adopt(proposal)
            let newAdoption = await voice.flushCorrection(); XCTAssertFalse(newAdoption)
            XCTAssertNotNil(voice.errorMessage)
            let unchanged = try await cold.recording(id)
            XCTAssertEqual(unchanged.correctedText, restored.correction)
        }
    }
}


extension VoiceViewModelTests {
    func testLoopFiveTodayEntryAutomaticallyRepairsColdEvidenceAndCancelsWithoutLateCapture() async throws {
        for retainedHint in [false, true] {
        for action in ["continue", "cancel", "dismiss", "select", "attach", "corrupt"] {
            let f = try VoiceStateFixture(); defer { f.cleanup() }
            let prior = try await f.seed(), target = prior.recording!.target
            let other = try await f.store.createRecording(id: UUID(), target: target)
            if !retainedHint { try FileManager.default.removeItem(at: f.root.appendingPathComponent("State/voice-preparations")) }
            var lastURL: URL!, lastBytes: Data!
            for index in 0..<70 {
                let id = UUID(uuidString: String(format: "00000000-0000-0000-0000-%012d", index))!
                let url = f.root.appendingPathComponent("State/receipts/\(id).json")
                let bytes = try JSONSerialization.data(withJSONObject: ["operationID": id.uuidString, "payloadHash": String(repeating: "a", count: 64), "day": f.day.value, "afterHash": String(repeating: "b", count: 64)], options: [.sortedKeys])
                try bytes.write(to: url)
                lastURL = url; lastBytes = bytes
            }
            if action == "corrupt" { try Data("malformed ordinary receipt".utf8).write(to: lastURL) }
            let gate = LoopFiveAutomaticEvidenceGate()
            let cold = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"),
                failure: { purpose, stage in if purpose == .voiceEvidence && stage == .beforeRead { gate.visit() } })
            let diary = DiaryViewModel(now: { ISO8601DateFormatter().date(from: "2026-09-12T08:00:00Z")! }, storageFactory: { cold }, voiceServices: f.service)
            await diary.start()
            var presented = false
            let capture = Task { await diary.beginVoiceCapture { presented = true } }
            await spin { presented && gate.waiting }
            let voice = diary.voice!
            XCTAssertTrue(presented); XCTAssertTrue(voice.insertionVerifying)
            XCTAssertFalse(voice.insertionKnown); XCTAssertFalse(voice.preventsDismissal)
            XCTAssertFalse(diary.isBusy); XCTAssertEqual(f.service.permissionRequests, 0)
            var transition: Task<Bool, Never>?
            switch action {
            case "cancel": voice.cancelInsertionVerification()
            case "dismiss": transition = Task { await voice.dismiss() }
            case "select": transition = Task { await voice.select(other); return true }
            case "attach": transition = Task { await voice.attach(target: target) }
            default: break
            }
            if ["dismiss", "select", "attach"].contains(action) { await spin { !voice.insertionVerifying } }
            gate.open()
            await capture.value
            if let transition { let result = await transition.value; XCTAssertTrue(result) }
            XCTAssertEqual(diary.text, "Existing typed text")
            let retainedDraft = try await cold.open(f.day).draft!
            let retainedTarget = try await cold.voiceTarget(retainedDraft)
            XCTAssertEqual(retainedTarget, target)
            if retainedHint && ["cancel", "dismiss", "corrupt"].contains(action) { XCTAssertNil(voice.recording) }
            else { XCTAssertEqual(voice.recording?.target, target) }
            if action == "continue" {
                XCTAssertEqual(f.service.permissionRequests, 1); XCTAssertEqual(f.service.starts.count, 1)
                XCTAssertEqual(voice.phase, .recording); XCTAssertTrue(voice.insertionKnown)
                await voice.pause()
            } else {
                XCTAssertEqual(f.service.permissionRequests, 0); XCTAssertTrue(f.service.starts.isEmpty)
                if action == "select" { XCTAssertEqual(voice.recording?.id, other.id) }
                if action == "corrupt" {
                    XCTAssertFalse(voice.insertionKnown); XCTAssertFalse(voice.canEditRecording)
                    XCTAssertNotNil(voice.errorMessage)
                    try lastBytes.write(to: lastURL)
                }
                if action == "cancel" || action == "corrupt" {
                    if voice.recording == nil { let prepared = await voice.attach(target: target); XCTAssertTrue(prepared) }
                    await voice.record()
                    XCTAssertEqual(f.service.starts.count, 1)
                    await voice.pause()
                }
            }
            XCTAssertFalse(voice.insertionVerifying)
        }
        }
    }
}

private final class LoopFiveAutomaticEvidenceGate: @unchecked Sendable {
    private let lock = NSLock()
    private let semaphore = DispatchSemaphore(value: 0)
    private var count = 0
    private var held = false
    var waiting: Bool { lock.lock(); defer { lock.unlock() }; return held }
    func visit() {
        lock.lock(); count += 1
        let stop = count == 65
        if stop { held = true }
        lock.unlock()
        if stop { semaphore.wait() }
    }
    func open() { semaphore.signal() }
}


extension VoiceViewModelTests {
    func testLoopFiveDeferredTodayRequestCannotReleaseNewDiaryPreparationOwnership() async throws {
        let f = try VoiceStateFixture(); defer { f.cleanup() }
        let prior = try await f.seed(), originalTarget = prior.recording!.target
        for index in 0..<70 {
            let id = UUID(uuidString: String(format: "00000000-0000-0000-0000-%012d", index))!
            let bytes = try JSONSerialization.data(withJSONObject: ["operationID": id.uuidString, "payloadHash": String(repeating: "a", count: 64), "day": f.day.value, "afterHash": String(repeating: "b", count: 64)], options: [.sortedKeys])
            try bytes.write(to: f.root.appendingPathComponent("State/receipts/\(id).json"))
        }
        let evidence = LoopFiveAutomaticEvidenceGate(), replacement = LoopFiveDraftPreparationGate()
        let cold = try LocalJournalStore(journalDirectory: f.root.appendingPathComponent("Journals"), stateDirectory: f.root.appendingPathComponent("State"), failure: { purpose, stage in
            if purpose == .voiceEvidence && stage == .beforeRead { evidence.visit() }
            if purpose == .draft && stage == .beforeWrite { replacement.visit() }
        })
        let diary = DiaryViewModel(now: { ISO8601DateFormatter().date(from: "2026-09-12T08:00:00Z")! }, storageFactory: { cold }, voiceServices: f.service)
        await diary.start()
        var presentations = 0
        let old = Task { await diary.beginVoiceCapture { presentations += 1 } }
        await spin { presentations == 1 && evidence.waiting }
        XCTAssertFalse(diary.isBusy); XCTAssertTrue(diary.voice!.insertionVerifying)
        replacement.arm()
        let newSection = try XCTUnwrap(diary.sections.first { $0 != originalTarget.section })
        diary.updateSection(newSection)
        let newer = Task { await diary.captureVoice() }
        await spin { diary.isBusy && !diary.voice!.insertionVerifying }
        evidence.open()
        await spin { replacement.waiting }
        await old.value
        XCTAssertTrue(diary.isBusy, "Old deferred capture released another request's busy ownership")
        XCTAssertEqual(diary.section, newSection); XCTAssertNil(diary.voice!.recording)
        XCTAssertEqual(presentations, 1); XCTAssertEqual(f.service.permissionRequests, 0)
        replacement.open()
        let prepared = await newer.value; XCTAssertTrue(prepared)
        XCTAssertFalse(diary.isBusy)
        XCTAssertEqual(diary.voice!.recording?.target.section, newSection)
        XCTAssertEqual(diary.voice!.recording?.target.day, originalTarget.day)
        XCTAssertEqual(diary.text, "Existing typed text")
        XCTAssertEqual(f.service.permissionRequests, 0); XCTAssertTrue(f.service.starts.isEmpty)
        let retained = try await cold.open(f.day).draft!
        XCTAssertEqual(retained.section, newSection); XCTAssertEqual(retained.text, diary.text)
    }
}

private final class LoopFiveDraftPreparationGate: @unchecked Sendable {
    private let lock = NSLock()
    private let semaphore = DispatchSemaphore(value: 0)
    private var armed = false
    private var held = false
    var waiting: Bool { lock.lock(); defer { lock.unlock() }; return held }
    func arm() { lock.lock(); armed = true; lock.unlock() }
    func visit() {
        lock.lock(); let stop = armed; armed = false
        if stop { held = true }
        lock.unlock()
        if stop { semaphore.wait() }
    }
    func open() { semaphore.signal() }
}
