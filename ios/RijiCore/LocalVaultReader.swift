import Foundation
import Darwin

public enum VaultReadError: Error, LocalizedError {
    case unavailable, unsafePath, unsupported, tooLarge, invalidText, directoryLimit
    public var errorDescription: String? {
        switch self {
        case .unavailable: return "无法读取。文件可能已移动，或文件夹访问权限已失效。"
        case .unsafePath: return "此项目是链接或特殊文件，暂不支持打开。"
        case .unsupported: return "暂不支持预览这种文件。"
        case .tooLarge: return "文件超过 32 MB，暂不在 App 内预览。"
        case .invalidText: return "文件不是有效的 UTF-8 文本，无法显示。"
        case .directoryLimit: return "此层目录项目过多，暂时无法完整显示。"
        }
    }
}

public struct VaultEntry: Identifiable, Equatable, Sendable {
    public enum Kind: String, Sendable { case folder, file, unavailable }
    public let path: [String]
    public let kind: Kind
    public let size: Int64
    public let isFilePlaceholder: Bool
    public var id: String { path.joined(separator: "/") }
    public var name: String { path.last ?? "" }
    public init(path: [String], kind: Kind, size: Int64 = 0, isFilePlaceholder: Bool = false) {
        self.path = path; self.kind = kind; self.size = size; self.isFilePlaceholder = isFilePlaceholder
    }
}

struct VaultCollectionIdentity: Equatable, Sendable {
    let device: dev_t
    let inode: ino_t
}

struct VaultWriteLocation {
    let root: URL
    let path: [String]
    let collectionPath: [String]
    let collection: VaultCollectionIdentity
}

/// A bounded, read-only view of a caller-authorized directory. No indexing or recursive scan.
/// Descriptor-relative traversal prevents a symlink replacement from escaping the selected root.
public enum LocalVaultReader {
    public static let maximumPreviewBytes = 32 * 1024 * 1024
    private static let maximumEntries = 20_000

    public static func entries(root: URL, path: [String] = []) throws -> [VaultEntry] {
        let descriptor = try openItem(root: root, path: path)
        guard let directory = fdopendir(descriptor) else { close(descriptor); throw VaultReadError.unavailable }
        defer { closedir(directory) }
        var result: [VaultEntry] = [], count = 0
        while true {
            errno = 0
            guard let item = readdir(directory) else {
                guard errno == 0 else { throw VaultReadError.unavailable }
                break
            }
            count += 1
            guard count <= maximumEntries else { throw VaultReadError.directoryLimit }
            let name = withUnsafePointer(to: &item.pointee.d_name) {
                $0.withMemoryRebound(to: CChar.self, capacity: Int(NAME_MAX) + 1) { String(cString: $0) }
            }
            if name.hasPrefix("."), name.hasSuffix(".icloud") {
                let original = String(name.dropFirst().dropLast(7))
                var details = stat()
                let regular = fstatat(dirfd(directory), name, &details, AT_SYMLINK_NOFOLLOW) == 0 && details.st_mode & S_IFMT == S_IFREG
                let suffix = (original as NSString).pathExtension.lowercased()
                let fileSuffixes = ["md", "markdown", "txt", "csv", "json", "yaml", "yml", "log", "pdf", "png", "jpg", "jpeg", "gif", "heic", "webp", "mp3", "m4a", "wav", "mp4", "mov"]
                result.append(VaultEntry(path: path + [original], kind: .unavailable, isFilePlaceholder: regular && fileSuffixes.contains(suffix)))
                continue
            }
            guard !name.hasPrefix(".") else { continue }
            var details = stat()
            let valid = fstatat(dirfd(directory), name, &details, AT_SYMLINK_NOFOLLOW) == 0
            let type = details.st_mode & S_IFMT
            let kind: VaultEntry.Kind = !valid ? .unavailable : type == S_IFDIR ? .folder : type == S_IFREG ? .file : .unavailable
            result.append(VaultEntry(path: path + [name], kind: kind, size: valid ? Int64(details.st_size) : 0))
        }
        return result.sorted {
            if ($0.kind == .folder) != ($1.kind == .folder) { return $0.kind == .folder }
            return $0.name.localizedStandardCompare($1.name) == .orderedAscending
        }
    }

