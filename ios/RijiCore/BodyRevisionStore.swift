import Foundation

/// A manual edit owns full UTF-8 bytes; rendering is never a source of stored Markdown.
public struct BodyEdit: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public let day: JournalDay
    public let before: Data?
    public let content: Data
    public let createdAt: Date
    public let sourceID: String?

    public init(day: JournalDay, before: Data?, content: Data, sourceID: String? = nil) {
        id = UUID(); self.sourceID = sourceID; self.day = day; self.before = before; self.content = content; createdAt = Date()
    }
}

public struct BodyConflict: Codable, Equatable, Sendable {
    public let edit: BodyEdit
    public let source: Data?
    public init(edit: BodyEdit, source: Data?) { self.edit = edit; self.source = source }
}

public struct BodyMergeDraft: Codable, Equatable, Sendable {
    public let conflict: BodyConflict
    public let content: Data
    public let priorEdits: [BodyEdit]?
    public init(conflict: BodyConflict, content: Data, priorEdits: [BodyEdit]? = nil) {
        self.conflict = conflict; self.content = content; self.priorEdits = priorEdits
    }
}

/// Read failures are isolated from the healthy saved document; no evidence is mutated.
public struct BodyRecoveryState: Sendable {
    public var edit: BodyEdit?
    public var merge: BodyMergeDraft?
    public var hasOperation = false
    public var operation: BodyEdit?
    public var isReadable = true
    public var localSnapshot: JournalSnapshot? {
        guard isReadable, let retained = edit ?? merge?.conflict.edit ?? operation else { return nil }
        return JournalSnapshot(day: retained.day, markdown: String(decoding: retained.content, as: UTF8.self),
                               sourceHash: nil, draft: nil, isPending: false)
    }
}

private struct BodyResolution: Codable {
    let conflict: BodyConflict
    let next: BodyEdit
    let superseded: [BodyEdit]?
}

struct BodyReceipt: Codable {
    let edit: BodyEdit
    let supersededBy: UUID?
}

extension LocalJournalStore {
    public func bodyRecoveryState(_ day: JournalDay) -> BodyRecoveryState {
        var result = BodyRecoveryState()
        do { result.edit = try retainedBodyEdit(day) } catch { result.isReadable = false }
        do { result.merge = try retainedBodyMerge(day) } catch { result.isReadable = false }
        do {
            let transaction: BodyEdit? = try decode(bodyURL("transactions", day.value))
            result.hasOperation = transaction != nil
            if let transaction {
                result.operation = transaction
                try validateBody(transaction, day: day)
                if let receipt: BodyReceipt = try decode(bodyURL("receipts", transaction.id.uuidString)), receipt.edit != transaction {
                    throw JournalError.operationMismatch
                }
            }
        } catch { result.isReadable = false; result.hasOperation = true }
        do {
            let resolution: BodyResolution? = try decode(bodyURL("resolutions", day.value))
            result.hasOperation = result.hasOperation || resolution != nil
            if let resolution {
                result.operation = resolution.next
                try validateBody(resolution.next, day: day)
                try validateBody(resolution.conflict.edit, day: day)
                if let receipt: BodyReceipt = try decode(bodyURL("receipts", resolution.next.id.uuidString)), receipt.edit != resolution.next {
                    throw JournalError.operationMismatch
                }
            }
        } catch { result.isReadable = false; result.hasOperation = true }
        return result
    }

    private func assertReadableBodyRecovery(_ day: JournalDay) throws {
        guard bodyRecoveryState(day).isReadable else { throw JournalError.storage }
    }

    public func retainedBodyEdit(_ day: JournalDay) throws -> BodyEdit? {
        let edit: BodyEdit? = try decode(bodyURL("drafts", day.value))
        if let edit { try validateBody(edit, day: day) }
        return edit
    }

