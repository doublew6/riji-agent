import Foundation

public struct CloudVersion: Codable, Equatable, Sendable, Identifiable {
    public let id: String
    public let content: Data?
    public let modifiedAt: Date?
    public var exceedsSizeLimit: Bool? = nil
    public init(id: String, content: Data?, modifiedAt: Date? = nil) {
        self.id = id; self.content = content; self.modifiedAt = modifiedAt
    }
}

public struct CloudVersionInventory: Equatable, Sendable {
    public let versions: [CloudVersion]
    public let hasConflict: Bool
    public let complete: Bool
    public let totalVersions: Int
    public init(versions: [CloudVersion], hasConflict: Bool, complete: Bool = true, totalVersions: Int? = nil) {
        self.versions = versions; self.hasConflict = hasConflict; self.complete = complete
        self.totalVersions = totalVersions ?? versions.count
    }
}

/// The adapter owns system objects; callers can resolve only identities whose bytes were reviewed.
public protocol CloudVersionProvider: Sendable {
    func inventory(at url: URL) throws -> CloudVersionInventory
    func displayInventory(at url: URL) throws -> CloudVersionInventory
    func requestDownloads(at url: URL)
    func resolve(_ reviewed: [CloudVersion], at url: URL) throws
}

extension CloudVersionProvider {
    public func displayInventory(at url: URL) throws -> CloudVersionInventory { try inventory(at: url) }
    public func requestDownloads(at url: URL) {}
}

public enum CloudRecoveryError: Error, LocalizedError {
    case incomplete, stale, pending, storage, limit, heldBody
    public var errorDescription: String? {
        switch self {
        case .limit: return "版本处理上限为 16 个系统副版本；当前文件、每个副版本与合并结果各限 2 MiB。超出上限仍可查看或导出可读取内容；请在原编辑器中处理，重试下载不会解除此限制。"
        case .heldBody: return "原本机正文草稿仍单独保留，只读等待核对。请打开“核对双方版本”，明确采用当前文件或合并草稿后再继续编辑。"
        case .incomplete: return "部分 iCloud 版本尚不可读取，或版本信息不完整。可以查看和导出已有内容，请重试读取全部版本后再确认。"
        case .stale: return "文件或版本集合已有新变化。合并文字和先前版本仍保留，请刷新版本并重新核对。"
        case .pending: return "版本处理尚待核对。请打开版本恢复，重试核验；原本机草稿仍单独保留。"
        case .storage: return "版本备份尚未可靠保存或读取。当前文字保留，请检查设备存储后重试。"
        }
    }
}

public struct SystemCloudVersionProvider: CloudVersionProvider {
    public init() {}
    public func displayInventory(at url: URL) throws -> CloudVersionInventory { try inventory(at: url, readContents: false) }
    public func inventory(at url: URL) throws -> CloudVersionInventory { try inventory(at: url, readContents: true) }
    private func inventory(at url: URL, readContents: Bool) throws -> CloudVersionInventory {
        var fresh = url; fresh.removeAllCachedResourceValues()
        let values = try fresh.resourceValues(forKeys: [.ubiquitousItemHasUnresolvedConflictsKey])
        let objects = NSFileVersion.unresolvedConflictVersionsOfItem(at: url) ?? []
        var complete = objects.count <= CloudReview.maximumVersions
        var versions: [CloudVersion] = []
        for object in objects.prefix(CloudReview.maximumVersions) {
            guard let identity = try? Self.identity(object) else { complete = false; continue }
            var version = CloudVersion(id: identity, content: nil, modifiedAt: object.modificationDate)
            if readContents && object.hasLocalContents {
                do { version = CloudVersion(id: identity, content: try Self.read(object), modifiedAt: object.modificationDate) }
                catch JournalError.scanLimit { version.exceedsSizeLimit = true }
                catch { }
            }
            versions.append(version)
        }
        let conflict = values.ubiquitousItemHasUnresolvedConflicts == true || !objects.isEmpty
        complete = complete && (!conflict || !objects.isEmpty) && Set(versions.map(\.id)).count == versions.count
        return CloudVersionInventory(versions: versions.sorted { $0.id < $1.id }, hasConflict: conflict, complete: complete, totalVersions: objects.count)
    }

    public func requestDownloads(at url: URL) {
        var objects = NSFileVersion.unresolvedConflictVersionsOfItem(at: url) ?? []
        if objects.isEmpty {
            let result = NonlocalVersionResult(), semaphore = DispatchSemaphore(value: 0)
            NSFileVersion.getNonlocalVersionsOfItem(at: url) { versions, error in
                result.set(error == nil ? versions ?? [] : []); semaphore.signal()
            }
            guard semaphore.wait(timeout: .now() + 30) == .success else { return }
            objects = result.get().filter { $0.isConflict && !$0.isResolved }
        }
        for object in objects.prefix(CloudReview.maximumVersions) where !object.hasLocalContents {
            _ = try? Self.read(object)
        }
    }

    public func resolve(_ reviewed: [CloudVersion], at url: URL) throws {
        let objects = NSFileVersion.unresolvedConflictVersionsOfItem(at: url) ?? []
        guard objects.count == reviewed.count else { throw CloudRecoveryError.stale }
        var exact: [NSFileVersion] = []
        for object in objects {
            let identity = try Self.identity(object)
            guard object.hasLocalContents, let version = reviewed.first(where: { $0.id == identity }),
                  let content = version.content, try Self.read(object) == content else { throw CloudRecoveryError.stale }
            exact.append(object)
        }
        // Never remove versions or mark an object not included in the reviewed inventory.
        for object in exact { object.isResolved = true }
    }

