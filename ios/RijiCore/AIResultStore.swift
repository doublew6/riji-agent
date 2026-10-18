import Foundation

public struct AIResultPreview: Codable, Equatable, Identifiable, Sendable {
    public let id: UUID
    public let account: String
    public let result: MobileResultValidation
    public let day: JournalDay
    public let sourceID: String?
    public let base: Data
    public let before: Data?
    public let text: String
    public let block: String
    public let after: Data
    public let createdAt: Date
}
public struct AIResultReceipt: Codable, Equatable, Sendable {
    public let operation: UUID
    public let resultID: String
    public let payloadHash: String
    public let blockHash: String
    public let day: JournalDay
}

public enum AIResultMarkdown {
    static func validate(_ preview: AIResultPreview) throws {
        var source = preview.result.provenance; source.savedDate = preview.day.value
        source.editedByUser = source.editedByUser || preview.text != preview.result.text
        var block = try render(preview.text, provenance: source)
        if preview.base.contains(13) { block = block.replacingOccurrences(of: "\n", with: "\r\n") }
        let headings = try MarkdownSectionAppender.headings(in: preview.base).filter { ["🧠 Notes", "Notes"].contains($0.title) }
        guard headings.count == 1, block == preview.block, preview.before == nil || preview.base == preview.before,
              try MarkdownSectionAppender.append(text: block, to: headings[0].title, in: preview.base) == preview.after else { throw JournalError.operationMismatch }
    }
    public static func render(_ text: String, provenance: DiscussionProvenance) throws -> String {
        guard !text.isEmpty, text.count <= 6000 else { throw MobileError.invalidInput }
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase; encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let metadata = String(decoding: try encoder.encode(provenance), as: UTF8.self)
            .replacingOccurrences(of: "<", with: "\\u003c").replacingOccurrences(of: ">", with: "\\u003e")
        let topic = provenance.topic.replacingOccurrences(of: "\r", with: " ").replacingOccurrences(of: "\n", with: " ")
        var lines = ["<!-- riji:ai-discussion-result \(metadata) -->", "> [!note] AI 导师讨论结果",
                     "> 这是 AI 讨论资料；保存不代表采纳建议，也不代表计划已经执行。", "> 问题：\(topic)",
                     "> 讨论时间：\(provenance.recordedAt)；保存日期：\(provenance.savedDate)",
                     "> 参与导师：\(provenance.mentors.joined(separator: "、"))；用户编辑：\(provenance.editedByUser ? "是" : "否")",
                     "> 讨论：\(provenance.discussionId)；摘要版本：\(provenance.summaryVersion)", ">"]
        // Quote every line, including apparent metadata, so user edits cannot inject provenance.
        lines += text.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\r", with: "\n")
            .components(separatedBy: "\n").map { "> " + $0 }
        lines.append("<!-- /riji:ai-discussion-result -->")
        return lines.joined(separator: "\n")
    }
}