    /// The pending buffer is separate from an uncertain write, so later typing cannot change its identity.
    public func retainBodyEdit(_ edit: BodyEdit) throws {
        try assertReadableBodyRecovery(edit.day)
        try validateBody(edit, day: edit.day)
        try select(edit.day)
        try files.write(encoder.encode(edit), to: bodyURL("drafts", edit.day.value), purpose: .bodyDraft) {
            try self.assertReadableBodyRecovery(edit.day)
        }
    }

    /// Only the exact replaceable buffer can be retired. Transactions, receipts,
    /// merge input and immutable history keep their independent ownership.
    public func retireBodyBuffer(_ edit: BodyEdit) throws {
        try assertReadableBodyRecovery(edit.day)
        try validateBody(edit, day: edit.day)
        guard try !hasBodyOperation(edit.day), try retainedBodyMerge(edit.day) == nil else {
            throw JournalError.pendingOperation
        }
        let retained = try retainedBodyEdit(edit.day)
        if retained == nil, let archived: BodyEdit = try decode(bodyURL("revisions", edit.id.uuidString)), archived == edit { return }
        guard retained == edit else { throw JournalError.operationMismatch }
        try archiveBody(edit)
        try files.remove(bodyURL("drafts", edit.day.value), purpose: .bodyDraft)
    }

    /// A previously opened file disappearing is not a new-date template preview.
    public func refreshBodySnapshot(_ day: JournalDay, sourceID: String?, previouslySaved: Bool) throws -> JournalSnapshot {
        guard try bodySourceID(day) == sourceID else { throw OriginalVaultError.changedIdentity }
        let value = try open(day)
        guard !previouslySaved || value.isSaved else { throw OriginalVaultError.unavailable }
        guard try bodySourceID(day) == sourceID else { throw OriginalVaultError.changedIdentity }
        return value
    }

    public func commitBodyEdit(_ edit: BodyEdit) throws {
        try assertNoAIResultPending(edit.day)
        try assertBodyNotHeldByCloud(edit)
        guard permitsBodyWriting else { throw OriginalVaultError.readOnly }
        try validateBody(edit, day: edit.day)
        try validateLiveBodySource(edit)
        try assertReadableBodyRecovery(edit.day)
        if let resolution: BodyResolution = try decode(bodyURL("resolutions", edit.day.value)),
           resolution.next.id != edit.id,
           ([resolution.conflict.edit] + (resolution.superseded ?? [])).contains(where: { $0.id == edit.id }) {
            throw JournalError.operationMismatch
        }
        if let receipt: BodyReceipt = try decode(bodyURL("receipts", edit.id.uuidString)) {
            guard receipt.edit == edit, receipt.supersededBy == nil else { throw JournalError.operationMismatch }
            let current = try readBodySource(edit.day)
            guard current == edit.content else { try retainBodyConflict(edit, source: current); throw JournalError.changedSource }
            try cleanBody(edit); return
        }
        if let pending = try pendingBodyEdit(edit.day) {
            guard pending == edit else { throw JournalError.pendingOperation }
            try finishBody(pending); return
        }
        try assertBodyDestination(edit.day)
        // The transaction and the current input are independent owners. Recovery may
        // finish an older confirmation while a newer buffer is already on disk.
        if try retainedBodyEdit(edit.day) == nil { try retainBodyEdit(edit) }
        let current = try readBodySource(edit.day)
        guard current == edit.before else { try retainBodyConflict(edit, source: current); throw JournalError.changedSource }
        try BodyMarkdown.validatePreservation(before: edit.before, after: edit.content)
        try archiveBody(edit)
        try files.write(encoder.encode(edit), to: bodyURL("transactions", edit.day.value), purpose: .bodyTransaction)
        try finishBody(edit)
    }

    @discardableResult public func recoverBodyEdit(_ day: JournalDay) throws -> BodyEdit? {
        try assertReadableBodyRecovery(day)
        let resolved = try recoverBodyResolution(day)
        guard let pending = try pendingBodyEdit(day) ?? resolved else { return nil }
        try commitBodyEdit(pending)
        return pending
    }

