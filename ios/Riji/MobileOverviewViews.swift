import SwiftUI
import RijiCore

struct MobileMemoriesView: View {
    @ObservedObject var model: MobileClientModel
    @State private var confirmOrganize = false
    @State private var category = "all"
    var body: some View {
        NavigationStack {
            if model.connected {
                RijiList {
                    Section {
                        if model.overviewUnsupported {
                            Text("此服务尚不支持主题概览，可继续查看原始记忆。").accessibilityIdentifier("mobileOverviewUnsupported")
                        } else if let page = model.overview, !page.isExpired {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(page.stateText).font(.subheadline.weight(.medium)).accessibilityIdentifier("mobileOverviewState")
                                if let date = page.organizedAt {
                                    Text("上次整理：" + overviewDate(date)).font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                                }
                                if model.overviewOffline {
                                    Text("离线副本 · " + overviewDate(page.updatedAt)).font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                                }
                            }
                        } else { Text(model.overviewLoading ? "正在读取主题概览" : "暂无有效概览，请重新读取。") }
                        if let notice = model.overviewNotice { Text(notice).font(.callout).accessibilityIdentifier("mobileOverviewNotice") }
                        MobileOperationView(model: model, target: "memory-overview")
                        if let page = model.overview, !page.isExpired {
                            NavigationLink { MobileMemoryComparisonsView(model: model) } label: {
                                Label("待我判断", systemImage: "checkmark.bubble").font(.subheadline.weight(.medium))
                            }.accessibilityIdentifier("mobileComparisons")
                        }
                    }
                    if let page = model.overview, !page.isExpired {
                        Section("最近依据更新") {
                            Text("仅含已下载概览中的依据更新，不是完整记忆变更日志。")
                                .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                            let recent = page.evidence.filter { $0.scope == "shared" }.sorted { ($0.updatedAt ?? "") > ($1.updatedAt ?? "") }.prefix(2)
                            if recent.isEmpty { Text("当前没有已下载的依据更新。") }
                            MobileOverviewEvidenceRows(model: model, ids: recent.map(\.id), compact: true, showUpdateDate: true)
                            NavigationLink("查看已下载依据更新") { MobileMemoryRecentView(model: model) }
                                .accessibilityIdentifier("mobileRecentMemory")
                        }
                        Section("持续主题") {
                            Picker("分类筛选", selection: $category) {
                                Text("全部主题").tag("all")
                                ForEach(page.categories) { item in Text(item.title).tag(item.id) }
                            }.accessibilityIdentifier("mobileThemeFilter")
                            let summaries = page.themeSummaries.filter { category == "all" || $0.category == category }
                            if summaries.isEmpty {
                                Text(page.nextCursor != nil ? "已下载部分暂无此类主题，可继续读取。" : "暂无有效主题摘要，可查看原始记忆。")
                            }
                        }
                        let summaries = page.themeSummaries.filter { category == "all" || $0.category == category }
                        ForEach(summaries) { item in
                            Section {
                                let featured = item.id == summaries.first?.id
                                NavigationLink { MobileMemorySummaryView(model: model, id: item.id) } label: {
                                    MobileThemeCard(item: item, featured: featured)
                                }.accessibilityIdentifier("mobileSummary-" + item.id)
                                    .accessibilityValue(featured ? "首个主题" : "主题")
                                    .navigationLinkIndicatorVisibility(.hidden)
                                    .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 12))
                                    .listRowBackground(Color(uiColor: featured ? RijiPalette.forestSurface : RijiPalette.warmSurface))
                            }
                        }
                        Section {
                            MobileOverviewMore(model: model)
                        }
                        Section("最近周复盘") {
                            NavigationLink { MobileWeeklyMemoryUnavailableView(model: model) } label: {
                                RijiRow(title: "记忆周复盘", detail: "当前服务尚未提供报告", symbol: "calendar")
                            }.accessibilityIdentifier("mobileWeeklyMemory")
                        }
                        Section {
                            NavigationLink("导师观察 · 分范围查看") { MobileMemoryObservationsView(model: model) }.accessibilityIdentifier("mobileObservations")
                            DisclosureGroup("按原分类查看") {
                                ForEach(page.categories) { category in
                                    NavigationLink(category.title) { MobileMemoryThemeView(model: model, category: category.id, title: category.title) }
                                        .accessibilityIdentifier("mobileTheme-" + category.id)
                                }
                            }
                        }
                        Section("整理状态") {
                            Text(page.organizationText).font(.callout)
                            Text("可整理 \(page.coverage.eligible) · 已覆盖 \(page.coverage.organized) · 未整理 \(page.coverage.unorganized) · 已停止 \(page.coverage.stopped) · 失效 \(page.coverage.invalid)")
                                .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary)).accessibilityIdentifier("mobileOverviewCoverage")
                            Text("以上为服务返回的整理范围，不代表完整日记覆盖。\n读取时间：" + overviewDate(page.updatedAt)).font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                            Button("请求整理") { confirmOrganize = true }.disabled(!model.canOrganizeMemory).accessibilityIdentifier("mobileOrganize")
                        }
                    }
                    Section {
                        NavigationLink("原始记忆") { MobileRawMemoriesView(model: model) }.accessibilityIdentifier("mobileRawMemories")
                        Button("刷新概览") { Task { await model.refreshOverview() } }.accessibilityIdentifier("mobileRefreshOverview")
                        MobileErrorNotice(model: model)
                    }
                }.listSectionSpacing(16).navigationTitle("记忆").navigationBarTitleDisplayMode(.inline)
                    .refreshable { await model.refreshOverview() }
                    .task(id: model.epoch) {
                        while !Task.isCancelled, model.connected {
                            model.expireOverview()
                            await model.refreshOverview()
                            if let operation = model.operation("memory-overview"), operation.unresolved { await model.reconcile(operation) }
                            let active = ["pending", "processing"].contains(model.overview?.jobStatus ?? "")
                            do { try await Task.sleep(nanoseconds: active ? 15_000_000_000 : 60_000_000_000) } catch { return }
                        }
                    }
                    .confirmationDialog("在已授权范围内请求整理？服务按预算分批继续；停止的历史条目保持停止。", isPresented: $confirmOrganize, titleVisibility: .visible) {
                        Button("确认请求整理") { Task { await model.organizeMemory() } }
                    }
            } else { MobileServiceState(model: model, title: "记忆") }
        }.rijiPage().id(model.accountIdentity)
    }
}

