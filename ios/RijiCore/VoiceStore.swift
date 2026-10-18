import Foundation
import CryptoKit
import Darwin

/// Voice metadata shares the journal actor; audio paths are derived only from its namespace.
extension LocalJournalStore {
    public func voiceNamespace() throws -> UUID {
        let url = state.appendingPathComponent("voice-namespace.json")
        if let value: UUID = try decode(url) { return value }
        let value = UUID()
        try files.write(encoder.encode(value), to: url, purpose: .voice)
        return value
    }

    public func voiceTarget(_ draft: JournalDraft) throws -> VoiceTarget {
        guard !draft.section.contains("\n") else { throw VoiceError.invalidTarget }
        try assertVoiceDestination(draft)
        return VoiceTarget(namespace: try voiceNamespace(), day: draft.day, section: draft.section,
                           sourceHash: draft.sourceHash, templateHash: draft.templateHash)
    }

    public func createRecording(id: UUID, target: VoiceTarget) throws -> VoiceRecording {
        guard target.namespace == (try voiceNamespace()) else { throw VoiceError.invalidTarget }
        if let existing = try readVoiceIfPresent(id) {
            guard existing.target == target else { throw JournalError.operationMismatch }
            return existing
        }
        guard try files.list(state.appendingPathComponent("voice")).count < 10000 else { throw VoiceError.limit }
        let value = VoiceRecording(id: id, target: target, createdAt: Date(), segments: [],
                                   transcripts: [], corrections: [], ended: false, deletionRequested: false)
        try writeVoice(value)
        try? retainVoicePreparationHint(value)
        return value
    }

    public func recording(_ id: UUID) throws -> VoiceRecording {
        guard let value = try readVoiceIfPresent(id) else { throw VoiceError.missing }
        return value
    }

    public func voiceLibrary() throws -> VoiceLibrary {
        let urls = try files.list(state.appendingPathComponent("voice"))
        guard urls.count <= 10000 else { throw VoiceError.limit }
        var values: [VoiceRecording] = []
        var unreadable = 0
        var scannedBytes = 0
        for url in urls where url.pathExtension == "json" {
            guard let data = try? files.read(url, maximumBytes: 4 * 1024 * 1024) else { unreadable += 1; continue }
            scannedBytes += data.count
            guard scannedBytes <= 20 * 1024 * 1024 else { throw VoiceError.limit }
            guard let id = UUID(uuidString: url.deletingPathExtension().lastPathComponent),
                  let value = try? decodeVoice(data, id: id) else { unreadable += 1; continue }
            values.append(value)
        }
        return VoiceLibrary(recordings: values.sorted { $0.createdAt > $1.createdAt }, unreadableCount: unreadable)
    }

    public func reserveVoiceSegment(_ recordingID: UUID, id: UUID, fileExtension: String = "m4a", captureExpected: Bool = false) throws -> URL {
        guard ["m4a", "wav"].contains(fileExtension) else { throw VoiceError.changedMedia }
        var value = try voiceManifest(recordingID)
        try assertVoiceAlive(value)
        try assertVoiceUninserted(recordingID)
        guard !value.ended, value.segments.count < 1000 else { throw VoiceError.limit }
        if let previous = value.segments.first(where: { $0.id == id }) {
            guard previous.fileExtension == fileExtension, !previous.finalized else { throw VoiceError.changedMedia }
        } else {
            var segment = VoiceSegment(id: id, fileExtension: fileExtension, finalized: false)
            if captureExpected { segment.captureExpected = true }
            value.segments.append(segment)
            try writeVoice(value)
        }
        let url = try voiceMediaURL(value, segmentID: id)
        var info = stat()
        guard lstat(url.path, &info) != 0, errno == ENOENT else { throw VoiceError.changedMedia }
        return url
    }

