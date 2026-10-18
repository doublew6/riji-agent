import Foundation

public enum OriginalVaultError: Error, LocalizedError {
    case layout, ambiguousDate, unavailable, conflict, readOnly, changedIdentity
    public var errorDescription: String? {
        switch self {
        case .layout: return "未找到受支持的 daily 目录与 templates 目录，或日记配置无法安全识别。请重新选择原日记文件夹。"
        case .ambiguousDate: return "原目录中同一天有多个文件，暂不能确定目标。请在文件目录核对后重试。"
        case .unavailable: return "原文件尚未下载、已移动或访问权限已失效。请在系统“文件”中核对下载与位置，必要时重新选择原目录；当前文字已留在正文中。"
        case .conflict: return "iCloud 为此日记保留了多个版本。可以继续阅读；请打开“查看并处理版本”，核对完整内容后选择版本或手动合并。"
        case .readOnly: return "此访问方式仅允许查看原目录，不能写入文件。"
        case .changedIdentity: return "原文件位置或目录身份已变化。旧草稿保留在原来源，请先核对。"
        }
    }
}

/// Local recovery metadata identifies a collection without granting file access.
public struct OriginalVaultRecoveryReference: Codable, Sendable {
    public let identity: String
    public let name: String
    public let daily: [String]
}

/// Synchronous access is owned by LocalJournalStore's actor. The production adapter
/// is read-only unless its caller explicitly enables coordinated manual body edits.
public final class OriginalVaultAccess: @unchecked Sendable {
    public enum WritePolicy: Sendable { case readOnly, coordinatedUserEdit, syntheticFixture }
    public let identity: String
    public let name: String
    public let canWrite: Bool
    private let root: URL?
    private let collectionIdentity: VaultCollectionIdentity?
    private let scoped: Bool
    private let prefix: [String]
    private let daily: [String]
    private let template: [String]
    private let failure: FileFailureHook
    private let versions: any CloudVersionProvider
    private let downloadLock = NSLock()
    private var downloadingVersions = false

    public init(root: URL, securityScoped: Bool = false, writePolicy: WritePolicy = .readOnly,
                failure: @escaping FileFailureHook = { _, _ in },
                versions: any CloudVersionProvider = SystemCloudVersionProvider()) throws {
        self.root = root; scoped = securityScoped; self.failure = failure; self.versions = versions
        canWrite = writePolicy != .readOnly
        let access = securityScoped ? root.startAccessingSecurityScopedResource() : false
        guard !securityScoped || access else { throw OriginalVaultError.unavailable }
        defer { if access { root.stopAccessingSecurityScopedResource() } }
        let layout = try Self.coordinate(root: root, path: []) { relative in
            guard relative.isEmpty else { throw OriginalVaultError.changedIdentity }
            return try Self.layout(root)
        }
        prefix = layout.prefix; daily = layout.daily; template = layout.template
        identity = try Self.identifier(root: root, prefix: layout.prefix)
        name = layout.prefix.last ?? root.lastPathComponent
        collectionIdentity = try LocalVaultReader.collectionIdentity(root: root, path: prefix)
    }

    public var recoveryReference: OriginalVaultRecoveryReference {
        OriginalVaultRecoveryReference(identity: identity, name: name, daily: Array(daily.dropFirst(prefix.count)))
    }

    /// This adapter can read only local recovery records; it has no source URL.
    public init(recovery reference: OriginalVaultRecoveryReference) throws {
        guard reference.identity.count == 64, reference.identity.allSatisfy({ $0.isHexDigit }),
              !reference.daily.isEmpty else { throw OriginalVaultError.changedIdentity }
        _ = try LocalVaultReader.childURL(root: URL(fileURLWithPath: "/"), path: reference.daily)
        identity = reference.identity; name = reference.name; daily = reference.daily
        root = nil; collectionIdentity = nil; prefix = []; template = []; scoped = false
        canWrite = false; failure = { _, _ in }; versions = SystemCloudVersionProvider()
    }

    /// Validate retained identity without consulting an unavailable or moved source.
    public func validateRetainedSource(_ sourceID: String?, day: JournalDay) throws {
        guard let sourceID, sourceID.hasPrefix(identity + ":") else { throw OriginalVaultError.changedIdentity }
        let relative = String(sourceID.dropFirst(identity.count + 1)).components(separatedBy: "/")
        _ = try LocalVaultReader.childURL(root: URL(fileURLWithPath: "/"), path: relative)
        let dailyPath = Array(daily.dropFirst(prefix.count))
        guard relative.starts(with: dailyPath), relative.count > dailyPath.count,
              relative.last == day.value + ".md" else { throw OriginalVaultError.changedIdentity }
    }

