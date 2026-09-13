import Foundation

extension LocalJournalStore {
    public func northStars() throws -> [NorthStarDefinition] {
        let directory = journals.deletingLastPathComponent().appendingPathComponent("Goals")
        var values = [NorthStarDefinition]()
        for url in try files.list(directory) where url.pathExtension == "md" {
            guard UUID(uuidString: url.deletingPathExtension().lastPathComponent) != nil,
                  let data = try files.read(url) else { continue }
            let records: [NorthStarDefinition] = try records(data, type: "goal")
            guard records.count == 1, let value = records.first, url.lastPathComponent == value.id.uuidString + ".md" else { throw JournalError.storage }
            values.append(value)
        }
        return values.sorted { $0.day == $1.day ? $0.id.uuidString < $1.id.uuidString : $0.day < $1.day }
    }

    public func currentNorthStars() throws -> [NorthStarDefinition] {
        let values = try northStars(); let superseded = Set(values.compactMap(\.previousID))
        return values.filter { !superseded.contains($0.id) }
    }

    public func saveNorthStar(_ goal: NorthStarDefinition) throws {
        let hash = try workflowHash(goal)
        if try retryWorkflow(id: goal.id, payloadHash: hash) { return }
        let required = [goal.name, goal.definition, goal.unit, goal.target, goal.period, goal.stage]
        let provided = required + [goal.measurement, goal.measurementSource].compactMap { $0 }
        guard provided.allSatisfy({ !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && $0.utf8.count <= 8000 }),
              !provided.contains(where: { $0.contains("<!-- riji-record:") }),
              goal.measurement == nil || !(goal.measurementSource ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw JournalError.invalidDefinition
        }
        if let previous = goal.previousID {
            guard try currentNorthStars().contains(where: { $0.id == previous }) else { throw JournalError.changedSource }
        }
        let source = "# 北极星：\(goal.name)\n\n确认日期：\(goal.day.value)\n版本：\(goal.id.uuidString)\n上一版本：\(goal.previousID?.uuidString ?? "无")\n\n口径：\(goal.definition)\n单位：\(goal.unit)\n目标：\(goal.target)\n周期：\(goal.period)\n阶段：\(goal.stage)\n当前测量：\(goal.measurement ?? "尚未记录")\n测量来源及截止时间：\(goal.measurementSource ?? "尚未记录")\n\n\(try record(goal, type: "goal"))\n"
        try commitWorkflow(id: goal.id, payloadHash: hash, target: .goal(goal.id), beforeHash: nil, after: Data(source.utf8))
    }

    public func mitHistory(_ day: JournalDay) throws -> [MITEvent] {
        guard let data = try files.read(workflowURL(.daily(day))) else { return [] }
        let values: [MITEvent] = try records(data, type: "mit")
        guard values.allSatisfy({ $0.day == day }), Set(values.map(\.id)).count == values.count else { throw JournalError.storage }
        return values
    }

    public func currentMIT(_ day: JournalDay) throws -> MITEvent? {
        let values = try mitHistory(day).filter { $0.feedbackFor == nil }
        let replaced = Set(values.compactMap(\.replaces))
        let current = values.filter { !replaced.contains($0.id) }
        guard current.count <= 1 else { throw JournalError.storage }
        return current.first
    }

    public func prepareMIT(_ event: MITEvent, section: String) throws -> MITDraft {
        let snapshot = try open(event.day)
        return MITDraft(event: event, section: section, sourceHash: snapshot.sourceHash,
                        effectiveTemplate: snapshot.draft?.effectiveTemplate ?? snapshot.markdown)
    }

