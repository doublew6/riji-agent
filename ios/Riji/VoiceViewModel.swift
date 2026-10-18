import Foundation
import Combine
import RijiCore

struct VoiceCaptureToken: Equatable, Sendable {
    let recordingID: UUID
    let segmentID: UUID
    let generation: UUID
}

enum VoiceHardwareEvent { case rotate, interrupted }
struct VoicePlaybackToken: Equatable {
    let recordingID: UUID
    let segmentID: UUID
    let generation: UUID
}
enum VoicePlaybackEvent { case finished, stopped, failed }
enum VoicePlaybackState: Equatable { case idle, verifying, playing, finished, stopped, failed }

@MainActor
protocol VoiceServices: AnyObject {
    var event: ((VoiceCaptureToken, VoiceHardwareEvent) -> Void)? { get set }
    var playbackEvent: ((VoicePlaybackToken, VoicePlaybackEvent) -> Void)? { get set }
    func microphonePermission() async -> Bool
    func start(url: URL, token: VoiceCaptureToken) throws
    func stop() -> Task<Double, Error>
    func rotate(url: URL, token: VoiceCaptureToken) async throws -> Double
    func verifiedDuration(of url: URL) async throws -> Double
    func validatedMedia(of url: URL) async throws -> VoiceMediaValidation
    func evidence(for token: VoiceCaptureToken) -> VoiceCaptureEvidence?
    func duration(of url: URL) throws -> Double
    func play(_ data: Data, token: VoicePlaybackToken) throws
    func stopPlayback()
    func transcribe(_ source: VoiceRecognitionSource) async throws -> String
    func cancelTranscription()
}

extension VoiceServices {
    func rotate(url: URL, token: VoiceCaptureToken) async throws -> Double {
        let duration = try await stop().value; try start(url: url, token: token); return duration
    }
    func verifiedDuration(of url: URL) async throws -> Double { try duration(of: url) }
    func evidence(for token: VoiceCaptureToken) -> VoiceCaptureEvidence? { nil }
    // Portable fixture compatibility. The production adapter snapshots and decodes the same bytes.
    func validatedMedia(of url: URL) async throws -> VoiceMediaValidation {
        let bytes = try Data(contentsOf: url)
        return VoiceMediaValidation(data: bytes, duration: try await verifiedDuration(of: url))
    }
}

@MainActor
final class UnavailableVoiceServices: VoiceServices {
    var event: ((VoiceCaptureToken, VoiceHardwareEvent) -> Void)?
    var playbackEvent: ((VoicePlaybackToken, VoicePlaybackEvent) -> Void)?
    func microphonePermission() async -> Bool { false }
    func start(url: URL, token: VoiceCaptureToken) throws { throw VoiceError.unavailable }
    func stop() -> Task<Double, Error> { Task { throw VoiceError.unavailable } }
    func duration(of url: URL) throws -> Double { throw VoiceError.unavailable }
    func play(_ data: Data, token: VoicePlaybackToken) throws { throw VoiceError.unavailable }
    func stopPlayback() {}
    func transcribe(_ source: VoiceRecognitionSource) async throws -> String { throw VoiceError.unavailable }
    func cancelTranscription() {}
}

@MainActor
final class VoiceViewModel: ObservableObject {
    enum Phase: Equatable { case idle, permission, recording, paused, ended }
    @Published private(set) var recording: VoiceRecording?
    @Published private(set) var library: [VoiceRecordingSummary] = []
    @Published private(set) var phase: Phase = .idle
    @Published private(set) var correction = ""
    @Published private(set) var errorMessage: String?
    @Published private(set) var status = ""
    @Published private(set) var busy = false
    @Published private(set) var transcribing = false
    @Published private(set) var preview: VoiceInsertionCandidate?
    @Published private(set) var needsSettings = false
    @Published private(set) var playbackVerified = false
    @Published private(set) var playbackState: VoicePlaybackState = .idle
    @Published private(set) var insertion: VoiceInsertionStamp?
    @Published private(set) var insertionKnown = false
    @Published private(set) var insertionVerifying = false
    private var insertionVerificationID: UUID?
    private var insertionVerificationTask: Task<Void, Never>?
    private var pendingRecordRequest: UUID?
    private var pendingAttachment: UUID?
    @Published private(set) var retainedPieceCount = 0
    @Published private(set) var libraryError: String?
    @Published private(set) var derivedCleanupPending = false
    @Published private(set) var correctionOriginNotice: String?
    private var pendingSourceAdoption: UUID?
    @Published private(set) var libraryUnreadableCount = 0
    @Published private(set) var libraryLoading = false
    @Published private(set) var libraryRebuildingCount = 0
    @Published private(set) var libraryLimitedCount = 0
    @Published private(set) var nextLibraryCursor: String?
    @Published private(set) var libraryPageNumber = 1
    @Published private(set) var emptyPiece: VoiceMediaReference?
    @Published private(set) var history: [VoiceCorrectionReference] = []
    @Published private(set) var nextHistoryOffset: Int?
    @Published private(set) var historyRebuildingCount = 0
    @Published private(set) var historyUnavailableCount = 0
    @Published private(set) var selectedHistory: VoiceCorrection?
    private var libraryCursors: [String?] = [nil]
    private var historySnapshot: String?
    @Published private(set) var audioRemovalComplete = false
    @Published private(set) var liveDuration: Double = 0
    private let store: LocalJournalStore
    private let services: VoiceServices
    private let verifySkip: (UUID, UUID) async throws -> URL
    private let verifyPlayback: (UUID, UUID) async throws -> Data
    private var playbackToken: VoicePlaybackToken?
    private var token: VoiceCaptureToken?
    private var captureBindings: [UUID: VoiceCaptureToken] = [:]
    private var queuedEvent: (VoiceCaptureToken, VoiceHardwareEvent)?
    private var earlyStop: Task<Double, Error>?
    private var interruptedGeneration: UUID?
    private var generation = UUID()
    private var pendingSegment: UUID?
    private var creationIntent: (UUID, VoiceTarget)?
    private var importIntent: (recordingID: UUID, segmentID: UUID, hash: String)?
    private var correctionValue: VoiceCorrection?
    private var persistence: Task<Void, Never>?
    private var skipTask: Task<Void, Never>?
    private var speechTask: Task<Void, Never>?
    private var timer: Timer?
    private var startedAt: Date?
    private var requestedStop: Bool?
    private var endPersistence: Task<Result<Void, Error>, Never>?
    @Published private(set) var stopNeedsRetry = false
    @Published private(set) var endRequestRetained = false
    @Published private(set) var correctionCleanupPending = false
    @Published private(set) var currentInventory: [VoiceSourcePiece] = []
    @Published private(set) var historyError: String?
    @Published private(set) var transcriptHistory: [VoiceTextReference] = []
    @Published private(set) var nextTranscriptOffset: Int?
    @Published private(set) var selectedTranscript: VoiceTranscript?
    @Published private(set) var selectedPiece: VoiceSegmentTranscript?
    @Published private(set) var transcriptHistoryIsSegments = false
    var diaryEditingBlocked: () -> Bool = { false }
    var hardwareStopAvailable: Bool { token != nil && earlyStop == nil }
    var captureStopPending: Bool { requestedStop != nil }
    var integrityWarning: String? {
        guard let value = recording else { return nil }
        if value.segments.contains(where: { ($0.captureEvidence?.rejectedFrames ?? 0) > 0 || ($0.captureEvidence?.unassignedFrames ?? 0) > 0 }) {
            return "采集发生过队列或写入失败，部分帧未写入原件。请核对录音，不代表完整保留。"
        }
        if value.segments.contains(where: { $0.captureEvidenceUnknown == true }) {
            return "恢复音频的采集完整性证据不可用；可播放不代表全部输入已保留。"
        }
        return nil
    }
    var preventsDismissal: Bool { phase == .recording || busy || (stopNeedsRetry && requestedStop == true) }
    var canEditRecording: Bool { insertionKnown && insertion == nil && recording?.currentCorrectionUnavailable != true }
    var canRecord: Bool { canEditRecording && !diaryEditingBlocked() && requestedStop == nil && !busy && !transcribing && recording?.deletionRequested == false && recording?.ended == false }

