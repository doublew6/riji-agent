import Foundation

extension LocalJournalStore {
    func workflowURL(_ target: WorkflowTarget) throws -> URL {
        guard original == nil else { throw OriginalVaultError.readOnly }
        let root = journals.deletingLastPathComponent()
        switch target {
        case .daily(let day): return journals.appendingPathComponent("\(day.value).md")
        case .review(let period):
            let valid = try ReviewPeriod(kind: period.kind, containing: period.start, timeZone: timeZone)
            guard valid == period else { throw JournalError.invalidDay }
            return root.appendingPathComponent("\(period.kind == .weekly ? "Weekly" : "Monthly")/\(period.start.value).md")
        case .template(let kind, let hash):
            guard hash.count == 64, hash.allSatisfy({ "0123456789abcdef".contains($0) }) else { throw JournalError.invalidTemplate }
            return root.appendingPathComponent("Templates/\(kind.rawValue)-\(hash).md")
        case .goal(let id): return root.appendingPathComponent("Goals/\(id.uuidString).md")
        }
    }

    func workflowState(_ folder: String, _ id: UUID) -> URL {
        state.appendingPathComponent("workflow-\(folder)/\(id.uuidString).json")
    }

    func workflowHash<T: Encodable>(_ value: T) throws -> String { SourceHash.digest(try encoder.encode(value)) }

    public func workflowHasOperation(_ id: UUID) throws -> Bool {
        try files.read(workflowState("transactions", id)) != nil || files.read(workflowState("receipts", id)) != nil
    }

    /// Receipt lookup precedes source checks, so an uncertain success remains retryable.
    func retryWorkflow(id: UUID, payloadHash: String) throws -> Bool {
        if let receipt: WorkflowReceipt = try decode(workflowState("receipts", id)) {
            guard receipt.payloadHash == payloadHash else { throw JournalError.operationMismatch }
            try files.remove(workflowState("transactions", id), purpose: .transaction)
            return true
        }
        if let transaction: WorkflowTransaction = try decode(workflowState("transactions", id)) {
            guard transaction.payloadHash == payloadHash else { throw JournalError.operationMismatch }
            try finishWorkflow(transaction)
            return true
        }
        return false
    }

    func commitWorkflow(id: UUID, payloadHash: String, target: WorkflowTarget, beforeHash: String?, after: Data) throws {
        if try retryWorkflow(id: id, payloadHash: payloadHash) { return }
        try assertNoWorkflowPending(target)
        if case .daily(let day) = target {
            try assertNoVoiceInsertion(day)
            try assertNoBodyPending(day)
            if try open(day).isPending || pendingTransaction(for: day) != nil { throw JournalError.pendingOperation }
        }
        let url = try workflowURL(target)
        guard try files.read(url).map(SourceHash.digest) == beforeHash else { throw JournalError.changedSource }
        let transaction = WorkflowTransaction(id: id, payloadHash: payloadHash, target: target, beforeHash: beforeHash,
                                              afterHash: SourceHash.digest(after), after: after)
        try files.write(encoder.encode(transaction), to: workflowState("transactions", id), purpose: .transaction)
        try finishWorkflow(transaction)
    }

    private func finishWorkflow(_ transaction: WorkflowTransaction) throws {
        let url = try workflowURL(transaction.target)
        let current = try files.read(url).map(SourceHash.digest)
        let afterHash = SourceHash.digest(transaction.after)
        guard transaction.afterHash == afterHash else { throw JournalError.operationMismatch }
        if current != afterHash {
            guard current == transaction.beforeHash else { throw JournalError.changedSource }
            try files.write(transaction.after, to: url, purpose: .diary)
        }
        guard try files.read(url).map(SourceHash.digest) == afterHash else { throw JournalError.changedSource }
        let receipt = WorkflowReceipt(id: transaction.id, payloadHash: transaction.payloadHash, afterHash: afterHash)
        try files.write(encoder.encode(receipt), to: workflowState("receipts", transaction.id), purpose: .receipt)
        try files.remove(workflowState("transactions", transaction.id), purpose: .transaction)
    }