    public func bodyConflict(_ day: JournalDay) throws -> BodyConflict? {
        guard let edit = try retainedBodyEdit(day) ?? pendingBodyEdit(day) else { return nil }
        try validateLiveBodySource(edit)
        let source = try readBodySource(day)
        if original != nil, edit.before != nil, source == nil { throw OriginalVaultError.unavailable }
        try archiveBody(BodyEdit(day: day, before: source, content: edit.content, sourceID: edit.sourceID))
        return BodyConflict(edit: edit, source: source)
    }

    public func retainedBodyMerge(_ day: JournalDay) throws -> BodyMergeDraft? {
        let merge: BodyMergeDraft? = try decode(bodyURL("merges", day.value))
        if let merge {
            try validateBody(merge.conflict.edit, day: day)
            guard String(data: merge.content, encoding: .utf8) != nil,
                  merge.conflict.source.map({ String(data: $0, encoding: .utf8) != nil }) ?? true else { throw JournalError.invalidMarkdown }
        }
        return merge
    }

    public func retainBodyMerge(_ merge: BodyMergeDraft) throws {
        try assertReadableBodyRecovery(merge.conflict.edit.day)
        guard String(data: merge.content, encoding: .utf8) != nil else { throw JournalError.invalidMarkdown }
        let previous = try retainedBodyMerge(merge.conflict.edit.day)
        var ids = Set<UUID>()
        let prior = ((merge.priorEdits ?? []) + (previous?.priorEdits ?? []) + (previous.map { [$0.conflict.edit] } ?? []))
            .filter { $0.id != merge.conflict.edit.id && ids.insert($0.id).inserted }
        let retained = BodyMergeDraft(conflict: merge.conflict, content: merge.content, priorEdits: prior.isEmpty ? nil : prior)
        try files.write(encoder.encode(retained), to: bodyURL("merges", merge.conflict.edit.day.value), purpose: .bodyMerge) {
            try self.assertReadableBodyRecovery(merge.conflict.edit.day)
        }
    }

    /// An explicit successful version choice may retire its exact reviewed merge,
    /// after archiving the handwritten content. Uncertain writes keep the merge.
    public func retireBodyMerge(_ merge: BodyMergeDraft) throws {
        let day = merge.conflict.edit.day
        try assertReadableBodyRecovery(day)
        guard try !hasBodyOperation(day) else { throw JournalError.pendingOperation }
        guard let retained = try retainedBodyMerge(day) else { return }
        guard retained == merge else { throw JournalError.operationMismatch }
        try archiveBody(BodyEdit(day: day, before: merge.conflict.source, content: merge.content, sourceID: merge.conflict.edit.sourceID))
        try files.remove(bodyURL("merges", day.value), purpose: .bodyMerge)
    }

    /// A durable resolution owns the new identity before any old transaction is superseded.
    public func resolveBodyConflict(_ conflict: BodyConflict, content: Data) throws -> BodyEdit {
        guard permitsBodyWriting else { throw OriginalVaultError.readOnly }
        try validateLiveBodySource(conflict.edit)
        let day = conflict.edit.day
        try assertReadableBodyRecovery(day)
        let previous: BodyResolution? = try decode(bodyURL("resolutions", day.value))
        if let previous, previous.conflict == conflict, previous.next.content == content {
            try authorizeBodyAfterCloud(previous.next)
            try finishBodyResolution(previous)
            return previous.next
        }
        guard try readBodySource(day) == conflict.source,
              try retainedBodyEdit(day) == conflict.edit else { throw JournalError.changedSource }
        if original != nil, conflict.edit.before != nil, conflict.source == nil { throw OriginalVaultError.unavailable }
        try assertBodyDestination(day)
        try BodyMarkdown.validatePreservation(before: conflict.source, after: content)
        let next = BodyEdit(day: day, before: conflict.source, content: content, sourceID: conflict.edit.sourceID)
        if let previous {
            // Keep the full abandoned confirmation, including its reviewed source and intended identity.
            try files.write(encoder.encode(previous), to: bodyURL("resolution-history", previous.next.id.uuidString), purpose: .bodyRevision)
            try archiveBody(previous.next)
            try archiveBody(previous.conflict.edit)
        }
        let merge = try retainedBodyMerge(day)
        let superseded = [conflict.edit] + (merge?.priorEdits ?? []) + (previous.map { ($0.superseded ?? []) + [$0.conflict.edit, $0.next] } ?? [])
        let resolution = BodyResolution(conflict: conflict, next: next, superseded: superseded)
        try files.write(encoder.encode(resolution), to: bodyURL("resolutions", day.value), purpose: .bodyResolution)
        try authorizeBodyAfterCloud(next)
        try finishBodyResolution(resolution)
        return next
    }

