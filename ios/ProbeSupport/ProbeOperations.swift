import Foundation

extension ProbeFixture {
    public func create(at root: URL, storage: Storage = .iCloud) throws {
        guard root.lastPathComponent == folderName else { throw ProbeFailure.wrongFixture }
        let parent = root.deletingLastPathComponent()
        try Self.coordinate(parent, writing: true) { _ in
            try Self.requireAvailable(parent, storage: storage, directory: true)
            guard !FileManager.default.fileExists(atPath: root.path),
                  (try? FileManager.default.attributesOfItem(atPath: root.path)) == nil else { throw ProbeFailure.exists }
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
            for path in Self.directories.sorted() {
                try FileManager.default.createDirectory(at: root.appendingPathComponent(path), withIntermediateDirectories: false)
            }
            for (path, bytes) in files(.pristine) {
                try bytes.write(to: root.appendingPathComponent(path), options: .withoutOverwriting)
            }
            // Newly created iCloud items may not have metadata immediately. No writer
            // admission follows creation until a separate cloud validation succeeds.
            try validateContents(root, phase: .pristine, storage: .localTest)
        }
    }

    public func writePhoneReceipt(_ root: URL, storage: Storage = .iCloud) throws {
        try Self.coordinate(root, writing: true) { url in
            try validateContents(url, phase: .phoneSaved, storage: storage)
            try files(.phoneReady)["phone-ready.json"]!.write(to: url.appendingPathComponent("phone-ready.json"), options: .withoutOverwriting)
            try validateContents(url, phase: .phoneReady, storage: .localTest)
        }
    }

    public func applyMacChanges(_ root: URL, evidence: URL, storage: Storage = .iCloud) throws {
        try Self.coordinate(root, writing: true) { url in
            try validateContents(url, phase: .phoneReady, storage: storage)
            try prepareEvidence(evidence, root: url)
            try files(.phoneReady)[Self.deletedPath]!.write(to: evidence.appendingPathComponent("deleted-synthetic.md"), options: .withoutOverwriting)
            for path in [Self.savedPath, Self.modifiedPath, Self.deletedPath, Self.createdPath] {
                try changeFile(root: url, path: path)
            }
            let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
            let hashes = try encoder.encode(self.hashes(.macReady))
            let hashesURL = evidence.appendingPathComponent("mac-hashes.json")
            try hashes.write(to: hashesURL, options: .withoutOverwriting)
            guard try Data(contentsOf: hashesURL) == hashes else { throw ProbeFailure.unexpectedBytes }
            let receipt = files(.macReady)["mac-ready.json"]!
            try receipt.write(to: url.appendingPathComponent("mac-ready.json"), options: .withoutOverwriting)
            try validateContents(url, phase: .macReady, storage: .localTest)
        }
    }

    private func prepareEvidence(_ evidence: URL, root: URL) throws {
        let parent = evidence.deletingLastPathComponent()
        try Self.requireDirectory(parent)
        let path = evidence.standardizedFileURL.path
        guard !path.hasPrefix(root.standardizedFileURL.path + "/"), path != root.standardizedFileURL.path,
              !FileManager.default.fileExists(atPath: path),
              (try? FileManager.default.attributesOfItem(atPath: path)) == nil else { throw ProbeFailure.unsafePath }
        let values = try parent.resourceValues(forKeys: [.isUbiquitousItemKey])
        guard values.isUbiquitousItem != true else { throw ProbeFailure.unsafePath }
        try FileManager.default.createDirectory(at: evidence, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
    }

    private func changeFile(root: URL, path: String) throws {
        let target = root.appendingPathComponent(path), previous = files(.phoneReady)[path], next = files(.macReady)[path]
        var error: NSError?, result: Result<Void, Error>?
        NSFileCoordinator().coordinate(writingItemAt: target, options: next == nil ? .forDeleting : .forReplacing, error: &error) { url in
            result = Result {
                guard url.standardizedFileURL == target.standardizedFileURL else { throw ProbeFailure.unsafePath }
                try Self.requireDirectory(target.deletingLastPathComponent())
                if let previous {
                    try Self.requireAvailable(target, storage: .localTest, directory: false)
                    guard try Data(contentsOf: target) == previous else { throw ProbeFailure.unexpectedBytes }
                } else {
                    guard (try? FileManager.default.attributesOfItem(atPath: target.path)) == nil else { throw ProbeFailure.exists }
                }
                if let next { try next.write(to: target, options: previous == nil ? .withoutOverwriting : .atomic) }
                else { try FileManager.default.removeItem(at: target) }
            }
        }
        guard error == nil, let result else { throw ProbeFailure.coordination }
        try result.get()
    }
}
