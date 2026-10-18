import Foundation
import RijiCore

extension MobileClientModel {
    var canCreateGroup: Bool { connected && foreground && capabilities?.capabilities["group_chat"] == true }
    var groupCreationTarget: String { "roundtable-create" }
    var roundtableDraftKey: String { "roundtable:" + (roundtableID ?? "new") }
    var canMutateRoundtable: Bool {
        connected && foreground && !roundtableOffline && roundtable != nil &&
            !busy.contains(roundtableDraftKey) && !hasUnresolved(roundtableDraftKey) &&
            !busy.contains("roundtable-end:" + (roundtableID ?? "")) && !hasUnresolved("roundtable-end:" + (roundtableID ?? ""))
    }
    var canOpenRoundtable: Bool {
        guard let credential else { return false }
        return capabilities?.capabilities["roundtable"] == true || capabilities?.capabilities["group_chat"] == true || roundtable != nil || local?.roundtableSelection != nil
            || local?.drafts.keys.contains(where: { $0.hasPrefix("roundtable:") }) == true
            || (!blockedRoundtableScopes.contains(credential.scope) && (try? roundtableCache.load(credential)) != nil)
    }
    var canSummarizeRoundtable: Bool {
        canMutateRoundtable && (roundtable?.active == true || ["stopped", "waiting_user"].contains(roundtable?.status ?? ""))
    }
    var preferredRoundtableSelection: String? {
        let remembered = local?.roundtableSelection.flatMap { $0.isEmpty ? nil : $0 }
        let preferred = [roundtableID, remembered, roundtableList?.currentId].compactMap { $0 }
        if let identifier = preferred.first(where: { !unavailableRoundtableIDs.contains($0) }) { return identifier }
        let available = (roundtableList?.items ?? []).filter { !unavailableRoundtableIDs.contains($0.id) }
        return available.first(where: { $0.status != "archived" })?.id ?? available.first?.id
    }
    private func dismissUnavailableRoundtable(_ identifier: String) {
        invalidateRoundtable()
        unavailableRoundtableIDs.insert(identifier)
        roundtableID = nil; saveRoundtableSelection(nil)
        roundtableID = preferredRoundtableSelection
        saveRoundtableSelection(roundtableID)
        roundtableLoaded = true
    }
    func invalidateRoundtable() {
        cancelRoundtableReads()
        roundtableCacheNotice = nil
        if let credential {
            do { try roundtableCache.invalidate(credential) }
            catch { blockedRoundtableScopes.insert(credential.scope) }
        }
        roundtable = nil; roundtableOffline = false; materialPreview = nil; resultValidation = nil
    }
    func openRoundtables(more: Bool = false, refreshSelection: Bool = true) async {
        let cursor = more ? roundtableList?.nextCursor : nil
        if more && cursor == nil { return }
        let previous = roundtableList
        if refreshSelection, roundtableID == nil, let selection = local?.roundtableSelection { roundtableID = selection.isEmpty ? nil : selection }
        guard let request = try? context("roundtable-list") else { return }
        do {
            var list: MobileRoundtableList = try await request.3.request(["roundtables"], cursor: cursor)
            _ = try list.validated()
            guard current(request, key: "roundtable-list") else { return }
            if more {
                guard let previous, previous.historyVersion == list.historyVersion,
                      roundtableList?.historyVersion == list.historyVersion else { throw MobileError.server(409, "roundtable_list_changed") }
                let ids = Set(list.items.map(\.id))
                list.items = previous.items.filter { !ids.contains($0.id) } + list.items
            }
            roundtableList = list; roundtableLoaded = true
            recordLatestReplies(Dictionary(uniqueKeysWithValues: list.items.compactMap { item in
                item.lastReplyId.map { ("roundtable:" + item.id, $0) }
            }))
            if refreshSelection, roundtableID == nil {
                if let selection = local?.roundtableSelection { roundtableID = selection.isEmpty ? nil : selection }
                else { roundtableID = list.currentId }
            }
            if refreshSelection && !more && roundtableID != nil { await refreshRoundtable() }
        } catch {
            guard current(request, key: "roundtable-list") else { return }
            if case MobileError.server(409, "roundtable_list_changed") = error {
                roundtableList = nil; await openRoundtables(); return
            }
            if more { show(error); return }
            await roundtableFailure(error, request: request, key: "roundtable-list")
        }
    }
    func selectRoundtable(_ identifier: String?) async {
        cancelRoundtableReads()
        roundtable = nil; roundtableOffline = false; materialPreview = nil; resultValidation = nil; roundtableCacheNotice = nil
        saveRoundtableSelection(identifier); roundtableID = identifier; roundtableLoaded = true
        if identifier != nil { await refreshRoundtable() }
    }
    func refreshRoundtable(older: Bool = false, restoreReading: Bool = true) async {
        guard let identifier = roundtableID, let request = try? context("roundtable-read") else { return }
        let cursor = older ? roundtable?.nextCursor : nil
        if older && cursor == nil { return }
        let old = roundtable
        do {
            let page: MobileRoundtable = try await request.3.request(["roundtables", identifier], cursor: cursor)
            _ = try page.validated()
            guard current(request, key: "roundtable-read"), roundtableID == identifier, page.conversationId == identifier else { return }
            var value = page
            if older {
                guard old?.historyVersion == page.historyVersion, roundtable?.historyVersion == page.historyVersion else {
                    throw MobileError.server(409, "conversation_page_changed")
                }
                let ids = Set(page.messages.map(\.id))
                value.messages += (old?.messages ?? []).filter { !ids.contains($0.id) }
            } else {
                do {
                    try roundtableCache.save(page, credential: request.2)
                    blockedRoundtableScopes.remove(request.2.scope); roundtableCacheNotice = nil
                } catch {
                    blockedRoundtableScopes.insert(request.2.scope)
                    roundtableCacheNotice = "已读取最新记录；离线副本未能保存。"
                }
                if old?.historyVersion == page.historyVersion, old?.messages.count ?? 0 > page.messages.count {
                    value.messages = old!.messages; value.nextCursor = old!.nextCursor
                }
            }
            unavailableRoundtableIDs.remove(identifier)
            roundtable = value; roundtableOffline = false; roundtableLoaded = true
            if !older, let latest = value.lastReplyId ?? value.messages.last(where: { $0.actor != "user" })?.id {
                recordLatestReplies(["roundtable:" + identifier: latest])
            }
            if old?.historyVersion != value.historyVersion { invalidateMaterialPreview(); invalidateRoundtableResult() }
            if !older && restoreReading { await restoreRoundtableReadingPosition(identifier) }
        } catch {
            guard current(request, key: "roundtable-read"), roundtableID == identifier else { return }
            if !older, case MobileError.server(404, _) = error {
                dismissUnavailableRoundtable(identifier); show(error); return
            }
            if case MobileError.server(409, "conversation_page_changed") = error {
                invalidateRoundtable(); await refreshRoundtable(restoreReading: false); return
            }
            await roundtableFailure(error, request: request, key: "roundtable-read")
        }
    }
    private func roundtableFailure(_ error: Error, request: (UUID, UUID, MobileCredential, MobileAPI), key: String) async {
        let fallback: Bool
        switch error {
        case MobileError.transport: fallback = true
        case MobileError.server(let status, _): fallback = status >= 500
        default: fallback = false
        }
        if fallback, key == "roundtable-list", !blockedRoundtableScopes.contains(request.2.scope) {
            roundtableList = try? roundtableCache.cachedList(request.2)
        }
        if fallback, !blockedRoundtableScopes.contains(request.2.scope),
           let cached = try? roundtableCache.load(request.2, conversationID: roundtableID), roundtableID == nil || roundtableID == cached.conversationId {
            roundtable = cached; roundtableID = cached.conversationId; roundtableOffline = true; roundtableLoaded = true
        } else if fallback {
            roundtable = nil; roundtableLoaded = true; roundtableOffline = true
        } else {
            invalidateRoundtable()
            roundtableLoaded = true; roundtableOffline = fallback
        }
        show(error)
    }
    func maintainRoundtable(now: Date = Date()) async {
        guard foreground else { return }
        guard let value = roundtable else { return }
        if roundtableOffline {
            if value.cacheExpiresAt <= now.timeIntervalSince1970 {
                invalidateRoundtable(); roundtableOffline = true
            }
        } else if value.active || value.cacheExpiresAt <= now.timeIntervalSince1970 + 30 {
            await refreshRoundtable()
            if let current = roundtable, current.cacheExpiresAt <= now.timeIntervalSince1970 {
                invalidateRoundtable(); roundtableOffline = true
            }
        }
    }
    func sendRoundtable(statement: String = "user_statement", conversationID: String? = nil) async {
        guard conversationID == nil || conversationID == roundtableID else { return }
        guard !draft(roundtableDraftKey).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              roundtableLoaded, !roundtableOffline else { return }
        let text = draft(roundtableDraftKey)
        if let identifier = roundtableID {
            guard canMutateRoundtable, let value = roundtable, value.status != "archived" else { return }
            if value.isGroup && !["completed", "partial", "stopped"].contains(value.status) { return }
            var body: [String: MobileJSON] = ["text": .string(text),
                "expected_revision": .number(Double(value.revision)), "statement_kind": .string(statement)]
            if value.isGroup { body["reply_personas"] = .array(groupResponders(text, members: value.members).map(MobileJSON.string)) }
            await mutate(MobilePendingOperation(kind: "roundtable_message", target: roundtableDraftKey,
                path: ["roundtables", identifier, "messages"], body: body))
        } else {
            await mutate(MobilePendingOperation(kind: "roundtable_create", target: roundtableDraftKey,
                path: ["roundtables"], body: ["text": .string(text), "statement_kind": .string(statement)]))
        }
    }
    func groupResponders(_ text: String, members: [String]) -> [String] {
        let names = ["gentle_reviewer": "温柔回顾者", "blunt_coach": "直率教练", "future_self": "未来的我", "wang_yangming": "王阳明"]
        return members.filter { persona in
            guard let name = names[persona] else { return false }
            let pattern = "[@＠]" + NSRegularExpression.escapedPattern(for: name)
            return text.range(of: pattern, options: .regularExpression) != nil
        }
    }
    func createGroup(personas: [String]) async -> String? {
        let allowed = Set(capabilities?.mentors.map(\.id) ?? [])
        guard canCreateGroup, (2...4).contains(personas.count), Set(personas).count == personas.count,
              Set(personas).isSubset(of: allowed) else { return nil }
        let operation = MobilePendingOperation(kind: "roundtable_create", target: groupCreationTarget,
            path: ["roundtables"], body: ["chat_kind": .string("group"), "personas": .array(personas.map(MobileJSON.string)), "text": .string("")])
        await mutate(operation)
        guard let receipt = local?.operations.first(where: { $0.id == operation.id }), receipt.state == "completed" else { return nil }
        return receipt.result?["conversation_id"]?.string
    }
    func roundtableCommand(_ action: String, text: String = "", supersedes: [String] = [], conversationID: String? = nil) async {
        guard conversationID == nil || conversationID == roundtableID else { return }
        guard let value = roundtable, connected, foreground, !roundtableOffline,
              ["stop", "end_turn"].contains(action) || canMutateRoundtable else { return }
        var body: [String: MobileJSON] = ["action": .string(action), "expected_revision": .number(Double(value.revision)),
            "text": .string(text), "supersedes": .array(supersedes.map(MobileJSON.string))]
        if action == "start_run" {
            guard let preview = materialPreview, preview.revision == value.revision else { return }
            body["personas"] = .array(preview.personas.map(MobileJSON.string)); body["mode"] = .string(preview.mode)
            body["rounds"] = .number(Double(preview.rounds)); body["preview_hash"] = .string(preview.previewHash)
        }
        let target = action == "stop" ? "roundtable-stop:" + value.conversationId : action == "end_turn" ? "roundtable-end:" + value.conversationId : roundtableDraftKey
        await mutate(MobilePendingOperation(kind: "roundtable_command", target: target,
            path: ["roundtables", value.conversationId, "commands"], body: body))
    }
    func previewMaterials(artifacts: [String], memories: [MobileMemory], personas: [String], mode: String, rounds: Int) async {
        guard canMutateRoundtable, let value = roundtable, let request = try? context("roundtable-materials") else { return }
        materialPreview = nil; materialPreviewError = nil
        do {
            let result: MobileMaterialPreview = try await request.3.request(["roundtables", value.conversationId, "materials", "preview"], body: [
                "expected_revision": .number(Double(value.revision)), "artifact_ids": .array(artifacts.map(MobileJSON.string)),
                "memories": .array(memories.map { .object(["id": .string($0.id), "expected_version": .string($0.version)]) }),
                "personas": .array(personas.map(MobileJSON.string)), "mode": .string(mode), "rounds": .number(Double(rounds))])
            guard current(request, key: "roundtable-materials"), roundtable?.revision == result.revision,
                  roundtableID == value.conversationId else { return }
            guard result.artifactIds == artifacts, result.personas == personas, result.mode == mode, result.rounds == rounds,
                  result.memories.map(\.id) == memories.map(\.id), result.memories.map(\.version) == memories.map(\.version) else { throw MobileError.invalidResponse }
            materialPreview = result
        } catch {
            guard current(request, key: "roundtable-materials") else { return }
            let failure = error as? MobileError ?? .transport
            if failure == .unauthorized || failure == .credentialStorage { show(failure) }
            else { materialPreviewError = failure.localizedDescription }
        }
    }
    @discardableResult
    func previewRoundtableResult(_ artifactIDs: [String], conversationID: String? = nil) async -> MobileResultValidation? {
        guard conversationID == nil || conversationID == roundtableID else { return nil }
        let target = "roundtable-result:" + (roundtableID ?? "")
        guard !busy.contains(target), !hasUnresolved(target) else { return nil }
        invalidateRoundtableResult()
        guard canMutateRoundtable, let value = roundtable else { return nil }
        if value.isGroup && !canSaveGroupResults(artifactIDs) { return nil }
        let operation = MobilePendingOperation(kind: "roundtable_result", target: target,
            path: ["roundtables", value.conversationId, "result-preview"], body: ["artifact_ids": .array(artifactIDs.map(MobileJSON.string))])
        await mutate(operation)
        guard roundtableID == value.conversationId,
              let receipt = local?.operations.first(where: { $0.id == operation.id }), receipt.state == "completed",
              let validation = resultValidation, receipt.result?["result_id"]?.string == validation.resultId,
              validation.provenance.problemId == value.conversationId,
              validation.provenance.artifactIds.sorted() == artifactIDs.sorted() else { return nil }
        return validation
    }
    func canSaveGroupResults(_ identifiers: [String]) -> Bool {
        guard (1...5).contains(identifiers.count), Set(identifiers).count == identifiers.count,
              let messages = roundtable?.messages else { return false }
        let selected = messages.filter { identifiers.contains($0.id) }
        return selected.count == identifiers.count && selected.allSatisfy { $0.actor != "user" && $0.unavailable == nil && !$0.superseded } &&
            Set(selected.map(\.runId)).count == 1 && selected.first?.runId.isEmpty == false
    }
    func groupResultSelection(toggling identifier: String, selected: Set<String>) -> Set<String> {
        if selected.contains(identifier) { return selected.subtracting([identifier]) }
        guard let item = roundtable?.messages.first(where: { $0.id == identifier }), item.actor != "user",
              item.unavailable == nil, !item.superseded else { return selected }
        let previous = (roundtable?.messages ?? []).filter { selected.contains($0.id) }
        if !selected.isEmpty && (previous.count != selected.count || previous.contains(where: { $0.runId != item.runId })) { return selected }
        return selected.count < 5 ? selected.union([identifier]) : selected
    }
    func restoreRoundtableReadingPosition(_ identifier: String, maxPages: Int = 12) async {
        guard roundtableID == identifier, let anchor = local?.roundtableReading?[identifier]?.anchor,
              !restoringRoundtables.contains(identifier) else { return }
        restoringRoundtables.insert(identifier); defer { restoringRoundtables.remove(identifier) }
        let currentEpoch = epoch
        for _ in 0..<maxPages {
            if roundtable?.messages.contains(where: { $0.id == anchor }) == true { roundtableReadingNotice = nil; return }
            guard !Task.isCancelled, foreground, epoch == currentEpoch, roundtableID == identifier,
                  roundtable?.nextCursor != nil, !roundtableOffline else { break }
            let before = roundtable?.messages.count ?? 0
            await refreshRoundtable(older: true, restoreReading: false)
            if (roundtable?.messages.count ?? 0) <= before { break }
        }
        guard epoch == currentEpoch, roundtableID == identifier else { return }
        if roundtable?.messages.contains(where: { $0.id == anchor }) == true { roundtableReadingNotice = nil }
        else if roundtable?.nextCursor == nil && !roundtableOffline {
            roundtableReadingNotice = "已读取当前可用历史，未找到上次阅读的位置。原草稿仍保留。"
        } else { roundtableReadingNotice = "上次阅读位置在更早记录中，尚未恢复。可继续读取，草稿仍保留。" }
    }
    func validateRoundtableResult(_ resultID: String, conversationID: String) async -> MobileResultValidation? {
        guard !roundtableOffline, let request = try? context("roundtable-result-validation") else { return nil }
        resultValidation = nil
        do {
            let value: MobileResultValidation = try await request.3.request(["roundtables", conversationID, "result-validation"], body: ["result_id": .string(resultID)])
            try value.validate()
            guard current(request, key: "roundtable-result-validation"), value.resultId == resultID,
                  value.provenance.problemId == conversationID else { return nil }
            resultValidation = value; return value
        } catch {
            if !Task.isCancelled, current(request, key: "roundtable-result-validation") { show(error) }
            return nil
        }
    }
    func refreshAfterRoundtable(_ operation: MobilePendingOperation) async {
        guard let accepted = local?.operations.first(where: { $0.id == operation.id }) else { return }
        let selected = roundtableID
        if operation.kind == "roundtable_result", selected == nil, operation.path.count > 1 {
            roundtableID = operation.path[1]
        }
        if operation.kind == "roundtable_create", operation.target != groupCreationTarget,
           selected == nil, let identifier = accepted.result?["conversation_id"]?.string { roundtableID = identifier }
        await openRoundtables(refreshSelection: false)
        if roundtableID == selected || selected == nil, roundtableID != nil { await refreshRoundtable() }
        if operation.kind == "roundtable_result", let resultID = accepted.result?["result_id"]?.string, operation.path.count > 1,
           roundtableID == operation.path[1] {
            _ = await validateRoundtableResult(resultID, conversationID: operation.path[1])
        }
    }
}
