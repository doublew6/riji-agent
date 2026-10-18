import XCTest
import AVFoundation
import Speech
import RijiCore

final class NativeVoiceTests: XCTestCase {
    private let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48000, channels: 1, interleaved: false)!
    private func pcm(_ count: Int, starting: Int = 0) -> AVAudioPCMBuffer {
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count))!
        buffer.frameLength = AVAudioFrameCount(count)
        for index in 0..<count { buffer.floatChannelData![0][index] = Float(sin(Double(index + starting) * 0.031) * 0.1) }
        return buffer
    }
    private func fixture() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("NativeSynthetic-\(UUID())")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }
    private func feed(_ frames: Int, writer: ContinuousVoiceWriter, offset: Int) {
        var remaining = frames, sent = 0
        while remaining > 0 {
            let count = min(1027, remaining)
            XCTAssertTrue(writer.accept(pcm(count, starting: offset + sent)))
            sent += count; remaining -= count
            if sent % 10270 == 0 { writer.drain() }
        }
        writer.drain()
    }

    func testProductionWriterAssignsEveryFrameAcrossDelayedValidationBoundaries() throws {
        let root = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let generation = UUID(), recordingID = UUID()
        let first = VoiceCaptureToken(recordingID: recordingID, segmentID: UUID(), generation: generation)
        let writer = try ContinuousVoiceWriter(url: root.appendingPathComponent("a.m4a"), token: first, format: format)
        let counts = [72001, 96017, 21623]
        var closed: [ClosedVoicePiece] = [], total = 0
        let validationGate = DispatchSemaphore(value: 0)
        let validationStarted = DispatchSemaphore(value: 0)
        let validated = expectation(description: "Closed original validation finished after later input")
        for (index, count) in counts.enumerated() {
            feed(count, writer: writer, offset: total); total += count
            if index < counts.count - 1 {
                let next = VoiceCaptureToken(recordingID: recordingID, segmentID: UUID(), generation: generation)
                let piece = try writer.rotate(url: root.appendingPathComponent("\(index).m4a"), token: next)
                closed.append(piece)
                if index == 0 {
                    DispatchQueue.global().async {
                        validationStarted.signal(); validationGate.wait()
                        XCTAssertEqual(try? NativeVoiceServices.decodeDuration(piece.url), Double(count) / 48000)
                        validated.fulfill()
                    }
                    XCTAssertEqual(validationStarted.wait(timeout: .now() + 2), .success)
                }
            } else { closed.append(writer.stop()) }
        }
        // Validation of the first piece is still blocked; every later frame has already been written.
        XCTAssertEqual(writer.snapshot().assignedFrames, Int64(total))
        validationGate.signal(); wait(for: [validated], timeout: 5)
        var start: Int64 = 0
        for (index, piece) in closed.enumerated() {
            let audio = try AVAudioFile(forReading: piece.url)
            XCTAssertEqual(audio.length, Int64(counts[index]))
            XCTAssertGreaterThan(try correlation(audio, firstFrame: start), 0.98)
            XCTAssertEqual(piece.evidence.firstFrame, start)
            XCTAssertEqual(piece.evidence.frameCount, Int64(counts[index]))
            XCTAssertEqual(try NativeVoiceServices.decodeDuration(piece.url), Double(counts[index]) / 48000)
            start += Int64(counts[index])
        }
        let accounting = writer.snapshot()
        XCTAssertEqual(accounting.acceptedFrames, accounting.assignedFrames)
        XCTAssertEqual(accounting.rejectedFrames, 0)
        XCTAssertFalse(accounting.failure)
    }

    private func correlation(_ file: AVAudioFile, firstFrame: Int64) throws -> Double {
        let decoded = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: 8192)!
        var position: Int64 = 0
        var product = 0.0, actualEnergy = 0.0, expectedEnergy = 0.0
        while file.framePosition < file.length {
            try file.read(into: decoded)
            for index in 0..<Int(decoded.frameLength) {
                let actual = Double(decoded.floatChannelData![0][index])
                let expected = sin(Double(firstFrame + position + Int64(index)) * 0.031) * 0.1
                product += actual * expected; actualEnergy += actual * actual; expectedEnergy += expected * expected
            }
            position += Int64(decoded.frameLength)
        }
        return product / sqrt(actualEnergy * expectedEnergy)
    }

    func testProductionWriterOverflowReportsEveryRejectedFrameAndStopsAdmission() throws {
        let root = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let hold = DispatchSemaphore(value: 0), started = DispatchSemaphore(value: 0)
        let token = VoiceCaptureToken(recordingID: UUID(), segmentID: UUID(), generation: UUID())
        let writer = try ContinuousVoiceWriter(url: root.appendingPathComponent("overflow.m4a"), token: token,
            format: format, maximumQueuedFrames: 2048, beforeWrite: { started.signal(); hold.wait() })
        let failure = expectation(description: "Explicit overflow interruption")
        writer.onFailure = { value in XCTAssertEqual(value, token); failure.fulfill() }
        XCTAssertTrue(writer.accept(pcm(1027)))
        XCTAssertEqual(started.wait(timeout: .now() + 2), .success)
        XCTAssertFalse(writer.accept(pcm(1027)))
        hold.signal(); _ = writer.stop()
        wait(for: [failure], timeout: 5)
        let accounting = writer.snapshot()
        XCTAssertEqual(accounting.acceptedFrames, 1027)
        XCTAssertEqual(accounting.assignedFrames, 1027)
        XCTAssertEqual(accounting.rejectedFrames, 1027)
        XCTAssertTrue(accounting.failure)
    }

    func testProductionWriterStartRotationWriteAndExplicitStopFailuresRetainOriginals() throws {
        let root = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let token = VoiceCaptureToken(recordingID: UUID(), segmentID: UUID(), generation: UUID())
        let collision = root.appendingPathComponent("collision.m4a"), bytes = Data("existing original".utf8)
        try bytes.write(to: collision)
        XCTAssertThrowsError(try ContinuousVoiceWriter(url: collision, token: token, format: format))
        let original = root.appendingPathComponent("original.m4a")
        let writer = try ContinuousVoiceWriter(url: original, token: token, format: format)
        XCTAssertTrue(writer.accept(pcm(1027))); writer.drain()
        XCTAssertThrowsError(try writer.rotate(url: collision, token: token))
        let closed = writer.stop()
        XCTAssertEqual(closed.evidence.frameCount, 1027)
        XCTAssertFalse(writer.accept(pcm(10)))
        XCTAssertEqual(try Data(contentsOf: collision), bytes)
        XCTAssertGreaterThan(try NativeVoiceServices.decodeDuration(original), 0)
        let failed = try ContinuousVoiceWriter(url: root.appendingPathComponent("write-failure.m4a"), token: token,
                                               format: format, beforeWrite: { throw JournalError.storage })
        let failure = expectation(description: "Storage failure callback")
        failed.onFailure = { _ in failure.fulfill() }
        XCTAssertTrue(failed.accept(pcm(1027))); failed.drain(); _ = failed.stop()
        wait(for: [failure], timeout: 5)
        XCTAssertTrue(failed.snapshot().failure)
        XCTAssertEqual(failed.snapshot().acceptedFrames - failed.snapshot().assignedFrames, 1027)
    }

    func testExactProductionSpeechRequestRequiresOnDeviceOriginalFileAndFinalOnly() {
        let original = URL(fileURLWithPath: "/tmp/synthetic-original-only.wav")
        let request = NativeVoiceServices.makeSpeechRequest(original)
        XCTAssertEqual(request.url, original)
        XCTAssertTrue(request.requiresOnDeviceRecognition)
        XCTAssertFalse(request.shouldReportPartialResults)
    }

    @MainActor
    func testOwnedEngineConfigurationChangeIsDeferredOnceAndIgnoresStaleEngines() async {
        // Unstarted engines and a private notification center never access an input node or session.
        let center = NotificationCenter(), owned = AVAudioEngine(), other = AVAudioEngine()
        let observer = VoiceEngineConfigurationObserver(center: center)
        var delivered: [UUID] = []
        let first = UUID(), second = UUID()
        let firstEvent = expectation(description: "Owned engine interruption is delivered asynchronously")
        observer.observe(owned, generation: first) { generation in
            delivered.append(generation); firstEvent.fulfill()
        }
        center.post(name: .AVAudioEngineConfigurationChange, object: other)
        center.post(name: .AVAudioEngineConfigurationChange, object: owned)
        center.post(name: .AVAudioEngineConfigurationChange, object: owned)
        XCTAssertTrue(delivered.isEmpty)
        await fulfillment(of: [firstEvent], timeout: 2)
        XCTAssertEqual(delivered, [first])

        observer.observe(owned, generation: first) { generation in delivered.append(generation) }
        center.post(name: .AVAudioEngineConfigurationChange, object: owned)
        observer.cancel() // A queued old callback cannot interrupt an explicitly resumed generation.
        let resumedEvent = expectation(description: "Only the new owned engine generation is delivered")
        observer.observe(other, generation: second) { generation in
            delivered.append(generation); resumedEvent.fulfill()
        }
        center.post(name: .AVAudioEngineConfigurationChange, object: owned)
        center.post(name: .AVAudioEngineConfigurationChange, object: other)
        XCTAssertEqual(delivered, [first])
        await fulfillment(of: [resumedEvent], timeout: 2)
        XCTAssertEqual(delivered, [first, second])
    }
}