    init(store: LocalJournalStore, services: VoiceServices? = nil,
         verifyPlayback: ((UUID, UUID) async throws -> Data)? = nil,
         verifySkip: ((UUID, UUID) async throws -> URL)? = nil) {
        self.store = store
        self.verifySkip = verifySkip ?? { try await store.verifiedVoiceURL($0, segmentID: $1) }
        self.verifyPlayback = verifyPlayback ?? { try await store.verifiedVoicePlayback($0, segmentID: $1) }
        #if os(iOS)
        self.services = services ?? NativeVoiceServices(cache: store.derivedVoiceCache)
        #else
        self.services = services ?? UnavailableVoiceServices()
        #endif
        self.services.event = { [weak self] token, event in
            Task { await self?.hardwareEvent(token, event: event) }
        }
        self.services.playbackEvent = { [weak self] token, event in self?.playbackFinished(token, event: event) }
    }

    func loadLibrary() async {
        guard !libraryLoading else { return }
        libraryLoading = true; defer { libraryLoading = false }
        await retryDerivedCleanup()
        await loadLibraryPage(cursor: libraryCursors.last ?? nil)
    }

    func retryDerivedCleanup() async {
        let cache = store.derivedVoiceCache
        let pending = await Task.detached {
            do { try cache.recoverStale(); return try cache.maintenancePending() }
            catch { return true }
        }.value
        derivedCleanupPending = pending
    }

    private func loadLibraryPage(cursor: String?) async {
        do {
            let result = try await store.voiceLibraryBatch(after: cursor)
            libraryRebuildingCount = result.rebuildingCount
            if result.rebuildingCount == 0 || library.isEmpty { library = result.recordings }
            libraryUnreadableCount = result.unreadableCount
            libraryLimitedCount = result.limitedCount
            nextLibraryCursor = result.nextCursor
            libraryError = nil
        } catch { libraryError = safeMessage(error) }
    }
    func nextLibraryPage() async {
        guard !libraryLoading, let cursor = nextLibraryCursor else { return }
        libraryLoading = true; defer { libraryLoading = false }
        await loadLibraryPage(cursor: cursor)
        if libraryError == nil { libraryCursors.append(cursor); libraryPageNumber = libraryCursors.count }
    }
    func previousLibraryPage() async {
        guard !libraryLoading, libraryCursors.count > 1 else { return }
        libraryLoading = true; defer { libraryLoading = false }
        await loadLibraryPage(cursor: libraryCursors[libraryCursors.count - 2])
        if libraryError == nil { libraryCursors.removeLast(); libraryPageNumber = libraryCursors.count }
    }
    func selectSummary(_ value: VoiceRecordingSummary) async {
        guard !preventsDismissal else { return }
        cancelPermission()
        do { await select(try await store.recording(value.id)) } catch { show(error) }
    }
    func loadHistory(offset: Int = 0) async {
        guard let id = recording?.id else { return }
        do {
            let page = try await store.voiceCorrectionHistoryBatch(id, offset: offset, snapshot: offset == 0 ? nil : historySnapshot)
            guard recording?.id == id else { return }
            if page.rebuildingCount == 0 || history.isEmpty { history = page.references }
            historySnapshot = page.snapshot
            nextHistoryOffset = page.nextOffset; historyRebuildingCount = page.rebuildingCount
            historyUnavailableCount = page.unavailableCount; historyError = nil
        } catch { historyError = safeMessage(error) }
    }
    func restartLibrary() async {
        guard !libraryLoading else { return }
        libraryLoading = true; defer { libraryLoading = false }
        await loadLibraryPage(cursor: nil)
        if libraryError == nil { libraryCursors = [nil]; libraryPageNumber = 1 }
    }

