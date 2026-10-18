import Foundation
import Darwin

public enum FilePurpose: Sendable { case aiPreview, aiTransaction, aiReceipt, cloudDraft, cloudBackup, cloudConfirmation, bodyMerge, bodyResolution, bodyDraft, bodyTransaction, bodyDiary, bodyReceipt, bodyRevision, voiceDerived, voiceHistory, voiceSummary, voiceEvidence, voiceTranscript, voiceCapture, voiceCorrection, voiceAbandonment, metadata, draft, transaction, diary, receipt, export, voice, audio, voiceIntent }
public enum FileStage: Sendable { case beforeRead, beforeWrite, beforeReplace, afterReplace, beforeRemove, afterRemove }
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
            #if os(iOS)
            try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: url.path)
            #endif
            try synchronizeDirectory(url)
            try synchronizeDirectory(url.deletingLastPathComponent())
        }
        catch { throw JournalError.storage }
    }

    func read(_ url: URL, maximumBytes: Int? = nil) throws -> Data? {
        let descriptor = open(url.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        if descriptor < 0 {
            if errno == ENOENT { return nil }
            throw JournalError.storage
        }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? handle.close() }
        var before = stat()
        guard fstat(descriptor, &before) == 0, before.st_mode & S_IFMT == S_IFREG else { throw JournalError.storage }
        if let maximumBytes, before.st_size > maximumBytes { throw JournalError.scanLimit }
        do {
            var data = Data()
            while let block = try handle.read(upToCount: 65536), !block.isEmpty {
                if let maximumBytes, data.count + block.count > maximumBytes { throw JournalError.scanLimit }
                data.append(block)
            }
            var after = stat(), named = stat()
            guard fstat(descriptor, &after) == 0, lstat(url.path, &named) == 0,
                  before.st_ino == named.st_ino, before.st_dev == named.st_dev, data.count == before.st_size,
                  after.st_size == before.st_size, after.st_mtimespec.tv_sec == before.st_mtimespec.tv_sec,
                  after.st_mtimespec.tv_nsec == before.st_mtimespec.tv_nsec,
                  after.st_ctimespec.tv_sec == before.st_ctimespec.tv_sec,
                  after.st_ctimespec.tv_nsec == before.st_ctimespec.tv_nsec else { throw JournalError.storage }
            return data
        } catch let error as JournalError { throw error }
        catch { throw JournalError.storage }
    }

    func list(_ url: URL) throws -> [URL] {
        do { return try FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: nil) }
        catch { throw JournalError.storage }
    }

    func write(_ data: Data, to url: URL, purpose: FilePurpose, beforeReplace: (() throws -> Void)? = nil) throws {
        let temporary = url.deletingLastPathComponent().appendingPathComponent(".\(UUID().uuidString).tmp")
        do {
            try failure(purpose, .beforeWrite)
            let descriptor = open(temporary.path, O_WRONLY | O_CREAT | O_EXCL, S_IRUSR | S_IWUSR)
            guard descriptor >= 0 else { throw JournalError.storage }
            let file = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
            defer { try? file.close(); try? FileManager.default.removeItem(at: temporary) }
            #if os(iOS)
            try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: temporary.path)
            #endif
            try file.write(contentsOf: data)
            try file.synchronize()
            try failure(purpose, .beforeReplace)
            try beforeReplace?()
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

    func synchronizeDirectory(_ url: URL) throws {
        let descriptor = open(url.path, O_RDONLY)
        guard descriptor >= 0 else { throw JournalError.storage }
        defer { close(descriptor) }
        guard fsync(descriptor) == 0 else { throw JournalError.storage }
    }
}