extension NativeVoiceTests {
    func testLoopThreeProductionAutomaticThresholdRotatesTwiceAndResetsFrameOrder() throws {
        let root = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let token = VoiceCaptureToken(recordingID: UUID(), segmentID: UUID(), generation: UUID())
        let writer = try ContinuousVoiceWriter(url: root.appendingPathComponent("first.m4a"), token: token, format: format)
        let transitions = DispatchQueue(label: "Synthetic.automatic.transitions")
        let rotated = DispatchSemaphore(value: 0)
        var closed: [ClosedVoicePiece] = []
        writer.onRotation = { previous in
            transitions.async {
                do {
                    let next = VoiceCaptureToken(recordingID: token.recordingID, segmentID: UUID(), generation: token.generation)
                    XCTAssertEqual(previous.recordingID, token.recordingID)
                    closed.append(try writer.rotate(url: root.appendingPathComponent("\(next.segmentID).m4a"), token: next))
                } catch { XCTFail("Automatic transition failed") }
                rotated.signal()
            }
        }
        let threshold = 45 * 48000
        for index in 0..<2 {
            // This executes the writer's actual >=45 branch; no test sends a rotate event.
            feed(threshold, writer: writer, offset: index * threshold)
            XCTAssertEqual(rotated.wait(timeout: .now() + 5), .success)
        }
        feed(21623, writer: writer, offset: threshold * 2)
        closed.append(writer.stop())
        XCTAssertEqual(closed.count, 3)
        var start: Int64 = 0
        for (index, piece) in closed.enumerated() {
            let frames = Int64(index < 2 ? threshold : 21623)
            let audio = try AVAudioFile(forReading: piece.url)
            XCTAssertEqual(audio.length, frames)
            XCTAssertEqual(piece.evidence.firstFrame, start)
            XCTAssertEqual(piece.evidence.frameCount, frames)
            XCTAssertGreaterThan(try correlation(audio, firstFrame: start), 0.98)
            start += frames
        }
        XCTAssertEqual(writer.snapshot().acceptedFrames, start)
        XCTAssertEqual(writer.snapshot().assignedFrames, start)
        XCTAssertEqual(writer.snapshot().rejectedFrames, 0)
    }

