import Foundation
import AVFoundation
import RijiCore
import Darwin

struct ClosedVoicePiece {
    let url: URL
    let token: VoiceCaptureToken
    let evidence: VoiceCaptureEvidence
}

/// One serial writer owns file switches. The audio callback only copies a bounded buffer and enqueues it.
final class ContinuousVoiceWriter: @unchecked Sendable {
    struct Accounting {
        var acceptedFrames: Int64 = 0
        var assignedFrames: Int64 = 0
        var rejectedFrames: Int64 = 0
        var failure: Bool = false
    }
    private let queue = DispatchQueue(label: "Riji.voice.writer", qos: .userInitiated)
    private let lock = NSLock()
    private let format: AVAudioFormat
    private let maximumQueuedFrames: Int64
    private let beforeWrite: () throws -> Void
    private var queuedFrames: Int64 = 0
    private var accounting = Accounting()
    private var accepting = true
    private var file: AVAudioFile?
    private var token: VoiceCaptureToken
    private var url: URL
    private var firstFrame: Int64 = 0
    private var pieceFrames: Int64 = 0
    private var rotationRequested = false
    var onRotation: ((VoiceCaptureToken) -> Void)?
    var onFailure: ((VoiceCaptureToken) -> Void)?

    init(url: URL, token: VoiceCaptureToken, format: AVAudioFormat,
         maximumQueuedFrames: Int64? = nil, beforeWrite: @escaping () throws -> Void = {}) throws {
        guard format.sampleRate.isFinite, format.sampleRate > 0, format.channelCount > 0,
              format.channelCount <= 8 else { throw VoiceError.unavailable }
        self.url = url; self.token = token; self.format = format; self.beforeWrite = beforeWrite
        self.maximumQueuedFrames = maximumQueuedFrames ?? Int64(format.sampleRate * 2)
        file = try Self.openOriginal(url, format: format)
    }

    @discardableResult
    func accept(_ source: AVAudioPCMBuffer) -> Bool {
        let frames = Int64(source.frameLength)
        lock.lock()
        guard accepting, !accounting.failure, frames > 0, source.format == format,
              frames <= maximumQueuedFrames - queuedFrames,
              frames * Int64(format.streamDescription.pointee.mBytesPerFrame) * Int64(format.channelCount) <= 16 * 1024 * 1024 else {
            accounting.rejectedFrames += frames
            let notify = !accounting.failure && accepting
            accounting.failure = true; accepting = false
            lock.unlock()
            if notify { queue.async { self.onFailure?(self.token) } }
            return false
        }
        queuedFrames += frames
        accounting.acceptedFrames += frames
        guard let copy = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: source.frameLength) else {
            failCopy(frames); return false
        }
        copy.frameLength = source.frameLength
        let src = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: source.audioBufferList))
        let dst = UnsafeMutableAudioBufferListPointer(copy.mutableAudioBufferList)
        for (index, entry) in src.enumerated() {
            guard let sourceBytes = entry.mData, let destinationBytes = dst[index].mData else { failCopy(frames); return false }
            memcpy(destinationBytes, sourceBytes, Int(entry.mDataByteSize))
        }
        // Keep copying and enqueue ordered with admission, including stop races.
        queue.async { self.write(copy, frames: frames) }
        lock.unlock()
        return true
    }

    private func failCopy(_ frames: Int64) {
        queuedFrames -= frames; accounting.failure = true; accepting = false; lock.unlock()
        queue.async { self.onFailure?(self.token) }
    }

    private func write(_ buffer: AVAudioPCMBuffer, frames: Int64) {
        defer { lock.lock(); queuedFrames -= frames; lock.unlock() }
        do {
            try beforeWrite()
            guard let file else { throw JournalError.storage }
            try file.write(from: buffer)
            pieceFrames += frames
            lock.lock(); accounting.assignedFrames += frames; lock.unlock()
            if !rotationRequested, Double(pieceFrames) / format.sampleRate >= 45 {
                rotationRequested = true; onRotation?(token)
            }
        } catch {
            lock.lock(); let notify = !accounting.failure; accounting.failure = true; accepting = false; lock.unlock()
            if notify { onFailure?(token) }
        }
    }

    /// The next file is prepared while the previous file continues accepting buffers.
    func rotate(url nextURL: URL, token nextToken: VoiceCaptureToken) throws -> ClosedVoicePiece {
        let next = try Self.openOriginal(nextURL, format: format)
        return try queue.sync {
            guard file != nil, nextToken.generation == token.generation, isAccepting else { throw VoiceError.interrupted }
            let closed = closedPiece()
            file = next; firstFrame += pieceFrames; pieceFrames = 0
            url = nextURL; token = nextToken; rotationRequested = false
            return closed
        }
    }

    var isAccepting: Bool { lock.lock(); defer { lock.unlock() }; return accepting && !accounting.failure }
    func closeAdmission() { lock.lock(); accepting = false; lock.unlock() }
    func finishStop() async -> ClosedVoicePiece {
        await withCheckedContinuation { continuation in
            queue.async { let closed = self.closedPiece(); self.file = nil; continuation.resume(returning: closed) }
        }
    }
    func stop() -> ClosedVoicePiece {
        closeAdmission()
        return queue.sync { let closed = closedPiece(); file = nil; return closed }
    }
    func snapshot() -> Accounting { lock.lock(); defer { lock.unlock() }; return accounting }
    func drain() { queue.sync {} }
    private func closedPiece() -> ClosedVoicePiece {
        var evidence = VoiceCaptureEvidence(firstFrame: firstFrame, frameCount: pieceFrames, sampleRate: format.sampleRate, streamID: token.generation)
        let counts = snapshot()
        if counts.failure {
            evidence.rejectedFrames = counts.rejectedFrames
            evidence.unassignedFrames = counts.acceptedFrames - counts.assignedFrames
        }
        return ClosedVoicePiece(url: url, token: token, evidence: evidence)
    }

    private static func openOriginal(_ url: URL, format: AVAudioFormat) throws -> AVAudioFile {
        let fd = Darwin.open(url.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard fd >= 0 else { throw VoiceError.changedMedia }
        defer { Darwin.close(fd) }
        var owned = stat(), current = stat()
        guard fstat(fd, &owned) == 0, lstat(url.path, &current) == 0, owned.st_ino == current.st_ino else { throw VoiceError.changedMedia }
        // AVAudioFile replaces contents; only this exact newly-created empty file is eligible.
        let result = try AVAudioFile(forWriting: url, settings: [AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: format.sampleRate, AVNumberOfChannelsKey: format.channelCount, AVEncoderBitRateKey: 96000],
            commonFormat: format.commonFormat, interleaved: format.isInterleaved)
        try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: url.path)
        return result
    }
}