private struct MobileThemeCard: View {
    let item: MobileOverviewSummary
    let featured: Bool
    private var secondary: Color { Color(uiColor: featured ? RijiPalette.onForestSecondary : RijiPalette.secondary) }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(item.stateText).font(.caption).foregroundStyle(secondary)
            Text(item.title).font(.title2.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
            Text(item.summary).font(.body).lineSpacing(5).lineLimit(3)
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text("查看认识与依据").fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                Image(systemName: "arrow.right").accessibilityHidden(true)
            }.font(.subheadline.weight(.semibold))
                .foregroundStyle(Color(uiColor: featured ? RijiPalette.onForest : RijiPalette.brand)).padding(.top, 4)
        }.rijiEmphasisSurface(featured: featured)
    }
}

private struct MobileOverviewMore: View {
    @ObservedObject var model: MobileClientModel
    var body: some View {
        if model.overview?.nextCursor != nil {
            Button(model.overviewLoading ? "正在读取" : "读取更多整理结果") { Task { await model.refreshOverview(more: true) } }
                .disabled(model.overviewLoading || model.overviewOffline).accessibilityIdentifier("mobileMoreOverview")
        }
        if model.overviewOffline { Text("离线副本，仅展示已下载部分。联网后可读取更多并核对当前来源。").font(.caption) }
    }
}

private struct MobileMemoryThemeView: View {
    @ObservedObject var model: MobileClientModel
    let category: String
    let title: String
    var body: some View {
        RijiList {
            if let page = model.overview, !page.isExpired {
                let summaries = page.themeSummaries.filter { $0.category == category }
                if summaries.isEmpty { Text(page.nextCursor != nil ? "已读取部分还没有此主题摘要；可继续读取整理结果。" : "此主题暂无可展示的摘要。原始记录仍可查阅。") }
                ForEach(summaries) { item in
                    NavigationLink { MobileMemorySummaryView(model: model, id: item.id) } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(item.title).font(.headline)
                            Text(item.summary).font(.subheadline)
                            Text(item.stateText).font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                        }
                    }.accessibilityIdentifier("mobileSummary-" + item.id)
                }
                MobileOverviewMore(model: model)
            } else { MobileOverviewInvalidView(model: model) }
        }.navigationTitle(title).navigationBarTitleDisplayMode(.inline)
    }
}