    @MainActor
    func testLoopThreeNativePlaybackUsesVerifiedBytesAfterOriginalPathReplacement() async throws {
        let root = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("original.wav")
        var original: AVAudioFile? = try AVAudioFile(forWriting: url, settings: format.settings)
        try original?.write(from: pcm(12000)); original = nil
        let verified = try Data(contentsOf: url)
        try Data("replacement is not audio".utf8).write(to: url)
        let services = NativeVoiceServices()
        let token = VoicePlaybackToken(recordingID: UUID(), segmentID: UUID(), generation: UUID())
        let completed = expectation(description: "Actual production playback finishes from verified bytes")
        services.playbackEvent = { received, event in
            XCTAssertEqual(received, token)
            guard case .finished = event else { XCTFail("Verified native playback did not finish"); completed.fulfill(); return }
            completed.fulfill()
        }
        try services.play(verified, token: token)
        await fulfillment(of: [completed], timeout: 5)
        XCTAssertEqual(try Data(contentsOf: url), Data("replacement is not audio".utf8))
    }
}

private final class NativeWriteGate: @unchecked Sendable {
    let entered = XCTestExpectation(description: "Real serial writer entered held write")
    let release = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var armed = false
    func arm() { lock.lock(); armed = true; lock.unlock() }
    func write() throws {
        lock.lock(); let hold = armed; armed = false; lock.unlock()
        if hold { entered.fulfill(); guard release.wait(timeout: .now() + 10) == .success else { throw JournalError.storage } }
    }
}

