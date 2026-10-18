import Foundation
import CryptoKit

extension LocalJournalStore {
    public func prepareVoiceInsertion(_ recordingID: UUID, draft: JournalDraft) throws -> VoiceInsertionCandidate {
        guard original == nil else { throw OriginalVaultError.readOnly }
        let value = try recording(recordingID)
        try assertVoiceAlive(value)
        try assertVoiceCorrectionReadable(value)
        try assertVoiceUninserted(recordingID)
        try assertNoVoiceInsertion(draft.day)
        try assertVoiceDestination(draft)
        let correction = value.effectiveCorrection
        try validateVoiceOrigin(value, origin: correction.origin)
        guard (value.insertionTarget ?? value.target) == (try voiceTarget(draft)) else {
            throw VoiceError.invalidTarget
        }
        guard try readDraft(draft.day) == draft else { throw JournalError.changedSource }
        let media = try voiceReferences(recordingID, allowingUnavailable: true)
        let inventory = try voiceSourceInventory(recordingID)
        guard !correction.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !media.isEmpty else { throw VoiceError.emptyTranscript }
        let preview = try voicePreview(correction: correction, media: media, inventory: inventory)
        guard !preview.isEmpty else { throw VoiceError.emptyTranscript }
        return VoiceInsertionCandidate(id: UUID(), recordingID: recordingID, target: value.insertionTarget ?? value.target,
                                       correction: correction, media: media, baseDraftID: draft.operationID,
                                       baseDraftHash: payloadHash(draft), preview: preview, inventory: inventory)
    }

    /// Persist the exact confirmed intent before replacing any draft. No post-write flag exists.
    public func insertVoice(_ candidate: VoiceInsertionCandidate) throws -> VoiceInsertionResult {
        guard original == nil else { throw OriginalVaultError.readOnly }
        if try voiceAbandonment(candidate) != nil { throw JournalError.operationMismatch }
        if try voiceInsertionRecorded(candidate) {
            try clearVoiceIntent(candidate)
            return .alreadyInserted
        }
        if let intent = try voiceIntent(candidate.target.day) {
            guard intent.candidate == candidate else { throw JournalError.operationMismatch }
            try applyVoiceIntent(intent)
            return .inserted
        }
        try validateVoiceCandidate(candidate)
        guard let draft = try readDraft(candidate.target.day) else { throw JournalError.changedSource }
        var merged = draft.updating(text: appendVoice(candidate.preview, to: draft.text), section: draft.section)
        merged.voiceInsertions = (draft.voiceInsertions ?? []) + [try voiceStamp(candidate)]
        try validate(merged)
        let intent = VoiceInsertionIntent(candidate: candidate, mergedDraft: merged)
        let encoded = try encodedVoiceIntent(intent)
        try files.write(encoded, to: voiceIntentURL(candidate.target.day), purpose: .voiceIntent)
        try applyVoiceIntent(intent)
        return .inserted
    }

    public func pendingVoiceInsertion(_ day: JournalDay) throws -> VoiceInsertionCandidate? {
        try voiceIntent(day)?.candidate
    }

    public func recoverVoiceInsertion(_ day: JournalDay) throws {
        guard let intent = try voiceIntent(day) else { return }
        _ = try insertVoice(intent.candidate)
    }

    public func voiceInsertionRecorded(_ candidate: VoiceInsertionCandidate) throws -> Bool {
        guard candidate.target.namespace == (try voiceNamespace()) else { throw VoiceError.invalidTarget }
        let expected = try voiceStamp(candidate)
        let stamps = try voiceInsertionInventory()
        let matches = stamps.filter { $0.recordingID == candidate.recordingID || $0.insertionID == candidate.id }
        guard matches.allSatisfy({ $0 == expected }) else { throw JournalError.operationMismatch }
        return !matches.isEmpty
    }

    /// The draft and ordinary receipt are the only authoritative insertion evidence.
    public func voiceInsertion(for recordingID: UUID) throws -> VoiceInsertionStamp? {
        let matches = try voiceInsertionInventory().filter { $0.recordingID == recordingID }
        guard matches.allSatisfy({ $0 == matches.first }) else { throw JournalError.operationMismatch }
        return matches.first
    }