    public func paths() throws -> [JournalDay: [String]] {
        try access { root in try Self.coordinate(root: root, path: []) { relative in
            guard relative.isEmpty else { throw OriginalVaultError.changedIdentity }
            return try Self.scan(root: root, daily: daily)
        } }
    }

    public func path(for day: JournalDay) throws -> [String] {
        try paths()[day] ?? daily + [day.value + ".md"]
    }

    public func sourceID(_ day: JournalDay) throws -> String {
        identity + ":" + (try path(for: day)).dropFirst(prefix.count).joined(separator: "/")
    }

    public func read(_ day: JournalDay) throws -> Data? {
        let display = try displaySnapshot(day)
        guard display.cloud == nil else { throw OriginalVaultError.conflict }
        return display.data
    }

    public func displaySnapshot(_ day: JournalDay, reviewVersions: Bool = false) throws -> OriginalDisplaySnapshot {
        let existing = try paths()[day], path = existing ?? daily + [day.value + ".md"]
        return try access { root in
            let coordinatedPath = existing == nil ? Array(path.dropLast()) : path
            return try Self.coordinate(root: root, path: coordinatedPath) { relative in
                try failure(.bodyDiary, .beforeRead)
                let target = existing == nil ? relative + [path.last!] : relative
                guard target == path else { throw OriginalVaultError.changedIdentity }
                let data = try Self.readOptional(root: root, path: target, allowConflict: true)
                guard let data else { return OriginalDisplaySnapshot(data: nil, cloud: nil) }
                let inventory: CloudVersionInventory
                do {
                    let url = try LocalVaultReader.childURL(root: root, path: target)
                    inventory = try reviewVersions ? versions.inventory(at: url) : versions.displayInventory(at: url)
                }
                catch { inventory = CloudVersionInventory(versions: [], hasConflict: true, complete: false) }
                let source = identity + ":" + target.dropFirst(prefix.count).joined(separator: "/")
                let review = reviewVersions || inventory.hasConflict || !inventory.complete
                    ? CloudReview(day: day, sourceID: source, current: data, inventory: inventory) : nil
                return OriginalDisplaySnapshot(data: data, cloud: review)
            }
        }
    }

    public func templateVersion() throws -> TemplateVersion {
        let data = try access { root in try Self.coordinate(root: root, path: template) { relative in
            try failure(.bodyDiary, .beforeRead)
            guard relative == template, let data = try Self.readOptional(root: root, path: relative) else { throw OriginalVaultError.unavailable }
            return data
        } }
        return try TemplateVersion(kind: .daily, raw: data)
    }

    /// Compare and replace within one coordinator callback, then verify exact bytes.
    /// Uncoordinated third-party writers cannot be given a universal CAS guarantee.
    public func write(_ edit: BodyEdit) throws { try write(edit, ai: nil, validation: nil) }

    func writeAIResult(_ preview: AIResultPreview, validation: MobileResultValidation) throws {
        try AIResultMarkdown.validate(preview)
        try write(BodyEdit(day: preview.day, before: preview.before, content: preview.after, sourceID: preview.sourceID), ai: preview, validation: validation)
    }