    public static func read(root: URL, path: [String], maximumBytes: Int = maximumPreviewBytes) throws -> Data {
        guard !path.isEmpty else { throw VaultReadError.unsafePath }
        let descriptor = try openItem(root: root, path: path)
        defer { close(descriptor) }
        var details = stat()
        guard fstat(descriptor, &details) == 0 else { throw VaultReadError.unavailable }
        guard details.st_mode & S_IFMT == S_IFREG else { throw VaultReadError.unsafePath }
        let limit = min(maximumBytes, maximumPreviewBytes)
        guard limit > 0, details.st_size <= limit else { throw VaultReadError.tooLarge }
        var data = Data(), buffer = [UInt8](repeating: 0, count: 64 * 1024)
        while true {
            let count = Darwin.read(descriptor, &buffer, buffer.count)
            if count < 0 && errno == EINTR { continue }
            guard count >= 0 else { throw VaultReadError.unavailable }
            if count == 0 { return data }
            guard data.count + count <= limit else { throw VaultReadError.tooLarge }
            data.append(contentsOf: buffer.prefix(count))
        }
    }

    public static func childURL(root: URL, path: [String]) throws -> URL {
        try validate(path)
        return path.reduce(root) { $0.appendingPathComponent($1) }
    }

    /// Callback URLs are interpreted relative to the original authorization, never as new roots.
    public static func relativePath(root: URL, callback: URL) throws -> [String] {
        guard root.isFileURL, callback.isFileURL else { throw VaultReadError.unsafePath }
        let base = root.standardizedFileURL.pathComponents, candidate = callback.standardizedFileURL.pathComponents
        guard candidate.starts(with: base) else { throw VaultReadError.unsafePath }
        let path = Array(candidate.dropFirst(base.count))
        try validate(path)
        return path
    }

    /// Descriptor-relative replacement keeps symlink swaps from redirecting a coordinated write.
    static func replace(location: VaultWriteLocation, expected: Data?, content: Data, failure: FileFailureHook,
                        beforeReplace: (() throws -> Void)? = nil) throws {
        let path = location.path
        guard content.count <= maximumPreviewBytes else { throw VaultReadError.tooLarge }
        try validate(path)
        guard let name = path.last else { throw VaultReadError.unsafePath }
        let directory = try openBoundParent(location)
        defer { close(directory) }
        let temporary = ".\(UUID().uuidString).tmp"
        try failure(.bodyDiary, .beforeWrite)
        let descriptor = openat(directory, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else { throw OriginalVaultError.unavailable }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? handle.close(); unlinkat(directory, temporary, 0) }
        try handle.write(contentsOf: content); try handle.synchronize()
        try failure(.bodyDiary, .beforeReplace)
        let verification = try openBoundParent(location)
        defer { close(verification) }
        var original = stat(), current = stat()
        guard fstat(directory, &original) == 0, fstat(verification, &current) == 0,
              original.st_dev == current.st_dev, original.st_ino == current.st_ino else { throw OriginalVaultError.changedIdentity }
        try preserveMetadata(directory: directory, name: name, replacement: descriptor, expected: expected)
        try handle.synchronize()
        let finalParent = try openBoundParent(location)
        defer { close(finalParent) }
        guard try identity(finalParent) == identity(directory) else { throw OriginalVaultError.changedIdentity }
        try beforeReplace?()
        guard renameat(directory, temporary, directory, name) == 0, fsync(directory) == 0 else { throw OriginalVaultError.unavailable }
        try failure(.bodyDiary, .afterReplace)
    }

