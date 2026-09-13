import Foundation

/// One actor owns all journal operations for an application process. No backend is involved.
public actor LocalJournalStore {
    public nonisolated let timeZone: TimeZone
    let journals: URL
    let state: URL
    let files: JournalFileSystem
    let encoder: JSONEncoder
    let decoder = JSONDecoder()

    public init(journalDirectory: URL, stateDirectory: URL, timeZone: TimeZone = .current,
                failure: @escaping FileFailureHook = { _, _ in }) throws {
        self.journals = journalDirectory
        self.state = stateDirectory
        let files = JournalFileSystem(failure: failure)
        self.files = files
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        self.encoder = encoder
        try files.createDirectory(journalDirectory)
        try files.createDirectory(stateDirectory)
        for folder in ["drafts", "transactions", "receipts", "workflow-transactions", "workflow-receipts", "workflow-drafts"] {
            try files.createDirectory(stateDirectory.appendingPathComponent(folder, isDirectory: true))
        }
        for folder in ["Templates", "Weekly", "Monthly", "Goals"] {
            try files.createDirectory(journalDirectory.deletingLastPathComponent().appendingPathComponent(folder))
        }
        let zoneURL = stateDirectory.appendingPathComponent("timezone.json")
        if let data = try files.read(zoneURL) {
            guard let name = try? JSONDecoder().decode(String.self, from: data),
                  let saved = TimeZone(identifier: name) else { throw JournalError.storage }
            self.timeZone = saved
        } else {
            try files.write(encoder.encode(timeZone.identifier), to: zoneURL, purpose: .metadata)
            self.timeZone = timeZone
        }
    }

    public func initialDay(now: Date = Date()) throws -> JournalDay {
        if let data = try files.read(state.appendingPathComponent("selection.json")),
           let day = try? decoder.decode(JournalDay.self, from: data) {
            if let draft = try readDraft(day), !draft.text.isEmpty { return day }
            if try pendingTransaction(for: day) != nil { return day }
        }
        return JournalDay(date: now, timeZone: timeZone)
    }

    public func select(_ day: JournalDay) throws {
        try files.write(encoder.encode(day), to: state.appendingPathComponent("selection.json"), purpose: .metadata)
    }

    public func open(_ day: JournalDay) throws -> JournalSnapshot {
        let data = try files.read(diaryURL(day))
        let draft = try readDraft(day)
        let template = try draft?.effectiveTemplate ?? selectedTemplate(.daily).instantiate(day: day)
        guard let markdown = data.map({ String(data: $0, encoding: .utf8) }) ?? template else {
            throw JournalError.invalidMarkdown
        }
        let pending = try draft.map { try files.read(transactionURL($0.operationID)) != nil ||
            files.read(receiptURL($0.operationID)) != nil } ?? false
        return JournalSnapshot(day: day, markdown: markdown, sourceHash: data.map(SourceHash.digest),
                               draft: draft, isPending: pending)
    }

    public func makeDraft(for snapshot: JournalSnapshot, section: String) throws -> JournalDraft {
        JournalDraft(day: snapshot.day, section: section, sourceHash: snapshot.sourceHash,
                     effectiveTemplate: snapshot.draft?.effectiveTemplate ?? snapshot.markdown)
    }

    public func saveDraft(_ draft: JournalDraft) throws {
        try validate(draft)
        if let transaction: JournalTransaction = try decode(transactionURL(draft.operationID)) {
            guard transaction.payloadHash == payloadHash(draft) else { throw JournalError.operationMismatch }
        }
        if let receipt: JournalReceipt = try decode(receiptURL(draft.operationID)) {
            guard receipt.payloadHash == payloadHash(draft) else { throw JournalError.operationMismatch }
            throw JournalError.pendingOperation
        }
        if let previous = try readDraft(draft.day) {
            if previous.operationID == draft.operationID && payloadHash(previous) != payloadHash(draft) { throw JournalError.operationMismatch }
            if previous.operationID != draft.operationID,
               try hasOperation(previous.operationID) { throw JournalError.pendingOperation }
        }
        try select(draft.day)
        try files.write(encoder.encode(draft), to: draftURL(draft.day), purpose: .draft)
    }

    public func commit(_ draft: JournalDraft) throws -> JournalReceipt {
        try assertNoWorkflowPending(.daily(draft.day))
        try validate(draft)
        let hash = payloadHash(draft)
        if let receipt: JournalReceipt = try decode(receiptURL(draft.operationID)) {
            guard receipt.payloadHash == hash else { throw JournalError.operationMismatch }
            try cleanup(draft)
            return receipt
        }
        if let transaction: JournalTransaction = try decode(transactionURL(draft.operationID)) {
            guard transaction.payloadHash == hash else { throw JournalError.operationMismatch }
            return try finish(transaction)
        }
        try saveDraft(draft)
        let before = try files.read(diaryURL(draft.day))
        guard before.map(SourceHash.digest) == draft.sourceHash else { throw JournalError.changedSource }
        let after = try MarkdownSectionAppender.append(text: draft.text, to: draft.section,
                                                      in: before ?? Data(draft.effectiveTemplate.utf8))
        let transaction = JournalTransaction(draft: draft, payloadHash: hash, beforeHash: draft.sourceHash,
                                             afterHash: SourceHash.digest(after), afterContent: after)
        try files.write(encoder.encode(transaction), to: transactionURL(draft.operationID), purpose: .transaction)
        return try finish(transaction)
    }

    /// Recovery only completes a previously persisted, explicitly confirmed operation.
    public func recover(_ day: JournalDay) throws -> JournalReceipt? {
        try recoverWorkflows(target: .daily(day))
        guard let draft = try readDraft(day) else { return try recoverOrphanedTransaction(day) }
        if try files.read(transactionURL(draft.operationID)) != nil || files.read(receiptURL(draft.operationID)) != nil {
            return try commit(draft)
        }
        return nil
    }

    public func hasOperation(_ id: UUID) throws -> Bool {
        try files.read(transactionURL(id)) != nil || files.read(receiptURL(id)) != nil
    }

    public func directory() throws -> [JournalDirectoryEntry] {
        let diaryDays = Set(try files.list(journals).compactMap { url -> JournalDay? in
            guard url.pathExtension == "md" else { return nil }
            return try? JournalDay(url.deletingPathExtension().lastPathComponent)
        })
        var draftDays = Set<JournalDay>()
        for url in try files.list(state.appendingPathComponent("drafts")) where url.pathExtension == "json" {
            guard let day = try? JournalDay(url.deletingPathExtension().lastPathComponent),
                  let draft = try readDraft(day) else { continue }
            let pending = try hasOperation(draft.operationID)
            if !draft.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || pending {
                draftDays.insert(day)
            }
        }
        for url in try files.list(state.appendingPathComponent("transactions")) where url.pathExtension == "json" {
            if let transaction: JournalTransaction = try decode(url) { draftDays.insert(transaction.draft.day) }
        }
        return diaryDays.union(draftDays).sorted(by: >).map {
            JournalDirectoryEntry(day: $0, hasDiary: diaryDays.contains($0), hasDraft: draftDays.contains($0))
        }
    }

    public func exportMarkdown(_ day: JournalDay) throws -> Data {
        guard let data = try files.read(diaryURL(day)) else { throw JournalError.noDiary }
        guard String(data: data, encoding: .utf8) != nil else { throw JournalError.invalidMarkdown }
        return data
    }

    /// Used by tests and sandbox-local export adapters; this never mutates the journal or draft.
    public func exportMarkdown(_ day: JournalDay, to destination: URL) throws {
        try files.write(exportMarkdown(day), to: destination, purpose: .export)
    }

    private func finish(_ transaction: JournalTransaction) throws -> JournalReceipt {
        let draft = transaction.draft
        guard transaction.payloadHash == payloadHash(draft), transaction.beforeHash == draft.sourceHash,
              transaction.afterHash == SourceHash.digest(transaction.afterContent) else { throw JournalError.operationMismatch }
        let current = try files.read(diaryURL(draft.day))
        let currentHash = current.map(SourceHash.digest)
        if currentHash != transaction.afterHash {
            guard currentHash == transaction.beforeHash else { throw JournalError.changedSource }
            try files.write(transaction.afterContent, to: diaryURL(draft.day), purpose: .diary)
        }
        guard try files.read(diaryURL(draft.day)).map(SourceHash.digest) == transaction.afterHash else {
            throw JournalError.changedSource
        }
        let receipt = JournalReceipt(operationID: draft.operationID, payloadHash: transaction.payloadHash,
                                     day: draft.day, afterHash: transaction.afterHash)
        try files.write(encoder.encode(receipt), to: receiptURL(draft.operationID), purpose: .receipt)
        try cleanup(draft)
        return receipt
    }

    private func cleanup(_ draft: JournalDraft) throws {
        if try readDraft(draft.day)?.operationID == draft.operationID {
            try files.remove(draftURL(draft.day), purpose: .draft)
        }
        try files.remove(transactionURL(draft.operationID), purpose: .transaction)
    }

    private func recoverOrphanedTransaction(_ day: JournalDay) throws -> JournalReceipt? {
        guard let transaction = try pendingTransaction(for: day) else { return nil }
        return try commit(transaction.draft)
    }

    private func pendingTransaction(for day: JournalDay) throws -> JournalTransaction? {
        for url in try files.list(state.appendingPathComponent("transactions")) where url.pathExtension == "json" {
            if let transaction: JournalTransaction = try decode(url), transaction.draft.day == day {
                return transaction
            }
        }
        return nil
    }

    private func validate(_ draft: JournalDraft) throws {
        guard !draft.text.contains("<!-- riji-record:") else { throw JournalError.invalidMarkdown }
        guard SourceHash.digest(Data(draft.effectiveTemplate.utf8)) == draft.templateHash else {
            throw JournalError.invalidTemplate
        }
    }

    private func payloadHash(_ draft: JournalDraft) -> String {
        // All stored fields are primitive Codable values; encoding cannot fail.
        SourceHash.digest(try! encoder.encode(draft))
    }

    private func readDraft(_ day: JournalDay) throws -> JournalDraft? { try decode(draftURL(day)) }

    func decode<T: Decodable>(_ url: URL) throws -> T? {
        guard let data = try files.read(url) else { return nil }
        guard let value = try? decoder.decode(T.self, from: data) else { throw JournalError.storage }
        return value
    }

    private func diaryURL(_ day: JournalDay) -> URL { journals.appendingPathComponent("\(day.value).md") }
    private func draftURL(_ day: JournalDay) -> URL { state.appendingPathComponent("drafts/\(day.value).json") }
    private func transactionURL(_ id: UUID) -> URL { state.appendingPathComponent("transactions/\(id.uuidString).json") }
    private func receiptURL(_ id: UUID) -> URL { state.appendingPathComponent("receipts/\(id.uuidString).json") }
}