    public func finalizeVoiceSegment(_ recordingID: UUID, id: UUID, duration: Double, evidence: VoiceCaptureEvidence? = nil, validation: VoiceMediaValidation? = nil) throws {
        var value = try voiceManifest(recordingID)
        try assertVoiceAlive(value)
        guard duration.isFinite, duration > 0,
              let index = value.segments.firstIndex(where: { $0.id == id }) else { throw VoiceError.changedMedia }
        let url = try voiceMediaURL(value, segmentID: id)
        let descriptor = Darwin.open(url.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard descriptor >= 0 else { throw VoiceError.changedMedia }
        defer { Darwin.close(descriptor) }
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_mode & S_IFMT == S_IFREG, fsync(descriptor) == 0 else { throw JournalError.storage }
        try files.synchronizeDirectory(url.deletingLastPathComponent())
        let fingerprint = try fingerprintVoiceFile(url)
        if let validation {
            guard validation.hash == fingerprint.hash, validation.byteCount == fingerprint.size,
                  validation.duration == duration else { throw VoiceError.changedMedia }
        }
        if value.segments[index].finalized {
            guard value.segments[index].hash == fingerprint.hash,
                  value.segments[index].byteCount == fingerprint.size else { throw VoiceError.changedMedia }
            if let evidence {
                let retained = try retainedVoiceCapture(value, segmentID: id, fingerprint: fingerprint, evidence: evidence)
                if let previous = value.segments[index].captureEvidence {
                    guard previous == retained else { throw VoiceError.changedMedia }
                } else {
                    value.segments[index].captureEvidence = retained
                    value.segments[index].captureEvidenceUnknown = nil
                    try writeVoice(value)
                }
            }
            return
        }
        value.segments[index].hash = fingerprint.hash
        value.segments[index].byteCount = fingerprint.size
        value.segments[index].duration = duration
        value.segments[index].finalized = true
        value.segments[index].captureEvidence = try retainedVoiceCapture(value, segmentID: id, fingerprint: fingerprint, evidence: evidence)
        if value.segments[index].captureExpected == true && value.segments[index].captureEvidence == nil {
            value.segments[index].captureEvidenceUnknown = true
        }
        try writeVoice(value)
    }

    public func endRecording(_ id: UUID) throws {
        var value = try voiceManifest(id)
        try assertVoiceAlive(value)
        value.ended = true
        try writeVoice(value)
    }

    public func recoverableVoiceURL(_ recordingID: UUID, segmentID: UUID) throws -> URL {
        let value = try voiceManifest(recordingID)
        try assertVoiceAlive(value)
        let url = try voiceMediaURL(value, segmentID: segmentID)
        _ = try fingerprintVoiceFile(url)
        return url
    }

    public func verifiedVoiceURL(_ recordingID: UUID, segmentID: UUID) throws -> URL {
        let value = try voiceManifest(recordingID)
        try assertVoiceAlive(value)
        guard let segment = value.segments.first(where: { $0.id == segmentID }), segment.finalized else {
            throw VoiceError.changedMedia
        }
        let url = try voiceMediaURL(value, segmentID: segmentID)
        let fingerprint = try fingerprintVoiceFile(url)
        guard fingerprint.hash == segment.hash, fingerprint.size == segment.byteCount else { throw VoiceError.changedMedia }
        return url
    }

    public func voiceReferences(_ id: UUID, allowingUnavailable: Bool = false) throws -> [VoiceMediaReference] {
        let value = try voiceManifest(id)
        try assertVoiceAlive(value)
        let finalized = value.segments.filter(\.finalized)
        if allowingUnavailable { return finalized.compactMap { try? voiceReference(value, segmentID: $0.id) } }
        guard !finalized.isEmpty else { throw VoiceError.changedMedia }
        return try finalized.map { try voiceReference(value, segmentID: $0.id) }
    }

    func voiceReference(_ value: VoiceRecording, segmentID: UUID) throws -> VoiceMediaReference {
        guard let segment = value.segments.first(where: { $0.id == segmentID }), segment.finalized else { throw VoiceError.changedMedia }
        let fingerprint = try fingerprintVoiceFile(voiceMediaURL(value, segmentID: segmentID))
        guard fingerprint.hash == segment.hash, fingerprint.size == segment.byteCount else { throw VoiceError.changedMedia }
        return VoiceMediaReference(segmentID: segment.id, hash: fingerprint.hash, byteCount: fingerprint.size,
                                   relativeLink: "../Attachments/Audio/\(value.id.uuidString)/\(segment.relativePath)")
    }

    public func retainVoiceTranscript(_ recordingID: UUID, proposal: VoiceTranscript) throws {
        var value = try voiceManifest(recordingID)
        try assertVoiceAlive(value); try assertVoiceUninserted(recordingID); try assertNoVoiceInsertion(value.target.day)
        guard proposal.media == (try voiceReferences(recordingID, allowingUnavailable: proposal.inventory != nil)),
              (proposal.skipped ?? []).allSatisfy({ proposal.media.contains($0) && (value.skippedTranscripts ?? []).contains($0) }) else { throw VoiceError.changedMedia }
        if let inventory = proposal.inventory { guard try voiceSourceInventory(recordingID) == inventory else { throw VoiceError.changedMedia } }
        else if value.segments.contains(where: { !$0.finalized }) { throw VoiceError.changedMedia }
        guard !proposal.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw VoiceError.emptyTranscript }
        value = try externalizeVoiceTexts(value)
        if let old = value.transcriptReferences?.first(where: { $0.id == proposal.id }) {
            guard try readVoiceText(value, reference: old).proposal == proposal else { throw JournalError.operationMismatch }; return
        }
        let reference = try persistVoiceText(value, proposal: proposal, piece: nil)
        value.transcriptReferences = (value.transcriptReferences ?? []) + [reference]
        try writeVoice(value)
    }