extension LocalJournalStore {
    private func aiURL(_ group: String, _ key: String) -> URL { state.appendingPathComponent("ai-\(group)/\(key).json") }
    private func aiKey(account: String, resultID: String) -> String { SourceHash.digest(Data((account + "\n" + resultID).utf8)) }
    public func aiPreview(account: String, resultID: String) throws -> AIResultPreview? {
        try decode(aiURL("previews", aiKey(account: account, resultID: resultID)))
    }
    private func aiPreviewFiles() throws -> [URL] {
        try files.list(state.appendingPathComponent("ai-previews")).filter { url in
            let key = url.deletingPathExtension().lastPathComponent
            return url.pathExtension == "json" && key.count == 64 && key.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
        }
    }
    public func pendingAIResults(account: String? = nil, day: JournalDay? = nil) throws -> [AIResultPreview] {
        try aiPreviewFiles().compactMap { url in
            guard let value: AIResultPreview = try decode(url), account == nil || value.account == account,
                  day == nil || value.day == day else { return nil }
            return value
        }.sorted { $0.createdAt < $1.createdAt }
    }
    public func aiResultReceipt(account: String, resultID: String) throws -> AIResultReceipt? {
        guard let operation: UUID = try decode(aiURL("bindings", aiKey(account: account, resultID: resultID))) else { return nil }
        return try decode(aiURL("receipts", operation.uuidString))
    }
    public func hasAIResultPending(_ day: JournalDay) throws -> Bool {
        if try files.read(aiURL("transactions", day.value)) != nil { return true }
        return try aiPreviewFiles().contains { url in
            guard let preview: AIResultPreview = try decode(url) else { return false }
            return preview.day == day
        }
    }
    func assertNoAIResultPending(_ day: JournalDay) throws {
        if try hasAIResultPending(day) { throw JournalError.pendingOperation }
    }
    public func prepareAIResult(_ result: MobileResultValidation, account: String, day: JournalDay, text: String) throws -> AIResultPreview {
        try result.validate()
        guard !account.isEmpty, permitsBodyWriting else { throw OriginalVaultError.readOnly }
        let key = aiKey(account: account, resultID: result.resultId)
        let operation: UUID = try decode(aiURL("bindings", key)) ?? UUID()
        if let transaction: AIResultPreview = try decode(aiURL("transactions", day.value)), transaction.id != operation { throw JournalError.pendingOperation }
        if let old = try aiPreview(account: account, resultID: result.resultId) {
            if let _: AIResultPreview = try decode(aiURL("transactions", old.day.value)) { throw JournalError.pendingOperation }
        }
        if let _: AIResultReceipt = try decode(aiURL("receipts", operation.uuidString)) { throw JournalError.operationMismatch }
        try assertAIWritable(day)
        for url in try aiPreviewFiles() {
            if let pending: AIResultPreview = try decode(url), pending.day == day, pending.id != operation { throw JournalError.pendingOperation }
        }
        let before = try readBodySource(day)
        let snapshot = try open(day)
        let base = before ?? Data(snapshot.markdown.utf8)
        let sections = try MarkdownSectionAppender.headings(in: base).filter { ["🧠 Notes", "Notes"].contains($0.title) }
        guard sections.count == 1 else { throw sections.isEmpty ? JournalError.missingSection : JournalError.ambiguousSection }
        var provenance = result.provenance; provenance.savedDate = day.value
        provenance.editedByUser = provenance.editedByUser || text != result.text
        var block = try AIResultMarkdown.render(text, provenance: provenance)
        if base.contains(13) { block = block.replacingOccurrences(of: "\n", with: "\r\n") }
        let after = try MarkdownSectionAppender.append(text: block, to: sections[0].title, in: base)
        let preview = AIResultPreview(id: operation, account: account, result: result, day: day, sourceID: try bodySourceID(day),
                                      base: base, before: before, text: text, block: block, after: after, createdAt: Date())
        try files.write(encoder.encode(operation), to: aiURL("bindings", key), purpose: .aiPreview)
        try files.write(encoder.encode(preview), to: aiURL("previews", key), purpose: .aiPreview)
        return preview
    }
    /// Keep the previous preview recoverable until its replacement has been persisted.
    /// A pending transaction is released only when exact source bytes prove no write.
    public func rebuildAIResultPreview(_ result: MobileResultValidation, account: String,
                                       day: JournalDay, text: String) throws -> AIResultPreview {
        try result.validate()
        _ = try AIResultMarkdown.render(text, provenance: result.provenance)
        if let old = try aiPreview(account: account, resultID: result.resultId) {
            if try reconcileAIResult(old) != nil { throw JournalError.operationMismatch }
            if let transaction: AIResultPreview = try decode(aiURL("transactions", old.day.value)) {
                guard transaction == old else { throw JournalError.pendingOperation }
                guard try bodySourceID(old.day) == old.sourceID,
                      try readBodySource(old.day) == old.before else { throw JournalError.changedSource }
                try files.remove(aiURL("transactions", old.day.value), purpose: .aiTransaction)
            }
        }
        return try prepareAIResult(result, account: account, day: day, text: text)
    }
    private func assertAIWritable(_ day: JournalDay) throws {
        guard permitsBodyWriting else { throw OriginalVaultError.readOnly }
        if try hasBodyRecovery(day) || bodyHeldByCloud(day) || cloudWorkPending(day) { throw JournalError.pendingOperation }
        try assertNoVoiceInsertion(day); try assertNoWorkflowPending(.daily(day))
        if try pendingTransaction(for: day) != nil { throw JournalError.pendingOperation }
        if let draft = try readDraft(day), !draft.text.isEmpty { throw JournalError.pendingOperation }
        if try open(day).cloudConflict != nil { throw OriginalVaultError.conflict }
    }
    /// Read-only with respect to the diary. An existing receipt never recreates deleted content.
    public func reconcileAIResult(_ preview: AIResultPreview) throws -> AIResultReceipt? {
        let expected = try aiReceipt(preview)
        if let receipt: AIResultReceipt = try decode(aiURL("receipts", preview.id.uuidString)) {
            guard receipt == expected else { throw JournalError.operationMismatch }
            try cleanAIResult(preview); return receipt
        }
        guard let transaction: AIResultPreview = try decode(aiURL("transactions", preview.day.value)) else { return nil }
        guard transaction == preview, try bodySourceID(preview.day) == preview.sourceID else { throw JournalError.operationMismatch }
        let current = try readBodySource(preview.day)
        if current == preview.before { return nil }
        guard let current else { throw JournalError.changedSource }
        let text = String(decoding: current, as: UTF8.self)
        let exact = text.components(separatedBy: preview.block).count - 1
        let resultMarker = "\"result_id\":\"\(preview.result.resultId)\""
        let markers = text.components(separatedBy: resultMarker).count - 1
        guard current == preview.after || (exact == 1 && markers == 1) else { throw JournalError.changedSource }
        try files.write(encoder.encode(expected), to: aiURL("receipts", preview.id.uuidString), purpose: .aiReceipt)
        try cleanAIResult(preview)
        return expected
    }
    public func confirmAIResult(_ preview: AIResultPreview, account: String, validation: MobileResultValidation) throws -> AIResultReceipt {
        guard preview.account == account else { throw MobileError.unauthorized }
        if let receipt = try reconcileAIResult(preview) { return receipt }
        try validation.validate()
        try AIResultMarkdown.validate(preview)
        guard validation.resultId == preview.result.resultId, validation.binding == preview.result.binding,
              validation.text == preview.result.text, validation.provenance == preview.result.provenance,
              Date().timeIntervalSince(preview.createdAt) <= 1800,
              try aiPreview(account: account, resultID: validation.resultId) == preview,
              try bodySourceID(preview.day) == preview.sourceID else { throw JournalError.operationMismatch }
        try assertAIWritable(preview.day)
        guard try readBodySource(preview.day) == preview.before else { throw JournalError.changedSource }
        if let existing: AIResultPreview = try decode(aiURL("transactions", preview.day.value)), existing != preview { throw JournalError.pendingOperation }
        try files.write(encoder.encode(preview), to: aiURL("transactions", preview.day.value), purpose: .aiTransaction)
        // Use coordinated source replacement only; never register this as an auto-recovered body edit.
        let edit = BodyEdit(day: preview.day, before: preview.before, content: preview.after, sourceID: preview.sourceID)
        if let original { try original.writeAIResult(preview, validation: validation) }
        else {
            try files.write(edit.content, to: diaryURL(preview.day), purpose: .bodyDiary) {
                try validation.validate()
                guard Date().timeIntervalSince(preview.createdAt) <= 1800 else { throw JournalError.operationMismatch }
                guard try self.readBodySource(preview.day) == preview.before else { throw JournalError.changedSource }
            }
        }
        guard let receipt = try reconcileAIResult(preview) else { throw JournalError.storage }
        return receipt
    }
    public func cancelAIResult(_ preview: AIResultPreview) throws {
        if let transaction: AIResultPreview = try decode(aiURL("transactions", preview.day.value)) {
            guard transaction == preview else { throw JournalError.pendingOperation }
            if try reconcileAIResult(preview) != nil { return }
            guard try bodySourceID(preview.day) == preview.sourceID,
                  try readBodySource(preview.day) == preview.before else { throw JournalError.changedSource }
            // Explicit cancellation can release a proven unapplied transaction even after remote revocation.
            try files.remove(aiURL("transactions", preview.day.value), purpose: .aiTransaction)
        }
        if try aiPreview(account: preview.account, resultID: preview.result.resultId) == preview {
            try files.remove(aiURL("previews", aiKey(account: preview.account, resultID: preview.result.resultId)), purpose: .aiPreview)
        }
    }
    private func aiReceipt(_ preview: AIResultPreview) throws -> AIResultReceipt {
        AIResultReceipt(operation: preview.id, resultID: preview.result.resultId, payloadHash: SourceHash.digest(try encoder.encode(preview)),
                        blockHash: SourceHash.digest(Data(preview.block.utf8)), day: preview.day)
    }
    private func cleanAIResult(_ preview: AIResultPreview) throws {
        if let transaction: AIResultPreview = try decode(aiURL("transactions", preview.day.value)), transaction == preview {
            try files.remove(aiURL("transactions", preview.day.value), purpose: .aiTransaction)
        }
        if try aiPreview(account: preview.account, resultID: preview.result.resultId) == preview {
            try files.remove(aiURL("previews", aiKey(account: preview.account, resultID: preview.result.resultId)), purpose: .aiPreview)
        }
    }
}
