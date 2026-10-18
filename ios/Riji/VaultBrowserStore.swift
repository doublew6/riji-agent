import Foundation
import RijiCore

struct VaultFolderSelection: Codable {
    enum Access: String, Codable { case securityScoped, appLocal, simulatorLocal }
    let bookmark: Data
    let access: Access
    let original: OriginalVaultRecoveryReference?
    init(bookmark: Data, access: Access, original: OriginalVaultRecoveryReference? = nil) {
        self.bookmark = bookmark; self.access = access; self.original = original
    }
}

struct VaultOriginalCandidate: Sendable {
    let url: URL
    let access: VaultFolderSelection.Access
    let original: OriginalVaultAccess
}

struct VaultRoot: Sendable {
    let name: String
    let entries: [VaultEntry]
}

struct VaultFileContents: Identifiable, Sendable {
    let id = UUID()
    let entry: VaultEntry
    let data: Data
    let text: String?
    let previewURL: URL?
    var cloudConflict = false
}

/// Owns only the folder reference. Selected vault files are never written by this actor.
actor VaultBrowserStore {
    private let stateDirectory: URL
    private let appRoot: URL
    private let failure: FileFailureHook
    private let versionProvider: any CloudVersionProvider
    private var selection: VaultFolderSelection?
    private var root: URL?
    private var previewDirectories = Set<String>()
    private var selectionURL: URL { stateDirectory.appendingPathComponent("vault-folder.json") }

    init(stateDirectory: URL, appRoot: URL = URL(fileURLWithPath: NSHomeDirectory()), failure: @escaping FileFailureHook = { _, _ in }, versions: (any CloudVersionProvider)? = nil) {
        self.stateDirectory = stateDirectory; self.appRoot = appRoot; self.failure = failure
        self.versionProvider = versions ?? Self.defaultVersions(stateDirectory)
    }

    func restore() throws -> VaultRoot? {
        guard FileManager.default.fileExists(atPath: selectionURL.path) else { return try bootstrapSimulatorFolder() }
        do {
            let bytes = try Data(contentsOf: selectionURL)
            guard bytes.count <= 256 * 1024 else { throw VaultReadError.unavailable }
            let saved = try JSONDecoder().decode(VaultFolderSelection.self, from: bytes)
            var stale = false
            let url = try URL(resolvingBookmarkData: saved.bookmark, options: [.withoutUI], relativeTo: nil, bookmarkDataIsStale: &stale)
            let result = try withAccess(url, saved.access) { try coordinatedEntries(root: url, path: []) }
            selection = stale ? try withAccess(url, saved.access) { try persist(url, saved.access, original: saved.original) } : saved
            root = url
            return VaultRoot(name: url.lastPathComponent, entries: result)
        } catch { throw VaultReadError.unavailable }
    }

    func connect(_ url: URL) throws -> VaultRoot {
        let access: VaultFolderSelection.Access = isWithinApp(url) ? .appLocal : .securityScoped
        return try connect(url, access: access)
    }

    func originalAccess() throws -> OriginalVaultAccess? {
        guard let root, let selection else { return nil }
        return try withAccess(root, selection.access) {
            let access = try OriginalVaultAccess(root: root, securityScoped: selection.access == .securityScoped,
                                                 writePolicy: .coordinatedUserEdit, failure: failure, versions: versionProvider)
            if let previous = selection.original, previous.identity != access.identity { throw OriginalVaultError.changedIdentity }
            // Persist recovery identity before allowing the editor to retain input.
            if selection.original == nil {
                self.selection = try persist(root, selection.access, original: access.recoveryReference)
            }
            return access
        }
    }

    func restoredOriginalCandidate() throws -> VaultOriginalCandidate? {
        _ = try restore()
        guard let root, let selection, let original = try originalAccess() else { return nil }
        return VaultOriginalCandidate(url: root, access: selection.access, original: original)
    }

    func recoveryAccess() throws -> OriginalVaultAccess? {
        guard FileManager.default.fileExists(atPath: selectionURL.path) else { return nil }
        let bytes = try Data(contentsOf: selectionURL)
        guard bytes.count <= 256 * 1024 else { throw VaultReadError.unavailable }
        let saved = try JSONDecoder().decode(VaultFolderSelection.self, from: bytes)
        guard let reference = saved.original else { return nil }
        return try OriginalVaultAccess(recovery: reference)
    }

    /// Preparing a candidate never changes the active root or the durable bookmark.
    func connectOriginal(_ url: URL) throws -> VaultOriginalCandidate {
        let access: VaultFolderSelection.Access = isWithinApp(url) ? .appLocal : .securityScoped
        return try withAccess(url, access) {
            let original = try OriginalVaultAccess(root: url, securityScoped: access == .securityScoped,
                                                   writePolicy: .coordinatedUserEdit, failure: failure, versions: versionProvider)
            return VaultOriginalCandidate(url: url, access: access, original: original)
        }
    }

    func commitOriginal(_ candidate: VaultOriginalCandidate) throws {
        try withAccess(candidate.url, candidate.access) {
            let current = try OriginalVaultAccess(root: candidate.url, securityScoped: candidate.access == .securityScoped)
            guard current.identity == candidate.original.identity else { throw OriginalVaultError.changedIdentity }
            let saved = try persist(candidate.url, candidate.access, original: candidate.original.recoveryReference)
            selection = saved; root = candidate.url
        }
    }

    func homeStore(_ original: OriginalVaultAccess) throws -> LocalJournalStore {
        try LocalJournalStore(journalDirectory: stateDirectory.appendingPathComponent("original-cache/" + original.identity),
                              stateDirectory: stateDirectory, failure: failure, original: original)
    }

    func entries(path: [String]) throws -> [VaultEntry] {
        guard let root, let selection else { throw VaultReadError.unavailable }
        return try withAccess(root, selection.access) { try coordinatedEntries(root: root, path: path) }
    }

    func northStarEntries(path: [String], identity: String) throws -> [VaultEntry] {
        try assertNorthStarIdentity(identity)
        return try entries(path: path)
    }

    /// Verify only the selected file, under the existing folder authorization.
    func northStarText(path: [String], identity: String) throws -> Data {
        try assertNorthStarIdentity(identity)
        guard NorthStarAnchor.validPath(path), let root, let selection else { throw NorthStarAnchorError.invalid }
        return try withAccess(root, selection.access) {
            try coordinate(root: root, path: path) { relative in
                let url = try LocalVaultReader.childURL(root: root, path: relative)
                try OriginalVaultAccess.assertAvailable(url)
                guard try !versionProvider.displayInventory(at: url).hasConflict else { throw NorthStarAnchorError.unavailable }
                try failure(.bodyDiary, .beforeRead)
                return try LocalVaultReader.read(root: root, path: relative, maximumBytes: NorthStarParagraph.maximumFileBytes)
            }
        }
    }

    private func assertNorthStarIdentity(_ identity: String) throws {
        guard let root, let selection, selection.original?.identity == identity else { throw NorthStarAnchorError.unavailable }
        try withAccess(root, selection.access) {
            let actual = try OriginalVaultAccess(root: root, securityScoped: selection.access == .securityScoped)
            guard actual.identity == identity else { throw NorthStarAnchorError.unavailable }
        }
    }

    func read(_ entry: VaultEntry) throws -> VaultFileContents {
        guard entry.kind == .file, let root, let selection else { throw VaultReadError.unsafePath }
        let suffix = (entry.name as NSString).pathExtension.lowercased()
        let isText = ["md", "markdown", "txt", "csv", "json", "yaml", "yml", "log"].contains(suffix)
        guard isText || ["pdf", "png", "jpg", "jpeg", "gif", "heic", "webp", "mp3", "m4a", "wav", "mp4", "mov"].contains(suffix) else {
            throw VaultReadError.unsupported
        }
        var cloudConflict = false
        let data = try withAccess(root, selection.access) {
            try coordinate(root: root, path: entry.path) { relative in
                try failure(.bodyDiary, .beforeRead)
                let data = try LocalVaultReader.read(root: root, path: relative)
                let url = try LocalVaultReader.childURL(root: root, path: relative)
                try OriginalVaultAccess.assertAvailable(url, allowConflict: true)
                cloudConflict = (try? versionProvider.displayInventory(at: url).hasConflict) ?? true
                return data
            }
        }
        if isText {
            guard data.count <= 2 * 1024 * 1024 else { throw VaultPreviewError.textTooLarge }
            guard let text = String(data: data, encoding: .utf8) else { throw VaultReadError.invalidText }
            return VaultFileContents(entry: entry, data: data, text: text, previewURL: nil, cloudConflict: cloudConflict)
        }
        let folder = stateDirectory.appendingPathComponent("vault-previews/\(UUID().uuidString)")
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let file = folder.appendingPathComponent(entry.name)
            try data.write(to: file, options: .atomic)
            previewDirectories.insert(folder.standardizedFileURL.path)
            return VaultFileContents(entry: entry, data: Data(), text: nil, previewURL: file, cloudConflict: cloudConflict)
        } catch { try? FileManager.default.removeItem(at: folder); throw VaultReadError.unavailable }
    }

    func discardPreview(_ contents: VaultFileContents) {
        guard let folder = contents.previewURL?.deletingLastPathComponent(), previewDirectories.contains(folder.standardizedFileURL.path) else { return }
        do { try FileManager.default.removeItem(at: folder); previewDirectories.remove(folder.standardizedFileURL.path) } catch { /* Retain ownership for a later retry. */ }
    }

    private func connect(_ url: URL, access: VaultFolderSelection.Access) throws -> VaultRoot {
        guard url.isFileURL else { throw VaultReadError.unsafePath }
        return try withAccess(url, access) {
            let entries = try coordinatedEntries(root: url, path: [])
            let saved = try persist(url, access)
            selection = saved; root = url
            return VaultRoot(name: url.lastPathComponent, entries: entries)
        }
    }

    @discardableResult private func persist(_ url: URL, _ access: VaultFolderSelection.Access, original: OriginalVaultRecoveryReference? = nil) throws -> VaultFolderSelection {
        let bookmark = try url.bookmarkData(options: [.minimalBookmark], includingResourceValuesForKeys: nil, relativeTo: nil)
        let saved = VaultFolderSelection(bookmark: bookmark, access: access, original: original)
        try FileManager.default.createDirectory(at: stateDirectory, withIntermediateDirectories: true)
        try failure(.metadata, .beforeWrite)
        try JSONEncoder().encode(saved).write(to: selectionURL, options: .atomic)
        return saved
    }

    private func withAccess<T>(_ url: URL, _ access: VaultFolderSelection.Access, _ operation: () throws -> T) throws -> T {
        if access == .appLocal {
            guard isWithinApp(url) else { throw VaultReadError.unavailable }
            return try operation()
        }
        #if DEBUG && targetEnvironment(simulator)
        if access == .simulatorLocal { return try operation() }
        #endif
        guard access == .securityScoped, url.startAccessingSecurityScopedResource() else { throw VaultReadError.unavailable }
        defer { url.stopAccessingSecurityScopedResource() }
        return try operation()
    }

    private func isWithinApp(_ url: URL) -> Bool {
        let base = appRoot.resolvingSymlinksInPath().standardizedFileURL.path
        let candidate = url.resolvingSymlinksInPath().standardizedFileURL.path
        return candidate == base || candidate.hasPrefix(base + "/")
    }

    private func coordinatedEntries(root: URL, path: [String]) throws -> [VaultEntry] {
        try coordinate(root: root, path: path) { relative in
            try failure(.metadata, .beforeRead)
            return try LocalVaultReader.entries(root: root, path: relative)
        }
    }

    private func coordinate<T>(root: URL, path: [String], _ operation: ([String]) throws -> T) throws -> T {
        let url = try LocalVaultReader.childURL(root: root, path: path)
        var error: NSError?, result: Result<T, Error>?
        NSFileCoordinator().coordinate(readingItemAt: url, options: [], error: &error) { coordinated in
            result = Result { try operation(LocalVaultReader.relativePath(root: root, callback: coordinated)) }
        }
        guard error == nil, let result else { throw VaultReadError.unavailable }
        return try result.get()
    }

    private static func defaultVersions(_ state: URL) -> any CloudVersionProvider {
        #if DEBUG
        let environment = ProcessInfo.processInfo.environment
        if let namespace = environment["RIJI_TEST_CONTAINER"], UUID(uuidString: namespace) != nil,
           environment["RIJI_TEST_CLOUD_CONFLICT"] == "1" {
            return SyntheticCloudVersionProvider(state: state)
        }
        #endif
        return SystemCloudVersionProvider()
    }

    private func bootstrapSimulatorFolder() throws -> VaultRoot? {
        #if DEBUG && targetEnvironment(simulator)
        let environment = ProcessInfo.processInfo.environment
        if let id = environment["RIJI_TEST_CONTAINER"], UUID(uuidString: id) != nil,
           environment["RIJI_TEST_VAULT_BROWSER"] == "1" {
            let fixture = stateDirectory.appendingPathComponent("VaultFixture/riji")
            for folder in ["daily", "assets", "templates", "weekly", "daily/nested"] {
                try FileManager.default.createDirectory(at: fixture.appendingPathComponent(folder), withIntermediateDirectories: true)
            }
            try Data("# Synthetic local note\r\n**Original body** e\u{301} 🧠\r\n".utf8).write(to: fixture.appendingPathComponent("daily/Synthetic.md"))
            try Data("Nested text\n".utf8).write(to: fixture.appendingPathComponent("daily/nested/Readme.txt"))
            try Data("# Original template\n".utf8).write(to: fixture.appendingPathComponent("templates/daily.md"))
            if environment["RIJI_TEST_NORTH_STAR"] == "1" {
                try Data("# Synthetic phase\n\nSynthetic phase direction: practice small steps each week.\n".utf8)
                    .write(to: fixture.appendingPathComponent("Phase.md"))
            }
            if environment["RIJI_TEST_ORIGINAL_VAULT"] == "1" {
                let date = environment["RIJI_TEST_DATE"].flatMap { ISO8601DateFormatter().date(from: $0) } ?? Date()
                let day = JournalDay(date: date, timeZone: .current)
                let previous = JournalDay(date: date.addingTimeInterval(-86400), timeZone: .current)
                let table = "# Original home synthetic\r\n| 名称 | 内容 | 第三列 | 第四列 | 第五列 | Sixth header |\r\n| --- | --- | --- | --- | --- | --- |\r\n"
                    + (0..<20).map { "| 中文 🧠 \($0) | | Synthetic \($0) | Value | More | End |\r\n" }.joined()
                    + "\r\nAfter synthetic table\r\n"
                let content = environment["RIJI_TEST_CLOUD_DIFF"] == "1" ? SyntheticCloudVersionProvider.diffCurrent : table
                try Data(content.utf8).write(to: fixture.appendingPathComponent("daily/\(day.value).md"))
                try Data("# Nested original synthetic\r\n".utf8).write(to: fixture.appendingPathComponent("daily/nested/\(previous.value).md"))
            }
            return try connect(fixture, access: .appLocal)
        }
        if (environment["RIJI_TEST_CONTAINER"] ?? "").isEmpty, let path = environment["RIJI_SIMULATOR_VAULT_ROOT"], !path.isEmpty {
            return try connect(URL(fileURLWithPath: path, isDirectory: true), access: .simulatorLocal)
        }
        #endif
        return nil
    }
}