    private func write(_ edit: BodyEdit, ai: AIResultPreview?, validation: MobileResultValidation?) throws {
        guard canWrite else { throw OriginalVaultError.readOnly }
        let path = try path(for: edit.day)
        guard edit.sourceID == identity + ":" + path.dropFirst(prefix.count).joined(separator: "/") else { throw OriginalVaultError.changedIdentity }
        try access { root in
            let parentPath = Array(path.dropLast()), name = path.last!
            let parent = try LocalVaultReader.childURL(root: root, path: parentPath)
            _ = try LocalVaultReader.entries(root: root, path: parentPath)
            var error: NSError?, result: Result<Void, Error>?
            NSFileCoordinator().coordinate(writingItemAt: parent.appendingPathComponent(name), options: .forReplacing, error: &error) { coordinated in
                result = Result {
                    try failure(.bodyDiary, .beforeRead)
                    guard try LocalVaultReader.collectionIdentity(root: root, path: prefix) == collectionIdentity else { throw OriginalVaultError.changedIdentity }
                    let relative = try LocalVaultReader.relativePath(root: root, callback: coordinated)
                    guard relative == path else { throw OriginalVaultError.changedIdentity }
                    let inventory = try versions.inventory(at: coordinated)
                    guard inventory.complete, !inventory.hasConflict else { throw OriginalVaultError.conflict }
                    let before = try Self.readOptional(root: root, path: relative)
                    guard before == edit.before else { throw JournalError.changedSource }
                    if let ai { try AIResultMarkdown.validate(ai) }
                    else { try BodyMarkdown.validatePreservation(before: before, after: edit.content) }
                    guard let collectionIdentity else { throw OriginalVaultError.changedIdentity }
                    let location = VaultWriteLocation(root: root, path: relative, collectionPath: prefix, collection: collectionIdentity)
                    try LocalVaultReader.replace(location: location, expected: before, content: edit.content, failure: failure, beforeReplace: {
                        if let validation { try validation.validate() }
                        if let ai, Date().timeIntervalSince(ai.createdAt) > 1800 { throw JournalError.operationMismatch }
                    })
                    guard try Self.readOptional(root: root, path: relative) == edit.content else { throw JournalError.changedSource }
                }
            }
            guard error == nil, let result else { throw OriginalVaultError.unavailable }
            try result.get()
        }
    }

    /// Explicit retries may fetch missing versions without holding the UI or store actor.
    func requestCloudDownloads(_ day: JournalDay) {
        downloadLock.lock()
        guard !downloadingVersions else { downloadLock.unlock(); return }
        downloadingVersions = true; downloadLock.unlock()
        DispatchQueue.global(qos: .utility).async { [self] in
            defer { downloadLock.lock(); downloadingVersions = false; downloadLock.unlock() }
            try? access { root in
                let path = try self.path(for: day)
                versions.requestDownloads(at: try LocalVaultReader.childURL(root: root, path: path))
            }
        }
    }

    /// Whole-file authorization is separate from the ordinary metadata-preserving editor.
    func resolveCloud(_ draft: CloudRecoveryDraft, retry: Bool) throws {
        guard canWrite else { throw OriginalVaultError.readOnly }
        guard draft.review.canResolve else { throw CloudRecoveryError.incomplete }
        let review = draft.review, path = try path(for: draft.review.day)
        guard try sourceID(review.day) == review.sourceID else { throw OriginalVaultError.changedIdentity }
        try access { root in
            let target = try LocalVaultReader.childURL(root: root, path: path)
            var error: NSError?, result: Result<Void, Error>?
            NSFileCoordinator().coordinate(writingItemAt: target, options: .forReplacing, error: &error) { url in
                result = Result {
                    guard try LocalVaultReader.relativePath(root: root, callback: url) == path,
                          try LocalVaultReader.collectionIdentity(root: root, path: prefix) == collectionIdentity,
                          let collectionIdentity else { throw OriginalVaultError.changedIdentity }
                    let current = try Self.readOptional(root: root, path: path, allowConflict: true)
                    let inventory = try versions.inventory(at: url)
                    try validateCloudInventory(inventory, review: review, allowSubset: retry && current == draft.content)
                    if current != draft.content {
                        guard current == review.current, inventory.versions == review.versions else { throw CloudRecoveryError.stale }
                        let location = VaultWriteLocation(root: root, path: path, collectionPath: prefix, collection: collectionIdentity)
                        try LocalVaultReader.replace(location: location, expected: current, content: draft.content, failure: { purpose, stage in
                            try self.failure(purpose, stage)
                            if stage == .beforeReplace {
                                try self.validateCloudInventory(self.versions.inventory(at: url), review: review, allowSubset: false)
                            }
                        })
                    }
                    guard try LocalVaultReader.collectionIdentity(root: root, path: prefix) == collectionIdentity,
                          try Self.readOptional(root: root, path: path, allowConflict: true) == draft.content else { throw CloudRecoveryError.stale }
                    let remaining = try versions.inventory(at: url)
                    try validateCloudInventory(remaining, review: review, allowSubset: true)
                    try versions.resolve(remaining.versions, at: url)
                    let verified = try versions.inventory(at: url)
                    guard verified.complete, !verified.hasConflict, verified.versions.isEmpty,
                          try LocalVaultReader.collectionIdentity(root: root, path: prefix) == collectionIdentity,
                          try Self.readOptional(root: root, path: path, allowConflict: true) == draft.content else { throw CloudRecoveryError.pending }
                }
            }
            guard error == nil, let result else { throw OriginalVaultError.unavailable }
            try result.get()
        }
    }

