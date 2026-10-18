import Foundation
import AVFoundation
import Speech
import Darwin
import RijiCore

/// A configuration notification runs on the engine's internal queue. Defer all
/// capture teardown to MainActor, and invalidate queued work when ownership ends.
@MainActor
final class VoiceEngineConfigurationObserver {
    private let center: NotificationCenter
    private var observer: NSObjectProtocol?
    private var binding: UUID?

    init(center: NotificationCenter = .default) { self.center = center }

    func observe(_ engine: AVAudioEngine, generation: UUID, onChange: @escaping (UUID) -> Void) {
        cancel()
        let identity = UUID()
        binding = identity
        observer = center.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: nil) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, self.binding == identity else { return }
                self.cancel()
                onChange(generation)
            }
        }
    }

    func cancel() {
        binding = nil
        if let observer { center.removeObserver(observer) }
        observer = nil
    }

    deinit { if let observer { center.removeObserver(observer) } }
}

/// Hardware access exists only in the iOS app, never the portable State test target.
@MainActor
final class NativeVoiceServices: NSObject, VoiceServices, AVAudioPlayerDelegate {
    var event: ((VoiceCaptureToken, VoiceHardwareEvent) -> Void)?
    var playbackEvent: ((VoicePlaybackToken, VoicePlaybackEvent) -> Void)?
    private var playbackToken: VoicePlaybackToken?
    private var engine: AVAudioEngine?
    private let engineChanges = VoiceEngineConfigurationObserver()
    private var writer: ContinuousVoiceWriter?
    private var pendingDrain: Task<Double, Error>?
    private var draining = false
    private var sessionOwned = false
    private var speechSnapshot: VoiceSpeechSnapshot?
    private var closedPieces: [UUID: ClosedVoicePiece] = [:]
    private var player: AVAudioPlayer?
    private var capture: VoiceCaptureToken?
    private var lastCapture: VoiceCaptureToken?
    #if DEBUG
    var transitionReady: (() async -> Void)?
    #endif
    private var blockedGeneration: UUID?
    private var observers: [NSObjectProtocol] = []
    private let notificationCenter: NotificationCenter
    private let derivedCache: VoiceDerivedAudioCache
    private var speech: SFSpeechRecognitionTask?
    private var speechID: UUID?
    private var continuation: CheckedContinuation<String, Error>?
    private var deadline: Task<Void, Never>?

