import Foundation
import Combine
import RijiCore

@MainActor
final class MobileClientModel: ObservableObject {
    @Published var roundtable: MobileRoundtable?
    @Published var roundtableList: MobileRoundtableList?
    @Published var roundtableID: String?
    @Published var unavailableRoundtableIDs: Set<String> = []
    @Published var roundtableLoaded = false
    @Published var roundtableOffline = false
    @Published var roundtableCacheNotice: String?
    @Published var roundtableReadingNotice: String?
    @Published var mentorReadingNotices: [String: String] = [:]
    var restoringRoundtables: Set<String> = []
    var restoringMentors: Set<String> = []
    @Published var materialPreview: MobileMaterialPreview?
    @Published var materialPreviewError: String?
    @Published var resultValidation: MobileResultValidation?
    let roundtableCache: MobileRoundtableCache
    var blockedRoundtableScopes: Set<String> = []
    @Published private(set) var capabilities: MobileCapabilities?
    @Published private(set) var conversations: [String: MobileConversation] = [:]
    @Published private(set) var olderMessages: [String: [MobileMessage]] = [:]
    @Published private(set) var olderCursors: [String: String] = [:]
    @Published private(set) var historyComplete: Set<String> = []
    @Published private(set) var memories: MobileMemoryPage?
    @Published var overview: MobileMemoryOverview?
    @Published var overviewOffline = false
    @Published var overviewUnsupported = false
    @Published var overviewLoading = false
    @Published var overviewNotice: String?
    let overviewCache: MobileOverviewCache
    var blockedOverviewScopes: Set<String> = []
    var overviewExpiryTask: Task<Void, Never>?
    @Published private(set) var memoryDetails: [String: MobileMemory] = [:]
    @Published private(set) var privacy: MobilePrivacy?
    @Published private(set) var local: MobileLocalState?
    @Published private(set) var error: String? { didSet { recoveryFailureScope = nil } }
    @Published private(set) var credentialError: String?
    @Published private(set) var installationError: String?
    @Published private(set) var credentialRestorationPending = false
    @Published private(set) var unsavedRecovery: [String: MobileLocalState] = [:]
    var recoveryError: String? { credential.flatMap { unsavedRecovery[$0.scope] } == nil ? nil : MobileError.storage.localizedDescription }
    var hasOtherRecovery: Bool { unsavedRecovery.keys.contains { $0 != credential?.scope } }
    @Published private(set) var connectionState: MobileConnectionState = .notConnected
    var connectionStatus: String { connectionState.label }
    @Published private(set) var busy: Set<String> = []
    @Published private(set) var epoch = UUID()
    @Published private(set) var accountIdentity = UUID()
    private(set) var credential: MobileCredential?
    private let credentials: any MobileCredentialStore
    private let persistence: MobileLocalStore
    private let transport: any MobileTransport
    private let installation: (any MobileInstallationSource)?
    private var tickets: [String: UUID] = [:]
    private var savedStates: [String: MobileLocalState] = [:]
    private var recoveryFailureScope: String?
    private var started = false
    @Published private(set) var foreground = true
    /// Internal authorization eligibility, not current network reachability.
    var connected: Bool { credential != nil }
    var serviceConnected: Bool { connectionState == .connected }
    let isSyntheticEnvironment: Bool
    var endpointLabel: String { credential?.endpoint ?? "" }
    #if DEBUG
    /// Only the isolated UI fixture binds this real comparison-presentation event.
    var correctionComparisonDidAppear: ((String) async -> Void)?
    #endif