    public func retainVoiceSegmentTranscript(_ recordingID: UUID, piece: VoiceSegmentTranscript) throws {
        var value = try voiceManifest(recordingID)
        try assertVoiceAlive(value); try assertVoiceUninserted(recordingID); try assertNoVoiceInsertion(value.target.day)
        guard try voiceReference(value, segmentID: piece.media.segmentID) == piece.media else { throw VoiceError.changedMedia }
        guard !piece.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw VoiceError.emptyTranscript }
        value = try externalizeVoiceTexts(value)
        if let old = value.segmentTranscriptReferences?.first(where: { $0.id == piece.media.segmentID }) {
            guard try readVoiceText(value, reference: old).piece == piece else { throw JournalError.operationMismatch }; return
        }
        let reference = try persistVoiceText(value, proposal: nil, piece: piece)
        value.segmentTranscriptReferences = (value.segmentTranscriptReferences ?? []) + [reference]
        try writeVoice(value)
    }

    public func retainVoiceCorrection(_ recordingID: UUID, correction: VoiceCorrection, adoptingSource: Bool = false) throws {
        guard correction.text.utf8.count <= 500000 else { throw VoiceError.limit }
        var value = try voiceManifest(recordingID)
        try assertVoiceUninserted(recordingID); try assertNoVoiceInsertion(value.target.day)
        try assertVoiceCorrectionReadable(value)
        if let reference = ((value.correctionReferences ?? []) + [value.currentCorrectionReference].compactMap { $0 })
            .first(where: { $0.id == correction.id }) {
            guard try readVoiceCorrection(value, reference: reference) == correction else { throw JournalError.operationMismatch }
            try validateVoiceOrigin(value, origin: correction.origin)
            try? retireVoiceDraftObjects(recordingID); return
        }
        try validateVoiceOrigin(value, origin: correction.origin, allowingRetained: !adoptingSource)
        if let previous = value.correctionDraft, previous.id == correction.id {
            guard previous == correction else { throw JournalError.operationMismatch }; return
        }
        value = try externalizeVoiceCorrections(value)
        value.currentCorrectionReference = try persistVoiceCorrection(value, correction: correction, temporary: true)
        value.correctionDraft = nil
        try writeVoice(value)
        try? retireVoiceDraftObjects(recordingID)
    }

    public func checkpointVoiceCorrection(_ id: UUID) throws {
        var value = try voiceManifest(id)
        try assertVoiceCorrectionReadable(value)
        let current = try value.currentCorrectionReference.map { try readVoiceCorrection(value, reference: $0) } ?? value.correctionDraft
        guard let correction = current else { return }
        let reference = try persistVoiceCorrection(value, correction: correction)
        if !(value.correctionReferences ?? []).contains(where: { $0.id == correction.id }) {
            value.correctionReferences = (value.correctionReferences ?? []) + [reference]
        }
        value.correctionDraft = nil; value.currentCorrectionReference = reference
        try writeVoice(value)
        try? retireVoiceDraftObjects(id)
    }

    /// A user-requested refresh changes only the insertion source, never the capture destination.
    public func refreshVoiceSource(_ id: UUID, draft: JournalDraft) throws {
        var value = try voiceManifest(id)
        try assertVoiceAlive(value)
        try assertVoiceUninserted(id)
        let target = try voiceTarget(draft)
        guard target.namespace == value.target.namespace, target.day == value.target.day,
              target.section.utf8.elementsEqual(value.target.section.utf8) else { throw VoiceError.invalidTarget }
        try assertNoVoiceInsertion(draft.day)
        value.insertionTarget = target
        try writeVoice(value)
    }

    public func deleteVoiceAudio(_ id: UUID) throws {
        var value = try voiceManifest(id)
        if !value.deletionRequested {
            value.deletionRequested = true
            value.ended = true
            try writeVoice(value)
        }
        // The durable tombstone is never cleared, including when removal or a late callback fails.
        try derivedVoiceCache.revoke(id)
        for segment in value.segments {
            let url = try voiceMediaURL(value, segmentID: segment.id)
            try files.failure(.audio, .beforeRemove)
            var info = stat()
            if lstat(url.path, &info) == 0 {
                guard (info.st_mode & S_IFMT) == S_IFREG, unlink(url.path) == 0 else { throw JournalError.storage }
                try files.synchronizeDirectory(url.deletingLastPathComponent())
            } else if errno != ENOENT { throw JournalError.storage }
            try files.failure(.audio, .afterRemove)
        }
    }

    /// Unfinished originals may be exported for recovery, without claiming they are playable.
    public func exportVoiceAudio(_ id: UUID, segmentID: UUID) throws -> Data {
        let value = try voiceManifest(id)
        try assertVoiceAlive(value)
        guard let segment = value.segments.first(where: { $0.id == segmentID }) else { throw VoiceError.missing }
        let url = try voiceMediaURL(value, segmentID: segmentID)
        let before = try fingerprintVoiceFile(url, allowEmpty: !segment.finalized)
        if segment.finalized {
            guard before.hash == segment.hash, before.size == segment.byteCount else { throw VoiceError.changedMedia }
        }
        let data = try readOriginalVoiceFile(url)
        guard SourceHash.digest(data) == before.hash, Int64(data.count) == before.size else { throw VoiceError.changedMedia }
        return data
    }

    public func voiceAudioRemovalComplete(_ id: UUID) throws -> Bool {
        let value = try voiceManifest(id)
        guard value.deletionRequested else { return false }
        for segment in value.segments {
            let url = try voiceMediaURL(value, segmentID: segment.id)
            var info = stat()
            if lstat(url.path, &info) == 0 { return false }
            guard errno == ENOENT else { throw JournalError.storage }
        }
        return try derivedVoiceCache.removalComplete(id)
    }

    private func readOriginalVoiceFile(_ url: URL) throws -> Data {
        let descriptor = Darwin.open(url.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard descriptor >= 0 else { throw VoiceError.changedMedia }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? handle.close() }
        var info = stat()
        guard fstat(descriptor, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
              info.st_size <= 32 * 1024 * 1024 else { throw VoiceError.changedMedia }
        var data = Data()
        while let block = try handle.read(upToCount: 65536), !block.isEmpty {
            guard data.count + block.count <= 32 * 1024 * 1024 else { throw VoiceError.limit }
            data.append(block)
        }
        return data
    }

    public func importVoiceAudio(_ id: UUID, segmentID: UUID, data: Data) throws -> URL {
        guard data.count <= 32 * 1024 * 1024 else { throw VoiceError.limit }
        let value = try voiceManifest(id)
        try assertVoiceAlive(value)
        try assertVoiceUninserted(id)
        let url = try voiceMediaURL(value, segmentID: segmentID)
        // Import never replaces a reserved original, even after an uncertain write.
        if let existing = try files.read(url, maximumBytes: 32 * 1024 * 1024) {
            guard existing == data else { throw VoiceError.changedMedia }; return url
        }
        try files.write(data, to: url, purpose: .audio)
        return url
    }

    func assertVoiceAlive(_ value: VoiceRecording) throws {
        guard !value.deletionRequested else { throw VoiceError.deleted }
    }

    func readVoiceIfPresent(_ id: UUID) throws -> VoiceRecording? {
        guard let data = try files.read(voiceURL(id), maximumBytes: 4 * 1024 * 1024) else { return nil }
        return try hydrateVoiceTexts(hydrateVoiceCorrections(decodeVoice(data, id: id)))
    }

    public func voiceManifest(_ id: UUID) throws -> VoiceRecording {
        guard let data = try files.read(voiceURL(id), maximumBytes: 4 * 1024 * 1024) else { throw VoiceError.missing }
        return try decodeVoice(data, id: id)
    }

    func decodeVoice(_ data: Data, id: UUID) throws -> VoiceRecording {
        guard let value = try? decoder.decode(VoiceRecording.self, from: data), value.id == id,
              value.target.namespace == (try voiceNamespace()), value.segments.count <= 1000,
              Set(value.segments.map(\.id)).count == value.segments.count,
              value.segments.allSatisfy({ segment in
                  ["m4a", "wav"].contains(segment.fileExtension) && (!segment.finalized ||
                    ((segment.duration ?? 0) > 0 && (segment.duration?.isFinite == true) &&
                     (segment.byteCount ?? 0) > 0 && (segment.byteCount ?? 0) <= 32 * 1024 * 1024 &&
                     segment.hash?.count == 64))
              }) else { throw VoiceError.missing }
        guard (value.correctionReferences?.count ?? 0) <= 10000, (value.transcriptReferences?.count ?? 0) <= 10000,
              (value.segmentTranscriptReferences?.count ?? 0) <= 1000 else { throw VoiceError.limit }
        let pieces = value.segmentTranscripts ?? []
        guard Set(pieces.map { $0.media.segmentID }).count == pieces.count,
              pieces.count <= value.segments.count, pieces.allSatisfy({ piece in
                  !piece.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
                  value.segments.contains { $0.id == piece.media.segmentID && $0.hash == piece.media.hash && $0.byteCount == piece.media.byteCount }
              }) else { throw VoiceError.missing }
        let skipped = value.skippedTranscripts ?? []
        guard Set(skipped.map(\.segmentID)).count == skipped.count,
              skipped.allSatisfy({ ref in
                  value.segments.contains { $0.id == ref.segmentID && $0.finalized && $0.hash == ref.hash && $0.byteCount == ref.byteCount } &&
                  !pieces.contains { $0.media.segmentID == ref.segmentID }
              }) else { throw VoiceError.missing }
        return value
    }

    func writeVoice(_ value: VoiceRecording) throws {
        let compact = try externalizeVoiceTexts(externalizeVoiceCorrections(value))
        let data = try encoder.encode(compact)
        guard data.count <= 4 * 1024 * 1024 else { throw VoiceError.limit }
        try files.write(data, to: voiceURL(value.id), purpose: .voice)
    }

    func voiceURL(_ id: UUID) -> URL { state.appendingPathComponent("voice/\(id.uuidString).json") }

    func voiceMediaURL(_ value: VoiceRecording, segmentID: UUID) throws -> URL {
        guard let segment = value.segments.first(where: { $0.id == segmentID }) else { throw VoiceError.missing }
        var root = journals.deletingLastPathComponent()
        for component in ["Attachments", "Audio", value.id.uuidString] {
            root.appendPathComponent(component)
            try files.createDirectory(root)
        }
        return root.appendingPathComponent(segment.relativePath)
    }

    func fingerprintVoiceFile(_ url: URL, allowEmpty: Bool = false) throws -> (hash: String, size: Int64) {
        try files.failure(.audio, .beforeRead)
        let descriptor = Darwin.open(url.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard descriptor >= 0 else { throw VoiceError.changedMedia }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? handle.close() }
        var before = stat()
        guard fstat(descriptor, &before) == 0, (before.st_mode & S_IFMT) == S_IFREG,
              (allowEmpty || before.st_size > 0), before.st_size <= 32 * 1024 * 1024 else { throw VoiceError.changedMedia }
        var hash = SHA256()
        var total: Int64 = 0
        while let block = try handle.read(upToCount: 65536), !block.isEmpty {
            total += Int64(block.count)
            guard total <= 32 * 1024 * 1024 else { throw VoiceError.limit }
            hash.update(data: block)
        }
        var after = stat(), named = stat()
        guard fstat(descriptor, &after) == 0, lstat(url.path, &named) == 0,
              named.st_ino == before.st_ino, named.st_dev == before.st_dev,
              total == before.st_size, after.st_size == before.st_size,
              after.st_ctimespec.tv_sec == before.st_ctimespec.tv_sec, after.st_ctimespec.tv_nsec == before.st_ctimespec.tv_nsec,
              after.st_mtimespec.tv_sec == before.st_mtimespec.tv_sec,
              after.st_mtimespec.tv_nsec == before.st_mtimespec.tv_nsec else { throw VoiceError.changedMedia }
        return (hash.finalize().map { String(format: "%02x", $0) }.joined(), total)
    }
}