    private func recoverBodyResolution(_ day: JournalDay) throws -> BodyEdit? {
        guard let value: BodyResolution = try decode(bodyURL("resolutions", day.value)) else { return nil }
        try assertBodyNotHeldByCloud(value.next)
        // The final commit can already have replaced the source; its own receipt verifies it.
        if try files.read(bodyURL("receipts", value.next.id.uuidString)) != nil { return value.next }
        try finishBodyResolution(value)
        return value.next
    }

    private func finishBodyResolution(_ value: BodyResolution) throws {
        try validateLiveBodySource(value.next)
        let day = value.next.day
        let current = try readBodySource(day)
        let transaction: BodyEdit? = try decode(bodyURL("transactions", day.value))
        guard current == value.conflict.source || (current == value.next.content && transaction?.id == value.next.id) else {
            throw JournalError.changedSource
        }
        try assertBodyDestination(day)
        try archiveBody(value.conflict.edit)
        try archiveBody(value.next)
        var retired = Set<UUID>()
        for old in [value.conflict.edit] + (value.superseded ?? []) where retired.insert(old.id).inserted {
            try archiveBody(old)
            let outcome = BodyReceipt(edit: old, supersededBy: value.next.id)
            try files.write(encoder.encode(outcome), to: bodyURL("receipts", old.id.uuidString), purpose: .bodyReceipt)
        }
        // Read the raw transaction: a superseded receipt never erases the replacement intent.
        if let pending: BodyEdit = try decode(bodyURL("transactions", day.value)), pending.id != value.next.id {
            try archiveBody(pending)
            let outcome = BodyReceipt(edit: pending, supersededBy: value.next.id)
            try files.write(encoder.encode(outcome), to: bodyURL("receipts", pending.id.uuidString), purpose: .bodyReceipt)
            try files.remove(bodyURL("transactions", day.value), purpose: .bodyTransaction)
        }
        let retained = try retainedBodyEdit(day)
        if retained == nil || retained == value.conflict.edit || retained == value.next { try retainBodyEdit(value.next) }
    }

    public func bodyRevisions(_ day: JournalDay) throws -> [BodyEdit] {
        try files.list(state.appendingPathComponent("body-revisions")).compactMap { url -> BodyEdit? in
            guard url.pathExtension == "json", let value: BodyEdit = try decode(url), value.day == day else { return nil }
            try validateBody(value, day: day)
            return value
        }.sorted { $0.createdAt < $1.createdAt }
    }

    func assertNoBodyPending(_ day: JournalDay) throws {
        try assertNoAIResultPending(day)
        if try hasBodyRecovery(day) { throw JournalError.pendingOperation }
    }

    /// Includes cleanup-only records and dismissed merge input, even without a buffer.
    public func hasBodyRecovery(_ day: JournalDay) throws -> Bool {
        for folder in ["drafts", "transactions", "resolutions", "merges"] {
            if try files.read(bodyURL(folder, day.value)) != nil { return true }
        }
        return false
    }