    func showHistory(_ reference: VoiceCorrectionReference) async {
        guard let id = recording?.id else { return }
        do {
            let value = try await store.voiceCorrection(id, version: reference.id)
            guard recording?.id == id else { return }
            selectedHistory = value; historyError = nil
        } catch { selectedHistory = nil; historyError = safeMessage(error) }
    }

    func attach(target: VoiceTarget, deferredVerification: (() -> Void)? = nil) async -> Bool {
        guard !preventsDismissal else { return false }
        cancelPermission()
        let attachment = UUID(); pendingAttachment = attachment
        busy = true
        var ownsBusy = true
        defer { if ownsBusy { busy = false }; if pendingAttachment == attachment { pendingAttachment = nil; insertionVerifying = false }; drainHardwareEvent() }
        guard await flushCorrection() else { return false }
        cancelTranscription(); stopPlayback()
        do {
            let id = creationIntent?.1 == target ? creationIntent!.0 : UUID()
            creationIntent = (id, target)
            let created: VoiceRecording
            while true {
                guard !Task.isCancelled, pendingAttachment == attachment else { return false }
                do { created = try await store.prepareRecording(id: id, target: target); break }
                catch VoiceError.verifying {
                    guard pendingAttachment == attachment else { return false }
                    ownsBusy = false; busy = false; insertionVerifying = true
                    status = "正在核验保存记录并准备原日期与区块。"; errorMessage = nil
                    deferredVerification?()
                    await Task.yield()
                }
            }
            guard !Task.isCancelled, pendingAttachment == attachment else { return false }
            busy = true; ownsBusy = true
            resetRecordingRuntime()
            recording = created
            creationIntent = nil
            correction = created.correctedText; correctionValue = created.correctionDraft ?? created.corrections.last
            correctionOriginNotice = try await store.voiceCorrectionOriginNotice(created.id, origin: correctionValue?.origin)
            phase = created.ended ? .ended : .idle
            errorMessage = nil
            await refreshInsertionState()
            status = "录音将保存在 \(target.day.value) · \(target.section)"
            return true
        } catch { show(error); return false }
    }

    func select(_ value: VoiceRecording) async {
        guard !preventsDismissal else { return }
        cancelPermission()
        busy = true
        defer { busy = false; drainHardwareEvent() }
        guard await flushCorrection() else { return }
        cancelPendingWork()
        do {
            let selected = try await store.recording(value.id)
            resetRecordingRuntime()
            recording = selected
            correction = recording?.correctedText ?? ""; correctionValue = recording?.currentCorrectionUnavailable == true ? nil : (recording?.correctionDraft ?? recording?.corrections.last)
            phase = recording?.ended == true ? .ended : .paused
            pendingSegment = nil; preview = nil; playbackVerified = false
            if recording?.deletionRequested == false { await recoverPieces() }
            status = "已恢复原日期与区块。尚未完成的末段可能不可播放；需要点继续才会录音。"
            await reload()
            currentInventory = (try? await store.voiceSourceInventory(value.id)) ?? []
        } catch { show(error) }
    }

    func record() async {
        guard pendingRecordRequest == nil, !busy, !transcribing, !diaryEditingBlocked(), requestedStop == nil,
              phase != .permission, phase != .recording, let value = recording,
              !value.deletionRequested, !value.ended, value.currentCorrectionUnavailable != true, insertion == nil else { return }
        let waiting = UUID(); pendingRecordRequest = waiting
        if !insertionKnown { await refreshInsertionState(waitingForCompletion: true) }
        guard pendingRecordRequest == waiting, !Task.isCancelled, recording?.id == value.id, canRecord else {
            if pendingRecordRequest == waiting { pendingRecordRequest = nil }; return
        }
        busy = true
        let retained = await flushCorrection()
        busy = false
        guard retained, pendingRecordRequest == waiting, !Task.isCancelled, recording?.id == value.id else {
            if pendingRecordRequest == waiting { pendingRecordRequest = nil }; return
        }
        pendingRecordRequest = nil
        preview = nil
        let request = UUID(); generation = request; phase = .permission
        stopPlayback()
        let granted = await services.microphonePermission()
        guard generation == request, phase == .permission, recording?.id == value.id else { return }
        guard granted else { phase = .paused; show(VoiceError.permission); return }
        busy = true
        defer { busy = false; drainHardwareEvent() }
        do { try await beginSegment() }
        catch { await recoverFailedStart(error) }
    }

    private func recoverFailedStart(_ error: Error) async {
        token = nil; pendingSegment = nil; earlyStop = nil; queuedEvent = nil
        timer?.invalidate(); timer = nil; startedAt = nil; liveDuration = 0
        services.stopPlayback(); phase = .paused
        await reload(); show(error)
    }

    func cancelPermission() {
        cancelInsertionVerification()
        if phase == .permission { generation = UUID(); phase = .paused }
    }