    private static func identity(_ version: NSFileVersion) throws -> String {
        SourceHash.digest(try NSKeyedArchiver.archivedData(withRootObject: version.persistentIdentifier, requiringSecureCoding: false))
    }

    private static func read(_ version: NSFileVersion) throws -> Data {
        var error: NSError?, result: Result<Data, Error>?
        NSFileCoordinator().coordinate(readingItemAt: version.url, options: [], error: &error) { url in
            result = Result {
                guard let data = try JournalFileSystem().read(url, maximumBytes: CloudReview.maximumBytes) else {
                    throw OriginalVaultError.unavailable
                }
                return data
            }
        }
        guard error == nil, let result else { throw OriginalVaultError.unavailable }
        return try result.get()
    }
}

public struct CloudReview: Codable, Equatable, Sendable, Identifiable {
    public static let maximumVersions = 16
    public static let maximumBytes = 2 * 1024 * 1024
    public static let maximumRecordBytes = 64 * 1024 * 1024
    public let id: UUID
    public let day: JournalDay
    public let sourceID: String
    public let current: Data
    public let versions: [CloudVersion]
    public let complete: Bool
    public var totalVersions: Int? = nil
    public var resolutionIssue: String? {
        if (totalVersions ?? versions.count) > Self.maximumVersions || current.count > Self.maximumBytes
            || versions.contains(where: { $0.exceedsSizeLimit == true || ($0.content?.count ?? 0) > Self.maximumBytes }) {
            return "系统报告 \(totalVersions ?? versions.count) 个副版本。" + CloudRecoveryError.limit.localizedDescription
        }
        if !complete || versions.contains(where: { $0.content == nil }) { return CloudRecoveryError.incomplete.localizedDescription }
        if versions.contains(where: { $0.content.map { String(data: $0, encoding: .utf8) == nil } ?? false }) {
            return "有版本不是有效 UTF-8 Markdown，请导出并在原编辑器中核对。原版本保持不变。"
        }
        return nil
    }
    public var canKeepCurrent: Bool { complete && versions.isEmpty && resolutionIssue == nil }
    public var canResolve: Bool {
        resolutionIssue == nil && complete && !versions.isEmpty && current.count <= Self.maximumBytes
            && versions.count <= Self.maximumVersions
            && versions.allSatisfy { version in
                guard let bytes = version.content else { return false }
                return bytes.count <= Self.maximumBytes && String(data: bytes, encoding: .utf8) != nil
            }
    }
    init(day: JournalDay, sourceID: String, current: Data, inventory: CloudVersionInventory) {
        id = UUID(); self.day = day; self.sourceID = sourceID; self.current = current
        versions = inventory.versions; complete = inventory.complete; totalVersions = inventory.totalVersions
    }
}

public struct CloudRecoveryDraft: Codable, Equatable, Sendable {
    public let review: CloudReview
    public let content: Data
    public init(review: CloudReview, content: Data) { self.review = review; self.content = content }
    public var localSnapshot: JournalSnapshot {
        JournalSnapshot(day: review.day, markdown: String(decoding: review.current, as: UTF8.self), sourceHash: nil, draft: nil, isPending: false)
    }
}

struct CloudConfirmation: Codable, Equatable {
    let id: UUID
    let draft: CloudRecoveryDraft
    let heldBodyIDs: [UUID]
    var completed: Bool
}

public struct OriginalDisplaySnapshot: Sendable {
    public let data: Data?
    public let cloud: CloudReview?
}

private final class NonlocalVersionResult: @unchecked Sendable {
    private let lock = NSLock()
    private var versions: [NSFileVersion] = []
    func set(_ value: [NSFileVersion]) { lock.lock(); defer { lock.unlock() }; versions = value }
    func get() -> [NSFileVersion] { lock.lock(); defer { lock.unlock() }; return versions }
}

public struct CloudBackupState: Sendable {
    public var drafts: [CloudRecoveryDraft] = []
    public var unreadableCount = 0
    public var warning: String? {
        unreadableCount == 0 ? nil : "有 \(unreadableCount) 份本日备份暂不可读取；以下可读版本仍可查看和导出，原恢复文件均保留。"
    }
}

public struct CloudReviewState: Sendable {
    public let draft: CloudRecoveryDraft
    public let warning: String?
    public init(draft: CloudRecoveryDraft, warning: String?) { self.draft = draft; self.warning = warning }
}

struct CloudBodyHold: Codable {
    let day: JournalDay
    let sourceID: String
    var ids: [UUID]
    var active: Bool
}

struct CloudCompletion: Codable {
    let draftDigest: String
}

private struct CloudEvidenceIdentity: Encodable {
    let day: JournalDay
    let sourceID: String
    let current: Data
    let versions: [CloudVersion]
    let complete: Bool
    let totalVersions: Int
    let content: Data
}

extension CloudRecoveryDraft {
    func evidenceDigest() throws -> String {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let identity = CloudEvidenceIdentity(day: review.day, sourceID: review.sourceID, current: review.current,
            versions: review.versions.sorted { $0.id < $1.id }, complete: review.complete,
            totalVersions: review.totalVersions ?? review.versions.count, content: content)
        return SourceHash.digest(try encoder.encode(identity))
    }
}
