import Foundation
import CryptoKit

extension LocalJournalStore {
    /// Compatibility convenience. Each batch yields the shared actor; the UI uses the explicit bounded API.
    public func voiceLibraryPage(after cursor: String? = nil) async throws -> VoiceLibraryPage {
        var result = try voiceLibraryBatch(after: cursor)
        while result.rebuildingCount > 0 {
            try Task.checkCancellation(); await Task.yield()
            result = try voiceLibraryBatch(after: cursor)
        }
        return result
    }

    /// At most 32 manifests / four MiB are decoded per actor call. Partial indexes are explicit.
    public func voiceLibraryBatch(after cursor: String? = nil) throws -> VoiceLibraryPage {
        let urls = try files.list(state.appendingPathComponent("voice")).filter { $0.pathExtension == "json" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        guard urls.count <= 10000 else { throw VoiceError.limit }
        try loadVoiceSummaryIndex()
        let names = Set(urls.map(\.lastPathComponent))
        voiceSummaryCache = voiceSummaryCache.filter { names.contains($0.key) }
        let versions = urls.map { ($0, try? VoiceFileVersion($0)) }
        let snapshot = SourceHash.digest(Data(versions.map { "\($0.0.lastPathComponent):\(String(describing: $0.1))" }.joined(separator: "\n").utf8))
        var offset = 0
        if let cursor {
            let parts = cursor.split(separator: ":")
            guard parts.count == 2, parts[0] == snapshot, let number = Int(parts[1]), number >= 0, number <= urls.count else { throw JournalError.changedSource }
            offset = number
        }
        let (bytesRead, countRead, remaining) = try scanVoiceSummaryBatch(versions)
        let index = VoiceSummaryIndex(namespace: try voiceNamespace(), entries: voiceSummaryCache)
        let encoded = try encoder.encode(index)
        guard encoded.count <= 16 * 1024 * 1024 else { throw VoiceError.limit }
        try files.write(encoded, to: state.appendingPathComponent("voice-summary-index.json"), purpose: .voiceSummary)
        let entries = voiceSummaryCache.sorted { a, b in
            guard let left = a.value.summary else { return b.value.summary != nil || a.key < b.key }
            guard let right = b.value.summary else { return false }
            return left.createdAt == right.createdAt ? left.id.uuidString < right.id.uuidString : left.createdAt > right.createdAt
        }
        if remaining > 0 {
            return VoiceLibraryPage(recordings: Array(entries.compactMap { $0.value.summary }.prefix(20)),
                unreadableCount: entries.filter { $0.value.summary == nil && !$0.value.limited }.count,
                limitedCount: entries.filter { $0.value.limited }.count, nextCursor: nil,
                rebuildingCount: remaining, scannedManifestBytes: bytesRead, scannedManifestCount: countRead)
        }
        let end = min(offset + 20, entries.count), page = entries[offset..<end]
        return VoiceLibraryPage(recordings: page.compactMap { $0.value.summary },
            unreadableCount: page.filter { $0.value.summary == nil && !$0.value.limited }.count,
            limitedCount: page.filter { $0.value.limited }.count,
            nextCursor: end < entries.count ? "\(snapshot):\(end)" : nil,
            scannedManifestBytes: bytesRead, scannedManifestCount: countRead)
    }

    private func scanVoiceSummaryBatch(_ versions: [(URL, VoiceFileVersion?)]) throws -> (Int, Int, Int) {
        var bytesRead = 0, countRead = 0, remaining = 0
        for (url, version) in versions {
            let name = url.lastPathComponent
            if let cached = voiceSummaryCache[name], cached.version == version { continue }
            voiceSummaryCache[name] = nil
            let size = Int(version?.size ?? 0)
            if version != nil && size <= 4 * 1024 * 1024 && (countRead >= 32 || bytesRead + size > 4 * 1024 * 1024) {
                remaining += 1; continue
            }
            do {
                guard let version else { throw VoiceError.missing }
                guard size <= 4 * 1024 * 1024 else { throw JournalError.scanLimit }
                countRead += 1; bytesRead += size
                try files.failure(.voiceSummary, .beforeRead)
                guard let id = UUID(uuidString: url.deletingPathExtension().lastPathComponent),
                      let data = try files.read(url, maximumBytes: 4 * 1024 * 1024) else { throw VoiceError.missing }
                let value = try decodeVoice(data, id: id)
                guard try VoiceFileVersion(url) == version else { throw JournalError.changedSource }
                voiceSummaryCache[name] = VoiceSummaryCacheEntry(version: version, summary: VoiceRecordingSummary(value))
            } catch {
                // Injected/transient storage errors are retryable; malformed entries remain isolated.
                if (error as? JournalError) == .storage { throw error }
                voiceSummaryCache[name] = VoiceSummaryCacheEntry(version: version, summary: nil, limited: (error as? JournalError) == .scanLimit)
            }
        }
        return (bytesRead, countRead, remaining)
    }

    private func loadVoiceSummaryIndex() throws {
        guard !voiceSummaryLoaded else { return }
        voiceSummaryLoaded = true
        if let bytes = try? files.read(state.appendingPathComponent("voice-summary-index.json"), maximumBytes: 16 * 1024 * 1024),
           let index = try? decoder.decode(VoiceSummaryIndex.self, from: bytes), index.namespace == (try voiceNamespace()), index.entries.count <= 10000 {
            voiceSummaryCache = index.entries.filter { name, entry in
                guard let id = UUID(uuidString: String(name.dropLast(5))), name.hasSuffix(".json") else { return false }
                return entry.summary.map { $0.id == id && $0.sectionLabel.count <= 128 && $0.duration.isFinite && $0.duration >= 0 && $0.segmentCount <= 1000 } ?? true
            }
        }
    }

    public func prepareRecording(id: UUID, target: VoiceTarget) throws -> VoiceRecording {
        let hintURL = try voicePreparationURL(target)
        if let bytes = try? files.read(hintURL, maximumBytes: 16384),
           let hint = try? decoder.decode(VoicePreparationHint.self, from: bytes), hint.targetHash == SourceHash.digest(try encoder.encode(target)),
           (try? VoiceFileVersion(voiceURL(hint.recordingID))) == hint.manifestVersion,
           hint.abandonmentInventory == (try voiceAbandonmentInventoryVersion()),
           let value = try? voiceManifest(hint.recordingID), value.target == target, voiceIsUnused(value),
           try voiceInsertion(for: value.id) == nil, try !voiceHasRetainedOperation(value.id, day: target.day) {
            return value
        }
        let value = try createRecording(id: id, target: target)
        try retainVoicePreparationHint(value)
        return value
    }

    func voiceIsUnused(_ value: VoiceRecording) -> Bool {
        value.segments.isEmpty && value.transcripts.isEmpty && value.corrections.isEmpty && value.correctionDraft == nil &&
            value.currentCorrectionReference == nil && (value.correctionReferences ?? []).isEmpty &&
            (value.transcriptReferences ?? []).isEmpty && (value.segmentTranscriptReferences ?? []).isEmpty &&
            (value.segmentTranscripts ?? []).isEmpty && (value.skippedTranscripts ?? []).isEmpty &&
            !value.ended && !value.deletionRequested && value.insertionTarget == nil
    }
    func retainVoicePreparationHint(_ value: VoiceRecording) throws {
        guard voiceIsUnused(value) else { return }
        let hint = VoicePreparationHint(targetHash: SourceHash.digest(try encoder.encode(value.target)), recordingID: value.id,
            manifestVersion: try VoiceFileVersion(voiceURL(value.id)), abandonmentInventory: try voiceAbandonmentInventoryVersion())
        try files.write(encoder.encode(hint), to: voicePreparationURL(value.target), purpose: .voiceSummary)
    }
    private func voicePreparationURL(_ target: VoiceTarget) throws -> URL {
        let folder = state.appendingPathComponent("voice-preparations")
        try files.createDirectory(folder)
        return folder.appendingPathComponent(SourceHash.digest(try encoder.encode(target)) + ".json")
    }

    private func voiceHasRetainedOperation(_ id: UUID, day: JournalDay) throws -> Bool {
        if try voiceIntent(day)?.candidate.recordingID == id { return true }
        for name in ["voice-corrections", "voice-correction-drafts", "voice-transcripts", "voice-capture"] {
            let retained = state.appendingPathComponent("\(name)/\(id.uuidString)")
            if FileManager.default.fileExists(atPath: retained.path), !(try files.list(retained)).isEmpty { return true }
        }
        // Reuse also requires the exact unused manifest and unchanged abandonment inventory captured
        // at its initial creation. A changed inventory invalidates that hint before this check.
        return false
    }

    private func voiceAbandonmentInventoryVersion() throws -> String {
        let folder = state.appendingPathComponent("voice-abandonments")
        try files.createDirectory(folder)
        let urls = try files.list(folder)
        guard urls.count <= 10000 else { throw VoiceError.limit }
        let identities = try urls.sorted { $0.lastPathComponent < $1.lastPathComponent }.map {
            "\($0.lastPathComponent):\(try VoiceFileVersion($0))"
        }
        return SourceHash.digest(Data(identities.joined(separator: "\n").utf8))
    }

    /// Compatibility wrapper; interactive history uses one bounded repair batch.
    public func voiceCorrectionHistory(_ id: UUID, offset: Int = 0) async throws -> VoiceCorrectionPage {
        var page = try voiceCorrectionHistoryBatch(id, offset: offset)
        while page.rebuildingCount > 0 {
            try Task.checkCancellation(); await Task.yield()
            page = try voiceCorrectionHistoryBatch(id, offset: offset)
        }
        return page
    }

    public func voiceCorrectionHistoryBatch(_ id: UUID, offset: Int = 0, snapshot expectedSnapshot: String? = nil) throws -> VoiceCorrectionPage {
        let value = try externalizeVoiceCorrections(voiceManifest(id))
        let folder = state.appendingPathComponent("voice-corrections/\(id.uuidString)")
        try files.createDirectory(folder)
        let urls = try files.list(folder).filter { $0.pathExtension == "json" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
        guard urls.count <= 10000 else { throw VoiceError.limit }
        let cacheFolder = state.appendingPathComponent("voice-history-index")
        try files.createDirectory(cacheFolder)
        let cacheURL = cacheFolder.appendingPathComponent("\(id.uuidString).json")
        if voiceCorrectionIndex?.recordingID != id {
            voiceCorrectionIndex = (try? files.read(cacheURL, maximumBytes: 8 * 1024 * 1024)).flatMap {
                try? decoder.decode(VoiceLegacyCorrectionIndex.self, from: $0)
            }
            if voiceCorrectionIndex?.recordingID != id || voiceCorrectionIndex?.target != value.target || (voiceCorrectionIndex?.entries.count ?? 0) > 10000 {
                voiceCorrectionIndex = VoiceLegacyCorrectionIndex(recordingID: id, target: value.target)
            }
        }
        var index = voiceCorrectionIndex!
        index.entries = index.entries.filter { name, entry in
            guard let id = UUID(uuidString: String(name.dropLast(5))), name.hasSuffix(".json") else { return false }
            return entry.reference.map { $0.id == id && $0.hash.count == 64 && $0.byteCount >= 0 && $0.byteCount <= 500000 && $0.temporaryDraft != true } ?? true
        }
        let names = Set(urls.map(\.lastPathComponent))
        index.entries = index.entries.filter { names.contains($0.key) }
        var digest = SHA256()
        digest.update(data: try encoder.encode(value.correctionReferences))
        for url in urls { digest.update(data: Data("\(url.lastPathComponent):\(String(describing: try? VoiceFileVersion(url)))\n".utf8)) }
        let snapshot = digest.finalize().map { String(format: "%02x", $0) }.joined()
        if let expectedSnapshot { guard expectedSnapshot == snapshot else { throw JournalError.changedSource } }
        let registered = value.correctionReferences ?? []
        let retained = Set(registered.map(\.id) + [value.currentCorrectionReference?.id].compactMap { $0 })
        var bytesRead = 0, count = 0, remaining = 0
        for url in urls {
            guard let versionID = UUID(uuidString: url.deletingPathExtension().lastPathComponent), !retained.contains(versionID) else { continue }
            let version = try? VoiceFileVersion(url)
            if index.entries[url.lastPathComponent]?.version == version, index.entries[url.lastPathComponent] != nil { continue }
            index.entries[url.lastPathComponent] = nil
            let size = version?.size ?? 0
            if version != nil, size <= 3100000, (count >= 32 || bytesRead + Int(size) > 4 * 1024 * 1024) { remaining += 1; continue }
            do {
                guard let version, size <= 3100000 else { throw VoiceError.missing }
                count += 1; bytesRead += Int(size)
                try files.failure(.voiceHistory, .beforeRead)
                let reference = try discoverVoiceCorrection(value, version: versionID)
                guard try VoiceFileVersion(url) == version else { throw JournalError.changedSource }
                index.entries[url.lastPathComponent] = VoiceLegacyCorrectionEntry(version: version, reference: reference)
            } catch {
                if (error as? JournalError) == .storage {
                    voiceCorrectionIndex = index
                    throw error
                }
                index.entries[url.lastPathComponent] = VoiceLegacyCorrectionEntry(version: version, reference: nil)
            }
        }
        let encoded = try encoder.encode(index)
        guard encoded.count <= 8 * 1024 * 1024 else { throw VoiceError.limit }
        try files.write(encoded, to: cacheURL, purpose: .voiceHistory)
        voiceCorrectionIndex = index
        let discovered = index.entries.sorted { $0.key < $1.key }.compactMap { entry -> VoiceCorrectionReference? in
            guard let ref = entry.value.reference, !retained.contains(ref.id) else { return nil }; return ref
        }
        let references = registered + discovered
        guard references.count <= 10000, offset >= 0, offset <= references.count || remaining > 0 else { throw VoiceError.missing }
        let start = min(offset, references.count), end = min(start + 20, references.count)
        return VoiceCorrectionPage(references: Array(references[start..<end]),
            nextOffset: remaining == 0 && end < references.count ? end : nil, rebuildingCount: remaining,
            unavailableCount: index.entries.values.filter { $0.reference == nil }.count, scannedBytes: bytesRead, scannedCount: count, snapshot: snapshot)
    }

    public func voiceCorrection(_ id: UUID, version: UUID) throws -> VoiceCorrection {
        let value = try voiceManifest(id)
        if let inline = value.corrections.first(where: { $0.id == version }) { return inline }
        let references = (value.correctionReferences ?? []) + [value.currentCorrectionReference].compactMap { $0 }
        let historical = (value.correctionReferences ?? []).filter { $0.id == version }
        guard historical.count <= 1 else { throw VoiceError.missing }
        let matches = references.filter { $0.id == version }
        let reference = try matches.first ?? discoverVoiceCorrection(value, version: version)
        guard matches.allSatisfy({ $0 == reference }) else { throw VoiceError.missing }
        return try readVoiceCorrection(value, reference: reference)
    }

    private func discoverVoiceCorrection(_ value: VoiceRecording, version: UUID) throws -> VoiceCorrectionReference {
        let url = try voiceCorrectionURL(value.id, version: version)
        guard let bytes = try files.read(url, maximumBytes: 3100000),
              let record = try? decoder.decode(VoiceCorrectionRecord.self, from: bytes),
              record.recordingID == value.id, record.target == value.target, record.correction.id == version,
              record.correction.text.utf8.count <= 500000 else { throw VoiceError.missing }
        return VoiceCorrectionReference(id: version, hash: try voiceCorrectionHash(record.correction),
            byteCount: record.correction.text.utf8.count)
    }

    func externalizeVoiceCorrections(_ value: VoiceRecording) throws -> VoiceRecording {
        var compact = value
        var references = value.correctionReferences ?? []
        for correction in value.corrections where !references.contains(where: { $0.id == correction.id }) {
            references.append(try persistVoiceCorrection(value, correction: correction))
        }
        guard references.count <= 10000 else { throw VoiceError.limit }
        if !references.isEmpty { compact.correctionReferences = references }
        if let current = value.correctionDraft {
            if compact.currentCorrectionReference?.id != current.id {
                compact.currentCorrectionReference = try persistVoiceCorrection(value, correction: current)
            }
        } else if compact.currentCorrectionReference == nil { compact.currentCorrectionReference = references.last }
        compact.corrections = []; compact.correctionDraft = nil
        compact.currentCorrectionUnavailable = nil; compact.unavailableHistory = nil
        return compact
    }

    func hydrateVoiceCorrections(_ value: VoiceRecording) throws -> VoiceRecording {
        var result = value
        let references = value.correctionReferences ?? []
        guard references.count <= 10000 else { throw VoiceError.limit }
        let groups = Dictionary(grouping: references, by: \.id)
        let invalid = Set(references.filter { groups[$0.id]!.count != 1 || $0.byteCount < 0 || $0.byteCount > 500000 || $0.hash.count != 64 }.map(\.id))
        result.unavailableHistory = Array(invalid).sorted { $0.uuidString < $1.uuidString }
        if value.correctionReferences != nil && references.filter({ !invalid.contains($0.id) }).reduce(0, { $0 + $1.byteCount }) <= 1000000 {
            result.corrections = []
            for reference in references where !invalid.contains(reference.id) {
                do { result.corrections.append(try readVoiceCorrection(value, reference: reference)) }
                catch { result.unavailableHistory = (result.unavailableHistory ?? []) + [reference.id] }
            }
        }
        if let current = value.currentCorrectionReference {
            do {
                try assertVoiceCorrectionReadable(value)
                result.correctionDraft = try readVoiceCorrection(value, reference: current)
            }
            catch { result.currentCorrectionUnavailable = true }
        }
        return result
    }

    func assertVoiceCorrectionReadable(_ value: VoiceRecording) throws {
        if let reference = value.currentCorrectionReference {
            let matches = (value.correctionReferences ?? []).filter { $0.id == reference.id }
            guard matches.count <= 1, matches.allSatisfy({ $0 == reference }) else { throw VoiceError.missing }
            _ = try readVoiceCorrection(value, reference: reference)
        }
        guard value.currentCorrectionUnavailable != true else { throw VoiceError.missing }
    }

    func persistVoiceCorrection(_ value: VoiceRecording, correction: VoiceCorrection, temporary: Bool = false) throws -> VoiceCorrectionReference {
        guard correction.text.utf8.count <= 500000 else { throw VoiceError.limit }
        var reference = VoiceCorrectionReference(id: correction.id, hash: try voiceCorrectionHash(correction),
                                                  byteCount: correction.text.utf8.count)
        if temporary { reference.temporaryDraft = true }
        let url = try voiceCorrectionURL(value.id, version: correction.id, temporary: temporary)
        let record = VoiceCorrectionRecord(recordingID: value.id, target: value.target, correction: correction)
        let bytes = try encoder.encode(record)
        guard bytes.count <= 3100000 else { throw VoiceError.limit }
        if try files.read(url, maximumBytes: 3100000) == nil {
            try files.write(bytes, to: url, purpose: .voiceCorrection)
        }
        guard try readVoiceCorrection(value, reference: reference) == correction else { throw JournalError.operationMismatch }
        return reference
    }

    func readVoiceCorrection(_ value: VoiceRecording, reference: VoiceCorrectionReference) throws -> VoiceCorrection {
        guard reference.byteCount >= 0, reference.byteCount <= 500000, reference.hash.count == 64 else { throw VoiceError.missing }
        let url = try voiceCorrectionURL(value.id, version: reference.id, temporary: reference.temporaryDraft == true)
        guard let data = try files.read(url, maximumBytes: 3100000),
              let record = try? decoder.decode(VoiceCorrectionRecord.self, from: data), record.recordingID == value.id,
              record.target == value.target, record.correction.id == reference.id,
              record.correction.text.utf8.count == reference.byteCount,
              try voiceCorrectionHash(record.correction) == reference.hash else { throw VoiceError.missing }
        return record.correction
    }

    private func voiceCorrectionHash(_ correction: VoiceCorrection) throws -> String {
        if correction.origin == nil { return SourceHash.digest(Data(correction.text.utf8)) }
        return try VoiceCorrectionOrigin.digest(correction)
    }

    func voiceCorrectionURL(_ id: UUID, version: UUID, temporary: Bool = false) throws -> URL {
        let kind = temporary ? "voice-correction-drafts" : "voice-corrections"
        let folder = state.appendingPathComponent("\(kind)/\(id.uuidString)")
        try files.createDirectory(folder)
        return folder.appendingPathComponent("\(version.uuidString).json")
    }

    public func skipVoiceTranscript(_ id: UUID, media: VoiceMediaReference) throws {
        try Task.checkCancellation()
        var value = try voiceManifest(id)
        try assertVoiceAlive(value); try assertVoiceUninserted(id); try assertNoVoiceInsertion(value.target.day)
        guard try voiceReference(value, segmentID: media.segmentID) == media else { throw VoiceError.changedMedia }
        guard try voiceSegmentTranscript(id, segmentID: media.segmentID) == nil else { throw JournalError.operationMismatch }
        if !(value.skippedTranscripts ?? []).contains(media) {
            try Task.checkCancellation()
            value.skippedTranscripts = (value.skippedTranscripts ?? []) + [media]
            try writeVoice(value)
        }
    }
}

extension LocalJournalStore {
    /// Retire only objects in the new temporary-draft layout after verifying their durable replacement.
    func retireVoiceDraftObjects(_ id: UUID) throws {
        let value = try voiceManifest(id)
        if try voiceIntent(value.target.day) != nil { return }
        var retained = Set((value.correctionReferences ?? []).filter { $0.temporaryDraft == true }.map(\.id))
        if let current = value.currentCorrectionReference, current.temporaryDraft == true { retained.insert(current.id) }
        let folder = state.appendingPathComponent("voice-correction-drafts/\(id.uuidString)")
        try files.createDirectory(folder)
        for url in try files.list(folder) where url.pathExtension == "json" {
            guard let version = UUID(uuidString: url.deletingPathExtension().lastPathComponent), !retained.contains(version),
                  let bytes = try files.read(url, maximumBytes: 3100000),
                  let old = try? decoder.decode(VoiceCorrectionRecord.self, from: bytes),
                  old.recordingID == id, old.target == value.target, old.correction.id == version else { continue }
            // Only a verified current value permits cleanup. Failed/uncertain writes leave all objects reachable for retry.
            try assertVoiceCorrectionReadable(value)
            guard value.currentCorrectionReference != nil else { continue }
            try files.remove(url, purpose: .voiceCorrection)
        }
    }

    public func retrySkippedVoiceTranscript(_ id: UUID, media: VoiceMediaReference) throws {
        try Task.checkCancellation()
        var value = try voiceManifest(id)
        try assertVoiceAlive(value); try assertVoiceUninserted(id); try assertNoVoiceInsertion(value.target.day)
        guard try voiceReference(value, segmentID: media.segmentID) == media,
              value.skippedTranscripts?.contains(media) == true else { throw VoiceError.changedMedia }
        try Task.checkCancellation()
        value.skippedTranscripts?.removeAll { $0 == media }
        try writeVoice(value)
    }
}

extension LocalJournalStore {
    func externalizeVoiceTexts(_ value: VoiceRecording) throws -> VoiceRecording {
        var result = value
        var proposals = value.transcriptReferences ?? []
        for proposal in value.transcripts where !proposals.contains(where: { $0.id == proposal.id }) {
            proposals.append(try persistVoiceText(value, proposal: proposal, piece: nil))
        }
        var pieces = value.segmentTranscriptReferences ?? []
        for piece in value.segmentTranscripts ?? [] where !pieces.contains(where: { $0.id == piece.media.segmentID }) {
            pieces.append(try persistVoiceText(value, proposal: nil, piece: piece))
        }
        guard proposals.count <= 10000, pieces.count <= 1000 else { throw VoiceError.limit }
        if !proposals.isEmpty { result.transcriptReferences = proposals }
        if !pieces.isEmpty { result.segmentTranscriptReferences = pieces }
        result.transcripts = []; result.segmentTranscripts = nil
        return result
    }

    func persistVoiceText(_ value: VoiceRecording, proposal: VoiceTranscript?, piece: VoiceSegmentTranscript?) throws -> VoiceTextReference {
        guard let id = proposal?.id ?? piece?.media.segmentID, (proposal == nil) != (piece == nil),
              (proposal?.text ?? piece?.text ?? "").utf8.count <= 500000 else { throw VoiceError.limit }
        var record = VoiceTextRecord(recordingID: value.id, target: value.target, proposal: proposal, piece: piece)
        if let piece, let segment = value.segments.first(where: { $0.id == piece.media.segmentID }) {
            record.pieceInventory = [VoiceSourcePiece(segmentID: segment.id, relativeLink: piece.media.relativeLink,
                finalized: true, hash: piece.media.hash, byteCount: piece.media.byteCount,
                integrity: segment.captureEvidence, integrityUnknown: segment.captureEvidenceUnknown)]
        }
        let bytes = try encoder.encode(record)
        guard bytes.count <= 4 * 1024 * 1024 else { throw VoiceError.limit }
        var reference = VoiceTextReference(id: id, hash: SourceHash.digest(bytes), byteCount: bytes.count)
        if piece != nil { reference.segment = true }
        let url = try voiceTextURL(value.id, reference: reference)
        if try files.read(url, maximumBytes: 4 * 1024 * 1024) == nil { try files.write(bytes, to: url, purpose: .voiceTranscript) }
        let retained = try readVoiceText(value, reference: reference)
        guard retained.proposal == proposal, retained.piece == piece else { throw JournalError.operationMismatch }
        return reference
    }

    func readVoiceText(_ value: VoiceRecording, reference: VoiceTextReference) throws -> VoiceTextRecord {
        guard reference.byteCount > 0, reference.byteCount <= 4 * 1024 * 1024, reference.hash.count == 64,
              let bytes = try files.read(voiceTextURL(value.id, reference: reference), maximumBytes: 4 * 1024 * 1024),
              bytes.count == reference.byteCount, SourceHash.digest(bytes) == reference.hash,
              let record = try? decoder.decode(VoiceTextRecord.self, from: bytes), record.recordingID == value.id,
              record.target == value.target, (reference.segment == true ? record.piece?.media.segmentID : record.proposal?.id) == reference.id,
              (record.proposal == nil) != (record.piece == nil) else { throw VoiceError.missing }
        return record
    }

    private func voiceTextURL(_ id: UUID, reference: VoiceTextReference) throws -> URL {
        let folder = state.appendingPathComponent("voice-transcripts/\(id.uuidString)/\(reference.segment == true ? "pieces" : "proposals")")
        try files.createDirectory(folder)
        return folder.appendingPathComponent("\(reference.id.uuidString).json")
    }

    func hydrateVoiceTexts(_ value: VoiceRecording) throws -> VoiceRecording {
        var result = value
        for references in [value.transcriptReferences ?? [], value.segmentTranscriptReferences ?? []] {
            guard references.count <= 10000 else { throw VoiceError.limit }
            let groups = Dictionary(grouping: references, by: \.id)
            let invalid = Set(references.filter { groups[$0.id]!.count != 1 || $0.byteCount <= 0 || $0.byteCount > 4 * 1024 * 1024 || $0.hash.count != 64 }.map(\.id))
            result.unavailableHistory = Array(Set((result.unavailableHistory ?? []) + invalid))
            let valid = references.filter { !invalid.contains($0.id) }
            let selected = valid.reduce(0, { $0 + $1.byteCount }) <= 1000000 ? valid : Array(valid.suffix(1))
            for reference in selected {
                do {
                    let record = try readVoiceText(value, reference: reference)
                    if let proposal = record.proposal, !result.transcripts.contains(where: { $0.id == proposal.id }) { result.transcripts.append(proposal) }
                    if let piece = record.piece, result.segmentTranscripts?.contains(where: { $0.media.segmentID == piece.media.segmentID }) != true {
                        result.segmentTranscripts = (result.segmentTranscripts ?? []) + [piece]
                    }
                } catch { result.unavailableHistory = (result.unavailableHistory ?? []) + [reference.id] }
            }
        }
        return result
    }

    public func voiceTranscriptHistory(_ id: UUID, segments: Bool = false, offset: Int = 0) throws -> VoiceTextPage {
        let value = try externalizeVoiceTexts(voiceManifest(id))
        let references = (segments ? value.segmentTranscriptReferences : value.transcriptReferences) ?? []
        guard offset >= 0, offset <= references.count else { throw VoiceError.missing }
        let end = min(offset + 20, references.count)
        return VoiceTextPage(references: Array(references[offset..<end]), nextOffset: end < references.count ? end : nil)
    }

    public func voiceTranscript(_ id: UUID, version: UUID) throws -> VoiceTranscript {
        let value = try voiceManifest(id)
        if let inline = value.transcripts.first(where: { $0.id == version }) { return inline }
        let matches = (value.transcriptReferences ?? []).filter { $0.id == version }
        guard matches.count == 1, let reference = matches.first,
              let proposal = try readVoiceText(value, reference: reference).proposal else { throw VoiceError.missing }
        return proposal
    }

    public func voiceSegmentTranscript(_ id: UUID, segmentID: UUID) throws -> VoiceSegmentTranscript? {
        let value = try voiceManifest(id)
        if let inline = value.segmentTranscripts?.first(where: { $0.media.segmentID == segmentID }) { return inline }
        let matches = (value.segmentTranscriptReferences ?? []).filter { $0.id == segmentID }
        guard matches.count <= 1 else { throw VoiceError.missing }
        if let reference = matches.first { return try readVoiceText(value, reference: reference).piece }
        let location = VoiceTextReference(id: segmentID, hash: "", byteCount: 0, segment: true)
        guard let bytes = try files.read(voiceTextURL(id, reference: location), maximumBytes: 4 * 1024 * 1024) else { return nil }
        let reference = VoiceTextReference(id: segmentID, hash: SourceHash.digest(bytes), byteCount: bytes.count, segment: true)
        guard let piece = try readVoiceText(value, reference: reference).piece,
              !piece.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, piece.text.utf8.count <= 500000,
              try voiceReference(value, segmentID: segmentID) == piece.media else { throw VoiceError.changedMedia }
        try assertVoiceAlive(value); try assertVoiceUninserted(id); try assertNoVoiceInsertion(value.target.day)
        var recovered = value
        recovered.segmentTranscriptReferences = (value.segmentTranscriptReferences ?? []) + [reference]
        try writeVoice(recovered)
        return piece
    }
}

extension LocalJournalStore {
    public func voiceCorrectionCleanupPending(_ id: UUID) throws -> Bool {
        let value = try voiceManifest(id)
        let retained = Set(((value.correctionReferences ?? []) + [value.currentCorrectionReference].compactMap { $0 })
            .filter { $0.temporaryDraft == true }.map(\.id))
        let folder = state.appendingPathComponent("voice-correction-drafts/\(id.uuidString)")
        try files.createDirectory(folder)
        return try files.list(folder).contains { url in
            guard url.pathExtension == "json", let version = UUID(uuidString: url.deletingPathExtension().lastPathComponent) else { return false }
            return !retained.contains(version)
        }
    }
    public func retryVoiceCorrectionCleanup(_ id: UUID) throws { try retireVoiceDraftObjects(id) }
}

struct VoiceLegacyCorrectionEntry: Codable {
    let version: VoiceFileVersion?
    let reference: VoiceCorrectionReference?
}
struct VoiceLegacyCorrectionIndex: Codable {
    let recordingID: UUID
    let target: VoiceTarget
    var entries: [String: VoiceLegacyCorrectionEntry] = [:]
}

extension LocalJournalStore {
    public func voicePieceCorrectionOrigin(_ id: UUID, piece: VoiceSegmentTranscript) throws -> VoiceCorrectionOrigin {
        let value = try voiceManifest(id)
        guard try voiceSegmentTranscript(id, segmentID: piece.media.segmentID) == piece else { throw VoiceError.changedMedia }
        let latest = try voiceManifest(id)
        let reference = latest.segmentTranscriptReferences?.first { $0.id == piece.media.segmentID }
        let inventory = try reference.flatMap { try readVoiceText(latest, reference: $0).pieceInventory }
        return try VoiceCorrectionOrigin(recordingID: id, target: value.target, piece: piece, inventory: inventory)
    }
}