    init(credentials: any MobileCredentialStore, persistence: MobileLocalStore, transport: any MobileTransport,
         installation: (any MobileInstallationSource)? = nil, isSyntheticEnvironment: Bool = false) {
        self.credentials = credentials; self.persistence = persistence; self.transport = transport
        self.roundtableCache = persistence.roundtableCache
        self.overviewCache = persistence.overviewCache
        self.installation = installation
        self.isSyntheticEnvironment = isSyntheticEnvironment
    }
    func start() async {
        guard !started else { return }; started = true
        await retryConnection()
    }
    func retryConnection() async {
        guard !busy.contains("connection") else { return }
        if connected && !credentialRestorationPending { refreshSavedInstallation() }
        else { retryCredentialRead() }
        guard connected else { return }
        busy.insert("connection"); let current = epoch
        defer { if current == epoch { busy.remove("connection") } }
        connectionState = .connecting
        await refreshCapabilities()
    }
    private func refreshSavedInstallation() {
        do {
            let saved = try readCredential()
            guard saved?.endpoint == credential?.endpoint, saved?.deviceId == credential?.deviceId,
                  saved?.token == credential?.token else { retryCredentialRead(); return }
            importInstallation()
        } catch {
            accountIdentity = UUID(); invalidateVisible(); credential = nil; local = nil
            credentialRestorationPending = true; connectionState = .recoveryRequired
        }
    }
    func pair(endpoint: String, code: String, deviceName: String) async {
        guard !busy.contains("connection") else { return }
        disconnect()
        guard credential == nil, credentialError == nil, !busy.contains("connection") else { return }
        busy.insert("connection"); let current = epoch
        defer { if current == epoch { busy.remove("connection") } }
        do {
            let endpoint = try MobileEndpoint(endpoint)
            guard !code.isEmpty, !deviceName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw MobileError.invalidInput }
            let api = MobileAPI(endpoint: endpoint, token: nil, transport: transport)
            let response: MobilePairing = try await api.request(["pair"], body: ["code": .string(code), "device_name": .string(deviceName)])
            guard current == epoch else { return }
            guard response.apiVersion == 1, MobileEndpoint.validIdentifier(response.deviceId),
                  !response.deviceToken.isEmpty, response.deviceToken.utf8.count <= 4096,
                  !response.deviceToken.contains(where: { $0.isNewline }) else { throw MobileError.invalidResponse }
            let saved = MobileCredential(endpoint: endpoint.origin, deviceId: response.deviceId, token: response.deviceToken)
            let state = try unsavedRecovery[saved.scope] ?? persistence.load(saved)
            if unsavedRecovery[saved.scope] == nil { savedStates[saved.scope] = state }
            try writeCredential(saved); credential = saved; local = state
            await refreshCapabilities()
        } catch {
            guard current == epoch else { return }; show(error)
            if credential == nil, connectionState != .authorizationRequired { connectionState = .notConnected }
        }
    }
    func disconnect() {
        invalidateOverview(); invalidateRoundtable()
        accountIdentity = UUID(); invalidateVisible()
        do {
            try discardPendingInstallation()
            try writeCredential(nil); credential = nil; local = nil; credentialRestorationPending = false; connectionState = .notConnected
        } catch {
            if error is MobileInstallationError { installationError = MobileInstallationError.incomplete.localizedDescription }
            else { show(error) }
        }
    }
    private func discardPendingInstallation() throws {
        guard let installation else { return }
        try MobileInstallation.discard(source: installation)
    }
    /// Re-read local credentials without deleting, pairing, or sending retained requests.
    func retryCredentialRead() {
        let prior = credential
        accountIdentity = UUID(); invalidateVisible(); credentialRestorationPending = true
        credential = nil; local = nil
        do {
            let restored = try readCredential()
            if let prior, prior.scope != restored?.scope || prior.token != restored?.token {
                try overviewCache.invalidate(prior); blockedOverviewScopes.insert(prior.scope)
                try roundtableCache.invalidate(prior); blockedRoundtableScopes.insert(prior.scope)
            }
            if let saved = restored {
                guard (try? MobileEndpoint(saved.endpoint).origin) == saved.endpoint,
                      MobileEndpoint.validIdentifier(saved.deviceId), !saved.token.isEmpty else { throw MobileError.storage }
                let state = try unsavedRecovery[saved.scope] ?? persistence.load(saved)
                if unsavedRecovery[saved.scope] == nil { savedStates[saved.scope] = state }
                credential = saved; local = state
            }
            credentialRestorationPending = false
            importInstallation()
            connectionState = connected ? .connecting : .notConnected
        } catch {
            connectionState = .recoveryRequired
            if error as? MobileError == .credentialStorage { show(error) }
            else { self.error = "本机连接记录无法读取，请恢复文件访问后重新读取设备凭据。已保存草稿与未保存恢复内容仍保留。" }
        }
    }
    private func importInstallation() {
        guard let installation else { return }
        do {
            guard try installation.read() != nil else { return }
            guard credential.flatMap({ unsavedRecovery[$0.scope] }) == nil else { throw MobileInstallationError.incomplete }
            if let selected = try MobileInstallation.apply(source: installation, existing: credential,
                credentials: credentials, persistence: persistence) {
                let state = try persistence.load(selected)
                if selected.scope != credential?.scope { invalidateOverview(); accountIdentity = UUID(); invalidateVisible() }
                credential = selected; local = state; savedStates[selected.scope] = state
            }
            installationError = nil
        } catch {
            try? installation.invalidateReceipt()
            installationError = MobileInstallationError.incomplete.localizedDescription
        }
    }
    private func readCredential() throws -> MobileCredential? {
        do { let saved = try credentials.read(); credentialError = nil; return saved }
        catch { credentialError = MobileError.credentialStorage.localizedDescription; throw MobileError.credentialStorage }
    }
    private func writeCredential(_ value: MobileCredential?) throws {
        do { try credentials.write(value); credentialError = nil }
        catch { credentialError = MobileError.credentialStorage.localizedDescription; throw MobileError.credentialStorage }
    }
    func suspend() { foreground = false; let available = capabilities; invalidateVisible(); capabilities = available }
    func resume() async { foreground = true; await retryConnection() }
    private func invalidateVisible() {
        roundtable = nil; roundtableID = nil; roundtableLoaded = false; roundtableList = nil
        unavailableRoundtableIDs = []
        roundtableReadingNotice = nil; mentorReadingNotices = [:]
        materialPreview = nil; materialPreviewError = nil; resultValidation = nil; roundtableOffline = false
        overview = nil; overviewOffline = false; overviewLoading = false; overviewNotice = nil; overviewUnsupported = false
        epoch = UUID(); tickets = [:]; busy = []
        capabilities = nil; conversations = [:]; memories = nil; memoryDetails = [:]; privacy = nil; error = nil
        olderMessages = [:]; olderCursors = [:]; historyComplete = []
    }
    private func invalidateReads() {
        invalidateRoundtable()
        invalidateOverview()
        tickets = tickets.filter { $0.key.hasPrefix("operation:") }
        conversations = [:]; memories = nil; memoryDetails = [:]
        olderMessages = [:]; olderCursors = [:]; historyComplete = []
    }
    private func invalidateOperationReads(_ operation: MobilePendingOperation) {
        if ["mentor_message", "mentor_control"].contains(operation.kind), operation.path.count > 1 {
            let persona = operation.path[1]
            tickets["conversation:" + persona] = nil
            conversations[persona] = nil; clearHistory(persona); invalidateOverview()
        } else if operation.kind.hasPrefix("roundtable_") {
            if roundtable?.isGroup == true || operation.target == groupCreationTarget {
                cancelRoundtableReads(); invalidateMaterialPreview(); invalidateRoundtableResult()
            } else { invalidateRoundtable() }
            invalidateOverview()
        } else { invalidateReads() }
    }
    func context(_ key: String) throws -> (UUID, UUID, MobileCredential, MobileAPI) {
        guard foreground, let credential else { throw MobileError.unauthorized }
        let ticket = UUID(); tickets[key] = ticket
        return (epoch, ticket, credential, MobileAPI(endpoint: try MobileEndpoint(credential.endpoint), token: credential.token, transport: transport))
    }
    func cancelRoundtableReads() {
        for key in ["roundtable-list", "roundtable-read", "roundtable-materials", "roundtable-result-validation"] { tickets[key] = nil }
    }
    func invalidateMaterialPreview() { tickets["roundtable-materials"] = nil; materialPreview = nil; materialPreviewError = nil }
    func invalidateRoundtableResult() { tickets["roundtable-result-validation"] = nil; resultValidation = nil }
    func cancelOverviewRead() { tickets["memory-overview"] = nil; overviewLoading = false }
    func current(_ context: (UUID, UUID, MobileCredential, MobileAPI), key: String) -> Bool {
        epoch == context.0 && tickets[key] == context.1 && credential?.scope == context.2.scope
    }
    func show(_ failure: Error, recoveryScope: String? = nil) {
        let value = failure as? MobileError ?? .transport
        if value == .credentialStorage { credentialError = value.localizedDescription; return }
        if value == .unauthorized {
            invalidateOverview(); invalidateRoundtable()
            accountIdentity = UUID(); invalidateVisible()
            // Preserve the revoked credential if a stale package cannot be discarded, so it can never become a fresh enrollment.
            do { try discardPendingInstallation(); try writeCredential(nil) }
            catch { installationError = MobileInstallationError.incomplete.localizedDescription }
            credential = nil; local = nil
            connectionState = .authorizationRequired
        }
        noteConnectionFailure(value)
        error = value.localizedDescription
        if value == .storage { recoveryFailureScope = recoveryScope ?? credential?.scope }
    }
    private func clearRecoveryFailure(scope: String) {
        if recoveryFailureScope == scope { error = nil }
    }
    private func noteConnectionFailure(_ failure: MobileError) {
        guard connected else { return }
        switch failure {
        case .transport: connectionState = .offline
        case .invalidResponse: connectionState = .unavailable
        case .server(let status, _) where status >= 500: connectionState = .unavailable
        default: break
        }
    }
    func refreshCapabilities() async {
        guard let request = try? context("capabilities") else { return }
        do {
            let result: MobileCapabilities = try await request.3.request(["capabilities"])
            guard current(request, key: "capabilities") else { return }
            let expected = Set(["gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"])
            guard result.apiVersion == 1, result.device.id == request.2.deviceId,
                  Set(result.mentors.map(\.id)) == expected, result.mentors.count == 4 else { throw MobileError.invalidResponse }
            capabilities = result; connectionState = .connected; error = nil
            recordLatestReplies(Dictionary(uniqueKeysWithValues: result.mentors.compactMap { mentor in
                mentor.lastReplyId.map { ("mentor:" + mentor.id, $0) }
            }))
            if result.capabilities["memory_overview"] != true { invalidateOverview(); overviewUnsupported = true }
            else if result.capabilities["privacy"] != true { invalidateOverview() }
        } catch {
            if current(request, key: "capabilities") {
                capabilities = nil; show(error)
                if connected, connectionState == .connecting || connectionState == .connected {
                    connectionState = .unavailable
                }
            }
        }
    }
    func refreshConversation(_ persona: String) async {
        let key = "conversation:" + persona
        guard let request = try? context(key) else { return }
        do {
            let value: MobileConversation = try await request.3.request(["mentors", persona, "conversation"])
            guard current(request, key: key) else { return }
            guard value.messages.count <= 200, value.messages.allSatisfy({ $0.actor == "user" || $0.actor == persona }) else { throw MobileError.invalidResponse }
            if conversations[persona]?.historyVersion != value.historyVersion { clearHistory(persona) }
            conversations[persona] = value; error = nil
            if let latest = value.lastReplyId ?? value.messages.last(where: { $0.actor != "user" })?.id {
                recordLatestReplies(["mentor:" + persona: latest])
            }
            await restoreMentorReadingPosition(persona)
        } catch { if current(request, key: key) { conversations[persona] = nil; clearHistory(persona); show(error) } }
    }
    private func clearHistory(_ persona: String) {
        olderMessages[persona] = nil; olderCursors[persona] = nil; historyComplete.remove(persona)
        tickets["history:" + persona] = nil; busy.remove("history:" + persona)
    }
    func historyCursor(_ persona: String) -> String? {
        historyComplete.contains(persona) ? nil : olderCursors[persona] ?? conversations[persona]?.nextCursor
    }
    func messages(_ persona: String) -> [MobileMessage] {
        (olderMessages[persona] ?? []) + (conversations[persona]?.messages ?? [])
    }
    func loadOlderMessages(_ persona: String) async {
        let key = "history:" + persona
        guard !busy.contains(key), let cursor = historyCursor(persona), let version = conversations[persona]?.historyVersion,
              let request = try? context(key) else { return }
        busy.insert(key)
        defer { if current(request, key: key) { busy.remove(key) } }
        do {
            let page: MobileConversation = try await request.3.request(["mentors", persona, "conversation"], cursor: cursor)
            guard current(request, key: key), conversations[persona]?.historyVersion == version else { return }
            guard page.historyVersion == version, page.messages.count <= 200,
                  page.messages.allSatisfy({ $0.actor == "user" || $0.actor == persona }) else { throw MobileError.invalidResponse }
            let existing = Set(messages(persona).map(\.id))
            olderMessages[persona] = page.messages.filter { !existing.contains($0.id) } + (olderMessages[persona] ?? [])
            olderCursors[persona] = page.nextCursor
            if page.nextCursor == nil { historyComplete.insert(persona) }
        } catch {
            guard current(request, key: key) else { return }
            clearHistory(persona)
            if case MobileError.server(409, "conversation_page_changed") = error { await refreshConversation(persona) }
            guard epoch == request.0, credential?.scope == request.2.scope else { return }
            show(error)
        }
    }
    func refreshMemories(cursor: String? = nil) async {
        guard let request = try? context("memories") else { return }
        do {
            let value: MobileMemoryPage = try await request.3.request(["memories"], cursor: cursor)
            guard current(request, key: "memories") else { return }
            memories = value; error = nil
        } catch { if current(request, key: "memories") { memories = nil; show(error) } }
    }
    func refreshMemory(_ id: String) async {
        let key = "memory:" + id
        guard let request = try? context(key) else { return }
        do {
            let value: MobileMemory = try await request.3.request(["memories", id])
            guard current(request, key: key) else { return }
            guard value.id == id else { throw MobileError.invalidResponse }
            if let old = overview?.evidence.first(where: { $0.id == id }), old.version != value.version {
                invalidateOverview(); invalidateRoundtable()
            }
            if let old = memoryDetails[id], old.version != value.version { invalidateRoundtable() }
            if value.privacy != "cloud" || value.status != "active" || value.state != "current" { invalidateRoundtable() }
            memoryDetails[id] = value; error = nil
        } catch { if current(request, key: key) {
            memoryDetails[id] = nil
            if case MobileError.server(let code, _) = error, [403, 404, 409].contains(code) { invalidateOverview(); invalidateRoundtable() }
            show(error)
        } }
    }
    func refreshPrivacy() async {
        guard let request = try? context("privacy") else { return }
        do {
            let value: MobilePrivacy = try await request.3.request(["privacy"])
            guard current(request, key: "privacy") else { return }
            if (privacy?.available == true && !value.available) ||
                (privacy != nil && privacy?.permissions != value.permissions) ||
                (value.available && (value.valid != true || value.permissions?.organization != true ||
                (overview != nil && value.binding != overview?.organizeBinding))) { invalidateOverview() }
            if !value.available || (privacy != nil && privacy?.permissions != value.permissions)
                || (value.available && (value.valid != true || value.permissions?.recall != true)) { invalidateRoundtable() }
            privacy = value; error = nil
        } catch { if current(request, key: "privacy") { privacy = nil; show(error) } }
    }
    var roundtableReadingState: MobileRoundtableReadingState? {
        guard let identifier = roundtableID else { return nil }
        return local?.roundtableReading?[identifier]
    }
    func saveRoundtableReadingState(selected: Set<String>, anchor: String?, conversationID: String? = nil) {
        guard conversationID == nil || conversationID == roundtableID else { return }
        guard let identifier = roundtableID, let credential, var state = local else { return }
        var values = state.roundtableReading ?? [:]
        values[identifier] = MobileRoundtableReadingState(selectedIDs: Array(selected).sorted(), anchor: anchor ?? values[identifier]?.anchor)
        if values.count > 100, let key = values.keys.sorted().first(where: { $0 != identifier }) { values[key] = nil }
        state.roundtableReading = values
        do { try persist(state, credential: credential) } catch { show(error) }
    }
    func saveRoundtableSelection(_ identifier: String?) {
        guard let credential, var state = local else { return }
        state.roundtableSelection = identifier ?? ""
        do { try persist(state, credential: credential) } catch { show(error) }
    }
    func saveGroupCreationSelection(_ personas: [String]) {
        guard let credential, var state = local else { return }
        state.groupCreationPersonas = personas
        do { try persist(state, credential: credential) } catch { show(error) }
    }
    func mentorReadingAnchor(_ persona: String) -> String? { local?.mentorReading?[persona] }
    func saveMentorReadingAnchor(_ persona: String, anchor: String?) {
        guard let anchor, let credential, var state = local else { return }
        var anchors = state.mentorReading ?? [:]; anchors[persona] = anchor
        state.mentorReading = anchors
        do { try persist(state, credential: credential) } catch { show(error) }
    }
    func draft(_ key: String) -> String { local?.drafts[key] ?? "" }
    func setDraft(_ key: String, text: String) {
        guard let credential, var state = local else { return }
        if key.hasPrefix("memory:") {
            var versions = state.memoryDraftVersions ?? [:]
            if text.isEmpty { versions[key] = nil }
            else if (state.drafts[key] ?? "").isEmpty { versions[key] = memoryDetails[String(key.dropFirst(7))]?.version }
            state.memoryDraftVersions = versions
        }
        state.drafts[key] = text; local = state
        do { try persist(state, credential: credential) } catch { show(error) }
    }
    func correctionNeedsRebase(_ memory: MobileMemory) -> Bool {
        !draft("memory:" + memory.id).isEmpty && local?.memoryDraftVersions?["memory:" + memory.id] != memory.version
    }
    func rebaseCorrection(_ memory: MobileMemory) {
        guard let credential, var state = local, memoryDetails[memory.id]?.version == memory.version else { return }
        var versions = state.memoryDraftVersions ?? [:]; versions["memory:" + memory.id] = memory.version
        state.memoryDraftVersions = versions
        do { try persist(state, credential: credential) } catch { show(error) }
    }
    private func persist(_ proposed: MobileLocalState, credential: MobileCredential, retainOnFailure: Bool = true) throws {
        var state = proposed; state.compact()
        do {
            try persistence.save(state, credential: credential); local = state; savedStates[state.scope] = state; unsavedRecovery[credential.scope] = nil
            clearRecoveryFailure(scope: state.scope)
        } catch {
            if retainOnFailure { local = state; unsavedRecovery[credential.scope] = state }
            throw MobileError.storage
        }
    }
    func operation(_ target: String) -> MobilePendingOperation? { local?.operations.last { $0.target == target } }
    func hasUnresolved(_ target: String) -> Bool { local?.operations.contains { $0.target == target && $0.unresolved } == true }
    func archives() throws -> MobileArchiveRead { try persistence.archives() }
    func pendingDraftClears(scope: String) -> Set<String> {
        guard let recovery = unsavedRecovery[scope] else { return [] }
        return Set(recovery.drafts.keys.filter { recovery.drafts[$0] == "" && savedStates[scope]?.drafts[$0]?.isEmpty == false })
    }
    func visibleArchives(_ archives: [MobileLocalState]) -> [MobileLocalState] {
        var values = archives.reduce(into: [String: MobileLocalState]()) { $0[$1.scope] = $1 }
        if let state = local { values[state.scope] = state }
        for (scope, state) in unsavedRecovery { values[scope] = state }
        return values.values.filter {
            unsavedRecovery[$0.scope] != nil || !$0.drafts.values.allSatisfy(\.isEmpty)
                || $0.operations.contains { $0.unresolved || $0.hasFailedInput }
        }.sorted { $0.scope < $1.scope }
    }

    @discardableResult func retryRecoverySave(scope: String) -> Bool {
        guard let state = unsavedRecovery[scope] else { return true }
        return saveLocalRecovery(state)
    }
    /// Discard only unsaved changes. Durable drafts and every unrecorded request remain recoverable.
    func discardRecovery(scope: String) {
        guard let recovery = unsavedRecovery[scope] else { return }
        var restored = recovery; restored.drafts = [:]; restored.memoryDraftVersions = [:]; restored.operations = []
        restored = savedStates[scope] ?? restored
        let recorded = Set(restored.operations.map(\.id))
        let extra = recovery.operations.filter { $0.unresolved && !recorded.contains($0.id) }
        restored.operations += extra
        unsavedRecovery[scope] = extra.isEmpty ? nil : restored
        if local?.scope == scope { local = restored }
        clearRecoveryFailure(scope: scope)
    }
    @discardableResult func discardFailedInputs(scope: String, operationID: String? = nil) -> Bool {
        do {
            guard var state = try unsavedRecovery[scope] ?? (local?.scope == scope ? local : nil)
                    ?? persistence.load(scope: scope) else { return false }
            for index in state.operations.indices where state.operations[index].hasFailedInput {
                if operationID == nil || state.operations[index].id == operationID { state.operations[index].body = [:] }
            }
            return saveLocalRecovery(state)
        } catch { if credential?.scope == scope { show(MobileError.storage) }; return false }
    }
    private func saveLocalRecovery(_ proposed: MobileLocalState) -> Bool {
        var state = proposed; state.compact()
        do {
            try persistence.save(state); savedStates[state.scope] = state; unsavedRecovery[state.scope] = nil
            if local?.scope == state.scope { local = state }
            clearRecoveryFailure(scope: state.scope)
            return true
        } catch { if credential?.scope == state.scope { show(MobileError.storage) }; return false }
    }

    func canSend(_ persona: String) -> Bool {
        guard connected, foreground, let conversation = conversations[persona], conversation.acceptsNewMessage else { return false }
        return ["mentor:", "control:", "stop:"].allSatisfy { !busy.contains($0 + persona) && !hasUnresolved($0 + persona) }
    }
    func send(_ persona: String) async {
        let key = "mentor:" + persona, text = draft("mentor:" + persona)
        guard canSend(persona) else { show(MobileError.unresolved); return }
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, text.unicodeScalars.count <= 10_000 else { show(MobileError.invalidInput); return }
        await mutate(MobilePendingOperation(kind: "mentor_message", target: key, path: ["mentors", persona, "messages"], body: ["text": .string(text)]))
    }
    func control(_ persona: String, action: String, revision: Int) async {
        await mutate(MobilePendingOperation(kind: "mentor_control", target: (action == "stop" ? "stop:" : "control:") + persona,
            path: ["mentors", persona, "control"], body: ["action": .string(action), "expected_revision": .number(Double(revision))]))
    }
    func memoryAction(_ memory: MobileMemory, action: String, permission: String? = nil) async {
        var body: [String: MobileJSON] = ["action": .string(action), "expected_version": .string(memory.version)]
        if action == "update" {
            let key = "memory:" + memory.id
            guard !draft(key).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  draft(key).unicodeScalars.count <= 2000 else { show(MobileError.invalidInput); return }
            guard let version = local?.memoryDraftVersions?[key], version == memory.version,
                  memoryDetails[memory.id]?.version == version else { show(MobileError.server(409, "memory_version_changed")); return }
            body["expected_version"] = .string(version); body["content"] = .string(draft(key))
        }
        if action == "delete" { body["confirmation"] = .string("DELETE") }
        if let permission { body["permission"] = .string(permission) }
        await mutate(MobilePendingOperation(kind: "memory_action", target: "memory:" + memory.id, path: ["memories", memory.id, "actions"], body: body))
    }
    func privacyAction(_ action: String, value: MobilePrivacy, permissions: MobilePermissions? = nil) async {
        guard action != "grant" || value.canGrant, let binding = value.binding, value.available else { show(MobileError.invalidInput); return }
        var body: [String: MobileJSON] = ["action": .string(action), "binding": .string(binding)]
        if let permissions { body["acknowledged"] = .bool(true); body["permissions"] = permissions.json }
        await mutate(MobilePendingOperation(kind: "privacy_action", target: ["revoke", "pause"].contains(action) ? "privacy-" + action : "privacy", path: ["privacy", "actions"], body: body))
    }
    func revokeDevice() async {
        await mutate(MobilePendingOperation(kind: "device_revoke", target: "device", path: ["device", "revoke"], body: [:]))
    }
    func mutate(_ operation: MobilePendingOperation) async {
        guard !busy.contains(operation.target), !hasUnresolved(operation.target) else { show(MobileError.unresolved); return }
        guard let request = try? context("operation:" + operation.id), var state = local else { return }
        let prepared: MobilePreparedRequest
        do { prepared = try request.3.preparedRequest(operation.path, body: operation.body) }
        catch { show(error); return }
        state.operations.append(operation)
        do { try persist(state, credential: request.2, retainOnFailure: false) }
        catch { show(error); return }
        invalidateOperationReads(operation)
        busy.insert(operation.target)
        defer { if request.0 == epoch { busy.remove(operation.target) } }
        do {
            let receipt: MobileReceipt = try await request.3.send(prepared)
            guard current(request, key: "operation:" + operation.id) else { return }
            try accept(receipt, operation: operation, credential: request.2)
        } catch {
            guard current(request, key: "operation:" + operation.id) else { return }
            markFailure(error, operation: operation, credential: request.2); show(error)
        }
        guard request.0 == epoch else { return }
        await refreshAfter(operation)
    }
    func reconcile(_ operation: MobilePendingOperation) async {
        guard operation.unresolved, !busy.contains(operation.target), let request = try? context("operation:" + operation.id) else { return }
        busy.insert(operation.target)
        defer { if request.0 == epoch { busy.remove(operation.target) } }
        do {
            let receipt: MobileReceipt = try await request.3.request(["operations", operation.id])
            guard current(request, key: "operation:" + operation.id) else { return }
            try accept(receipt, operation: operation, credential: request.2)
        } catch {
            guard current(request, key: "operation:" + operation.id) else { return }
            if case MobileError.server(404, _) = error {
                markNotRecorded(operation, credential: request.2)
            } else {
                markFailure(MobileError.unresolved, operation: operation, credential: request.2); show(error)
            }
        }
        guard request.0 == epoch else { return }; await refreshAfter(operation)
    }
    private func markNotRecorded(_ operation: MobilePendingOperation, credential: MobileCredential) {
        guard var state = local, let index = state.operations.firstIndex(where: { $0.id == operation.id }) else { return }
        state.operations[index].state = "not_recorded"
        do { try persist(state, credential: credential); error = nil }
        catch { show(error) }
    }
    func retryRecordedRequest(_ operation: MobilePendingOperation) async {
        guard let stored = local?.operations.first(where: { $0.id == operation.id }), stored.state == "not_recorded",
              !busy.contains(stored.target), let request = try? context("operation:" + stored.id), var state = local,
              let index = state.operations.firstIndex(where: { $0.id == stored.id }) else { return }
        let prepared: MobilePreparedRequest
        do { prepared = try request.3.preparedRequest(stored.path, body: stored.body) }
        catch { markFailure(error, operation: stored, credential: request.2); show(error); return }
        state.operations[index].state = "pending"
        invalidateOperationReads(stored)
        do { try persist(state, credential: request.2, retainOnFailure: false) } catch { show(error); return }
        busy.insert(stored.target)
        defer { if request.0 == epoch { busy.remove(stored.target) } }
        do {
            let receipt: MobileReceipt = try await request.3.send(prepared)
            guard current(request, key: "operation:" + stored.id) else { return }
            try accept(receipt, operation: stored, credential: request.2)
        } catch {
            guard current(request, key: "operation:" + stored.id) else { return }
            markFailure(error, operation: stored, credential: request.2); show(error)
        }
        guard request.0 == epoch else { return }; await refreshAfter(stored)
    }
    private func accept(_ receipt: MobileReceipt, operation: MobilePendingOperation, credential: MobileCredential) throws {
        guard receipt.operationId == operation.id, receipt.kind == operation.kind,
              ["pending", "completed", "failed", "unknown"].contains(receipt.state),
              var state = local, let index = state.operations.firstIndex(where: { $0.id == operation.id }) else { throw MobileError.invalidResponse }
        state.operations[index].state = receipt.state; state.operations[index].error = receipt.error
        state.operations[index].result = receipt.result
        if receipt.state == "completed" {
            invalidateOperationReads(operation)
            let submitted = operation.body[["mentor_message", "roundtable_create", "roundtable_message"].contains(operation.kind) ? "text" : "content"]?.string
            if let submitted, state.drafts[operation.target] == submitted {
                state.drafts[operation.target] = ""; state.memoryDraftVersions?[operation.target] = nil
            }
        }
        if receipt.state == "completed", operation.kind == "roundtable_create", operation.target != groupCreationTarget,
           let id = receipt.result?["conversation_id"]?.string {
            let destination = "roundtable:" + id
            if let newer = state.drafts[operation.target], !newer.isEmpty {
                let existing = state.drafts[destination] ?? ""
                if existing.isEmpty || existing == newer { state.drafts[destination] = newer; state.drafts[operation.target] = "" }
            }
            state.roundtableSelection = id
        }
        if receipt.state == "completed", operation.kind == "roundtable_create", operation.target == groupCreationTarget {
            state.groupCreationPersonas = []
        }
        try persist(state, credential: credential, retainOnFailure: false)
        error = receipt.state == "failed" ? MobileError.server(400, receipt.error ?? "unavailable").localizedDescription : nil
        if receipt.state == "completed", operation.kind == "device_revoke" { disconnect() }
    }
    private func markFailure(_ failure: Error, operation: MobilePendingOperation, credential: MobileCredential) {
        guard var state = local, let index = state.operations.firstIndex(where: { $0.id == operation.id }) else { return }
        let value = failure as? MobileError
        var definite = value == .invalidInput || value == .invalidEndpoint
        if case .server(let status, _) = value { definite = [400, 403, 409, 422].contains(status) }
        state.operations[index].state = definite ? "failed" : "unknown"
        if case .server(_, let code) = value { state.operations[index].error = code }
        if value == .invalidInput || value == .invalidEndpoint { state.operations[index].error = "invalid_input_unsent" }
        do { try persist(state, credential: credential) } catch { show(MobileError.storage, recoveryScope: credential.scope) }
    }
    private func refreshAfter(_ operation: MobilePendingOperation) async {
        let failure = error, failureScope = recoveryFailureScope, current = epoch
        defer {
            if current == epoch, let failure, failureScope == nil || failureScope.flatMap({ unsavedRecovery[$0] }) != nil {
                error = failure; recoveryFailureScope = failureScope
            }
        }
        switch operation.kind {
        case "mentor_message", "mentor_control": if operation.path.count > 1 { await refreshConversation(operation.path[1]) }
        case "memory_action":
            memories = nil
            if local?.operations.first(where: { $0.id == operation.id })?.result?["deleted"] != .bool(true) { await refreshMemory(operation.path[1]) }
            await refreshOverview()
        case "roundtable_create", "roundtable_message", "roundtable_command", "roundtable_result": await refreshAfterRoundtable(operation)
        case "memory_organize": await refreshOverview()
        case "privacy_action": await refreshPrivacy(); await refreshOverview()
        default: break
        }
    }
}

