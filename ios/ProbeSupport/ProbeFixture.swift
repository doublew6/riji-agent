import CryptoKit
import Foundation

/// Test-only protocol shared by the phone harness, local guards and developer CLI.
/// No application target includes this file.
public struct ProbeFixture: Sendable {
    public enum Phase: String, CaseIterable, Sendable {
        case pristine, phoneSaved, phoneReady, macReady
    }
    public enum Storage: Sendable { case iCloud, localTest }
    public let run: UUID
    public static let namespace = "BD30E2CF-68EB-4E71-909F-58115F3EF954"
    public static let savedPath = "daily/nested/2099-01-01.md"
    public static let modifiedPath = "daily/nested/2099-01-02.md"
    public static let deletedPath = "daily/nested/2099-01-03.md"
    public static let createdPath = "daily/2099-01-04.md"
    public static let directories: Set<String> = ["daily", "daily/nested", "templates"]

    public init(run: UUID) { self.run = run }
    public var folderName: String { "Riji-iCloud-Probe-\(run.uuidString)" }

    public func document(_ label: String) -> Data {
        Data("---\r\nsynthetic_probe: \(run.uuidString)\r\n---\r\n# Synthetic only\r\n\(label) 中文 🧠 e\u{301}\r\n| Item | Value |\r\n| --- | --- |\r\n| | 1 |\r\n".utf8)
    }

    public func files(_ phase: Phase) -> [String: Data] {
        var files = [
            "probe.json": Data("{\"protocol\":1,\"run\":\"\(run.uuidString)\",\"syntheticOnly\":true}\n".utf8),
            "templates/daily.md": document("Template {{date:YYYY-MM-DD}} {{unknown}}"),
            Self.savedPath: document("Initial saved day"),
            Self.modifiedPath: document("Initial modified day"),
            Self.deletedPath: document("Initial deleted day")
        ]
        if phase != .pristine { files[Self.savedPath] = document("Phone saved day") }
        if phase == .phoneReady || phase == .macReady {
            files["phone-ready.json"] = receipt("phone-ready", checks: "read,save,idempotency,durable-drafts")
        }
        if phase == .macReady {
            files[Self.savedPath] = document("Mac updated saved day")
            files[Self.modifiedPath] = document("Mac modified drafted day")
            files[Self.deletedPath] = nil
            files[Self.createdPath] = document("Mac created drafted day")
            files["mac-ready.json"] = receipt("mac-ready", checks: "phone-propagated,modify,delete,create")
        }
        return files
    }

    private func receipt(_ phase: String, checks: String) -> Data {
        Data("{\"checks\":\"\(checks)\",\"phase\":\"\(phase)\",\"protocol\":1,\"run\":\"\(run.uuidString)\"}\n".utf8)
    }

    public func hashes(_ phase: Phase) -> [String: String] {
        files(phase).mapValues { Self.digest($0) }
    }

    public static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    public func validate(_ root: URL, phase: Phase, storage: Storage = .iCloud) throws {
        guard root.lastPathComponent == folderName else { throw ProbeFailure.wrongFixture }
        try Self.coordinate(root, writing: false) { url in
            try validateContents(url, phase: phase, storage: storage)
        }
    }