    private static func preserveMetadata(directory: Int32, name: String, replacement: Int32, expected: Data?) throws {
        let target = openat(directory, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        if target < 0 {
            guard errno == ENOENT else { throw OriginalVaultError.unavailable }
            guard expected == nil else { throw JournalError.changedSource }
            return
        }
        let file = FileHandle(fileDescriptor: target, closeOnDealloc: true)
        defer { try? file.close() }
        var info = stat()
        guard fstat(target, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_size <= maximumPreviewBytes else { throw OriginalVaultError.unavailable }
        var bytes = Data()
        while let block = try file.read(upToCount: 65_536), !block.isEmpty {
            guard bytes.count + block.count <= maximumPreviewBytes else { throw VaultReadError.tooLarge }
            bytes.append(block)
        }
        guard bytes == expected else { throw JournalError.changedSource }
        // Failure preserves the original inode and removes only the temporary file.
        guard fchmod(replacement, info.st_mode & 0o7777) == 0,
              fcopyfile(target, replacement, nil, copyfile_flags_t(COPYFILE_ACL | COPYFILE_XATTR)) == 0 else {
            throw OriginalVaultError.unavailable
        }
    }

    static func collectionIdentity(root: URL, path: [String]) throws -> VaultCollectionIdentity {
        let descriptor = try openItem(root: root, path: path)
        defer { close(descriptor) }
        return try identity(descriptor)
    }

    private static func identity(_ descriptor: Int32) throws -> VaultCollectionIdentity {
        var value = stat()
        guard fstat(descriptor, &value) == 0, value.st_mode & S_IFMT == S_IFDIR else { throw OriginalVaultError.changedIdentity }
        return VaultCollectionIdentity(device: value.st_dev, inode: value.st_ino)
    }

    /// The checked collection descriptor is also the root of the actual traversal.
    private static func openBoundParent(_ location: VaultWriteLocation) throws -> Int32 {
        guard location.path.starts(with: location.collectionPath),
              location.path.count > location.collectionPath.count else { throw OriginalVaultError.changedIdentity }
        let collection = try openItem(root: location.root, path: location.collectionPath)
        do {
            guard try identity(collection) == location.collection else { throw OriginalVaultError.changedIdentity }
            let relative = Array(location.path.dropFirst(location.collectionPath.count).dropLast())
            return try descend(collection, path: relative)
        } catch { close(collection); throw error }
    }

    /// On success ownership moves to the returned descriptor. On failure the
    /// initial descriptor remains owned by the caller; intermediate ones close.
    private static func descend(_ initial: Int32, path: [String]) throws -> Int32 {
        var descriptor = initial
        for name in path {
            let next = openat(descriptor, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_NONBLOCK)
            if next < 0 { if descriptor != initial { close(descriptor) }; throw VaultReadError.unavailable }
            if descriptor != initial { close(descriptor) }
            descriptor = next
        }
        if descriptor != initial { close(initial) }
        return descriptor
    }

    private static func openItem(root: URL, path: [String]) throws -> Int32 {
        try validate(path)
        guard root.isFileURL else { throw VaultReadError.unsafePath }
        var descriptor = Darwin.open(root.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_NONBLOCK)
        guard descriptor >= 0 else { throw VaultReadError.unavailable }
        for (index, name) in path.enumerated() {
            let flags = O_RDONLY | O_NOFOLLOW | O_NONBLOCK | (index < path.count - 1 ? O_DIRECTORY : 0)
            let next = openat(descriptor, name, flags)
            close(descriptor)
            guard next >= 0 else { throw VaultReadError.unavailable }
            descriptor = next
        }
        return descriptor
    }

    private static func validate(_ path: [String]) throws {
        guard path.count <= 64, path.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." && !$0.contains("/") && !$0.contains("\0") }) else {
            throw VaultReadError.unsafePath
        }
    }
}