    public func commitMIT(_ draft: MITDraft) throws {
        let event = draft.event; let hash = try workflowHash(draft)
        if try retryWorkflow(id: event.id, payloadHash: hash) { return }
        let current = try currentMIT(event.day)
        if let feedback = event.feedbackFor {
            guard feedback == current?.id, event.status != nil, !event.result.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw JournalError.invalidDefinition }
        } else {
            guard event.replaces == current?.id else { throw JournalError.changedSource }
            guard !event.action.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  !event.criterion.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw JournalError.invalidDefinition }
            if let goal = event.goalID {
                guard try currentNorthStars().contains(where: { $0.id == goal }) else { throw JournalError.changedSource }
            }
        }
        let userValues = [event.action, event.criterion, event.rationale, event.effort, event.result]
        guard userValues.allSatisfy({ $0.utf8.count <= 16_000 && !$0.contains("<!-- riji-record:") }) else { throw JournalError.invalidDefinition }
        let before = try files.read(workflowURL(.daily(event.day)))
        guard before.map(SourceHash.digest) == draft.sourceHash else { throw JournalError.changedSource }
        let content: String
        if event.feedbackFor != nil {
            content = "**MIT 实际反馈 · \(event.day.value) · \(event.status!.rawValue)**\n\(event.result)\n计划版本：\(event.feedbackFor!.uuidString)"
        } else {
            content = "**\(event.replaces == nil ? "确认 MIT" : "更换 MIT（保留旧计划）") · \(event.day.value)**\n行动：\(event.action)\n完成标准：\(event.criterion)\n原因：\(event.rationale.isEmpty ? "手动计划" : event.rationale)\n建议投入：\(event.effort.isEmpty ? "尚未填写" : event.effort)\n北极星版本：\(event.goalID?.uuidString ?? "未关联，手动计划")\n计划版本：\(event.id.uuidString)"
        }
        let after = try MarkdownSectionAppender.append(text: content + "\n" + record(event, type: "mit"), to: draft.section,
                                                      in: before ?? Data(draft.effectiveTemplate.utf8))
        try commitWorkflow(id: event.id, payloadHash: hash, target: .daily(event.day), beforeHash: draft.sourceHash, after: after)
    }

    public func periodSources(_ period: ReviewPeriod) throws -> (sources: [ReviewSource], missing: [JournalDay]) {
        _ = try workflowURL(.review(period))
        var sources = [ReviewSource](); var missing = [JournalDay]()
        for day in period.days(timeZone: timeZone) {
            if let data = try files.read(workflowURL(.daily(day)), maximumBytes: 2_097_152) {
                guard data.count <= 2_097_152, String(data: data, encoding: .utf8) != nil else { throw JournalError.invalidMarkdown }
                sources.append(ReviewSource(day: day, hash: SourceHash.digest(data)))
            } else { missing.append(day) }
        }
        return (sources, missing)
    }

    public func prepareReview(_ period: ReviewPeriod, now: Date = Date(), retaining text: String? = nil) throws -> ReviewDraft {
        let sources = try periodSources(period); let template = try selectedTemplate(period.kind)
        let before = try files.read(workflowURL(.review(period)))
        return ReviewDraft(operationID: UUID(), period: period,
                           text: text ?? template.instantiate(day: period.start, title: period.title), template: template,
                           sources: sources.sources, missingDays: sources.missing, targetHash: before.map(SourceHash.digest),
                           asOf: JournalDay(date: now, timeZone: timeZone), timeZone: timeZone.identifier)
    }

    public func savedReviewDraft(_ period: ReviewPeriod) throws -> ReviewDraft? {
        let url = try reviewDraftURL(period)
        guard let draft: ReviewDraft = try decode(url) else { return nil }
        if let receipt: WorkflowReceipt = try decode(workflowState("receipts", draft.operationID)) {
            guard receipt.payloadHash == (try workflowHash(draft)) else { throw JournalError.operationMismatch }
            try files.remove(url, purpose: .draft)
            return nil
        }
        return draft
    }

    public func recoverReview(_ period: ReviewPeriod) throws { try recoverWorkflows(target: .review(period)) }

    public func retainReviewDraft(_ draft: ReviewDraft) throws {
        let url = try reviewDraftURL(draft.period)
        if let previous: ReviewDraft = try decode(url) {
            if previous.operationID == draft.operationID, try workflowHash(previous) != workflowHash(draft) { throw JournalError.operationMismatch }
            if previous.operationID != draft.operationID,
               try files.read(workflowState("transactions", previous.operationID)) != nil { throw JournalError.pendingOperation }
        }
        try files.write(encoder.encode(draft), to: url, purpose: .draft)
    }

    public func commitReview(_ draft: ReviewDraft) throws {
        let hash = try workflowHash(draft)
        if try retryWorkflow(id: draft.operationID, payloadHash: hash) {
            try cleanupReview(draft); return
        }
        try retainReviewDraft(draft)
        let current = try periodSources(draft.period)
        guard current.sources == draft.sources, current.missing == draft.missingDays,
              try selectedTemplate(draft.period.kind).id == draft.template.id else { throw JournalError.changedSource }
        guard !draft.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              draft.text.utf8.count <= 262_144, !draft.text.contains("<!-- riji-record:") else { throw JournalError.invalidMarkdown }
        let target = WorkflowTarget.review(draft.period); let before = try files.read(workflowURL(target))
        guard before.map(SourceHash.digest) == draft.targetHash else { throw JournalError.changedSource }
        let links = draft.sources.map { "- [\($0.day.value)](../Journals/\($0.day.value).md) · ID \($0.id) · SHA256 \($0.hash)" }.joined(separator: "\n")
        let details = "\n\n---\n本人确认的手动复盘 · 版本 \(draft.operationID.uuidString)\n范围：\(draft.period.start.value) — \(draft.period.end.value) · \(draft.timeZone)\n截止：\(draft.asOf.value) · \(draft.phase.rawValue)\n模板版本：\(draft.template.id)\n依据：本地日记，逐日一次；未使用周报重复计数。\n缺少日记：\(draft.missingDays.map(\.value).joined(separator: "、"))\n\(links.isEmpty ? "本周期没有已保存日记。以下内容为本人手动复盘，未生成事实或数值。" : links)\n\n"
        var after = before ?? Data()
        after.append(Data(((before == nil ? "" : "\n\n---\n\n") + draft.text + details + (try record(draft, type: "review")) + "\n").utf8))
        try commitWorkflow(id: draft.operationID, payloadHash: hash, target: target, beforeHash: draft.targetHash, after: after)
        try cleanupReview(draft)
    }

    private func cleanupReview(_ draft: ReviewDraft) throws {
        let url = try reviewDraftURL(draft.period)
        if let current: ReviewDraft = try decode(url), current.operationID == draft.operationID {
            try files.remove(url, purpose: .draft)
        }
    }

    private func reviewDraftURL(_ period: ReviewPeriod) throws -> URL {
        _ = try workflowURL(.review(period))
        return state.appendingPathComponent("workflow-drafts/\(period.id).json")
    }

    /// Typed forms and confirmation candidates survive relaunch outside saved Markdown/search.
    public func retainForm(_ values: [String: String], name: String) throws {
        try files.write(encoder.encode(values), to: formURL(name), purpose: .draft)
    }
    public func loadForm(_ name: String) throws -> [String: String] { try decode(formURL(name)) ?? [:] }
    private func formURL(_ name: String) throws -> URL {
        guard name.count < 80, !name.isEmpty, name.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-") }) else { throw JournalError.storage }
        return state.appendingPathComponent("workflow-drafts/form-\(name).json")
    }
}
