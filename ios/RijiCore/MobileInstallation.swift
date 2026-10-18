import Foundation
import Darwin

public enum MobileInstallationError: Error, LocalizedError {
    case invalid, consumption, incomplete
    public var errorDescription: String? {
        "服务安装未完成，请更新安装后重试。本地日记和原有设备记录仍保留。"
    }
}

public struct MobileInstallationPackage: Codable, Sendable {
    public let apiVersion: Int
    public let endpoint: String
    public let previousEndpoint: String?
    public let deviceId: String?
    public let deviceToken: String?
    enum CodingKeys: String, CodingKey {
        case apiVersion = "api_version", endpoint, previousEndpoint = "previous_endpoint"
        case deviceId = "device_id", deviceToken = "device_token"
    }
    public init(endpoint: String, previousEndpoint: String? = nil, deviceId: String? = nil, deviceToken: String? = nil) {
        apiVersion = 1; self.endpoint = endpoint; self.previousEndpoint = previousEndpoint
        self.deviceId = deviceId; self.deviceToken = deviceToken
    }
    public func validate() throws {
        guard apiVersion == 1, endpoint.utf8.count <= 2048,
              (try? MobileEndpoint(endpoint).origin) == endpoint,
              previousEndpoint.map({ $0.utf8.count <= 2048 && (try? MobileEndpoint($0).origin) == $0 }) ?? true,
              (deviceId == nil) == (deviceToken == nil) else { throw MobileInstallationError.invalid }
        if let deviceId, let deviceToken {
            guard MobileEndpoint.validIdentifier(deviceId), (32...128).contains(deviceToken.utf8.count),
                  deviceToken.utf8.allSatisfy({ (65...90).contains($0) || (97...122).contains($0) || (48...57).contains($0) || $0 == 45 || $0 == 95 })
            else { throw MobileInstallationError.invalid }
        }
    }
}

public struct MobileInstallationReceipt: Codable, Sendable {
    public let status: String
    public let usedDeviceId: String?
    public let unusedProvisionedDeviceId: String?
    enum CodingKeys: String, CodingKey {
        case status, usedDeviceId = "used_device_id", unusedProvisionedDeviceId = "unused_provisioned_device_id"
    }
}

public protocol MobileInstallationSource: Sendable {
    func read() throws -> Data?
    func invalidateReceipt() throws
    func consume(_ data: Data) throws
    func receipt(_ value: MobileInstallationReceipt) throws
}

/// The installer writes only to Library/Caches. Nothing is imported from Documents or iCloud.
public final class MobileInstallationFile: MobileInstallationSource, @unchecked Sendable {
    private let directory: URL
    private let claim: @Sendable (URL, URL) throws -> Void
    private var packageURL: URL { directory.appendingPathComponent("MobileInstallation.json") }
    private var recoveryURL: URL { directory.appendingPathComponent(".MobileInstallationRecovery") }
    public init(directory: URL) { self.directory = directory; claim = Self.claimPath }
    init(directory: URL, claim: @escaping @Sendable (URL, URL) throws -> Void) {
        self.directory = directory; self.claim = claim
    }
    public func read() throws -> Data? {
        try requireNoRecovery()
        return try read(packageURL)
    }
    private func requireNoRecovery() throws {
        var info = stat()
        guard lstat(recoveryURL.path, &info) != 0, errno == ENOENT else { throw MobileInstallationError.incomplete }
    }
    private func read(_ file: URL) throws -> Data? {
        let descriptor = open(file.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        if descriptor < 0, errno == ENOENT { return nil }
        guard descriptor >= 0 else { throw MobileInstallationError.invalid }
        defer { close(descriptor) }
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_size > 0, info.st_size <= 16_384, info.st_nlink == 1 else { throw MobileInstallationError.invalid }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: false)
        let data = try handle.read(upToCount: 16_385) ?? Data()
        guard data.count == info.st_size, data.count <= 16_384 else { throw MobileInstallationError.invalid }
        return data
    }
    public func invalidateReceipt() throws {
        let file = directory.appendingPathComponent("MobileInstallationReceipt.json")
        guard unlink(file.path) == 0 || errno == ENOENT else { throw MobileInstallationError.incomplete }
    }
    static func claimPath(_ source: URL, _ destination: URL) throws {
        guard renamex_np(source.path, destination.path, UInt32(RENAME_EXCL)) == 0 else { throw MobileInstallationError.consumption }
    }
    public func consume(_ data: Data) throws {
        try requireNoRecovery()
        let recovery = recoveryURL
        var attributes: [FileAttributeKey: Any] = [.posixPermissions: 0o700]
        #if os(iOS)
        attributes[.protectionKey] = FileProtectionType.complete
        #endif
        try FileManager.default.createDirectory(at: recovery, withIntermediateDirectories: false, attributes: attributes)
        let claimed = recovery.appendingPathComponent("package.json")
        do {
            try claim(packageURL, claimed)
            // A new USB replacement is never removed. Unexpected claimed bytes remain recoverable.
            let claimedData = try read(claimed)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: claimed.path)
            var replacement = stat()
            guard claimedData == data, lstat(packageURL.path, &replacement) != 0, errno == ENOENT,
                  unlink(claimed.path) == 0 else { throw MobileInstallationError.consumption }
            _ = rmdir(recovery.path)
        } catch {
            _ = rmdir(recovery.path) // Never remove unexpected secret-bearing recovery files.
            throw MobileInstallationError.consumption
        }
    }
    public func receipt(_ value: MobileInstallationReceipt) throws {
        let data = try JSONEncoder().encode(value)
        let url = directory.appendingPathComponent("MobileInstallationReceipt.json")
        #if os(iOS)
        try data.write(to: url, options: [.atomic, .completeFileProtection])
        #else
        try data.write(to: url, options: .atomic)
        #endif
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
}