extension LocalJournalStore {
    public func verifiedVoicePlayback(_ id: UUID, segmentID: UUID) throws -> Data {
        let value = try voiceManifest(id)
        try assertVoiceAlive(value)
        guard value.segments.contains(where: { $0.id == segmentID && $0.finalized }) else { throw VoiceError.changedMedia }
        return try exportVoiceAudio(id, segmentID: segmentID)
    }

    public func voiceSourceInventory(_ id: UUID) throws -> [VoiceSourcePiece] {
        let value = try voiceManifest(id)
        try assertVoiceAlive(value)
        return try value.segments.map { segment in
            let link = "../Attachments/Audio/\(id.uuidString)/\(segment.relativePath)"
            if segment.finalized {
                let observed = try? fingerprintVoiceFile(voiceMediaURL(value, segmentID: segment.id))
                let available = observed?.hash == segment.hash && observed?.size == segment.byteCount
                var piece = VoiceSourcePiece(skipped: value.skippedTranscripts?.contains(where: { $0.segmentID == segment.id }) == true ? true : nil,
                    segmentID: segment.id, relativeLink: link, finalized: true, hash: segment.hash, byteCount: segment.byteCount)
                if !available {
                    piece.unavailable = true; piece.observedHash = observed?.hash; piece.observedByteCount = observed?.size
                    if observed == nil { piece.unavailableState = voiceUnavailableState(try voiceMediaURL(value, segmentID: segment.id)) }
                }
                piece.integrity = segment.captureEvidence; piece.integrityUnknown = segment.captureEvidenceUnknown
                return piece
            }
            let url = try voiceMediaURL(value, segmentID: segment.id)
            let fingerprint = try? fingerprintVoiceFile(url, allowEmpty: true)
            guard let fingerprint else {
                return VoiceSourcePiece(segmentID: segment.id, relativeLink: link, finalized: false,
                    hash: segment.hash, byteCount: segment.byteCount, unavailable: true,
                    unavailableState: voiceUnavailableState(url), integrity: segment.captureEvidence,
                    integrityUnknown: segment.captureExpected == true && segment.captureEvidence == nil ? true : segment.captureEvidenceUnknown)
            }
            var piece = VoiceSourcePiece(segmentID: segment.id, relativeLink: link, finalized: false, hash: fingerprint.hash, byteCount: fingerprint.size)
            if segment.captureExpected == true {
                piece.integrityUnknown = true
                let recordURL = state.appendingPathComponent("voice-capture/\(id.uuidString)/\(segment.id.uuidString).json")
                if let bytes = try files.read(recordURL, maximumBytes: 4096),
                   let retained = try? decoder.decode(VoiceCaptureRecord.self, from: bytes), retained.recordingID == id,
                   retained.segmentID == segment.id, retained.hash == fingerprint.hash, retained.byteCount == fingerprint.size {
                    piece.integrity = retained.evidence; piece.integrityUnknown = nil
                }
            }
            return piece
        }
    }