extension MobileClientModel {
    func hasUnreadReply(_ key: String) -> Bool {
        guard let latest = local?.lastKnownReplies?[key], !latest.isEmpty else { return false }
        return local?.lastReadReplies?[key] != latest
    }
    func recordLatestReplies(_ replies: [String: String]) {
        guard !replies.isEmpty, let credential, var state = local else { return }
        var known = state.lastKnownReplies ?? [:]
        for (key, identifier) in replies where identifier.isEmpty || MobileEndpoint.validIdentifier(identifier) {
            known[key] = identifier.isEmpty ? nil : identifier
        }
        guard known != (state.lastKnownReplies ?? [:]) else { return }
        state.lastKnownReplies = known
        do { try persist(state, credential: credential) } catch { show(error) }
    }
    func markReplyRead(_ key: String, visibleMessageID: String?) {
        guard let visibleMessageID, let latest = local?.lastKnownReplies?[key], let credential, var state = local else { return }
        let isVisible: Bool
        if key.hasPrefix("mentor:") {
            let persona = String(key.dropFirst(7)), messages = messages(String(key.dropFirst(7)))
            isVisible = messages.contains(where: { $0.id == latest && $0.actor == persona }) &&
                (visibleMessageID == latest || visibleMessageID == messages.last?.id)
        } else {
            guard key == roundtableDraftKey else { return }
            let messages = roundtable?.messages ?? []
            isVisible = messages.contains(where: { $0.id == latest && $0.actor != "user" }) &&
                (visibleMessageID == latest || visibleMessageID == messages.last?.id)
        }
        guard isVisible, state.lastReadReplies?[key] != latest else { return }
        var read = state.lastReadReplies ?? [:]; read[key] = latest; state.lastReadReplies = read
        do { try persist(state, credential: credential) } catch { show(error) }
    }
    func pollChatInbox() async {
        while !Task.isCancelled, foreground, connected {
            await refreshChatInbox()
            do { try await Task.sleep(nanoseconds: 10_000_000_000) } catch { return }
        }
    }
    func refreshChatInbox() async {
        guard let request = try? context("chat-inbox") else { return }
        do {
            let value: MobileCapabilities = try await request.3.request(["capabilities"])
            let expected = Set(["gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"])
            guard current(request, key: "chat-inbox") else { return }
            guard value.apiVersion == 1, value.device.id == request.2.deviceId,
                  value.mentors.count == 4, Set(value.mentors.map(\.id)) == expected else { throw MobileError.invalidResponse }
            capabilities = value; connectionState = .connected
            recordLatestReplies(Dictionary(uniqueKeysWithValues: value.mentors.compactMap { mentor in
                mentor.lastReplyId.map { ("mentor:" + mentor.id, $0) }
            }))
            guard value.capabilities["roundtable"] == true || value.capabilities["group_chat"] == true else { return }
            let list: MobileRoundtableList = try await request.3.request(["roundtables"])
            _ = try list.validated()
            guard current(request, key: "chat-inbox") else { return }
            roundtableList = list
            recordLatestReplies(Dictionary(uniqueKeysWithValues: list.items.compactMap { item in
                item.lastReplyId.map { ("roundtable:" + item.id, $0) }
            }))
        } catch {
            guard current(request, key: "chat-inbox") else { return }
            if error as? MobileError == .unauthorized { show(error) }
            else {
                let failure = error as? MobileError ?? .transport
                noteConnectionFailure(failure)
                if case .server(let status, _) = failure, (400..<500).contains(status) { connectionState = .unavailable }
            }
        }
    }
    func restoreMentorReadingPosition(_ persona: String, maxPages: Int = 12) async {
        guard let anchor = mentorReadingAnchor(persona), !restoringMentors.contains(persona) else { return }
        restoringMentors.insert(persona); defer { restoringMentors.remove(persona) }
        let currentEpoch = epoch
        for _ in 0..<maxPages {
            if messages(persona).contains(where: { $0.id == anchor }) { mentorReadingNotices[persona] = nil; return }
            guard !Task.isCancelled, foreground, epoch == currentEpoch, historyCursor(persona) != nil else { break }
            let before = messages(persona).count
            await loadOlderMessages(persona)
            if messages(persona).count <= before { break }
        }
        guard epoch == currentEpoch else { return }
        if !messages(persona).contains(where: { $0.id == anchor }) {
            mentorReadingNotices[persona] = historyCursor(persona) == nil ? "已读取当前可用历史，未找到上次阅读的位置。原草稿仍保留。" :
                "上次阅读位置在更早记录中，尚未恢复。可继续读取，草稿仍保留。"
        } else { mentorReadingNotices[persona] = nil }
    }
}