private struct MobileOverviewInvalidView: View {
    @ObservedObject var model: MobileClientModel
    var body: some View {
        Text("依据或权限已变化，这条认识暂不展示。纠正草稿仍保留。").accessibilityIdentifier("mobileSummaryInvalid")
        Button("重新读取概览") { Task { await model.refreshOverview() } }
    }
}

private struct MobileMemorySummaryView: View {
    @ObservedObject var model: MobileClientModel
    let id: String
    var body: some View {
        RijiList {
            if let page = model.overview, !page.isExpired, let item = page.summaries.first(where: { $0.id == id }) {
                Section {
                    VStack(alignment: .leading, spacing: 18) {
                        Text(item.stateText).font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                        Text(item.title).font(.title2.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
                        Text(item.summary).font(.body).lineSpacing(7).textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true).accessibilityIdentifier("mobileSummaryText")
                        Text("这是对所列证据的归纳；不代表所有场景或完整日记。同日材料不自动算作独立经历。")
                            .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                    }.padding(.vertical, 10)
                }
                Section("依据 · 选择原始记忆核对或纠正") { MobileOverviewEvidenceRows(model: model, ids: item.evidenceIds) }
                Section {
                    Text("这条认识不准确时，请选择上方依据纠正。服务确认后，关联摘要会重新核对。").font(.callout)
                    Text("主题改名、置顶与演化时间线尚未接入。")
                        .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                }
            } else { MobileOverviewInvalidView(model: model) }
        }.navigationTitle("认识与依据").navigationBarTitleDisplayMode(.inline)
    }
}

private struct MobileOverviewEvidenceRows: View {
    @ObservedObject var model: MobileClientModel
    let ids: [String]
    var compact = false
    var showUpdateDate = false
    var body: some View {
        if let page = model.overview, !page.isExpired {
            ForEach(ids, id: \.self) { id in
                if let item = page.evidence.first(where: { $0.id == id }) {
                    NavigationLink { MobileMemoryDetailView(model: model, id: id) } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(item.content).lineLimit(compact ? 2 : nil)
                            if showUpdateDate {
                                Text(kind(item) + " · 更新于：" + (item.updatedAt.map(overviewDate) ?? "时间未明确"))
                                    .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                            }
                            if !compact {
                                Text(kind(item) + " · 记录时间：" + (item.observedAt.map(overviewDate) ?? "未明确"))
                                    .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                            }
                            if item.certainty == "inferred" { Text("推断内容，尚待验证").font(.caption) }
                            if !compact { Text("核对来源与纠正").font(.caption).foregroundStyle(.tint) }
                        }
                    }.accessibilityIdentifier("mobileOverviewEvidence-" + id)
                }
            }
        }
    }
    private func kind(_ item: MobileMemory) -> String {
        ["goal": "目标", "plan": "计划", "event": "经历记录", "observation": "待验证观察", "preference": "偏好", "state": "阶段状态"][item.kind ?? ""] ?? "原始记忆"
    }
}