extension NativeVoiceTests {
    @MainActor
    func testLoopFourProductionNativeStopClosesAdmissionBeforeHeldDrainAndPersistsEnd() async throws {
        let root = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"))
        let day = try JournalDay("2026-09-13"), snapshot = try await store.open(day)
        let draft = try await store.makeDraft(for: snapshot, section: "🧠 Notes")
        try await store.saveDraft(draft)
        let target = try await store.voiceTarget(draft), id = UUID(), firstID = UUID(), secondID = UUID()
        _ = try await store.createRecording(id: id, target: target)
        let firstURL = try await store.reserveVoiceSegment(id, id: firstID, captureExpected: true)
        let generation = UUID(), first = VoiceCaptureToken(recordingID: id, segmentID: firstID, generation: generation)
        let gate = NativeWriteGate(); defer { gate.release.signal() }
        let writer = try ContinuousVoiceWriter(url: firstURL, token: first, format: format, maximumQueuedFrames: 2048, beforeWrite: gate.write)
        let service = NativeVoiceServices()
        try service.installCapture(writer, token: first)
        feed(1027, writer: writer, offset: 0)
        let nextURL = try await store.reserveVoiceSegment(id, id: secondID, captureExpected: true)
        let next = VoiceCaptureToken(recordingID: id, segmentID: secondID, generation: generation)
        _ = try await service.rotate(url: nextURL, token: next)
        gate.arm(); XCTAssertTrue(writer.accept(pcm(1027, starting: 1027)))
        await fulfillment(of: [gate.entered], timeout: 3)
        XCTAssertFalse(writer.accept(pcm(2049, starting: 2054)))
        let before = Date(), drain = service.stop(), sameDrain = service.stop()
        XCTAssertLessThan(Date().timeIntervalSince(before), 0.1)
        XCTAssertFalse(writer.isAccepting)
        XCTAssertNil(service.evidence(for: next))
        // The real write remains held. MainActor and the journal actor can both make progress.
        await Task.yield()
        try await store.endRecording(id)
        let ended = try await store.voiceManifest(id)
        XCTAssertTrue(ended.ended); XCTAssertFalse(ended.segments.last!.finalized)
        do { _ = try await service.rotate(url: root.appendingPathComponent("late.m4a"), token: next); XCTFail("Ended generation rotated") }
        catch { XCTAssertEqual(error as? VoiceError, .interrupted) }
        gate.release.signal()
        let firstDrain = try await drain.value, repeatedDrain = try await sameDrain.value
        XCTAssertEqual(firstDrain, repeatedDrain)
        for (token, url, offset) in [(first, firstURL, Int64(0)), (next, nextURL, Int64(1027))] {
            let evidence = service.evidence(for: token)!
            XCTAssertEqual(evidence.firstFrame, offset); XCTAssertEqual(evidence.frameCount, 1027)
            let validated = try await service.validatedMedia(of: url)
            try await store.finalizeVoiceSegment(id, id: token.segmentID, duration: validated.duration, evidence: evidence, validation: validated)
            XCTAssertGreaterThan(try correlation(AVAudioFile(forReading: url), firstFrame: offset), 0.98)
            let wrong = VoiceCaptureToken(recordingID: UUID(), segmentID: token.segmentID, generation: generation)
            XCTAssertNil(service.evidence(for: wrong))
        }
        let final = try await store.voiceManifest(id)
        XCTAssertTrue(final.ended)
        XCTAssertEqual(final.segments.last?.captureEvidence?.rejectedFrames, 2049)
        XCTAssertEqual(final.segments.last?.captureEvidence?.unassignedFrames, 0)
        XCTAssertEqual(writer.snapshot().acceptedFrames, writer.snapshot().assignedFrames)
    }