    /// A read-only acknowledgement retires a stale local intent, never writes source bytes or version flags.
    func confirmCloudCurrent(_ review: CloudReview, persist: () throws -> Void) throws {
        guard canWrite, review.canKeepCurrent else { throw CloudRecoveryError.incomplete }
        guard try sourceID(review.day) == review.sourceID else { throw OriginalVaultError.changedIdentity }
        let path = try path(for: review.day)
        try access { root in
            try Self.coordinate(root: root, path: path) { relative in
                guard relative == path,
                      try LocalVaultReader.collectionIdentity(root: root, path: prefix) == collectionIdentity,
                      try Self.readOptional(root: root, path: path, allowConflict: true) == review.current else { throw CloudRecoveryError.stale }
                let inventory = try versions.inventory(at: LocalVaultReader.childURL(root: root, path: path))
                guard inventory.complete, !inventory.hasConflict, inventory.versions.isEmpty else { throw CloudRecoveryError.stale }
                try persist()
            }
        }
    }

    private func validateCloudInventory(_ inventory: CloudVersionInventory, review: CloudReview, allowSubset: Bool) throws {
        guard inventory.complete, inventory.versions.allSatisfy({ $0.content != nil }),
              Set(inventory.versions.map(\.id)).count == inventory.versions.count else { throw CloudRecoveryError.incomplete }
        guard inventory.versions.allSatisfy({ review.versions.contains($0) }),
              allowSubset || inventory.versions == review.versions else { throw CloudRecoveryError.stale }
    }

    private func access<T>(_ operation: (URL) throws -> T) throws -> T {
        guard let root else { throw OriginalVaultError.unavailable }
        let access = scoped ? root.startAccessingSecurityScopedResource() : false
        guard !scoped || access else { throw OriginalVaultError.unavailable }
        defer { if access { root.stopAccessingSecurityScopedResource() } }
        guard try Self.identifier(root: root, prefix: Self.collectionPrefix(root)) == identity else {
            throw OriginalVaultError.changedIdentity
        }
        return try operation(root)
    }

    private static func collectionPrefix(_ root: URL) throws -> [String] {
        let entries = try LocalVaultReader.entries(root: root)
        if entries.contains(where: { $0.name == "daily" && $0.kind == .folder }),
           entries.contains(where: { $0.name == "templates" && $0.kind == .folder }) { return [] }
        guard entries.filter({ $0.name == "riji" && $0.kind == .folder }).count == 1 else { throw OriginalVaultError.layout }
        return ["riji"]
    }

    private static func layout(_ root: URL) throws -> (prefix: [String], daily: [String], template: [String]) {
        let prefix = try collectionPrefix(root)
        let entries = try LocalVaultReader.entries(root: root, path: prefix)
        guard entries.contains(where: { $0.name == "daily" && $0.kind == .folder }),
              entries.contains(where: { $0.name == "templates" && $0.kind == .folder }) else { throw OriginalVaultError.layout }
        var daily = prefix + ["daily"], template = prefix + ["templates", "daily.md"]
        let configurationBase = FileManager.default.fileExists(atPath: root.appendingPathComponent(".obsidian").path) ? [] : prefix
        let configuration = configurationBase + [".obsidian", "daily-notes.json"]
        let hidden = try LocalVaultReader.childURL(root: root, path: configurationBase + [".obsidian"])
        if FileManager.default.fileExists(atPath: hidden.path) {
            if let raw = try readOptional(root: root, path: configuration) {
                guard raw.count <= 32768, let settings = try JSONSerialization.jsonObject(with: raw) as? [String: Any] else { throw OriginalVaultError.layout }
                for key in ["folder", "template", "format"] where settings[key] != nil {
                    guard settings[key] is String else { throw OriginalVaultError.layout }
                }
                if let format = settings["format"] as? String, !format.isEmpty, format != "YYYY-MM-DD" { throw OriginalVaultError.layout }
                if let folder = settings["folder"] as? String, !folder.isEmpty { daily = configurationBase + (try configuredPath(folder)) }
                if let value = settings["template"] as? String, !value.isEmpty {
                    template = configurationBase + (try configuredPath(value.hasSuffix(".md") ? value : value + ".md"))
                }
            }
        }
        guard daily.starts(with: prefix), template.starts(with: prefix),
              daily.count > prefix.count, template.count > prefix.count else { throw OriginalVaultError.layout }
        _ = try LocalVaultReader.entries(root: root, path: daily)
        return (prefix, daily, template)
    }