    private func beginSegment() async throws {
        guard let value = recording, !value.deletionRequested else { throw VoiceError.deleted }
        guard interruptedGeneration != generation else { throw VoiceError.interrupted }
        if pendingSegment != nil { throw VoiceError.changedMedia }
        preview = nil
        let id = UUID(); pendingSegment = id
        let url = try await store.reserveVoiceSegment(value.id, id: id, captureExpected: true)
        guard requestedStop == nil, interruptedGeneration != generation, phase == .permission else { throw VoiceError.interrupted }
        let capture = VoiceCaptureToken(recordingID: value.id, segmentID: id, generation: generation)
        try services.start(url: url, token: capture)
        captureBindings[capture.segmentID] = capture
        token = capture; phase = .recording; startedAt = Date(); liveDuration = 0
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.phase == .recording, let start = self.startedAt else { return }
                self.liveDuration = Date().timeIntervalSince(start)
            }
        }
        await reload(); errorMessage = nil
        if phase == .recording { status = "正在录音 · 约每 45 秒保留一段" }
    }

    func pause(ending: Bool = false) async {
        if phase == .permission { cancelPermission() }
        requestedStop = (requestedStop ?? false) || ending
        stopNeedsRetry = false
        interruptedGeneration = generation
        // Admission closes before any await, even if a journal read or closed-piece validation is held.
        if token != nil, earlyStop == nil { earlyStop = services.stop() }
        if ending, endPersistence == nil, let id = recording?.id {
            endPersistence = Task {
                do {
                    try await store.endRecording(id)
                    if recording?.id == id { endRequestRetained = true }
                    return .success(())
                }
                catch { return .failure(error) }
            }
        }
        timer?.invalidate(); timer = nil; phase = .paused
        status = ending ? "采集已停止，正在保留结束状态与原件。" : "采集已停止，正在核验已接收原件。"
        guard !busy else { return }
        await completeStopRequest()
    }

    private func completeStopRequest() async {
        guard !busy, requestedStop != nil else { return }
        busy = true
        defer { busy = false; drainHardwareEvent() }
        do {
            if token != nil || earlyStop != nil { try await finishSegment() }
            await recoverPieces()
            guard pendingSegment == nil else { throw VoiceError.changedMedia }
            let ending = requestedStop == true
            if ending, let id = recording?.id {
                if let persistence = endPersistence { try await persistence.value.get() }
                else { try await store.endRecording(id); endRequestRetained = true }
            }
            requestedStop = nil; endPersistence = nil; stopNeedsRetry = false
            phase = ending ? .ended : .paused
            await reload()
            status = ending ? "录音已结束。请回放核对最后一段。" : "已暂停。请回放核对最后一段。"
        } catch { phase = .paused; stopNeedsRetry = true; show(error) }
    }

    private func finishSegment(continuing: Bool = false) async throws {
        let capture = token
        token = nil; timer?.invalidate(); timer = nil; liveDuration = 0
        phase = .paused
        let stopped = earlyStop ?? services.stop()
        earlyStop = nil
        if !continuing { services.stopPlayback() }
        _ = try await stopped.value
        guard let capture else { throw VoiceError.changedMedia }
        let url = try await store.recoverableVoiceURL(capture.recordingID, segmentID: capture.segmentID)
        let validation = try await services.validatedMedia(of: url)
        try await store.finalizeVoiceSegment(capture.recordingID, id: capture.segmentID, duration: validation.duration,
                                            evidence: services.evidence(for: capture), validation: validation)
        pendingSegment = nil
        await reload()
    }

    private func hardwareEvent(_ capture: VoiceCaptureToken, event: VoiceHardwareEvent) async {
        if case .interrupted = event, token == nil, pendingSegment == capture.segmentID,
           recording?.id == capture.recordingID, generation == capture.generation {
            interruptedGeneration = capture.generation
            return
        }
        if case .interrupted = event, captureBindings[capture.segmentID] == capture,
           recording?.id == capture.recordingID, token?.generation == capture.generation,
           token != capture, phase == .recording || earlyStop != nil {
            await pause()
            return
        }
        guard token == capture, phase == .recording || earlyStop != nil else { return }
        if case .interrupted = event { interruptedGeneration = capture.generation }
        if busy {
            if case .interrupted = event {
                if earlyStop == nil { earlyStop = services.stop() }
                phase = .paused
                queuedEvent = (capture, .interrupted)
            } else if queuedEvent == nil { queuedEvent = (capture, event) }
            return
        }
        busy = true
        defer { busy = false; drainHardwareEvent() }
        do {
            switch event {
            case .rotate: try await rotateSegment(capture)
            case .interrupted:
                try await finishSegment()
                status = "录音被打断。请核对已接收片段；需要明确点继续。"
            }
        } catch {
            if requestedStop != nil { return }
            if token != nil { try? await finishSegment() }
            await recoverFailedStart(error)
        }
    }

    private func rotateSegment(_ previous: VoiceCaptureToken) async throws {
        let id = UUID()
        // Reservation can await journal storage while the current native input keeps recording.
        let url = try await store.reserveVoiceSegment(previous.recordingID, id: id, captureExpected: true)
        guard requestedStop == nil, token == previous, interruptedGeneration != generation, phase == .recording else { throw VoiceError.interrupted }
        let next = VoiceCaptureToken(recordingID: previous.recordingID, segmentID: id, generation: generation)
        captureBindings[next.segmentID] = next
        _ = try await services.rotate(url: url, token: next)
        guard requestedStop == nil, token == previous, phase == .recording else { throw VoiceError.interrupted }
        token = next; pendingSegment = id; startedAt = Date(); liveDuration = 0
        let original = try await store.recoverableVoiceURL(previous.recordingID, segmentID: previous.segmentID)
        let validation = try await services.validatedMedia(of: original)
        try await store.finalizeVoiceSegment(previous.recordingID, id: previous.segmentID, duration: validation.duration,
                                             evidence: services.evidence(for: previous), validation: validation)
        await reload()
    }

    private func drainHardwareEvent() {
        if requestedStop != nil && !stopNeedsRetry {
            queuedEvent = nil
            Task { await completeStopRequest() }
            return
        }
        guard let queued = queuedEvent else { return }
        queuedEvent = nil
        Task { await hardwareEvent(queued.0, event: queued.1) }
    }

    private func recoverPieces() async {
        guard let id = recording?.id, let value = try? await store.recording(id) else { return }
        for segment in value.segments where !segment.finalized {
            do {
                let url = try await store.recoverableVoiceURL(value.id, segmentID: segment.id)
                let validation = try await services.validatedMedia(of: url)
                let recoveredToken = captureBindings[segment.id] ?? VoiceCaptureToken(recordingID: value.id, segmentID: segment.id, generation: generation)
                try await store.finalizeVoiceSegment(value.id, id: segment.id, duration: validation.duration, evidence: services.evidence(for: recoveredToken), validation: validation)
                if pendingSegment == segment.id { pendingSegment = nil }
            } catch { status = "末段音频尚未核验是否存在或可播放。此前已核验片段可回放。" }
        }
    }

    func retryRecovery() async {
        guard !busy, phase != .recording else { return }
        busy = true; defer { busy = false; drainHardwareEvent() }
        await recoverPieces(); await reload()
        if pendingSegment != nil { pendingSegment = nil }
        if requestedStop != nil { stopNeedsRetry = false; endPersistence = nil }
        status = "已核对可用片段，未删除任何已有原件；未生成或不可播放的末段仍待核对。继续会新建片段。"
    }

    func updateCorrection(_ text: String) {
        guard canEditRecording, !diaryEditingBlocked(), !busy, !correction.utf8.elementsEqual(text.utf8) else { return }
        correction = text; preview = nil
        correctionValue = VoiceCorrection(text: text, origin: correctionValue?.origin)
        let previous = persistence
        persistence = Task { await previous?.value; _ = await persistCorrection() }
    }

    private func persistCorrection() async -> Bool {
        guard insertion == nil, let id = recording?.id, let value = correctionValue else { return true }
        let adoption = pendingSourceAdoption
        while true {
            do {
                try await store.retainVoiceCorrection(id, correction: value, adoptingSource: adoption != nil)
                if pendingSourceAdoption == adoption { pendingSourceAdoption = nil }
                correctionCleanupPending = (try? await store.voiceCorrectionCleanupPending(id)) != false
                let notice = try await store.voiceCorrectionOriginNotice(id, origin: value.origin)
                if correctionValue?.id == value.id { correctionOriginNotice = notice; status = "校正文字已保留"; errorMessage = nil }
                return true
            } catch VoiceError.verifying {
                await refreshInsertionState(waitingForCompletion: true)
                guard recording?.id == id, correctionValue?.id == value.id, insertionKnown, insertion == nil else { return false }
            } catch { show(error); return false }
        }
    }

    func flushCorrection() async -> Bool {
        await persistence?.value
        if recording?.currentCorrectionUnavailable == true { return correctionValue == nil }
        guard await persistCorrection() else { return false }
        do {
            if insertion == nil, recording?.currentCorrectionUnavailable != true, let id = recording?.id {
                try await store.checkpointVoiceCorrection(id)
                correctionCleanupPending = (try? await store.voiceCorrectionCleanupPending(id)) != false
            }
            return true
        } catch { show(error); return false }
    }

    func retryCorrectionCleanup() async {
        guard !busy, let id = recording?.id else { return }
        do {
            try await store.retryVoiceCorrectionCleanup(id)
            let pending = try await store.voiceCorrectionCleanupPending(id)
            if recording?.id == id { correctionCleanupPending = pending }
        }
        catch { correctionCleanupPending = true; show(error) }
    }

    func invalidatePreview() { preview = nil }

    var correctionOrigin: VoiceCorrectionOrigin? { correctionValue?.origin }
    func adopt(_ proposal: VoiceTranscript) {
        guard let value = recording, (value.transcripts.contains(proposal) || selectedTranscript == proposal) else { return }
        do { try adoptText(proposal.text, origin: VoiceCorrectionOrigin(recordingID: value.id, target: value.target, proposal: proposal)) }
        catch { show(error) }
    }
    func adoptPiece(_ piece: VoiceSegmentTranscript) async {
        guard let value = recording, canEditRecording, !diaryEditingBlocked(), !busy,
              (value.segmentTranscripts?.contains(piece) == true || selectedPiece == piece) else { return }
        let currentCorrectionID = correctionValue?.id
        do {
            let origin = try await store.voicePieceCorrectionOrigin(value.id, piece: piece)
            guard recording?.id == value.id, correctionValue?.id == currentCorrectionID else { return }
            try adoptText(piece.text, origin: origin)
        } catch { show(error) }
    }
    private func adoptText(_ text: String, origin: VoiceCorrectionOrigin) throws {
        guard canEditRecording, !diaryEditingBlocked(), !busy else { return }
        correction = text; preview = nil
        correctionValue = VoiceCorrection(text: text, origin: origin)
        pendingSourceAdoption = UUID(); correctionOriginNotice = origin.notice
        let previous = persistence
        persistence = Task { await previous?.value; _ = await persistCorrection() }
    }

    func transcribe(allowingUnavailable: Bool = false) {
        guard canEditRecording, !diaryEditingBlocked(), !busy, !transcribing, phase != .recording, phase != .permission,
              let value = recording, !value.deletionRequested else { return }
        transcribing = true; emptyPiece = nil; stopPlayback()
        let request = UUID(); generation = request
        speechTask = Task {
            defer { if generation == request { transcribing = false } }
            do {
                let media = try await store.voiceReferences(value.id, allowingUnavailable: true)
                guard !media.isEmpty else { throw VoiceError.changedMedia }
                // A previously retained original cannot silently become a different source on retry.
                let retained = try await store.voiceManifest(value.id)
                currentInventory = try await store.voiceSourceInventory(value.id)
                for reference in retained.segmentTranscriptReferences ?? [] where !allowingUnavailable && !media.contains(where: { $0.segmentID == reference.id }) {
                    throw VoiceError.changedMedia
                }
                for reference in media {
                    try checkSpeechRequest(request, recordingID: value.id)
                    let source = try await store.voiceRecognitionSource(value.id, media: reference)
                    let current = try await store.recording(value.id)
                    try checkSpeechRequest(request, recordingID: value.id)
                    if current.skippedTranscripts?.contains(reference) == true { continue }
                    if let old = try await store.voiceSegmentTranscript(value.id, segmentID: reference.segmentID) {
                        guard old.media == reference else { throw VoiceError.changedMedia }
                    } else {
                        let text: String
                        do { text = try await services.transcribe(source) }
                        catch VoiceError.emptyTranscript {
                            try checkSpeechRequest(request, recordingID: value.id)
                            emptyPiece = reference; throw VoiceError.emptyTranscript
                        }
                        try checkSpeechRequest(request, recordingID: value.id)
                        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                            emptyPiece = reference; throw VoiceError.emptyTranscript
                        }
                        try await store.retainVoiceSegmentTranscript(value.id, piece: VoiceSegmentTranscript(media: reference, text: text))
                    }
                    try checkSpeechRequest(request, recordingID: value.id)
                    await reload()
                    status = "已保留 \(retainedPieceCount)/\(media.count) 段设备转写，校正文字保持不变。"
                }
                try await assembleTranscript(media, request: request, recordingID: value.id)
            } catch { if generation == request { show(error) } }
        }
    }

    private func checkSpeechRequest(_ request: UUID, recordingID: UUID) throws {
        guard !Task.isCancelled, generation == request, recording?.id == recordingID else { throw CancellationError() }
    }

    private func assembleTranscript(_ media: [VoiceMediaReference], request: UUID, recordingID: UUID) async throws {
        let current = try await store.recording(recordingID)
        try checkSpeechRequest(request, recordingID: recordingID)
        let skipped = media.filter { current.skippedTranscripts?.contains($0) == true }
        var texts: [String] = []
        for reference in media where !skipped.contains(reference) {
            guard let piece = try await store.voiceSegmentTranscript(recordingID, segmentID: reference.segmentID), piece.media == reference else { throw VoiceError.changedMedia }
            texts.append(piece.text)
        }
        let text = texts.joined(separator: "\n")
        let inventory = try await store.voiceSourceInventory(recordingID)
        let retained = current.transcripts.first { $0.media == media && $0.inventory == inventory && $0.text.utf8.elementsEqual(text.utf8) }
        var proposal = retained ?? VoiceTranscript(id: request, media: media, text: text)
        if !skipped.isEmpty { proposal.skipped = skipped }
        proposal.inventory = inventory
        try await store.retainVoiceTranscript(recordingID, proposal: proposal)
        try checkSpeechRequest(request, recordingID: recordingID)
        await reload()
        let incomplete = skipped.count + inventory.filter { !$0.usable }.count
        status = incomplete == 0 ? "在此设备转写完成。请核对原稿后选择采用，校正文字不会自动替换。" : "这份转写不完整：\(incomplete) 段被略过或尚不可用。所有原件身份保留。"
    }

    @discardableResult
    func skipEmptyPiece() async -> Bool {
        guard canEditRecording, !diaryEditingBlocked(), phase != .recording, phase != .permission, !busy, !transcribing, let value = recording, let reference = emptyPiece else { return false }
        let request = generation
        busy = true
        defer { busy = false; skipTask = nil; drainHardwareEvent() }
        var succeeded = false
        let task = Task {
            do {
                _ = try await verifySkip(value.id, reference.segmentID)
                try Task.checkCancellation()
                guard generation == request, recording?.id == value.id, emptyPiece == reference else { return }
                try await store.skipVoiceTranscript(value.id, media: reference)
                try Task.checkCancellation()
                guard generation == request, recording?.id == value.id else { return }
                emptyPiece = nil
                await reload()
                succeeded = true
            } catch { if generation == request { show(error) } }
        }
        skipTask = task
        await task.value
        return succeeded && generation == request && recording?.id == value.id
    }

    func skipAndContinueTranscription() async {
        let request = generation, id = recording?.id
        guard await skipEmptyPiece(), generation == request, recording?.id == id else { return }
        transcribe()
    }

    func retrySkippedPiece(_ reference: VoiceMediaReference) async {
        guard canEditRecording, phase != .recording, phase != .permission, !diaryEditingBlocked(), !busy, !transcribing, let id = recording?.id else { return }
        let request = generation
        busy = true
        defer { busy = false; drainHardwareEvent() }
        let task = Task {
            do {
                _ = try await verifySkip(id, reference.segmentID)
                try checkSpeechRequest(request, recordingID: id)
                try await store.retrySkippedVoiceTranscript(id, media: reference)
                try checkSpeechRequest(request, recordingID: id)
                await reload()
                status = "已撤回此段的略过选择。点在此设备转写可重新识别；旧原稿和校正保留。"
            } catch { if generation == request { show(error) } }
        }
        skipTask = task; await task.value; skipTask = nil
    }

    func loadTranscriptHistory(segments: Bool = false, offset: Int = 0) async {
        guard let id = recording?.id else { return }
        do {
            let page = try await store.voiceTranscriptHistory(id, segments: segments, offset: offset)
            guard recording?.id == id else { return }
            transcriptHistory = page.references; nextTranscriptOffset = page.nextOffset
            transcriptHistoryIsSegments = segments; historyError = nil
        } catch { historyError = safeMessage(error) }
    }

    func showTranscript(_ reference: VoiceTextReference) async {
        guard let id = recording?.id else { return }
        do {
            if reference.segment == true {
                let piece = try await store.voiceSegmentTranscript(id, segmentID: reference.id)
                guard recording?.id == id else { return }
                selectedPiece = piece; selectedTranscript = nil
            } else {
                let proposal = try await store.voiceTranscript(id, version: reference.id)
                guard recording?.id == id else { return }
                selectedTranscript = proposal; selectedPiece = nil
            }
            historyError = nil
        } catch { selectedPiece = nil; selectedTranscript = nil; historyError = safeMessage(error) }
    }

    func cancelTranscription() {
        emptyPiece = nil; generation = UUID(); skipTask?.cancel(); speechTask?.cancel(); services.cancelTranscription(); transcribing = false
    }

    func play(_ segment: VoiceSegment) async {
        guard !busy, !transcribing, phase != .recording, phase != .permission,
              let value = recording, !value.deletionRequested else { return }
        stopPlayback()
        let request = VoicePlaybackToken(recordingID: value.id, segmentID: segment.id, generation: UUID())
        playbackToken = request; playbackState = .verifying
        do {
            let data = try await verifyPlayback(value.id, segment.id)
            let latest = try await store.voiceManifest(value.id)
            guard !latest.deletionRequested else { throw VoiceError.deleted }
            guard let current = latest.segments.first(where: { $0.id == segment.id }), current.finalized,
                  current.hash == SourceHash.digest(data), current.byteCount == Int64(data.count) else { throw VoiceError.changedMedia }
            guard playbackToken == request, recording?.id == value.id, !busy, !transcribing,
                  phase != .recording, phase != .permission, recording?.deletionRequested == false else {
                if playbackToken == request { stopPlayback() }
                return
            }
            try services.play(data, token: request)
            guard playbackToken == request else { return }
            playbackVerified = true; playbackState = .playing; status = "正在回放已核验的原录音"
        } catch {
            guard playbackToken == request else { return }
            playbackToken = nil; services.stopPlayback(); playbackVerified = false; playbackState = .failed
            show(error)
        }
    }

    func stopPlayback() {
        playbackToken = nil
        services.stopPlayback()
        playbackVerified = false
        if playbackState == .playing || playbackState == .verifying { playbackState = .stopped; status = "回放已停止" }
    }

    private func playbackFinished(_ request: VoicePlaybackToken, event: VoicePlaybackEvent) {
        guard playbackToken == request, recording?.id == request.recordingID else { return }
        playbackToken = nil
        switch event {
        case .finished: playbackState = .finished; status = "原录音回放完成"
        case .stopped: playbackState = .stopped; playbackVerified = false; status = "回放已停止"
        case .failed: playbackState = .failed; playbackVerified = false; show(VoiceError.changedMedia)
        }
    }

    func deleteAudio() async {
        guard !busy, phase != .recording, let id = recording?.id else { return }
        busy = true; defer { busy = false; drainHardwareEvent() }
        cancelPermission(); cancelTranscription(); stopPlayback(); preview = nil; playbackVerified = false
        guard await flushCorrection() else { return }
        do { try await store.deleteVoiceAudio(id); status = "原音频已申请删除，正在核验移除结果。" }
        catch { show(error) }
        await reload(); phase = .ended
        if recording?.deletionRequested == true {
            requestedStop = nil; endPersistence = nil; stopNeedsRetry = false; endRequestRetained = true
            token = nil; earlyStop = nil; pendingSegment = nil; queuedEvent = nil
            status = audioRemovalComplete ? "原音频已删除。校正文字与日记保持不变。" : "原音频已申请删除，部分移除待重试。文字保持不变。"
        }
    }

    func lockInsertion(_ candidate: VoiceInsertionCandidate) -> Bool {
        guard canEditRecording, !busy, phase != .recording, phase != .permission, preview == candidate else { return false }
        busy = true
        return true
    }

    func unlockInsertion() { busy = false; drainHardwareEvent() }

    func prepareInsertion(draft: JournalDraft) async -> VoiceInsertionCandidate? {
        guard canEditRecording, !busy, phase != .recording, phase != .permission, let id = recording?.id else { return nil }
        busy = true; defer { busy = false; drainHardwareEvent() }
        guard await flushCorrection() else { return nil }
        do {
            preview = try await store.prepareVoiceInsertion(id, draft: draft)
            currentInventory = preview?.inventory ?? []; errorMessage = nil; return preview
        }
        catch { show(error); return nil }
    }

    func lockSourceRefresh() -> Bool {
        guard canEditRecording, !busy, phase != .recording, phase != .permission else { return false }
        busy = true
        return true
    }

    func refreshSource(draft: JournalDraft, locked: Bool = false) async {
        guard canEditRecording, (!busy || locked), phase != .recording, phase != .permission,
              let id = recording?.id else { return }
        busy = true; defer { if !locked { busy = false; drainHardwareEvent() } }
        guard await flushCorrection() else { return }
        do {
            try await store.refreshVoiceSource(id, draft: draft)
            preview = nil; await reload()
            status = "已刷新当前日记来源。请重新预览，原日期与区块保留。"
            errorMessage = nil
        } catch { show(error) }
    }

    func export(_ segment: VoiceSegment) async -> Data? {
        guard !busy, phase != .recording, phase != .permission, let id = recording?.id else { return nil }
        busy = true; defer { busy = false; drainHardwareEvent() }
        do { return try await store.exportVoiceAudio(id, segmentID: segment.id) }
        catch { show(error); return nil }
    }

    func importAudio(_ data: Data) async {
        guard canEditRecording, !busy, phase != .recording, phase != .permission, let value = recording, !value.ended else { return }
        busy = true; defer { busy = false; drainHardwareEvent() }
        do {
            let hash = SourceHash.digest(data)
            let id = importIntent?.recordingID == value.id && importIntent?.hash == hash ? importIntent!.segmentID : UUID()
            importIntent = (value.id, id, hash)
            let latest = try await store.recording(value.id)
            if !latest.segments.contains(where: { $0.id == id }) {
                _ = try await store.reserveVoiceSegment(value.id, id: id, fileExtension: "wav")
            }
            let url = try await store.importVoiceAudio(value.id, segmentID: id, data: data)
            let validation = try await services.validatedMedia(of: url)
            try await store.finalizeVoiceSegment(value.id, id: id, duration: validation.duration, validation: validation)
            importIntent = nil
            await reload(); phase = .paused; errorMessage = nil
            status = "已导入测试音频。回放验证不代表语音识别成功。"
        } catch { show(error) }
    }

    func dismiss() async -> Bool {
        guard !preventsDismissal else { return false }
        cancelPermission()
        busy = true; defer { busy = false; drainHardwareEvent() }
        guard await flushCorrection() else { return false }
        cancelPermission(); cancelTranscription(); stopPlayback(); return true
    }

    private func reload() async {
        guard let id = recording?.id else { return }
        do {
            let value = try await store.recording(id)
            guard recording?.id == id else { return }
            recording = value
            correctionOriginNotice = try await store.voiceCorrectionOriginNotice(id, origin: correctionValue?.origin)
            correctionCleanupPending = (try? await store.voiceCorrectionCleanupPending(id)) != false
            retainedPieceCount = value.segmentTranscriptReferences?.count ?? value.segmentTranscripts?.count ?? 0
            audioRemovalComplete = (try? await store.voiceAudioRemovalComplete(id)) == true
            await refreshInsertionState()

        } catch { show(error) }
    }
    func refreshInsertionState(waitingForCompletion: Bool = false) async {
        if let task = insertionVerificationTask {
            if waitingForCompletion { await task.value }; return
        }
        guard let id = recording?.id else { return }
        let request = UUID(); insertionVerificationID = request
        insertionKnown = false; insertionVerifying = true
        do {
            let result = try await store.voiceInsertion(for: id)
            guard insertionVerificationID == request, recording?.id == id else { return }
            acceptInsertionEvidence(result)
            finishInsertionVerification(request)
        } catch {
            guard insertionVerificationID == request, recording?.id == id else { return }
            guard error as? VoiceError == .verifying else { finishInsertionVerification(request); show(error); return }
            status = "正在分批核验本地保存记录，完成后继续当前操作。"; errorMessage = nil
            let task = Task { [weak self] in
                guard let self else { return }
                await self.continueInsertionVerification(id, request: request)
            }
            insertionVerificationTask = task
            if waitingForCompletion { await task.value }
        }
    }

    private func continueInsertionVerification(_ id: UUID, request: UUID) async {
        defer { finishInsertionVerification(request) }
        while !Task.isCancelled, insertionVerificationID == request, recording?.id == id {
            await Task.yield()
            do {
                let result = try await store.voiceInsertion(for: id)
                guard !Task.isCancelled, insertionVerificationID == request, recording?.id == id else { return }
                acceptInsertionEvidence(result); status = "已核验本地保存记录。"; return
            } catch {
                guard !Task.isCancelled, insertionVerificationID == request, recording?.id == id else { return }
                if error as? VoiceError != .verifying { show(error); return }
            }
        }
    }

    private func acceptInsertionEvidence(_ result: VoiceInsertionStamp?) {
        insertion = result; insertionKnown = true
        if result != nil { preview = nil; cancelPermission(); cancelTranscription() }
    }

    private func finishInsertionVerification(_ request: UUID) {
        guard insertionVerificationID == request else { return }
        insertionVerificationID = nil; insertionVerificationTask = nil; insertionVerifying = false
    }

    func cancelInsertionVerification() {
        pendingRecordRequest = nil
        pendingAttachment = nil
        insertionVerificationID = nil; insertionVerificationTask?.cancel(); insertionVerificationTask = nil
        insertionVerifying = false
    }

    private func resetRecordingRuntime() {
        cancelInsertionVerification()
        requestedStop = nil; endPersistence = nil; stopNeedsRetry = false; endRequestRetained = false
        token = nil; captureBindings = [:]; pendingSegment = nil; queuedEvent = nil; earlyStop = nil; interruptedGeneration = nil
        importIntent = nil; timer?.invalidate(); timer = nil; startedAt = nil; liveDuration = 0
        preview = nil; playbackVerified = false; playbackState = .idle; playbackToken = nil
        insertion = nil; insertionKnown = false; audioRemovalComplete = false; retainedPieceCount = 0
        historyError = nil; transcriptHistory = []; nextTranscriptOffset = nil; selectedTranscript = nil; selectedPiece = nil
        currentInventory = []; correctionCleanupPending = false
        emptyPiece = nil; history = []; nextHistoryOffset = nil; selectedHistory = nil; historyRebuildingCount = 0; historyUnavailableCount = 0; historySnapshot = nil
        correctionValue = nil; correctionOriginNotice = nil; pendingSourceAdoption = nil
        persistence = nil; errorMessage = nil; needsSettings = false
    }

    private func cancelPendingWork() { cancelPermission(); cancelTranscription(); stopPlayback() }
    private func safeMessage(_ error: Error) -> String {
        (error as? VoiceError)?.localizedDescription ?? (error as? JournalError ?? .storage).localizedDescription
    }
    private func show(_ error: Error) {
        needsSettings = (error as? VoiceError) == .permission
        errorMessage = safeMessage(error)
    }
}