    init(cache: VoiceDerivedAudioCache = VoiceSpeechSnapshot.fallbackCache, center: NotificationCenter = .default) {
        derivedCache = cache; notificationCenter = center
        super.init()
        for name in [AVAudioSession.interruptionNotification, AVAudioSession.routeChangeNotification,
                     AVAudioSession.mediaServicesWereResetNotification] {
            observers.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] note in
                Task { @MainActor in self?.interruption(note) }
            })
        }
    }

    func microphonePermission() async -> Bool {
        await AVAudioApplication.requestRecordPermission()
    }

    func start(url: URL, token: VoiceCaptureToken) throws {
        guard blockedGeneration != token.generation else { throw VoiceError.interrupted }
        guard engine == nil, writer == nil, !draining else { throw VoiceError.changedMedia }
        if lastCapture?.recordingID != token.recordingID { closedPieces = [:] }
        let session = AVAudioSession.sharedInstance()
        let input = AVAudioEngine()
        do {
            try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .allowBluetooth])
            try session.setActive(true); sessionOwned = true
            let format = input.inputNode.outputFormat(forBus: 0)
            let sink = try ContinuousVoiceWriter(url: url, token: token, format: format)
            try installCapture(sink, token: token, engine: input)
            input.inputNode.installTap(onBus: 0, bufferSize: 4096, format: format) { buffer, _ in _ = sink.accept(buffer) }
            engineChanges.observe(input, generation: token.generation) { [weak self, weak input] generation in
                guard let self, let input, self.engine === input,
                      let affected = self.capture, affected.generation == generation else { return }
                self.blockedGeneration = generation
                self.event?(affected, .interrupted)
            }
            input.prepare(); try input.start()
        } catch {
            engineChanges.cancel()
            if writer != nil { _ = stop() }
            writer = nil; engine = nil; capture = nil
            releaseIdleSession()
            throw JournalError.storage
        }
    }

    func rotate(url: URL, token: VoiceCaptureToken) async throws -> Double {
        guard let writer, blockedGeneration != token.generation else { throw VoiceError.interrupted }
        // File preparation and queued writer transition cannot occupy MainActor's hardware-stop path.
        let closed = try await Task.detached(priority: .userInitiated) { try writer.rotate(url: url, token: token) }.value
        closedPieces[closed.token.segmentID] = closed
        #if DEBUG
        await transitionReady?()
        #endif
        guard self.writer === writer, blockedGeneration != token.generation else { throw VoiceError.interrupted }
        capture = token; lastCapture = token
        return Double(closed.evidence.frameCount) / closed.evidence.sampleRate
    }

    /// Shared production lifecycle entry; tests inject the production writer without an ambient engine.
    func installCapture(_ sink: ContinuousVoiceWriter, token: VoiceCaptureToken, engine: AVAudioEngine? = nil) throws {
        guard writer == nil, !draining, blockedGeneration != token.generation else { throw VoiceError.interrupted }
        if lastCapture?.recordingID != token.recordingID { closedPieces = [:] }
        pendingDrain = nil; writer = sink; self.engine = engine; capture = token; lastCapture = token
        sink.onRotation = { [weak self] token in Task { @MainActor in self?.event?(token, .rotate) } }
        sink.onFailure = { [weak self] token in Task { @MainActor in self?.event?(token, .interrupted) } }
    }

    func stop() -> Task<Double, Error> {
        guard let sink = writer else { return pendingDrain ?? Task { throw VoiceError.changedMedia } }
        sink.closeAdmission()
        blockedGeneration = capture?.generation
        engineChanges.cancel()
        if let input = engine { input.inputNode.removeTap(onBus: 0); input.stop() }
        engine = nil; writer = nil; capture = nil; draining = true
        releaseIdleSession()
        let drain = Task<Double, Error> {
            let closed = await sink.finishStop()
            self.closedPieces[closed.token.segmentID] = closed
            self.draining = false
            return Double(closed.evidence.frameCount) / closed.evidence.sampleRate
        }
        pendingDrain = drain
        return drain
    }

    func evidence(for token: VoiceCaptureToken) -> VoiceCaptureEvidence? {
        guard let closed = closedPieces[token.segmentID], closed.token == token else { return nil }
        return closed.evidence
    }

    func validatedMedia(of url: URL) async throws -> VoiceMediaValidation {
        let cache = derivedCache
        return try await Task.detached(priority: .utility) { try Self.validateMedia(url, cache: cache) }.value
    }
    func verifiedDuration(of url: URL) async throws -> Double { try await validatedMedia(of: url).duration }
    func duration(of url: URL) throws -> Double { try Self.decodeDuration(url) }
    nonisolated static func decodeDuration(_ original: URL) throws -> Double { try validateMedia(original).duration }

    nonisolated static func validateMedia(_ original: URL, cache: VoiceDerivedAudioCache = VoiceSpeechSnapshot.fallbackCache) throws -> VoiceMediaValidation {
        let bytes = try readValidationBytes(original)
        let snapshot = try VoiceSpeechSnapshot(data: bytes, fileExtension: original.pathExtension, cache: cache,
            recordingID: UUID(uuidString: original.deletingLastPathComponent().lastPathComponent) ?? UUID(),
            segmentID: UUID(uuidString: original.deletingPathExtension().lastPathComponent) ?? UUID())
        do {
            let result = VoiceMediaValidation(data: bytes, duration: try decodedSnapshotDuration(snapshot.url))
            try snapshot.finish(); return result
        } catch { snapshot.remove(); throw error }
    }

    nonisolated private static func decodedSnapshotDuration(_ url: URL) throws -> Double {
        do {
            let file = try AVAudioFile(forReading: url)
            guard let buffer = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: 8192) else { throw VoiceError.changedMedia }
            var frames: AVAudioFramePosition = 0
            while file.framePosition < file.length {
                try file.read(into: buffer)
                guard buffer.frameLength > 0 else { throw VoiceError.changedMedia }
                frames += AVAudioFramePosition(buffer.frameLength)
                guard frames <= 32 * 1024 * 1024 else { throw VoiceError.limit }
            }
            let duration = Double(frames) / file.processingFormat.sampleRate
            let playback = try AVAudioPlayer(contentsOf: url)
            guard duration.isFinite, duration > 0, playback.duration > 0, playback.prepareToPlay() else { throw VoiceError.changedMedia }
            return duration
        } catch { throw VoiceError.changedMedia }
    }

    nonisolated private static func readValidationBytes(_ original: URL) throws -> Data {
        let descriptor = Darwin.open(original.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard descriptor >= 0 else { throw VoiceError.changedMedia }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? handle.close() }
        var info = stat(), after = stat(), named = stat()
        guard fstat(descriptor, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_size > 0, info.st_size <= 32 * 1024 * 1024, fsync(descriptor) == 0 else { throw VoiceError.changedMedia }
        var data = Data()
        while let block = try handle.read(upToCount: 65536), !block.isEmpty {
            guard data.count + block.count <= 32 * 1024 * 1024 else { throw VoiceError.limit }
            data.append(block)
        }
        guard data.count == info.st_size, fstat(descriptor, &after) == 0, lstat(original.path, &named) == 0,
              named.st_ino == info.st_ino, named.st_dev == info.st_dev, after.st_size == info.st_size,
              after.st_mtimespec.tv_sec == info.st_mtimespec.tv_sec, after.st_mtimespec.tv_nsec == info.st_mtimespec.tv_nsec,
              after.st_ctimespec.tv_sec == info.st_ctimespec.tv_sec, after.st_ctimespec.tv_nsec == info.st_ctimespec.tv_nsec else { throw VoiceError.changedMedia }
        return data
    }

    func play(_ data: Data, token: VoicePlaybackToken) throws {
        stopPlayback()
        do {
            guard data.count <= 32 * 1024 * 1024 else { throw VoiceError.limit }
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback); try session.setActive(true); sessionOwned = true
            let value = try AVAudioPlayer(data: data)
            value.delegate = self
            player = value; playbackToken = token
            guard value.prepareToPlay(), value.play() else { throw VoiceError.changedMedia }
        } catch {
            player?.stop(); player = nil; playbackToken = nil
            releaseIdleSession()
            throw VoiceError.changedMedia
        }
    }
    func stopPlayback() { finishPlayback(.stopped) }

    private func releaseIdleSession() {
        if engine == nil && writer == nil && player == nil && sessionOwned {
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            sessionOwned = false
        }
    }

    private func finishPlayback(_ event: VoicePlaybackEvent) {
        let token = playbackToken
        player?.stop(); player = nil; playbackToken = nil
        releaseIdleSession()
        if let token { playbackEvent?(token, event) }
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in if self.player === player { self.finishPlayback(flag ? .finished : .failed) } }
    }

    nonisolated func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        Task { @MainActor in if self.player === player { self.finishPlayback(.failed) } }
    }

    deinit { for observer in observers { notificationCenter.removeObserver(observer) } }

    private func interruption(_ notification: Notification) {
        if notification.name == AVAudioSession.interruptionNotification,
           let kind = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
           kind != AVAudioSession.InterruptionType.began.rawValue { return }
        if notification.name == AVAudioSession.routeChangeNotification,
           let reason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
           reason != AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue { return }
        finishPlayback(.stopped)
        if let affected = capture {
            blockedGeneration = affected.generation
            event?(affected, .interrupted)
        }
    }

    func transcribe(_ source: VoiceRecognitionSource) async throws -> String {
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN")),
              recognizer.isAvailable, recognizer.supportsOnDeviceRecognition else { throw VoiceError.unavailable }
        let authorization = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
        }
        try Task.checkCancellation()
        guard authorization == .authorized else { throw VoiceError.permission }
        guard recognizer.isAvailable, recognizer.supportsOnDeviceRecognition else { throw VoiceError.unavailable }
        let cache = derivedCache
        let snapshot = try await Task.detached(priority: .utility) {
            try VoiceSpeechSnapshot(source: source, cache: cache)
        }.value
        defer { snapshot.remove() }
        try Task.checkCancellation()
        let text = try await recognize(snapshot, with: recognizer)
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw VoiceError.emptyTranscript }
        return text
    }

    private func recognize(_ snapshot: VoiceSpeechSnapshot, with recognizer: SFSpeechRecognizer) async throws -> String {
        try await recognizeSnapshot(snapshot) { request, finish in
            recognizer.recognitionTask(with: request) { result, error in
                if let result, result.isFinal { finish(.success(result.bestTranscription.formattedString)) }
                else if error != nil { finish(.failure(VoiceError.unavailable)) }
            }
        }
    }

    /// Owns the exact request file and terminal callback. Native tests inject only Speech's task starter.
    func recognizeSnapshot(_ snapshot: VoiceSpeechSnapshot,
                           start: (SFSpeechURLRecognitionRequest, @escaping (Result<String, Error>) -> Void) -> SFSpeechRecognitionTask?) async throws -> String {
        guard speechID == nil else { throw VoiceError.interrupted }
        let id = UUID()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { callback in
                guard !Task.isCancelled else { snapshot.remove(); callback.resume(throwing: CancellationError()); return }
                speechID = id; continuation = callback; speechSnapshot = snapshot
                let request = Self.makeSpeechRequest(snapshot)
                speech = start(request) { [weak self] result in
                    Task { @MainActor in
                        guard let self, self.speechID == id else { return }
                        self.finishSpeech(result)
                    }
                }
                deadline = Task { [weak self] in
                    try? await Task.sleep(for: .seconds(90))
                    guard !Task.isCancelled, self?.speechID == id else { return }
                    self?.finishSpeech(.failure(VoiceError.unavailable))
                }
            }
        } onCancel: { Task { @MainActor [weak self] in if self?.speechID == id { self?.cancelTranscription() } } }
    }

    nonisolated static func makeSpeechRequest(_ snapshot: VoiceSpeechSnapshot) -> SFSpeechURLRecognitionRequest {
        makeSpeechRequest(snapshot.url)
    }
    // Compatibility builder used by the retained original URL-contract test.
    nonisolated static func makeSpeechRequest(_ url: URL) -> SFSpeechURLRecognitionRequest {
        let request = SFSpeechURLRecognitionRequest(url: url)
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = false
        return request
    }

    func cancelTranscription() { finishSpeech(.failure(CancellationError())) }
    private func finishSpeech(_ result: Result<String, Error>) {
        let callback = continuation
        continuation = nil; speechID = nil; deadline?.cancel(); deadline = nil
        speech?.cancel(); speech = nil
        var completed = result
        do { try speechSnapshot?.finish() } catch { completed = .failure(JournalError.storage) }
        speechSnapshot = nil
        callback?.resume(with: completed)
    }
}