    func assertNoWorkflowPending(_ target: WorkflowTarget) throws {
        for url in try files.list(state.appendingPathComponent("workflow-transactions")) where url.pathExtension == "json" {
            if let transaction: WorkflowTransaction = try decode(url), transaction.target == target { throw JournalError.pendingOperation }
        }
    }

    public func recoverWorkflows() throws { try recoverWorkflows(target: nil) }
    func recoverWorkflows(target: WorkflowTarget?) throws {
        for url in try files.list(state.appendingPathComponent("workflow-transactions")) where url.pathExtension == "json" {
            guard let transaction: WorkflowTransaction = try decode(url), target == nil || transaction.target == target else { continue }
            _ = try retryWorkflow(id: transaction.id, payloadHash: transaction.payloadHash)
        }
    }

    public func templates(_ kind: TemplateKind) throws -> [TemplateVersion] {
        try recoverTemplateTransactions(kind)
        var result = [TemplateVersion.initial(kind)]
        let directory = journals.deletingLastPathComponent().appendingPathComponent("Templates")
        for url in try files.list(directory).sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) {
            guard url.lastPathComponent.hasPrefix(kind.rawValue + "-"), url.pathExtension == "md",
                  let data = try files.read(url) else { continue }
            let value = try TemplateVersion(kind: kind, raw: data)
            guard url.lastPathComponent == value.id + ".md" else { throw JournalError.invalidTemplate }
            if !result.contains(where: { $0.id == value.id }) { result.append(value) }
        }
        return result
    }

    public func selectedTemplate(_ kind: TemplateKind) throws -> TemplateVersion {
        guard let id: String = try decode(state.appendingPathComponent("template-\(kind.rawValue).json")) else { return .initial(kind) }
        guard let value = try templates(kind).first(where: { $0.id == id }) else { throw JournalError.invalidTemplate }
        return value
    }

    /// Caller has already displayed raw preview. Import does not silently change the selection.
    public func importTemplate(_ version: TemplateVersion, operationID: UUID) throws {
        guard original == nil else { throw OriginalVaultError.readOnly }
        let validated = try TemplateVersion(kind: version.kind, raw: version.raw)
        let target = WorkflowTarget.template(validated.kind, SourceHash.digest(validated.raw))
        let hash = try workflowHash(validated)
        if try retryWorkflow(id: operationID, payloadHash: hash) { return }
        try recoverWorkflows(target: target)
        let existing = try files.read(workflowURL(target))
        if let existing { guard existing == version.raw else { throw JournalError.changedSource }; return }
        try commitWorkflow(id: operationID, payloadHash: hash, target: target, beforeHash: nil, after: validated.raw)
    }

    private func recoverTemplateTransactions(_ kind: TemplateKind) throws {
        for url in try files.list(state.appendingPathComponent("workflow-transactions")) where url.pathExtension == "json" {
            guard let transaction: WorkflowTransaction = try decode(url),
                  case .template(let savedKind, _) = transaction.target, savedKind == kind else { continue }
            _ = try retryWorkflow(id: transaction.id, payloadHash: transaction.payloadHash)
        }
    }

    public func selectTemplate(_ version: TemplateVersion) throws {
        guard original == nil else { throw OriginalVaultError.readOnly }
        guard try templates(version.kind).contains(where: { $0.id == version.id }) else { throw JournalError.invalidTemplate }
        try files.write(encoder.encode(version.id), to: state.appendingPathComponent("template-\(version.kind.rawValue).json"), purpose: .metadata)
    }

    public func prepareBlankDay(_ day: JournalDay) throws -> BlankDayDraft {
        let snapshot = try open(day)
        // An unsaved day's snapshot already contains its retained draft's effective template.
        // Saved Markdown may contain provenance and must never be validated as an import.
        let template = try selectedTemplate(.daily)
        return BlankDayDraft(operationID: UUID(), day: day, template: template, renderedContent: snapshot.markdown)
    }

    public func createBlankDay(_ draft: BlankDayDraft) throws {
        guard original == nil else { throw OriginalVaultError.readOnly }
        guard draft.template.kind == .daily else { throw JournalError.invalidTemplate }
        let hash = try workflowHash(draft)
        if try retryWorkflow(id: draft.operationID, payloadHash: hash) { return }
        let target = WorkflowTarget.daily(draft.day)
        if let existing = try files.read(workflowURL(target)) {
            guard existing == Data(draft.preview.utf8) else { throw JournalError.changedSource }
            return
        }
        try commitWorkflow(id: draft.operationID, payloadHash: hash, target: target, beforeHash: nil,
                           after: Data(draft.preview.utf8))
    }

    public func readSavedFile(_ file: SavedFile, maximumBytes: Int? = nil) throws -> Data {
        let target: WorkflowTarget
        switch file { case .daily(let day): target = .daily(day); case .review(let period): target = .review(period) }
        guard let data = try files.read(workflowURL(target), maximumBytes: maximumBytes) else { throw JournalError.noDiary }
        guard String(data: data, encoding: .utf8) != nil else { throw JournalError.invalidMarkdown }
        return data
    }

    public func search(_ query: String) throws -> SearchResults {
        let query = String(query.trimmingCharacters(in: .whitespacesAndNewlines).prefix(200))
        guard !query.isEmpty else { return SearchResults(matches: [], scanned: 0, truncated: false, skippedOversized: 0) }
        var candidates = [SavedFile]()
        for entry in try directory() where entry.hasDiary { candidates.append(.daily(entry.day)) }
        for kind in [TemplateKind.weekly, .monthly] {
            let folder = journals.deletingLastPathComponent().appendingPathComponent(kind == .weekly ? "Weekly" : "Monthly")
            for url in try files.list(folder) where url.pathExtension == "md" {
                guard let day = try? JournalDay(url.deletingPathExtension().lastPathComponent),
                      let period = try? ReviewPeriod(kind: kind, containing: day, timeZone: timeZone), period.start == day else { continue }
                candidates.append(.review(period))
            }
        }
        candidates.sort { $0.date == $1.date ? $0.id < $1.id : $0.date > $1.date }
        var matches = [SearchMatch](); var bytes = 0; var scanned = 0; var skippedOversized = 0
        for file in candidates.prefix(2000) {
            let data: Data
            do { data = try readSavedFile(file, maximumBytes: 2_097_152) }
            catch JournalError.scanLimit { skippedOversized += 1; continue }
            bytes += data.count
            if data.count > 2_097_152 || bytes > 20_971_520 { break }
            scanned += 1
            let source = String(decoding: data, as: UTF8.self)
            // Internal provenance is not searched as if it were user prose.
            let visible = source.components(separatedBy: "\n").filter { !$0.hasPrefix("<!-- riji-record:") }.joined(separator: "\n")
            guard file.title.localizedCaseInsensitiveContains(query) || visible.localizedCaseInsensitiveContains(query) else { continue }
            let range = visible.range(of: query, options: [.caseInsensitive, .diacriticInsensitive])
            let start = range.map { visible.index($0.lowerBound, offsetBy: -40, limitedBy: visible.startIndex) ?? visible.startIndex } ?? visible.startIndex
            matches.append(SearchMatch(file: file, snippet: String(visible[start...].prefix(180))))
            if matches.count >= 100 { break }
        }
        return SearchResults(matches: matches, scanned: scanned, truncated: scanned < candidates.count,
                             skippedOversized: skippedOversized)
    }

    func record<T: Encodable>(_ value: T, type: String) throws -> String {
        "<!-- riji-record:\(type):\(try encoder.encode(value).base64EncodedString()) -->"
    }

    func records<T: Decodable>(_ data: Data, type: String) throws -> [T] {
        let prefix = "<!-- riji-record:\(type):"
        return try String(decoding: data, as: UTF8.self).components(separatedBy: "\n").compactMap { rawLine in
            let line = rawLine.hasSuffix("\r") ? String(rawLine.dropLast()) : rawLine
            guard line.hasPrefix(prefix), line.hasSuffix(" -->") else { return nil }
            guard let encoded = Data(base64Encoded: String(line.dropFirst(prefix.count).dropLast(4))),
                  let value = try? decoder.decode(T.self, from: encoded) else { throw JournalError.storage }
            return value
        }
    }
}
