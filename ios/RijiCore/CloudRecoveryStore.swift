import Foundation

extension LocalJournalStore {
    public func retainedCloudDraft(_ day: JournalDay) throws -> CloudRecoveryDraft? {
        guard let draft: CloudRecoveryDraft = try readCloudRecord(cloudURL("drafts", day.value)) else { return nil }
        guard draft.review.day == day else { throw CloudRecoveryError.storage }
        try validateCloudDraft(draft)
        return draft
    }

    /// Reading never depends on the ability to persist a new proposal or decode an older one.
    public func prepareCloudReview(_ day: JournalDay) throws -> CloudReviewState {
        guard let original else { throw OriginalVaultError.readOnly }
        var previous: CloudRecoveryDraft?, warning: String?
        do { previous = try retainedCloudDraft(day) } catch { warning = CloudRecoveryError.storage.localizedDescription }
        let display = try original.displaySnapshot(day, reviewVersions: true)
        guard let review = display.cloud else { throw CloudRecoveryError.stale }
        if review.versions.contains(where: { $0.content == nil && $0.exceedsSizeLimit != true }) || !review.complete {
            original.requestCloudDownloads(day)
        }
        let hasManualChanges = previous.map { $0.content != $0.review.current } ?? false
        let preserveProposal = try hasManualChanges || cloudConfirmationPending(day)
        var draft = CloudRecoveryDraft(review: review, content: preserveProposal ? previous?.content ?? review.current : review.current)
        if let previous, try previous.evidenceDigest() == draft.evidenceDigest() { draft = previous }
        return CloudReviewState(draft: draft, warning: warning)
    }

    public func reviewCloudVersions(_ day: JournalDay) throws -> CloudRecoveryDraft {
        let result = try prepareCloudReview(day)
        try retainCloudDraft(result.draft)
        return result.draft
    }

    public func retainCloudDraft(_ draft: CloudRecoveryDraft) throws {
        try validateCloudDraft(draft)
        let data = try encodeCloudRecord(draft), url = cloudURL("drafts", draft.review.day.value)
        if let previous = try retainedCloudDraft(draft.review.day) {
            let before = CloudRecoveryDraft(review: previous.review, content: Data())
            let after = CloudRecoveryDraft(review: draft.review, content: Data())
            if try before.evidenceDigest() != after.evidenceDigest() { try writeCloudEvidence(previous) }
        }
        if try files.read(url, maximumBytes: CloudReview.maximumRecordBytes) == data { return }
        try writeCloudBytes(data, to: url, purpose: .cloudDraft)
    }

    public func cloudConfirmationPending(_ day: JournalDay) throws -> Bool {
        try cloudConfirmation(day).map { !$0.completed } ?? false
    }

    public func cloudWorkPending(_ day: JournalDay) throws -> Bool {
        if try cloudConfirmationPending(day) { return true }
        guard let draft = try retainedCloudDraft(day) else { return false }
        let completion: CloudCompletion? = try readCloudRecord(cloudURL("completed", day.value))
        return try completion?.draftDigest != draft.evidenceDigest()
    }

    /// Close bookkeeping only after the system conflict is gone; never replay a source write.
    func reconcileSettledCloudReview(_ day: JournalDay) throws {
        guard let original, original.canWrite, try cloudWorkPending(day) else { return }
        let draft = try retainedCloudDraft(day), confirmation = try cloudConfirmation(day)
        let pending = confirmation.flatMap { $0.completed ? nil : $0 }
        guard pending != nil || draft.map({ $0.content == $0.review.current }) == true else { return }
        guard let review = try original.displaySnapshot(day, reviewVersions: true).cloud,
              review.canKeepCurrent else { return }
        if let pending {
            guard pending.draft.review.sourceID == review.sourceID, pending.draft.content == review.current else { return }
            try verifyCloudEvidence(pending)
        } else if let draft {
            guard draft.review.sourceID == review.sourceID else { return }
            try writeCloudEvidence(draft)
        }
        try original.confirmCloudCurrent(review) {
            if var pending {
                pending.completed = true
                try writeCloudConfirmation(pending)
                try completeCloudDraft(pending.draft)
            } else if let draft { try completeCloudDraft(draft) }
        }
    }

