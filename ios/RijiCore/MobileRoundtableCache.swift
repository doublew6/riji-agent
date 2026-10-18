import Foundation
import Darwin

/// A short-lived first page only. Kept outside draft archives and excluded from backups.
public final class MobileRoundtableCache: @unchecked Sendable {
    private struct Envelope: Codable {
        let credentialHash: String
        let savedAt: Double
        let page: MobileRoundtable
        let otherPages: [MobileRoundtable]?
    }
    private let directory: URL
    private let lock = NSLock()
    private let removeFile: @Sendable (URL) throws -> Void
    public init(directory: URL, removeFile: @escaping @Sendable (URL) throws -> Void = { try FileManager.default.removeItem(at: $0) }) {
        self.directory = directory; self.removeFile = removeFile
    }
    // Admission is deliberately outside the cache directory. A failed cache deletion
    // must remain revoked after relaunch. No text, credentials, or paths enter defaults.
    private func admissionKey(_ credential: MobileCredential) -> String {
        "mobile-roundtable-admission-" + SourceHash.digest(Data((directory.path + credential.scope).utf8))
    }
    private func admit(_ value: String?, credential: MobileCredential) throws {
        UserDefaults.standard.set(value, forKey: admissionKey(credential))
        guard UserDefaults.standard.synchronize() else { throw MobileError.storage }
    }
    private func key(_ credential: MobileCredential) -> String { SourceHash.digest(Data((credential.scope + credential.token).utf8)) }
    private func file(_ credential: MobileCredential) -> URL { directory.appendingPathComponent(credential.scope + ".json") }
    public func load(_ credential: MobileCredential, now: Date = Date(), conversationID: String? = nil) throws -> MobileRoundtable? {
        lock.lock(); defer { lock.unlock() }
        return try readPages(credential, now: now).first { conversationID == nil || $0.conversationId == conversationID }
    }
    public func cachedList(_ credential: MobileCredential, now: Date = Date()) throws -> MobileRoundtableList? {
        lock.lock(); defer { lock.unlock() }
        let pages = try readPages(credential, now: now)
        return pages.isEmpty ? nil : MobileRoundtableList(cached: pages)
    }
    private func readPages(_ credential: MobileCredential, now: Date) throws -> [MobileRoundtable] {
        var info = stat(); let url = file(credential)
        if lstat(url.path, &info) != 0 { if errno == ENOENT || errno == ENOTDIR { return [] }; throw MobileError.storage }
        guard info.st_mode & S_IFMT == S_IFREG, info.st_size <= 1_048_576 else { throw MobileError.storage }
        do {
            let data = try Data(contentsOf: url)
            guard UserDefaults.standard.string(forKey: admissionKey(credential)) == SourceHash.digest(data) else { return [] }
            let value = try JSONDecoder().decode(Envelope.self, from: data)
            guard value.credentialHash == key(credential), value.savedAt <= now.timeIntervalSince1970,
                  now.timeIntervalSince1970 - value.savedAt <= 900 else { return [] }
            let pages = [value.page] + (value.otherPages ?? [])
            return pages.filter { (try? $0.validated(now: now)) != nil }
        } catch { try? admit(nil, credential: credential); try? removeFile(url); return [] }
    }
    public func save(_ page: MobileRoundtable, credential: MobileCredential) throws {
        lock.lock(); defer { lock.unlock() }
        _ = try page.validated()
        do {
            var previous = ((try? readPages(credential, now: Date())) ?? [])
                .filter { $0.conversationId != page.conversationId }
            try admit(nil, credential: credential)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            var values = URLResourceValues(); values.isExcludedFromBackup = true
            var folder = directory; try folder.setResourceValues(values)
            var data = Data()
            previous = Array(previous.prefix(7))
            repeat {
                data = try JSONEncoder().encode(Envelope(credentialHash: key(credential), savedAt: Date().timeIntervalSince1970,
                    page: page, otherPages: previous))
                if data.count > 1_048_576 && !previous.isEmpty { previous.removeLast() } else { break }
            } while true
            guard data.count <= 1_048_576 else { throw MobileError.storage }
            #if os(iOS)
            try data.write(to: file(credential), options: [.atomic, .completeFileProtection])
            #else
            try data.write(to: file(credential), options: .atomic)
            #endif
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file(credential).path)
            try admit(SourceHash.digest(data), credential: credential)
        } catch { throw MobileError.storage }
    }
    public func invalidate(_ credential: MobileCredential) throws {
        lock.lock(); defer { lock.unlock() }
        do { try admit(nil, credential: credential); try removeFile(file(credential)) }
        catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileNoSuchFileError { return }
        catch { throw MobileError.storage }
    }
}