    public func hasBodyOperation(_ day: JournalDay) throws -> Bool {
        try files.read(bodyURL("transactions", day.value)) != nil || files.read(bodyURL("resolutions", day.value)) != nil
    }

    func bodyRecoveryDays() throws -> Set<JournalDay> {
        var days = Set<JournalDay>()
        for folder in ["drafts", "transactions", "resolutions", "merges"] {
            for url in try files.list(state.appendingPathComponent("body-\(folder)")) where url.pathExtension == "json" {
                if let day = try? JournalDay(url.deletingPathExtension().lastPathComponent) { days.insert(day) }
            }
        }
        return days
    }

    private func assertBodyDestination(_ day: JournalDay) throws {
        try assertNoVoiceInsertion(day)
        try assertNoWorkflowPending(.daily(day))
        if let transaction = try pendingTransaction(for: day) {
            // Completed append cleanup belongs to the append operation, even without its draft.
            if try files.read(receiptURL(transaction.draft.operationID)) != nil { _ = try commit(transaction.draft) }
            else { throw JournalError.pendingOperation }
        }
        if let draft = try readDraft(day), try hasOperation(draft.operationID) { throw JournalError.pendingOperation }
    }

    private func finishBody(_ edit: BodyEdit) throws {
        try assertBodyDestination(edit.day)
        let current = try readBodySource(edit.day)
        if current != edit.content {
            guard current == edit.before else { try retainBodyConflict(edit, source: current); throw JournalError.changedSource }
            do { try writeBodySource(edit, before: current) }
            catch {
                if let latest = try? readBodySource(edit.day), latest != current, latest != edit.content { try? retainBodyConflict(edit, source: latest) }
                throw error
            }
        }
        let verified = try readBodySource(edit.day)
        guard verified == edit.content else { try retainBodyConflict(edit, source: verified); throw JournalError.changedSource }
        let receipt = BodyReceipt(edit: edit, supersededBy: nil)
        try files.write(encoder.encode(receipt), to: bodyURL("receipts", edit.id.uuidString), purpose: .bodyReceipt)
        try cleanBody(edit)
    }

    private func cleanBody(_ edit: BodyEdit) throws {
        if try retainedBodyEdit(edit.day)?.id == edit.id {
            try files.remove(bodyURL("drafts", edit.day.value), purpose: .bodyDraft)
        }
        if let resolution: BodyResolution = try decode(bodyURL("resolutions", edit.day.value)), resolution.next.id == edit.id {
            if let merge = try retainedBodyMerge(edit.day), merge.conflict == resolution.conflict, merge.content == edit.content {
                try files.remove(bodyURL("merges", edit.day.value), purpose: .bodyMerge)
            }
            try files.remove(bodyURL("resolutions", edit.day.value), purpose: .bodyResolution)
        }
        if try pendingBodyEdit(edit.day)?.id == edit.id {
            try files.remove(bodyURL("transactions", edit.day.value), purpose: .bodyTransaction)
        }
    }

    private func pendingBodyEdit(_ day: JournalDay) throws -> BodyEdit? {
        guard let edit: BodyEdit = try decode(bodyURL("transactions", day.value)) else { return nil }
        try validateBody(edit, day: day)
        if let receipt: BodyReceipt = try decode(bodyURL("receipts", edit.id.uuidString)), receipt.supersededBy != nil {
            guard receipt.edit == edit else { throw JournalError.operationMismatch }
            throw JournalError.pendingOperation
        }
        return edit
    }

    private func retainBodyConflict(_ edit: BodyEdit, source: Data?) throws {
        if try retainedBodyEdit(edit.day) == nil { try retainBodyEdit(edit) }
        try archiveBody(edit)
        try archiveBody(BodyEdit(day: edit.day, before: source, content: edit.content, sourceID: edit.sourceID))
    }

    private func archiveBody(_ edit: BodyEdit) throws {
        let url = bodyURL("revisions", edit.id.uuidString)
        if let existing: BodyEdit = try decode(url) {
            guard existing == edit else { throw JournalError.operationMismatch }; return
        }
        try files.write(encoder.encode(edit), to: url, purpose: .bodyRevision)
    }

