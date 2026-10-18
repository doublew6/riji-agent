import Foundation

extension LocalJournalStore {
    public func abandonableVoiceInsertion(_ day: JournalDay) throws -> VoiceInsertionCandidate? {
        guard let intent = try voiceIntent(day), try isVoiceInsertionUnapplied(intent.candidate) else { return nil }
        return intent.candidate
    }

    /// Explicit abandonment never removes text or audio, and never reuses the old confirmation identity.
    public func abandonVoiceInsertion(_ candidate: VoiceInsertionCandidate) throws {
        if let old = try voiceAbandonment(candidate) {
            guard old.candidate == candidate else { throw JournalError.operationMismatch }
            try assertAbandonedVoiceInsertionUnapplied(candidate)
            if let bytes = try files.read(voiceIntentURL(candidate.target.day), maximumBytes: 4 * 1024 * 1024) {
                guard let pending = try? decoder.decode(VoiceInsertionIntent.self, from: bytes) else { throw JournalError.storage }
                if pending.candidate == candidate { _ = try voiceIntent(candidate.target.day) }
            }
            return
        }
        guard let intent = try voiceIntent(candidate.target.day), intent.candidate == candidate,
              try isVoiceInsertionUnapplied(candidate) else { throw JournalError.operationMismatch }
        let record = VoiceAbandonment(candidate: candidate)
        try files.write(encoder.encode(record), to: voiceAbandonmentURL(candidate.id), purpose: .voiceAbandonment)
        guard try voiceAbandonment(candidate) == record else { throw JournalError.storage }
        // A failed clear is reconciled from the durable abandonment on retry/relaunch.
        _ = try voiceIntent(candidate.target.day)
    }

    func assertAbandonedVoiceInsertionUnapplied(_ candidate: VoiceInsertionCandidate) throws {
        let evidence = try voiceInsertionInventory()
        guard !evidence.contains(where: { $0.insertionID == candidate.id }) else { throw JournalError.operationMismatch }
    }

    func isVoiceInsertionUnapplied(_ candidate: VoiceInsertionCandidate) throws -> Bool {
        guard try !voiceInsertionRecorded(candidate), let draft = try readDraft(candidate.target.day) else { return false }
        return draft.operationID == candidate.baseDraftID && payloadHash(draft) == candidate.baseDraftHash
    }

    func voiceAbandonment(_ candidate: VoiceInsertionCandidate) throws -> VoiceAbandonment? {
        guard let bytes = try files.read(voiceAbandonmentURL(candidate.id), maximumBytes: 4 * 1024 * 1024) else { return nil }
        guard let value = try? decoder.decode(VoiceAbandonment.self, from: bytes), value.candidate == candidate else {
            throw JournalError.operationMismatch
        }
        return value
    }

    private func voiceAbandonmentURL(_ id: UUID) throws -> URL {
        let folder = state.appendingPathComponent("voice-abandonments")
        try files.createDirectory(folder)
        return folder.appendingPathComponent("\(id.uuidString).json")
    }
}