#if DEBUG
extension VoiceViewModel {
    func prepareSyntheticHistory(_ bytes: Data) async {
        guard ProcessInfo.processInfo.environment["RIJI_TEST_VOICE_HISTORY"] == "1",
              ProcessInfo.processInfo.environment["RIJI_TEST_CONTAINER"].flatMap(UUID.init(uuidString:)) != nil,
              let value = recording, value.segments.isEmpty, canEditRecording, !busy else { return }
        busy = true; defer { busy = false }
        do {
            for index in 0..<2 {
                let id = UUID()
                _ = try await store.reserveVoiceSegment(value.id, id: id, fileExtension: "wav", captureExpected: true)
                let url = try await store.importVoiceAudio(value.id, segmentID: id, data: bytes)
                let validation = try await services.validatedMedia(of: url)
                var evidence = VoiceCaptureEvidence(firstFrame: 0, frameCount: 16000, sampleRate: 16000, streamID: UUID())
                evidence.rejectedFrames = 7
                try await store.finalizeVoiceSegment(value.id, id: id, duration: validation.duration,
                    evidence: index == 0 ? evidence : nil, validation: validation)
            }
            _ = try await store.reserveVoiceSegment(value.id, id: UUID())
            let media = try await store.voiceReferences(value.id)
            try await store.skipVoiceTranscript(value.id, media: media[1])
            let inventory = try await store.voiceSourceInventory(value.id)
            for index in 0..<6 {
                let id = index == 0 ? UUID(uuidString: "F1050000-0000-0000-0000-000000000001")! : UUID()
                let text = index == 0 ? "Historical synthetic original e\u{301}\n仅为测试原稿" : String(repeating: "synthetic history \(index) ", count: 15000)
                var proposal = VoiceTranscript(id: id, media: media, text: text)
                proposal.skipped = [media[1]]; proposal.inventory = inventory
                try await store.retainVoiceTranscript(value.id, proposal: proposal)
            }
            await reload(); status = "合成历史已准备；未执行语音识别。"
        } catch { show(error) }
    }
}
#endif