    private func validateBody(_ edit: BodyEdit, day: JournalDay) throws {
        if let original { try original.validateRetainedSource(edit.sourceID, day: day) }
        else if edit.sourceID != nil { throw OriginalVaultError.changedIdentity }
        guard edit.day == day, String(data: edit.content, encoding: .utf8) != nil,
              edit.before.map({ String(data: $0, encoding: .utf8) != nil }) ?? true else { throw JournalError.invalidMarkdown }
    }

    private func validateLiveBodySource(_ edit: BodyEdit) throws {
        guard try edit.sourceID == bodySourceID(edit.day) else { throw OriginalVaultError.changedIdentity }
    }

    private func bodyURL(_ folder: String, _ key: String) -> URL {
        state.appendingPathComponent("body-\(folder)/\(key).json")
    }
}

/// Metadata is visible as raw text but cannot be accidentally removed by ordinary body editing.
public enum BodyMarkdown {
    /// Native admission uses exactly the same preservation rule as storage, including line boundaries.
    public static func permitsEdit(in text: String, range: NSRange, replacement: String) -> Bool {
        guard range.location >= 0, range.length >= 0, NSMaxRange(range) <= (text as NSString).length else { return false }
        let next = (text as NSString).replacingCharacters(in: range, with: replacement)
        return (try? validatePreservation(before: Data(text.utf8), after: Data(next.utf8))) != nil
    }

    public static func headingRanges(in text: String) -> [(MarkdownHeading, NSRange)] {
        guard let headings = try? MarkdownSectionAppender.headings(in: Data(text.utf8)) else { return [] }
        let boundaries = Set(headings.flatMap { [$0.start, $0.end] })
        var mapping: [Int: Int] = [0: 0]
        var bytes = 0, units = 0
        for scalar in text.unicodeScalars {
            bytes += scalar.utf8.count; units += scalar.utf16.count
            if boundaries.contains(bytes) { mapping[bytes] = units }
        }
        return headings.compactMap { heading in
            guard let start = mapping[heading.start], let end = mapping[heading.end] else { return nil }
            return (heading, NSRange(location: start, length: end - start))
        }
    }

    public static func protectedRanges(in text: String) -> [NSRange] {
        let full = NSRange(location: 0, length: (text as NSString).length)
        let patterns = ["\\A(?:\\uFEFF)?---\\r?\\n(?:[\\s\\S]*?\\r?\\n)?(?:---|\\.\\.\\.)(?:\\r?\\n|\\z)",
                        "<!-- riji-record:[\\s\\S]*?-->", "(?m)^[ \t]*<!-- /?riji:ai-discussion-result[^\\r\\n]*-->"]
        return patterns.flatMap { pattern in
            (try? NSRegularExpression(pattern: pattern).matches(in: text, range: full).map(\.range)) ?? []
        }.sorted { $0.location < $1.location }
    }

    public static func validatePreservation(before: Data?, after: Data) throws {
        guard let before else {
            guard !String(decoding: after, as: UTF8.self).contains("riji:ai-discussion-result") else { throw JournalError.protectedMetadata }
            return
        }
        func protected(_ data: Data) -> [Data] {
            let text = String(decoding: data, as: UTF8.self)
            return protectedRanges(in: text).map { Data((text as NSString).substring(with: $0).utf8) }
        }
        func records(_ data: Data) -> [String] {
            String(decoding: data, as: UTF8.self).components(separatedBy: "\n").compactMap { raw in
                let line = raw.hasSuffix("\r") ? String(raw.dropLast()) : raw
                return line.hasPrefix("<!-- riji-record:") && line.hasSuffix(" -->") ? line : nil
            }
        }
        guard protected(before) == protected(after), records(before) == records(after) else { throw JournalError.protectedMetadata }
    }
}