    @MainActor
    func testLoopFourProductionSpeechSnapshotOwnsVerifiedBytesUntilTerminalCallbackOrCancellation() async throws {
        for cancel in [false, true] {
            let root = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
            let original = root.appendingPathComponent("speech.m4a")
            let capture = VoiceCaptureToken(recordingID: UUID(), segmentID: UUID(), generation: UUID())
            let writer = try ContinuousVoiceWriter(url: original, token: capture, format: format)
            feed(72001, writer: writer, offset: 0); _ = writer.stop()
            let bytes = try Data(contentsOf: original)
            let snapshot = try VoiceSpeechSnapshot(data: bytes, fileExtension: "m4a")
            let service = NativeVoiceServices()
            var finish: ((Result<String, Error>) -> Void)?
            let started = expectation(description: "Actual production Speech request builder ran")
            let task = Task {
                try await service.recognizeSnapshot(snapshot) { request, callback in
                    XCTAssertEqual(request.url, snapshot.url); XCTAssertNotEqual(request.url, original)
                    XCTAssertTrue(request.requiresOnDeviceRecognition); XCTAssertFalse(request.shouldReportPartialResults)
                    finish = callback; started.fulfill(); return nil
                }
            }
            await fulfillment(of: [started], timeout: 2)
            try Data("replacement during held recognition".utf8).write(to: original)
            XCTAssertEqual(try Data(contentsOf: snapshot.url), bytes)
            try bytes.write(to: original)
            if cancel {
                task.cancel()
                do { _ = try await task.value; XCTFail("Cancelled recognition succeeded") } catch { XCTAssertTrue(error is CancellationError) }
            } else {
                finish?(.success("Synthetic callback original"))
                let text = try await task.value
                XCTAssertEqual(text, "Synthetic callback original")
            }
            XCTAssertFalse(FileManager.default.fileExists(atPath: snapshot.url.path))
            finish?(.success("Late duplicate")); await Task.yield()
            XCTAssertEqual(try Data(contentsOf: original), bytes)
        }
    }
}

extension NativeVoiceTests {
    @MainActor
    func testLoopFourProductionDecodedIdentityRejectsReplacedPathBeforeCoreFinalization() async throws {
        let root = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"))
        let day = try JournalDay("2026-09-13"), snapshot = try await store.open(day)
        let draft = try await store.makeDraft(for: snapshot, section: "🧠 Notes")
        try await store.saveDraft(draft)
        let target = try await store.voiceTarget(draft), id = UUID(), segmentID = UUID()
        _ = try await store.createRecording(id: id, target: target)
        let url = try await store.reserveVoiceSegment(id, id: segmentID, captureExpected: true)
        let token = VoiceCaptureToken(recordingID: id, segmentID: segmentID, generation: UUID())
        let sink = try ContinuousVoiceWriter(url: url, token: token, format: format), native = NativeVoiceServices()
        try native.installCapture(sink, token: token)
        feed(72001, writer: sink, offset: 0)
        _ = try await native.stop().value
        let validation = try await native.validatedMedia(of: url), evidence = native.evidence(for: token)!
        let bytes = try Data(contentsOf: url)
        XCTAssertEqual(validation.hash, SourceHash.digest(bytes)); XCTAssertEqual(validation.byteCount, Int64(bytes.count))
        try Data("Replaced after the real native decoder returned".utf8).write(to: url)
        do {
            try await store.finalizeVoiceSegment(id, id: segmentID, duration: validation.duration, evidence: evidence, validation: validation)
            XCTFail("Decoder evidence was attached to different bytes")
        } catch { XCTAssertEqual(error as? VoiceError, .changedMedia) }
        let unfinalized = try await store.voiceManifest(id).segments[0]
        XCTAssertFalse(unfinalized.finalized); XCTAssertNil(unfinalized.hash); XCTAssertNil(unfinalized.captureEvidence)
        try bytes.write(to: url)
        let recovery = try await native.validatedMedia(of: url)
        try await store.finalizeVoiceSegment(id, id: segmentID, duration: recovery.duration, evidence: evidence, validation: recovery)
        let finalized = try await store.voiceManifest(id).segments[0]
        XCTAssertEqual(finalized.hash, validation.hash); XCTAssertEqual(finalized.captureEvidence?.frameCount, 72001)
        XCTAssertEqual(try Data(contentsOf: url), bytes)
    }
}