    private static func configuredPath(_ value: String) throws -> [String] {
        let path = value.components(separatedBy: "/")
        guard !value.contains("\\"), !value.contains("{{"), path.count < 16 else { throw OriginalVaultError.layout }
        _ = try LocalVaultReader.childURL(root: URL(fileURLWithPath: "/"), path: path)
        return path
    }

    private static func identifier(root: URL, prefix: [String]) throws -> String {
        var url = try LocalVaultReader.childURL(root: root, path: prefix)
        _ = try LocalVaultReader.entries(root: root, path: prefix)
        // A bookmark URL can cache the previous directory's identifier after a
        // move. Re-read identity so a replacement at its old path stays foreign.
        url.removeAllCachedResourceValues()
        let values = try url.resourceValues(forKeys: [.fileResourceIdentifierKey, .volumeIdentifierKey])
        guard let file = values.fileResourceIdentifier, let volume = values.volumeIdentifier else { throw OriginalVaultError.unavailable }
        return SourceHash.digest(Data("\(volume):\(file)".utf8))
    }

    private static func scan(root: URL, daily: [String]) throws -> [JournalDay: [String]] {
        var pending = [daily], result: [JournalDay: [String]] = [:], count = 0
        while let path = pending.popLast() {
            guard path.count - daily.count <= 16 else { throw VaultReadError.directoryLimit }
            for entry in try LocalVaultReader.entries(root: root, path: path) {
                count += 1
                guard count <= 20_000 else { throw VaultReadError.directoryLimit }
                if entry.kind == .folder { pending.append(entry.path); continue }
                guard entry.name.hasSuffix(".md"), let day = try? JournalDay(String(entry.name.dropLast(3))) else {
                    if entry.kind == .unavailable && !entry.isFilePlaceholder { throw OriginalVaultError.unavailable }
                    continue
                }
                guard result[day] == nil else { throw OriginalVaultError.ambiguousDate }
                result[day] = entry.path
            }
        }
        return result
    }

    private static func readOptional(root: URL, path: [String], allowConflict: Bool = false) throws -> Data? {
        let entries = try LocalVaultReader.entries(root: root, path: Array(path.dropLast()))
        guard let entry = entries.first(where: { $0.name == path.last }) else { return nil }
        guard entry.kind == .file else { throw OriginalVaultError.unavailable }
        let data = try LocalVaultReader.read(root: root, path: path)
        try assertAvailable(LocalVaultReader.childURL(root: root, path: path), allowConflict: allowConflict)
        return data
    }

    public static func assertAvailable(_ url: URL, allowConflict: Bool = false) throws {
        var fresh = url; fresh.removeAllCachedResourceValues()
        let values = try fresh.resourceValues(forKeys: [.isUbiquitousItemKey, .ubiquitousItemDownloadingStatusKey, .ubiquitousItemHasUnresolvedConflictsKey])
        if !allowConflict && (values.ubiquitousItemHasUnresolvedConflicts == true || NSFileVersion.unresolvedConflictVersionsOfItem(at: url)?.isEmpty == false) { throw OriginalVaultError.conflict }
        if values.isUbiquitousItem == true, values.ubiquitousItemDownloadingStatus != .current, values.ubiquitousItemDownloadingStatus != .downloaded {
            try? FileManager.default.startDownloadingUbiquitousItem(at: url)
            throw OriginalVaultError.unavailable
        }
    }

    private static func coordinate<T>(root: URL, path: [String], operation: ([String]) throws -> T) throws -> T {
        let target = try LocalVaultReader.childURL(root: root, path: path)
        var error: NSError?, result: Result<T, Error>?
        NSFileCoordinator().coordinate(readingItemAt: target, options: [], error: &error) { url in result = Result { try operation(LocalVaultReader.relativePath(root: root, callback: url)) } }
        guard error == nil, let result else { throw OriginalVaultError.unavailable }
        return try result.get()
    }
}