    /// Bodies are validated once per file version. A cold large inventory exposes resumable progress.
    func voiceInsertionInventory() throws -> [VoiceInsertionStamp] {
        let urls = try ["drafts", "receipts"].flatMap { try files.list(state.appendingPathComponent($0)) }
            .filter { $0.pathExtension == "json" }.sorted { $0.path < $1.path }
        var digest = SHA256()
        for url in urls {
            let version = try VoiceFileVersion(url)
            digest.update(data: Data("\(url.path):\(version)\n".utf8))
        }
        let signature = digest.finalize().map { String(format: "%02x", $0) }.joined()
        var scan = voiceEvidenceScan?.signature == signature ? voiceEvidenceScan! : VoiceEvidenceScan(signature: signature)
        if scan.offset == urls.count { return Array(scan.stamps.values) }
        var count = 0
        while scan.offset < urls.count {
            let url = urls[scan.offset], version = try VoiceFileVersion(url)
            let stamps: [VoiceInsertionStamp]
            if let cached = voiceEvidenceCache[url.path], cached.version == version { stamps = cached.stamps }
            else {
                if count >= 64 { voiceEvidenceScan = scan; throw VoiceError.verifying }
                count += 1; voiceEvidenceCacheStampCount -= voiceEvidenceCache[url.path]?.stamps.count ?? 0
                voiceEvidenceCache[url.path] = nil
                stamps = try readVoiceEvidence(url, version: version)
                // The aggregate signature allows hot queries for any number of ordinary receipts;
                // this bounded per-file accelerator helps changed/cold scans without becoming authority.
                if voiceEvidenceCache.count >= 20000 || voiceEvidenceCacheStampCount + stamps.count > 10000 {
                    voiceEvidenceCache.removeAll(keepingCapacity: true); voiceEvidenceCacheStampCount = 0
                }
                voiceEvidenceCacheStampCount += stamps.count
                voiceEvidenceCache[url.path] = VoiceEvidenceCacheEntry(version: version, stamps: stamps)
            }
            for stamp in stamps {
                guard scan.stamps[stamp.recordingID].map({ $0 == stamp }) ?? true,
                      scan.insertions[stamp.insertionID].map({ $0 == stamp.recordingID }) ?? true else { throw JournalError.operationMismatch }
                guard scan.stamps[stamp.recordingID] != nil || scan.stamps.count < VoiceMetadataBounds.stampCount else { throw VoiceError.limit }
                scan.stamps[stamp.recordingID] = stamp; scan.insertions[stamp.insertionID] = stamp.recordingID
            }
            scan.offset += 1
        }
        let result = Array(scan.stamps.values)
        try VoiceMetadataBounds.validate(result)
        voiceEvidenceScan = scan
        return result
    }

    private func readVoiceEvidence(_ url: URL, version: VoiceFileVersion) throws -> [VoiceInsertionStamp] {
        try files.failure(.voiceEvidence, .beforeRead)
        let bytes = try VoiceEvidenceReader(url: url).projection()
        let stamps: [VoiceInsertionStamp]
        if url.deletingLastPathComponent().lastPathComponent == "drafts" {
            guard let value = try? decoder.decode(JournalDraft.self, from: bytes),
                  url.deletingPathExtension().lastPathComponent == value.day.value else { throw JournalError.storage }
            stamps = value.voiceInsertions ?? []
        } else {
            guard let value = try? decoder.decode(JournalReceipt.self, from: bytes),
                  UUID(uuidString: url.deletingPathExtension().lastPathComponent) == value.operationID else { throw JournalError.storage }
            stamps = value.voiceInsertions ?? []
        }
        try VoiceMetadataBounds.validate(stamps)
        guard try VoiceFileVersion(url) == version else { throw JournalError.storage }
        return stamps
    }

    func assertVoiceUninserted(_ id: UUID) throws {
        guard try voiceInsertion(for: id) == nil else { throw JournalError.operationMismatch }
    }

    func assertNoVoiceInsertion(_ day: JournalDay) throws {
        if try voiceIntent(day) != nil { throw JournalError.pendingOperation }
    }

    func assertVoiceDestination(_ draft: JournalDraft) throws {
        try assertNoBodyPending(draft.day)
        try assertNoWorkflowPending(.daily(draft.day))
        guard try pendingTransaction(for: draft.day) == nil, !(try hasOperation(draft.operationID)) else {
            throw JournalError.pendingOperation
        }
        let source = try files.read(diaryURL(draft.day))
        guard source.map(SourceHash.digest) == draft.sourceHash else { throw JournalError.changedSource }
        let headings = try MarkdownSectionAppender.headings(in: source ?? Data(draft.effectiveTemplate.utf8))
        let matches = headings.filter { $0.title.utf8.elementsEqual(draft.section.utf8) }
        guard matches.count == 1 else { throw VoiceError.invalidTarget }
    }

    private func validateVoiceCandidate(_ candidate: VoiceInsertionCandidate) throws {
        let value = try recording(candidate.recordingID)
        try assertVoiceAlive(value)
        try assertVoiceCorrectionReadable(value)
        try validateVoiceOrigin(value, origin: candidate.correction.origin)
        if let inventory = candidate.inventory { guard try voiceSourceInventory(value.id) == inventory else { throw VoiceError.changedMedia } }
        guard let draft = try readDraft(candidate.target.day), draft.operationID == candidate.baseDraftID,
              payloadHash(draft) == candidate.baseDraftHash else { throw JournalError.changedSource }
        try assertVoiceDestination(draft)
        guard (value.insertionTarget ?? value.target) == candidate.target, candidate.target == (try voiceTarget(draft)),
              value.effectiveCorrection == candidate.correction,
              try voiceReferences(value.id, allowingUnavailable: candidate.inventory != nil) == candidate.media,
              candidate.preview.utf8.elementsEqual(try voicePreview(correction: candidate.correction, media: candidate.media, inventory: candidate.inventory).utf8)
        else { throw JournalError.operationMismatch }
    }

