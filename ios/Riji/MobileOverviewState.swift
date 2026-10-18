import Foundation
import RijiCore

extension MobileClientModel {
    func invalidateOverview(cancelRead: Bool = true) {
        overviewExpiryTask?.cancel(); overviewExpiryTask = nil
        if cancelRead { cancelOverviewRead() }
        overview = nil; overviewOffline = false; overviewNotice = "依据或使用范围已变化，请重新读取概览。"
        guard let credential else { return }
        blockedOverviewScopes.insert(credential.scope)
        do { try overviewCache.invalidate(credential) }
        catch { show(MobileError.storage) }
    }
    func expireOverview() {
        guard overview?.isExpired == true else { return }
        invalidateOverview()
        overviewNotice = "这份概览副本已过期，联网后重新读取。"
    }
    func refreshOverview(more: Bool = false) async {
        if capabilities != nil && capabilities?.capabilities["memory_overview"] != true {
            invalidateOverview(); overviewUnsupported = true; return
        }
        if more && overviewLoading { return }
        let cursor = more ? overview?.nextCursor : nil
        if more && (cursor == nil || overviewOffline) { return }
        guard let request = try? context("memory-overview") else { return }
        let previous = overview
        overviewLoading = true
        defer { if current(request, key: "memory-overview") { overviewLoading = false } }
        do {
            let value: MobileMemoryOverview = try await request.3.request(["memory-overview"], cursor: cursor)
            guard current(request, key: "memory-overview") else { return }
            let page = try value.validated()
            if more {
                guard let previous, previous.version == overview?.version,
                      previous.summaries.count + previous.observations.count + previous.comparisons.count < 2000 else { throw MobileError.invalidResponse }
                overview = try previous.appending(page)
            } else {
                // Remove the previous snapshot before caching a replacement. Failed persistence
                // cannot leave a known older version eligible for offline fallback.
                invalidateOverview(cancelRead: false)
                overview = previous.map { page.retainingEntries(from: $0) } ?? page
                if !["permission_denied", "invalid"].contains(page.state) {
                    do { try overviewCache.save(page, credential: request.2); blockedOverviewScopes.remove(request.2.scope) }
                    catch { overviewNotice = "当前结果已读取，但未能保存离线副本。" }
                }
            }
            overviewOffline = false; overviewUnsupported = false
            if !blockedOverviewScopes.contains(request.2.scope) { overviewNotice = nil }
            scheduleOverviewExpiry()
        } catch {
            guard current(request, key: "memory-overview") else { return }
            if case MobileError.server(409, "memory_overview_changed") = error {
                invalidateOverview(); overviewLoading = false
                if more { await refreshOverview(); return }
            } else if case MobileError.server(404, _) = error {
                invalidateOverview(); overviewUnsupported = true
            } else if transientOverviewFailure(error) {
                restoreOverviewCache(request.2)
            } else { invalidateOverview() }
            show(error)
        }
    }
    private func transientOverviewFailure(_ error: Error) -> Bool {
        if error as? MobileError == .transport { return true }
        if case MobileError.server(let status, _) = error { return status >= 500 }
        return false
    }
    private func restoreOverviewCache(_ credential: MobileCredential) {
        overview = nil
        guard !blockedOverviewScopes.contains(credential.scope) else { return }
        do {
            overview = try overviewCache.load(credential)
            overviewOffline = overview != nil
            overviewNotice = overview == nil ? "服务暂不可用，当前没有有效离线副本。" : "离线副本 · 当前无法确认远端变化，仅供阅读。"
            scheduleOverviewExpiry()
        } catch { overviewNotice = "离线副本无法读取，请联网重试。" }
    }
    private func scheduleOverviewExpiry() {
        overviewExpiryTask?.cancel()
        guard let expires = overview?.cacheExpiresAt else { return }
        overviewExpiryTask = Task { [weak self] in
            let delay = max(0, expires - Date().timeIntervalSince1970)
            do { try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000)) } catch { return }
            self?.expireOverview()
        }
    }
    var canOrganizeMemory: Bool {
        connected && foreground && overview?.canOrganize == true && overview?.isExpired == false
            && !overviewOffline && !overviewLoading && !busy.contains("memory-overview") && !hasUnresolved("memory-overview")
    }
    func organizeMemory() async {
        guard canOrganizeMemory, let overview else { return }
        await mutate(MobilePendingOperation(kind: "memory_organize", target: "memory-overview",
            path: ["memory-overview", "actions"], body: ["action": .string("organize"),
            "expected_version": .string(overview.version), "binding": .string(overview.organizeBinding), "acknowledged": .bool(true)]))
    }
}