@MainActor
private final class SyntheticNativePort: VoiceServices {
    let native: NativeVoiceServices
    var writer: ContinuousVoiceWriter?
    var beforeWrite: () throws -> Void = {}
    var event: ((VoiceCaptureToken, VoiceHardwareEvent) -> Void)? {
        get { native.event } set { native.event = newValue }
    }
    var playbackEvent: ((VoicePlaybackToken, VoicePlaybackEvent) -> Void)? {
        get { native.playbackEvent } set { native.playbackEvent = newValue }
    }
    init(native: NativeVoiceServices) { self.native = native }
    func microphonePermission() async -> Bool { true }
    func start(url: URL, token: VoiceCaptureToken) throws {
        let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48000, channels: 1, interleaved: false)!
        let sink = try ContinuousVoiceWriter(url: url, token: token, format: format, beforeWrite: beforeWrite)
        try native.installCapture(sink, token: token); writer = sink
    }
    func stop() -> Task<Double, Error> { native.stop() }
    func rotate(url: URL, token: VoiceCaptureToken) async throws -> Double { try await native.rotate(url: url, token: token) }
    func validatedMedia(of url: URL) async throws -> VoiceMediaValidation { try await native.validatedMedia(of: url) }
    func duration(of url: URL) throws -> Double { try native.duration(of: url) }
    func play(_ data: Data, token: VoicePlaybackToken) throws { try native.play(data, token: token) }
    func stopPlayback() { native.stopPlayback() }
    func evidence(for token: VoiceCaptureToken) -> VoiceCaptureEvidence? { native.evidence(for: token) }
    func transcribe(_ source: VoiceRecognitionSource) async throws -> String { throw VoiceError.unavailable }
    func cancelTranscription() { native.cancelTranscription() }
}