private struct MobileMemoryObservationsView: View {
    @ObservedObject var model: MobileClientModel
    @State private var persona = "shared"
    var body: some View {
        RijiList {
            Picker("查看范围", selection: $persona) {
                Text("共享记录中的观察").tag("shared")
                ForEach(model.capabilities?.mentors ?? []) { Text($0.name).tag($0.id) }
            }.accessibilityIdentifier("mobileObservationScope")
            Text("待验证观察保留所属范围，不作为已经确认的共享事实。").font(.caption)
            if let page = model.overview, !page.isExpired {
                let observations = page.observations.filter { ($0.personaId ?? "shared") == persona }
                let summaries = page.mentorSummaries.filter { ($0.personaId ?? "shared") == persona }
                if observations.isEmpty && summaries.isEmpty { Text(page.nextCursor != nil ? "已读取部分还没有此范围的观察；可继续读取整理结果。" : "此范围暂无有依据的观察。") }
                ForEach(observations) { item in
                    Section("待验证观察") {
                        Text(item.summary)
                        Text("适用限制：" + item.limitation).font(.callout)
                        Text("支持依据").font(.caption)
                        MobileOverviewEvidenceRows(model: model, ids: item.supportIds)
                        Text(item.counterIds.isEmpty ? "当前未返回反例，不代表没有反例。" : "反例").font(.caption)
                        MobileOverviewEvidenceRows(model: model, ids: item.counterIds)
                    }
                }
                ForEach(summaries) { item in
                    NavigationLink(item.title) { MobileMemorySummaryView(model: model, id: item.id) }
                }
                MobileOverviewMore(model: model)
            } else { MobileOverviewInvalidView(model: model) }
        }.navigationTitle("导师观察").navigationBarTitleDisplayMode(.inline)
    }
}

private struct MobileMemoryComparisonsView: View {
    @ObservedObject var model: MobileClientModel
    var body: some View {
        RijiList {
            if let page = model.overview, !page.isExpired {
                Text("比较是待复核建议，保留双方记录；不会自动合并或删除。当前连接尚不能接受、拆分或延后结构建议。可先核对下方依据并纠正原始记忆。").font(.caption)
                if page.comparisons.isEmpty { Text(page.nextCursor != nil ? "待复核比较尚未全部读取，可继续读取整理结果。" : "当前没有待复核比较。") }
                ForEach(page.comparisons) { item in
                    Section(item.title) {
                        Text(item.personaId.flatMap { id in model.capabilities?.mentors.first { $0.id == id }?.name } ?? "共享记录").font(.caption)
                        Text(item.reason)
                        MobileOverviewEvidenceRows(model: model, ids: item.evidenceIds)
                    }
                }
                MobileOverviewMore(model: model)
            } else { MobileOverviewInvalidView(model: model) }
        }.navigationTitle("待我判断").navigationBarTitleDisplayMode(.inline)
    }
}

private struct MobileMemoryRecentView: View {
    @ObservedObject var model: MobileClientModel
    var body: some View {
        RijiList {
            if let page = model.overview, !page.isExpired {
                Text("已读取概览依据中的最近更新；不代表全部记忆的变更日志。").font(.caption)
                let rows = page.evidence.sorted { ($0.updatedAt ?? "") > ($1.updatedAt ?? "") }
                if rows.isEmpty { Text("当前概览没有可查看的证据更新。") }
                ForEach(rows) { item in
                    Section(item.updatedAt.map(overviewDate) ?? "更新时间未明确") { MobileOverviewEvidenceRows(model: model, ids: [item.id], compact: true, showUpdateDate: true) }
                }
                MobileOverviewMore(model: model)
            } else { MobileOverviewInvalidView(model: model) }
        }.navigationTitle("已下载依据更新").navigationBarTitleDisplayMode(.inline)
    }
}

private func overviewDate(_ value: String) -> String {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let fractional = formatter.date(from: value)
    formatter.formatOptions = [.withInternetDateTime]
    guard let date = fractional ?? formatter.date(from: value) else { return "时间未明确" }
    return date.formatted(date: .abbreviated, time: .shortened)
}

private struct MobileWeeklyMemoryUnavailableView: View {
    @ObservedObject var model: MobileClientModel
    var body: some View {
        RijiList {
            RijiUnavailable(title: "记忆周复盘尚未提供", detail: "当前服务没有可核验的记忆周报告。这里的报告与原目录中的周记文件分别保存，不能用手动周记冒充 AI 复盘。")
                .accessibilityIdentifier("mobileWeeklyMemoryUnavailable")
            NavigationLink("查看已下载依据更新") { MobileMemoryRecentView(model: model) }
            NavigationLink("查看原始记忆") { MobileRawMemoriesView(model: model) }
        }.navigationTitle("记忆周复盘").navigationBarTitleDisplayMode(.inline)
    }
}