    /// Must run inside a coordinated callback. Enumeration is bounded and never follows links.
    public func validateContents(_ root: URL, phase: Phase, storage: Storage) throws {
        guard root.lastPathComponent == folderName else { throw ProbeFailure.wrongFixture }
        try Self.requireDirectory(root)
        let expected = files(phase)
        var foundFiles = Set<String>(), foundDirectories = Set<String>(), pending = [""]
        while let relative = pending.popLast() {
            let directory = relative.isEmpty ? root : root.appendingPathComponent(relative)
            try Self.requireAvailable(directory, storage: storage, directory: true)
            let children = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            guard children.count <= expected.count + Self.directories.count else { throw ProbeFailure.unexpectedEntry }
            for child in children {
                let path = relative.isEmpty ? child.lastPathComponent : relative + "/" + child.lastPathComponent
                if Self.directories.contains(path) {
                    try Self.requireDirectory(child)
                    guard foundDirectories.insert(path).inserted else { throw ProbeFailure.unexpectedEntry }
                    pending.append(path)
                } else {
                    guard let bytes = expected[path] else { throw ProbeFailure.unexpectedEntry }
                    try Self.requireAvailable(child, storage: storage, directory: false)
                    let attributes = try FileManager.default.attributesOfItem(atPath: child.path)
                    guard (attributes[.size] as? NSNumber)?.intValue == bytes.count,
                          try Data(contentsOf: child) == bytes else {
                        throw path == "probe.json" ? ProbeFailure.wrongFixture : ProbeFailure.unexpectedBytes
                    }
                    foundFiles.insert(path)
                }
            }
        }
        guard foundFiles == Set(expected.keys), foundDirectories == Self.directories else { throw ProbeFailure.missingEntry }
    }

    public static func requireDirectory(_ url: URL) throws {
        guard url.standardizedFileURL == url.resolvingSymlinksInPath().standardizedFileURL,
              try FileManager.default.attributesOfItem(atPath: url.path)[.type] as? FileAttributeType == .typeDirectory else {
            throw ProbeFailure.unsafePath
        }
    }

    public static func requireAvailable(_ url: URL, storage: Storage, directory: Bool) throws {
        let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
        guard attributes[.type] as? FileAttributeType == (directory ? .typeDirectory : .typeRegular),
              (attributes[.referenceCount] as? NSNumber)?.intValue ?? 1 == 1 || directory else { throw ProbeFailure.unsafePath }
        let values = try url.resourceValues(forKeys: [.isUbiquitousItemKey, .ubiquitousItemDownloadingStatusKey,
                                                     .ubiquitousItemHasUnresolvedConflictsKey])
        guard values.ubiquitousItemHasUnresolvedConflicts != true,
              NSFileVersion.unresolvedConflictVersionsOfItem(at: url)?.isEmpty != false else { throw ProbeFailure.osConflict }
        if storage == .iCloud {
            guard values.isUbiquitousItem == true else { throw ProbeFailure.notICloud }
            if !directory {
                guard values.ubiquitousItemDownloadingStatus == .current || values.ubiquitousItemDownloadingStatus == .downloaded else {
                    throw ProbeFailure.unavailable
                }
            }
        }
    }

    public static func coordinate<T>(_ root: URL, writing: Bool, operation: (URL) throws -> T) throws -> T {
        try requireDirectory(root)
        var error: NSError?, result: Result<T, Error>?
        let callback: (URL) -> Void = { url in
            result = Result {
                guard url.standardizedFileURL == root.standardizedFileURL else { throw ProbeFailure.unsafePath }
                try requireDirectory(root)
                return try operation(url)
            }
        }
        if writing {
            NSFileCoordinator().coordinate(writingItemAt: root, options: .forMerging, error: &error, byAccessor: callback)
        } else {
            NSFileCoordinator().coordinate(readingItemAt: root, options: [], error: &error, byAccessor: callback)
        }
        guard error == nil, let result else { throw ProbeFailure.coordination }
        return try result.get()
    }
}

/// Only fixed codes are logged; underlying Foundation errors may contain private URLs.
public enum ProbeFailure: String, Error {
    case wrongFixture, unexpectedEntry, unexpectedBytes, missingEntry, unsafePath
    case notICloud, unavailable, osConflict, coordination, exists, invalidArguments
    case cancelled, pickerTimeout, noPresenter, namespace, state, assertion, staleBookmark
    case sameProcess, rejectedWriteSucceeded, receipt, deadline
    public static func code(_ error: Error) -> String { (error as? Self)?.rawValue ?? "unavailable" }
}
