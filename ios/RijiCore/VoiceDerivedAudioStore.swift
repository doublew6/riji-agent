import Foundation
import Darwin

/// A namespace-owned, request-owned cache. Original audio and user exports never enter cleanup.
public final class VoiceDerivedAudioCache: @unchecked Sendable {
    private static let lock = NSRecursiveLock()
    private static var active: Set<String> = []
    public let directory: URL
    private let files: JournalFileSystem

    public init(directory: URL, failure: @escaping FileFailureHook = { _, _ in }) {
        self.directory = directory; files = JournalFileSystem(failure: failure)
    }

    public func snapshot(data: Data, recordingID: UUID, segmentID: UUID, fileExtension: String) throws -> VoiceDerivedAudioLease {
        Self.lock.lock(); defer { Self.lock.unlock() }
        guard !data.isEmpty, data.count <= 32 * 1024 * 1024, ["m4a", "wav"].contains(fileExtension) else { throw VoiceError.changedMedia }
        let folder = try recordingFolder(recordingID)
        guard try files.read(folder.appendingPathComponent("revoked.json"), maximumBytes: 100) == nil else { throw VoiceError.deleted }
        let name = "\(segmentID.uuidString)-\(SourceHash.digest(data))-\(UUID().uuidString).\(fileExtension)"
        let url = folder.appendingPathComponent(name)
        // The ownership layout is durable before the data is written, including uncertain-write recovery.
        try files.write(data, to: url, purpose: .voiceDerived)
        guard try files.read(url, maximumBytes: 32 * 1024 * 1024) == data else { throw JournalError.storage }
        Self.active.insert(Self.key(url))
        return VoiceDerivedAudioLease(url: url, cache: self)
    }

    public func revoke(_ recordingID: UUID) throws {
        Self.lock.lock(); defer { Self.lock.unlock() }
        let folder = try recordingFolder(recordingID)
        try files.write(Data("true".utf8), to: folder.appendingPathComponent("revoked.json"), purpose: .voiceDerived)
        try clean(recordingID)
    }

    /// Startup retries only stale requests in this exact namespace; active requests keep their immutable bytes.
    public func recoverStale() throws {
        Self.lock.lock(); defer { Self.lock.unlock() }
        try files.createDirectory(directory)
        for url in try files.list(directory) {
            guard let id = UUID(uuidString: url.lastPathComponent) else { continue }
            try clean(id)
        }
    }

    public func removalComplete(_ recordingID: UUID) throws -> Bool {
        Self.lock.lock(); defer { Self.lock.unlock() }
        return try ownedURLs(recordingID).isEmpty
    }

    public func maintenancePending() throws -> Bool {
        Self.lock.lock(); defer { Self.lock.unlock() }
        try files.createDirectory(directory)
        for url in try files.list(directory) {
            guard let id = UUID(uuidString: url.lastPathComponent) else { continue }
            if try ownedURLs(id).contains(where: { !Self.active.contains(Self.key($0)) }) { return true }
        }
        return false
    }

    fileprivate func finish(_ url: URL) throws {
        Self.lock.lock(); defer { Self.lock.unlock() }
        Self.active.remove(Self.key(url))
        try removeOwned(url)
    }

    private static func key(_ url: URL) -> String {
        url.deletingLastPathComponent().standardizedFileURL.resolvingSymlinksInPath().appendingPathComponent(url.lastPathComponent).path
    }

    private func clean(_ recordingID: UUID) throws {
        for url in try ownedURLs(recordingID) where !Self.active.contains(Self.key(url)) { try removeOwned(url) }
    }

    private func recordingFolder(_ id: UUID) throws -> URL {
        try files.createDirectory(directory)
        let folder = directory.appendingPathComponent(id.uuidString)
        try files.createDirectory(folder)
        return folder
    }

    private func ownedURLs(_ id: UUID) throws -> [URL] {
        let folder = try recordingFolder(id)
        return try files.list(folder).filter { url in
            let stem = url.deletingPathExtension().lastPathComponent
            guard ["m4a", "wav"].contains(url.pathExtension), stem.count == 138 else { return false }
            let first = String(stem.prefix(36)), hash = String(stem.dropFirst(37).prefix(64)), last = String(stem.suffix(36))
            return UUID(uuidString: first) != nil && UUID(uuidString: last) != nil && hash.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
        }
    }

    private func removeOwned(_ url: URL) throws {
        var info = stat()
        guard lstat(url.path, &info) == 0 else { if errno == ENOENT { return }; throw JournalError.storage }
        guard info.st_mode & S_IFMT == S_IFREG else { throw JournalError.storage }
        try files.remove(url, purpose: .voiceDerived)
    }
}

public final class VoiceDerivedAudioLease: @unchecked Sendable {
    public let url: URL
    private let cache: VoiceDerivedAudioCache
    fileprivate init(url: URL, cache: VoiceDerivedAudioCache) { self.url = url; self.cache = cache }
    public func finish() throws { try cache.finish(url) }
    deinit { try? finish() }
}