    /// Confirmation persists and verifies every byte before touching the source or version flags.
    public func confirmCloudResolution(_ draft: CloudRecoveryDraft) throws {
        guard let original, original.canWrite else { throw OriginalVaultError.readOnly }
        try validateCloudDraft(draft)
        guard draft.review.canResolve else { throw CloudRecoveryError.incomplete }
        if let previous = try retainedCloudDraft(draft.review.day), previous != draft { try writeCloudEvidence(previous) }
        try retainCloudDraft(draft)
        let previous = try cloudConfirmation(draft.review.day)
        if let previous, previous.draft == draft {
            try verifyCloudEvidence(previous)
            if previous.completed {
                guard try original.read(draft.review.day) == draft.content else { throw CloudRecoveryError.stale }
                try completeCloudDraft(draft); return
            }
            try finishCloudResolution(previous, original: original, retry: true); return
        }
        let latest = try original.displaySnapshot(draft.review.day, reviewVersions: true)
        guard latest.data == draft.review.current, let review = latest.cloud,
              review.sourceID == draft.review.sourceID, review.versions == draft.review.versions,
              review.complete else { throw CloudRecoveryError.stale }
        try observeCloudConflict(draft.review.day)
        let held = try cloudBodyHold(draft.review.day)?.ids ?? []
        let confirmation = CloudConfirmation(id: UUID(), draft: draft, heldBodyIDs: held, completed: false)
        try writeCloudBytes(encodeCloudRecord(confirmation), to: evidenceURL(draft.review.day, confirmation.id.uuidString), purpose: .cloudBackup)
        try writeCloudConfirmation(confirmation)
        try finishCloudResolution(confirmation, original: original, retry: false)
    }

    public func retryCloudResolution(_ day: JournalDay) throws {
        guard let original, let confirmation = try cloudConfirmation(day) else { throw CloudRecoveryError.pending }
        try verifyCloudEvidence(confirmation)
        if confirmation.completed {
            guard try original.read(day) == confirmation.draft.content else { throw CloudRecoveryError.stale }
            try completeCloudDraft(confirmation.draft); return
        }
        try finishCloudResolution(confirmation, original: original, retry: true)
    }

    /// The user reviews current bytes again after an external edit or interrupted completion.
    public func keepReviewedCloudCurrent(_ draft: CloudRecoveryDraft) throws {
        guard let original, draft.review.canKeepCurrent else { throw CloudRecoveryError.incomplete }
        try validateCloudDraft(draft)
        try writeCloudEvidence(draft)
        let previous = try cloudConfirmation(draft.review.day)
        if let previous { try verifyCloudEvidence(previous) }
        try original.confirmCloudCurrent(draft.review) {
            if var previous { previous.completed = true; try writeCloudConfirmation(previous) }
            try retainCloudDraft(draft)
            try completeCloudDraft(draft)
        }
    }

    public func cloudVersionBackups(_ day: JournalDay) throws -> [CloudRecoveryDraft] {
        try cloudBackupState(day).drafts
    }