    private func applyVoiceIntent(_ intent: VoiceInsertionIntent) throws {
        let candidate = intent.candidate
        try validateVoiceCandidate(candidate)
        guard let base = try readDraft(candidate.target.day),
              intent.mergedDraft.day == base.day,
              intent.mergedDraft.text.utf8.elementsEqual(appendVoice(candidate.preview, to: base.text).utf8),
              intent.mergedDraft.section.utf8.elementsEqual(base.section.utf8),
              intent.mergedDraft.sourceHash == base.sourceHash,
              intent.mergedDraft.effectiveTemplate.utf8.elementsEqual(base.effectiveTemplate.utf8),
              intent.mergedDraft.voiceInsertions == (base.voiceInsertions ?? []) + [try voiceStamp(candidate)]
        else { throw JournalError.operationMismatch }
        _ = try encodedVoiceIntent(intent)
        try files.write(encoder.encode(intent.mergedDraft), to: draftURL(base.day), purpose: .draft)
        guard try voiceInsertionRecorded(candidate) else { throw JournalError.storage }
        try clearVoiceIntent(candidate)
    }

    func clearVoiceIntent(_ candidate: VoiceInsertionCandidate) throws {
        if let intent = try voiceIntent(candidate.target.day) {
            guard intent.candidate == candidate else { throw JournalError.operationMismatch }
            try files.remove(voiceIntentURL(candidate.target.day), purpose: .voiceIntent)
        }
    }

    func voiceIntent(_ day: JournalDay) throws -> VoiceInsertionIntent? {
        guard let data = try files.read(voiceIntentURL(day), maximumBytes: 4 * 1024 * 1024) else { return nil }
        guard let value = try? decoder.decode(VoiceInsertionIntent.self, from: data), value.candidate.target.day == day else {
            throw JournalError.storage
        }
        if try voiceAbandonment(value.candidate) != nil {
            try assertAbandonedVoiceInsertionUnapplied(value.candidate)
            try files.remove(voiceIntentURL(day), purpose: .voiceIntent)
            return nil
        }
        return value
    }

    private func encodedVoiceIntent(_ intent: VoiceInsertionIntent) throws -> Data {
        try validate(intent.mergedDraft)
        try preflightVoiceMetadata(intent.mergedDraft)
        let draftBytes = try encoder.encode(intent.mergedDraft)
        let intentBytes = try encoder.encode(intent)
        guard draftBytes.count <= 4 * 1024 * 1024, intentBytes.count <= 4 * 1024 * 1024 else { throw VoiceError.limit }
        return intentBytes
    }

    func voiceIntentURL(_ day: JournalDay) -> URL { state.appendingPathComponent("voice-intents/\(day.value).json") }
    private func voiceStamp(_ value: VoiceInsertionCandidate) throws -> VoiceInsertionStamp {
        VoiceInsertionStamp(recordingID: value.recordingID, insertionID: value.id,
                            payloadHash: SourceHash.digest(try encoder.encode(value)))
    }
    private func appendVoice(_ preview: String, to text: String) -> String {
        text.isEmpty ? preview : text + (text.hasSuffix("\n") ? "\n" : "\n\n") + preview
    }
    private func voicePreview(correction: VoiceCorrection, media: [VoiceMediaReference], inventory: [VoiceSourcePiece]? = nil) throws -> String {
        let originNotice = try correction.origin.flatMap { try voiceCorrectionOriginNotice($0.recordingID, origin: $0) }
        let notice = [originNotice ?? "", inventory.map(VoiceSourceNotices.text) ?? ""].filter { !$0.isEmpty }.joined(separator: "\n")
        let links: String
        if let inventory, inventory.contains(where: { !$0.usable || $0.skipped == true }) {
            links = inventory.enumerated().map { "[原录音 \($0.offset + 1)\($0.element.usable ? "" : "（未核验）")](\($0.element.relativeLink))" }.joined(separator: "\n")
        } else {
            links = media.enumerated().map { "[原录音 \($0.offset + 1)](\($0.element.relativeLink))" }.joined(separator: "\n")
        }
        let tail = [notice, links].filter { !$0.isEmpty }.joined(separator: "\n")
        return correction.text + (correction.text.isEmpty || tail.isEmpty ? "" : "\n\n") + tail
    }
}

extension LocalJournalStore {
    func preflightVoiceMetadata(_ draft: JournalDraft) throws {
        guard let stamps = draft.voiceInsertions, !stamps.isEmpty else { return }
        try VoiceMetadataBounds.validate(stamps)
        var combined = Dictionary(uniqueKeysWithValues: try voiceInsertionInventory().map { ($0.recordingID, $0) })
        for stamp in stamps {
            guard combined[stamp.recordingID].map({ $0 == stamp }) ?? true else { throw JournalError.operationMismatch }
            combined[stamp.recordingID] = stamp
        }
        try VoiceMetadataBounds.validate(Array(combined.values))
    }
}

struct VoiceEvidenceScan {
    let signature: String
    var offset = 0
    var stamps: [UUID: VoiceInsertionStamp] = [:]
    var insertions: [UUID: UUID] = [:]
}