enum VaultPreviewError: Error, LocalizedError {
    case textTooLarge
    var errorDescription: String? { "文本超过 2 MB，暂不在 App 内预览。" }
}

#if DEBUG
/// Confined to the UUID UI-test container; never enumerates or marks NSFileVersion objects.
private struct SyntheticCloudVersionProvider: CloudVersionProvider {
    let state: URL
    static let diffCurrent = "# Synthetic diary\r\n## Morning\r\n- [ ] Plan today\r\n\r\n## Workout\r\n| Exercise | Kg | Sets |\r\n| --- | --- | --- |\r\n| Bench | 20 | 3 |\r\n\r\n## Notes\r\nOriginal thought.\r\n\r\n## Evening\r\nUnchanged evening paragraph.\r\n"
    static var diffOther: String {
        diffCurrent.replacingOccurrences(of: "[ ]", with: "[x]")
            .replacingOccurrences(of: "Bench | 20", with: "Bench | 25")
            .replacingOccurrences(of: "Original thought.", with: "Another thought.")
    }
    private func marker(_ url: URL) -> URL { state.appendingPathComponent("synthetic-cloud-" + SourceHash.digest(Data(url.path.utf8)) + ".json") }
    func inventory(at url: URL) throws -> CloudVersionInventory {
        let fixture = state.appendingPathComponent("VaultFixture/riji/daily").standardizedFileURL.path + "/"
        guard url.standardizedFileURL.path.hasPrefix(fixture), url.pathExtension == "md",
              !FileManager.default.fileExists(atPath: marker(url).path) else {
            return CloudVersionInventory(versions: [], hasConflict: false)
        }
        let bytes = Data((ProcessInfo.processInfo.environment["RIJI_TEST_CLOUD_DIFF"] == "1" ? Self.diffOther
            : "---\r\nversion: alternate\r\n---\r\n# Synthetic cloud alternate\r\nFull source 🧠 e\u{301}\r\n").utf8)
        return CloudVersionInventory(versions: [CloudVersion(id: "synthetic-alternate", content: bytes)], hasConflict: true)
    }
    func resolve(_ reviewed: [CloudVersion], at url: URL) throws {
        guard try inventory(at: url).versions == reviewed else { throw CloudRecoveryError.stale }
        try Data("resolved".utf8).write(to: marker(url), options: .atomic)
    }
}
#endif