    private func voiceUnavailableState(_ url: URL) -> String {
        var info = stat()
        if lstat(url.path, &info) != 0 { return errno == ENOENT ? "missing" : "unreadable" }
        return "unreadable:\(info.st_dev):\(info.st_mode):\(info.st_ino):\(info.st_size):\(info.st_mtimespec.tv_sec):\(info.st_mtimespec.tv_nsec):\(info.st_ctimespec.tv_sec):\(info.st_ctimespec.tv_nsec)"
    }

    private func retainedVoiceCapture(_ value: VoiceRecording, segmentID: UUID,
                                     fingerprint: (hash: String, size: Int64), evidence: VoiceCaptureEvidence?) throws -> VoiceCaptureEvidence? {
        let folder = state.appendingPathComponent("voice-capture/\(value.id.uuidString)")
        try files.createDirectory(folder)
        let url = folder.appendingPathComponent("\(segmentID.uuidString).json")
        if let retained = voiceCaptureCache[segmentID] {
            guard retained.recordingID == value.id, retained.hash == fingerprint.hash,
                  retained.byteCount == fingerprint.size, evidence == nil || retained.evidence == evidence else { throw VoiceError.changedMedia }
        }
        let evidence = evidence ?? voiceCaptureCache[segmentID]?.evidence
        if let evidence {
            guard evidence.firstFrame >= 0, evidence.frameCount >= 0, evidence.sampleRate.isFinite, evidence.sampleRate > 0,
                  (evidence.rejectedFrames ?? 0) >= 0, (evidence.unassignedFrames ?? 0) >= 0 else { throw VoiceError.changedMedia }
            let record = VoiceCaptureRecord(recordingID: value.id, segmentID: segmentID, hash: fingerprint.hash,
                                            byteCount: fingerprint.size, evidence: evidence)
            if voiceCaptureCache.count >= 1000 && voiceCaptureCache[segmentID] == nil { voiceCaptureCache.removeAll() }
            voiceCaptureCache[segmentID] = record
            if try files.read(url, maximumBytes: 4096) == nil { try files.write(encoder.encode(record), to: url, purpose: .voiceCapture) }
        }
        guard let bytes = try files.read(url, maximumBytes: 4096) else { return nil }
        guard let record = try? decoder.decode(VoiceCaptureRecord.self, from: bytes), record.recordingID == value.id,
              record.segmentID == segmentID, record.hash == fingerprint.hash, record.byteCount == fingerprint.size,
              evidence == nil || record.evidence == evidence else { throw VoiceError.changedMedia }
        guard record.evidence.firstFrame >= 0, record.evidence.frameCount >= 0,
              record.evidence.sampleRate.isFinite, record.evidence.sampleRate > 0,
              (record.evidence.rejectedFrames ?? 0) >= 0, (record.evidence.unassignedFrames ?? 0) >= 0 else { throw VoiceError.changedMedia }
        return record.evidence
    }
}