public enum MobileInstallation {
    /// The caller must successfully read Keychain first. A failed read is never an empty installation.
    public static func apply(source: any MobileInstallationSource, existing: MobileCredential?,
                             credentials: any MobileCredentialStore, persistence: MobileLocalStore) throws -> MobileCredential? {
        var package: MobileInstallationPackage?
        do {
            guard let data = try source.read() else { return nil }
            try source.invalidateReceipt()
            guard data.count <= 16_384 else { throw MobileInstallationError.invalid }
            let parsed = try JSONDecoder().decode(MobileInstallationPackage.self, from: data)
            try parsed.validate(); package = parsed
            let selected = try select(parsed, existing: existing)
            try persistence.validateMigration(from: existing, to: selected)
            try source.consume(data)
            try persistence.migrate(from: existing, to: selected)
            // Keychain's update is atomic; it must not delete the original before trying replacement.
            if existing?.endpoint != selected.endpoint || existing == nil { try credentials.write(selected) }
            try? source.receipt(receipt("success", used: selected, package: parsed))
            return selected
        } catch {
            try? source.invalidateReceipt()
            try? source.receipt(receipt("error", used: existing, package: package))
            throw MobileInstallationError.incomplete
        }
    }
    public static func discard(source: any MobileInstallationSource) throws {
        guard let data = try source.read() else { return }
        try source.invalidateReceipt()
        let parsed = try? JSONDecoder().decode(MobileInstallationPackage.self, from: data)
        let package = parsed.flatMap { (try? $0.validate()) != nil ? $0 : nil }
        try source.consume(data)
        try? source.receipt(receipt("error", used: nil, package: package))
    }
    private static func select(_ package: MobileInstallationPackage, existing: MobileCredential?) throws -> MobileCredential {
        if let existing {
            guard existing.endpoint == package.endpoint || package.previousEndpoint == existing.endpoint else { throw MobileInstallationError.invalid }
            return MobileCredential(endpoint: package.endpoint, deviceId: existing.deviceId, token: existing.token)
        }
        guard let device = package.deviceId, let token = package.deviceToken else { throw MobileInstallationError.invalid }
        return MobileCredential(endpoint: package.endpoint, deviceId: device, token: token)
    }
    private static func receipt(_ status: String, used: MobileCredential?, package: MobileInstallationPackage?) -> MobileInstallationReceipt {
        MobileInstallationReceipt(status: status, usedDeviceId: used?.deviceId,
            unusedProvisionedDeviceId: package?.deviceId == used?.deviceId ? nil : package?.deviceId)
    }
}