extension NativeVoiceTests {
    @MainActor
    func testLoopFiveProductionSessionNotificationsFilterAndPersistOwnedSyntheticCapture() async throws {
        for name in [AVAudioSession.interruptionNotification, AVAudioSession.routeChangeNotification, AVAudioSession.mediaServicesWereResetNotification] {
            let root = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
            let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"))
            let center = NotificationCenter(), native = NativeVoiceServices(cache: store.derivedVoiceCache, center: center)
            let port = SyntheticNativePort(native: native), model = VoiceViewModel(store: store, services: port)
            let day = try JournalDay("2026-09-13"), snapshot = try await store.open(day)
            let draft = try await store.makeDraft(for: snapshot, section: "🧠 Notes")
            try await store.saveDraft(draft)
            let attached = await model.attach(target: try await store.voiceTarget(draft)); XCTAssertTrue(attached)
            await model.record()
            feed(72001, writer: port.writer!, offset: 0)
            center.post(name: AVAudioSession.interruptionNotification, object: nil, userInfo: [AVAudioSessionInterruptionTypeKey: AVAudioSession.InterruptionType.ended.rawValue])
            center.post(name: AVAudioSession.routeChangeNotification, object: nil, userInfo: [AVAudioSessionRouteChangeReasonKey: AVAudioSession.RouteChangeReason.newDeviceAvailable.rawValue])
            try await Task.sleep(for: .milliseconds(30))
            XCTAssertEqual(model.phase, .recording); XCTAssertTrue(port.writer!.isAccepting)
            center.post(name: name, object: nil, userInfo: [AVAudioSessionInterruptionTypeKey: AVAudioSession.InterruptionType.began.rawValue,
                AVAudioSessionRouteChangeReasonKey: AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue])
            for _ in 0..<200 where model.phase == .recording || model.busy { try await Task.sleep(for: .milliseconds(10)) }
            XCTAssertEqual(model.phase, .paused); XCTAssertFalse(port.writer!.isAccepting)
            let retained = try await store.recording(model.recording!.id)
            XCTAssertEqual(retained.segments.count, 1); XCTAssertTrue(retained.segments[0].finalized)
            XCTAssertEqual(retained.segments[0].captureEvidence?.frameCount, 72001)
            let original = try await store.exportVoiceAudio(retained.id, segmentID: retained.segments[0].id)
            XCTAssertEqual(SourceHash.digest(original), retained.segments[0].hash)
            await model.record(); XCTAssertEqual(model.phase, .recording)
            feed(1027, writer: port.writer!, offset: 0)
            await model.pause(ending: true)
            XCTAssertEqual(model.phase, .ended)
        }
    }

    @MainActor
    func testLoopFiveActualNextWriterFailureBeforeTransitionReturnReachesControllerRecovery() async throws {
        let root = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
        let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"))
        let native = NativeVoiceServices(cache: store.derivedVoiceCache, center: NotificationCenter())
        let port = SyntheticNativePort(native: native), model = VoiceViewModel(store: store, services: port)
        let fault = NativeNextWriteFault()
        port.beforeWrite = { try fault.check() }
        let snapshot = try await store.open(JournalDay("2026-09-13"))
        let draft = try await store.makeDraft(for: snapshot, section: "🧠 Notes")
        try await store.saveDraft(draft)
        let attached = await model.attach(target: try await store.voiceTarget(draft)); XCTAssertTrue(attached)
        await model.record(); feed(72001, writer: port.writer!, offset: 0)
        var release: CheckedContinuation<Void, Never>?
        native.transitionReady = { await withCheckedContinuation { release = $0 } }
        let stream = model.recording!.segments[0], id = model.recording!.id
        let previous = VoiceCaptureToken(recordingID: id, segmentID: stream.id,
            generation: UUID())
        // The writer's actual threshold callback supplies the controller-owned token.
        feed(48_000 * 45 - 72001, writer: port.writer!, offset: 72001)
        for _ in 0..<200 where release == nil { try await Task.sleep(for: .milliseconds(5)) }
        XCTAssertNotNil(release)
        // Keep a playable beginning in the next original, then fail a real production writer write.
        feed(1027, writer: port.writer!, offset: 48_000 * 45)
        fault.arm(); XCTAssertTrue(port.writer!.accept(pcm(1027)))
        port.writer!.drain()
        for _ in 0..<200 where model.phase == .recording { try await Task.sleep(for: .milliseconds(5)) }
        XCTAssertFalse(port.writer!.isAccepting); XCTAssertEqual(model.phase, .paused)
        release?.resume(); native.transitionReady = nil
        for _ in 0..<200 where model.busy || model.captureStopPending { try await Task.sleep(for: .milliseconds(10)) }
        let retained = try await store.recording(id)
        XCTAssertEqual(retained.segments.count, 2); XCTAssertTrue(retained.segments.allSatisfy(\.finalized))
        XCTAssertEqual(retained.segments[0].captureEvidence?.frameCount, 48_000 * 45)
        XCTAssertEqual(retained.segments[1].captureEvidence?.frameCount, 1027)
        XCTAssertEqual(retained.segments[1].captureEvidence?.unassignedFrames, 1027)
        XCTAssertNil(native.evidence(for: previous))
        XCTAssertFalse(retained.ended)
    }
}