    /// New evidence is namespaced by day. Legacy flat records remain untouched and readable.
    public func cloudBackupState(_ day: JournalDay) throws -> CloudBackupState {
        var result = CloudBackupState(), found: [String: CloudRecoveryDraft] = [:]
        let root = state.appendingPathComponent("cloud-evidence"), folder = root.appendingPathComponent(day.value)
        let own = FileManager.default.fileExists(atPath: folder.path) ? try files.list(folder) : []
        let legacy = try files.list(root).filter { $0.pathExtension == "json" }
        guard own.count + legacy.count <= 10000 else { throw JournalError.scanLimit }
        for url in (own + legacy).sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) where url.pathExtension == "json" {
            do {
                guard let data = try files.read(url, maximumBytes: CloudReview.maximumRecordBytes) else { throw CloudRecoveryError.storage }
                let draft = (try? decoder.decode(CloudConfirmation.self, from: data))?.draft
                    ?? (try? decoder.decode(CloudRecoveryDraft.self, from: data))
                guard let draft else { throw CloudRecoveryError.storage }
                guard draft.review.day == day else { continue }
                try validateCloudDraft(draft)
                let digest = try draft.evidenceDigest()
                if found[digest] == nil { found[digest] = draft }
            } catch { if url.deletingLastPathComponent().standardizedFileURL.path == folder.standardizedFileURL.path { result.unreadableCount += 1 } }
        }
        result.drafts = found.keys.sorted().compactMap { found[$0] }
        return result
    }

    /// Observing a cloud conflict assigns durable ownership before any later automatic recovery.
    public func observeCloudConflict(_ day: JournalDay) throws {
        guard let original else { return }
        let recovery = bodyRecoveryState(day)
        let ids = [recovery.edit?.id, recovery.operation?.id, recovery.merge?.conflict.edit.id].compactMap { $0 }
        guard !ids.isEmpty else { return }
        let source = recovery.edit?.sourceID ?? recovery.operation?.sourceID ?? recovery.merge?.conflict.edit.sourceID
        try original.validateRetainedSource(source, day: day)
        var hold = try cloudBodyHold(day) ?? CloudBodyHold(day: day, sourceID: source!, ids: [], active: true)
        hold.ids = Array(Set(hold.ids + ids)).sorted { $0.uuidString < $1.uuidString }; hold.active = true
        try writeCloudBytes(encodeCloudRecord(hold), to: cloudURL("body-holds", day.value), purpose: .cloudConfirmation)
    }

    public func bodyHeldByCloud(_ day: JournalDay) throws -> Bool {
        try cloudBodyHold(day)?.active == true
    }

    func assertBodyNotHeldByCloud(_ edit: BodyEdit) throws {
        if let hold = try cloudBodyHold(edit.day), hold.active || hold.ids.contains(edit.id) { throw CloudRecoveryError.heldBody }
        if try cloudConfirmation(edit.day)?.heldBodyIDs.contains(edit.id) == true { throw CloudRecoveryError.heldBody }
    }

    /// Called only after an explicit body comparison has durably recorded its replacement intent.
    func authorizeBodyAfterCloud(_ next: BodyEdit) throws {
        guard var hold = try cloudBodyHold(next.day) else { return }
        guard try readBodySource(next.day) == next.before else { throw JournalError.changedSource }
        let recovery = bodyRecoveryState(next.day)
        hold.ids = Array(Set(hold.ids + [recovery.edit?.id, recovery.merge?.conflict.edit.id].compactMap { $0 }))
            .filter { $0 != next.id }.sorted { $0.uuidString < $1.uuidString }
        hold.active = false
        try writeCloudBytes(encodeCloudRecord(hold), to: cloudURL("body-holds", next.day.value), purpose: .cloudConfirmation)
    }

    func cloudRecoveryDays() throws -> Set<JournalDay> {
        var days = Set<JournalDay>()
        for folder in ["drafts", "confirmations"] {
            for url in try files.list(state.appendingPathComponent("cloud-" + folder)) where url.pathExtension == "json" {
                guard let day = try? JournalDay(url.deletingPathExtension().lastPathComponent) else { continue }
                if (try? cloudWorkPending(day)) != false { days.insert(day) }
            }
        }
        return days
    }

    private func cloudBodyHold(_ day: JournalDay) throws -> CloudBodyHold? {
        guard let hold: CloudBodyHold = try readCloudRecord(cloudURL("body-holds", day.value)) else { return nil }
        guard hold.day == day else { throw CloudRecoveryError.storage }
        try original?.validateRetainedSource(hold.sourceID, day: day)
        return hold
    }

    private func finishCloudResolution(_ value: CloudConfirmation, original: OriginalVaultAccess, retry: Bool) throws {
        try original.resolveCloud(value.draft, retry: retry)
        var completed = value; completed.completed = true
        try writeCloudConfirmation(completed)
        try completeCloudDraft(value.draft)
    }

    private func completeCloudDraft(_ draft: CloudRecoveryDraft) throws {
        let completion = try CloudCompletion(draftDigest: draft.evidenceDigest())
        try writeCloudBytes(encodeCloudRecord(completion), to: cloudURL("completed", draft.review.day.value), purpose: .cloudConfirmation)
    }

    private func validateCloudDraft(_ draft: CloudRecoveryDraft) throws {
        guard let original else { throw OriginalVaultError.readOnly }
        try original.validateRetainedSource(draft.review.sourceID, day: draft.review.day)
        guard draft.content.count <= CloudReview.maximumBytes, draft.review.current.count <= CloudReview.maximumBytes,
              draft.review.versions.count <= CloudReview.maximumVersions,
              draft.review.versions.allSatisfy({ ($0.content?.count ?? 0) <= CloudReview.maximumBytes }) else { throw CloudRecoveryError.limit }
        guard Set(draft.review.versions.map(\.id)).count == draft.review.versions.count,
              draft.review.versions.allSatisfy({ !$0.id.isEmpty && $0.id.count <= 256 }),
              String(data: draft.content, encoding: .utf8) != nil else { throw CloudRecoveryError.incomplete }
    }

    private func cloudConfirmation(_ day: JournalDay) throws -> CloudConfirmation? {
        guard let confirmation: CloudConfirmation = try readCloudRecord(cloudURL("confirmations", day.value)) else { return nil }
        guard confirmation.draft.review.day == day else { throw CloudRecoveryError.storage }
        try validateCloudDraft(confirmation.draft)
        return confirmation
    }

    private func verifyCloudEvidence(_ confirmation: CloudConfirmation) throws {
        let url = try evidenceURL(confirmation.draft.review.day, confirmation.id.uuidString)
        let legacy = cloudURL("evidence", confirmation.id.uuidString)
        let archived: CloudConfirmation? = try readCloudRecord(url) ?? readCloudRecord(legacy)
        guard let archived, archived.id == confirmation.id, archived.draft == confirmation.draft,
              archived.heldBodyIDs == confirmation.heldBodyIDs else { throw CloudRecoveryError.storage }
    }

    private func writeCloudConfirmation(_ confirmation: CloudConfirmation) throws {
        try writeCloudBytes(encodeCloudRecord(confirmation), to: cloudURL("confirmations", confirmation.draft.review.day.value), purpose: .cloudConfirmation)
    }

    private func writeCloudEvidence(_ draft: CloudRecoveryDraft) throws {
        try validateCloudDraft(draft)
        let url = try evidenceURL(draft.review.day, draft.evidenceDigest())
        if let old: CloudRecoveryDraft = try readCloudRecord(url) {
            guard try old.evidenceDigest() == draft.evidenceDigest() else { throw CloudRecoveryError.storage }; return
        }
        try writeCloudBytes(encodeCloudRecord(draft), to: url, purpose: .cloudBackup)
    }

    private func encodeCloudRecord<T: Encodable>(_ value: T) throws -> Data {
        let cloudEncoder = JSONEncoder(); cloudEncoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let data = try cloudEncoder.encode(value)
        guard data.count <= CloudReview.maximumRecordBytes else { throw CloudRecoveryError.limit }
        return data
    }

    private func writeCloudBytes(_ data: Data, to url: URL, purpose: FilePurpose) throws {
        guard data.count <= CloudReview.maximumRecordBytes else { throw CloudRecoveryError.limit }
        try files.write(data, to: url, purpose: purpose)
        guard try files.read(url, maximumBytes: CloudReview.maximumRecordBytes) == data else { throw CloudRecoveryError.storage }
    }

    private func readCloudRecord<T: Decodable>(_ url: URL) throws -> T? {
        guard let data = try files.read(url, maximumBytes: CloudReview.maximumRecordBytes) else { return nil }
        guard let value = try? decoder.decode(T.self, from: data) else { throw CloudRecoveryError.storage }
        return value
    }

    private func evidenceURL(_ day: JournalDay, _ key: String) throws -> URL {
        let folder = state.appendingPathComponent("cloud-evidence/" + day.value)
        if !FileManager.default.fileExists(atPath: folder.path) { try files.createDirectory(folder) }
        return folder.appendingPathComponent(key + ".json")
    }

    private func cloudURL(_ folder: String, _ key: String) -> URL {
        state.appendingPathComponent("cloud-\(folder)/\(key).json")
    }
}