extension LocalJournalStore {
    public func voiceRecognitionSource(_ id: UUID, media: VoiceMediaReference) throws -> VoiceRecognitionSource {
        let value = try voiceManifest(id)
        try assertVoiceAlive(value)
        guard try voiceReference(value, segmentID: media.segmentID) == media else { throw VoiceError.changedMedia }
        return try VoiceRecognitionSource(media: media, data: exportVoiceAudio(id, segmentID: media.segmentID))
    }
}

extension LocalJournalStore {
    func validateVoiceOrigin(_ value: VoiceRecording, origin: VoiceCorrectionOrigin?, allowingRetained: Bool = true) throws {
        guard let origin else { return }
        guard origin.recordingID == value.id, origin.target == value.target else { throw VoiceError.invalidTarget }
        // A verified current correction hashes its complete immutable origin independently of history.
        // Manual edits keep that provenance even after the separate original transcript is damaged.
        if allowingRetained, let reference = value.currentCorrectionReference,
           try readVoiceCorrection(value, reference: reference).origin == origin { return }
        let expected: VoiceCorrectionOrigin
        if origin.segment == true {
            guard let piece = try voiceSegmentTranscript(value.id, segmentID: origin.versionID) else { throw VoiceError.missing }
            expected = try voicePieceCorrectionOrigin(value.id, piece: piece)
        } else {
            expected = try VoiceCorrectionOrigin(recordingID: value.id, target: value.target,
                proposal: voiceTranscript(value.id, version: origin.versionID))
        }
        guard expected == origin else { throw JournalError.operationMismatch }
    }

    public func voiceCorrectionOriginNotice(_ id: UUID, origin: VoiceCorrectionOrigin?) throws -> String? {
        guard let origin else { return nil }
        let value = try voiceManifest(id)
        try validateVoiceOrigin(value, origin: origin)
        let readable = (try? validateVoiceOrigin(value, origin: origin, allowingRetained: false)) != nil
        return origin.notice + (readable ? "" : "\n" + VoiceCorrectionOrigin.unavailableNotice)
    }
}
