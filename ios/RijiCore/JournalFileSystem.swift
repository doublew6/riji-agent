import Foundation
import Darwin

public enum FilePurpose: Sendable { case metadata, draft, transaction, diary, receipt, export }
public enum FileStage: Sendable { case beforeWrite, beforeReplace, afterReplace, beforeRemove, afterRemove }
public typealias FileFailureHook = @Sendable (FilePurpose, FileStage) throws -> Void

/// Each replacement is flushed before rename; the containing directory is flushed afterwards.
/// The hook exercises failures on both sides of the atomic replacement without private data.
struct JournalFileSystem: Sendable {
    let failure: FileFailureHook

    init(failure: @escaping FileFailureHook = { _, _ in }) { self.failure = failure }

    func createDirectory(_ url: URL) throws {
        do {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
            var info = stat()
            guard lstat(url.path, &info) == 0, (info.st_mode & S_IFMT) == S_IFDIR else { throw JournalError.storage }
            try synchronizeDirectory(url)
            try synchronizeDirectory(url.deletingLastPathComponent())
        }
        catch { throw JournalError.storage }
    }

    func read(_ url: URL, maximumBytes: Int? = nil) throws -> Data? {
        var info = stat()
        if lstat(url.path, &info) != 0 {
            if errno == ENOENT { return nil }
            throw JournalError.storage
        }
        guard (info.st_mode & S_IFMT) == S_IFREG else { throw JournalError.storage }
        if let maximumBytes, info.st_size > maximumBytes { throw JournalError.scanLimit }
        do { return try Data(contentsOf: url) }
        catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError {
            return nil
        } catch { throw JournalError.storage }
    }

    func list(_ url: URL) throws -> [URL] {
        do { return try FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: nil) }
        catch { throw JournalError.storage }
    }

    func write(_ data: Data, to url: URL, purpose: FilePurpose) throws {
        let temporary = url.deletingLastPathComponent().appendingPathComponent(".\(UUID().uuidString).tmp")
        do {
            try failure(purpose, .beforeWrite)
            let descriptor = open(temporary.path, O_WRONLY | O_CREAT | O_EXCL, S_IRUSR | S_IWUSR)
            guard descriptor >= 0 else { throw JournalError.storage }
            let file = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
            defer { try? file.close(); try? FileManager.default.removeItem(at: temporary) }
            try file.write(contentsOf: data)
            try file.synchronize()
            try failure(purpose, .beforeReplace)
            guard rename(temporary.path, url.path) == 0 else { throw JournalError.storage }
            try synchronizeDirectory(url.deletingLastPathComponent())
            try failure(purpose, .afterReplace)
        } catch { throw JournalError.storage }
    }

    func remove(_ url: URL, purpose: FilePurpose) throws {
        do {
            try failure(purpose, .beforeRemove)
            if try read(url) != nil { try FileManager.default.removeItem(at: url) }
            try synchronizeDirectory(url.deletingLastPathComponent())
            try failure(purpose, .afterRemove)
        } catch { throw JournalError.storage }
    }

    private func synchronizeDirectory(_ url: URL) throws {
        let descriptor = open(url.path, O_RDONLY)
        guard descriptor >= 0 else { throw JournalError.storage }
        defer { close(descriptor) }
        guard fsync(descriptor) == 0 else { throw JournalError.storage }
    }
}