private final class NativeNextWriteFault: @unchecked Sendable {
    private let lock = NSLock()
    private var armed = false
    func arm() { lock.lock(); armed = true; lock.unlock() }
    func check() throws { lock.lock(); defer { lock.unlock() }; if armed { armed = false; throw JournalError.storage } }
}

extension NativeVoiceTests {
    @MainActor
    func testLoopFiveSpeechDeletionRaceKeepsActiveImmutableLeaseAndReportsCleanupFailure() async throws {
        for cancel in [false, true] {
            let root = try fixture(); defer { try? FileManager.default.removeItem(at: root) }
            let fault = NativeNextWriteFault()
            let store = try LocalJournalStore(journalDirectory: root.appendingPathComponent("Journals"), stateDirectory: root.appendingPathComponent("State"),
                failure: { purpose, stage in if purpose == .voiceDerived && stage == .beforeRemove { try fault.check() } })
            let day = try JournalDay("2026-09-13"), state = try await store.open(day)
            let draft = try await store.makeDraft(for: state, section: "🧠 Notes")
            try await store.saveDraft(draft)
            let target = try await store.voiceTarget(draft), id = UUID(), segmentID = UUID()
            _ = try await store.createRecording(id: id, target: target)
            let url = try await store.reserveVoiceSegment(id, id: segmentID)
            let token = VoiceCaptureToken(recordingID: id, segmentID: segmentID, generation: UUID())
            let writer = try ContinuousVoiceWriter(url: url, token: token, format: format)
            feed(72001, writer: writer, offset: 0); _ = writer.stop()
            let bytes = try Data(contentsOf: url)
            let cache = store.derivedVoiceCache, native = NativeVoiceServices(cache: cache, center: NotificationCenter())
            let validation = try await native.validatedMedia(of: url)
            try await store.finalizeVoiceSegment(id, id: segmentID, duration: validation.duration, validation: validation)
            let media = try await store.voiceReferences(id)[0], source = try await store.voiceRecognitionSource(id, media: media)
            let snapshot = try VoiceSpeechSnapshot(source: source, cache: cache)
            let other = try VoiceSpeechSnapshot(data: bytes, fileExtension: "m4a", cache: cache)
            var callback: ((Result<String, Error>) -> Void)?
            let started = expectation(description: "Owned native Speech request started")
            let recognition = Task {
                try await native.recognizeSnapshot(snapshot) { request, finish in
                    XCTAssertEqual(request.url, snapshot.url); XCTAssertTrue(request.requiresOnDeviceRecognition)
                    callback = finish; started.fulfill(); return nil
                }
            }
            await fulfillment(of: [started], timeout: 2)
            try await store.deleteVoiceAudio(id)
            let pending = try await store.voiceAudioRemovalComplete(id)
            XCTAssertFalse(pending); XCTAssertEqual(try Data(contentsOf: snapshot.url), bytes)
            try cache.recoverStale(); XCTAssertEqual(try Data(contentsOf: snapshot.url), bytes)
            fault.arm()
            if cancel { recognition.cancel() } else { callback?(.success("Synthetic terminal text")) }
            do { _ = try await recognition.value; XCTFail("Cleanup failure was reported as recognition success") }
            catch { XCTAssertEqual(error as? JournalError, .storage) }
            XCTAssertTrue(try cache.maintenancePending())
            XCTAssertEqual(try Data(contentsOf: snapshot.url), bytes)
            callback?(.success("Late obsolete result")); await Task.yield()
            try cache.recoverStale()
            XCTAssertFalse(FileManager.default.fileExists(atPath: snapshot.url.path))
            XCTAssertEqual(try Data(contentsOf: other.url), bytes)
            let complete = try await store.voiceAudioRemovalComplete(id)
            XCTAssertTrue(complete)
            try other.finish()
            let retained = try await store.voiceManifest(id)
            XCTAssertTrue(retained.deletionRequested); XCTAssertTrue(retained.transcripts.isEmpty)
        }
    }
}