/// A request owns this protected regular file until completion, failure or cancellation.
final class VoiceSpeechSnapshot: @unchecked Sendable {
    // Only compatibility/native probes use this cache; the app injects its actual journal namespace.
    static let fallbackCache = VoiceDerivedAudioCache(directory: FileManager.default.temporaryDirectory.appendingPathComponent("Riji-Owned-Voice-Cache"))
    private let lease: VoiceDerivedAudioLease
    var url: URL { lease.url }
    init(source: VoiceRecognitionSource, cache: VoiceDerivedAudioCache = fallbackCache) throws {
        guard SourceHash.digest(source.data) == source.media.hash, Int64(source.data.count) == source.media.byteCount else { throw VoiceError.changedMedia }
        let sourceURL = URL(fileURLWithPath: source.media.relativeLink)
        guard let recordingID = UUID(uuidString: sourceURL.deletingLastPathComponent().lastPathComponent) else { throw VoiceError.changedMedia }
        lease = try cache.snapshot(data: source.data, recordingID: recordingID, segmentID: source.media.segmentID, fileExtension: sourceURL.pathExtension)
    }
    init(data: Data, fileExtension: String, cache: VoiceDerivedAudioCache = fallbackCache,
         recordingID: UUID = UUID(), segmentID: UUID = UUID()) throws {
        lease = try cache.snapshot(data: data, recordingID: recordingID, segmentID: segmentID, fileExtension: fileExtension)
    }
    func finish() throws { try lease.finish() }
    func remove() { try? finish() }
}
