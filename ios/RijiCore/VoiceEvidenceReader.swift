import Foundation
import Darwin

/// Validates the entire JSON stream while retaining only identity/stamps. Long body strings never accumulate.
final class VoiceEvidenceReader {
    private let handle: FileHandle
    private let original: stat
    private let url: URL
    private var buffer: [UInt8] = []
    private var index = 0
    private var output: Data?
    private let maximumField = VoiceMetadataBounds.projectionBytes

    init(url: URL) throws {
        self.url = url
        let descriptor = Darwin.open(url.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard descriptor >= 0 else { throw JournalError.storage }
        handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_mode & S_IFMT == S_IFREG else { throw JournalError.storage }
        original = info
    }
    deinit { try? handle.close() }

    func projection() throws -> Data {
        let retained: Set<String> = ["operationID", "day", "sourceHash", "templateHash", "voiceInsertions", "payloadHash", "afterHash"]
        let bodies: Set<String> = ["text", "section", "effectiveTemplate"]
        var fields: [String: Data] = [:]
        var keys = Set<String>()
        try whitespace(); try expect(123); try whitespace()
        if try peek() != 125 {
            while true {
                let key = try stringKey()
                guard keys.insert(key).inserted, keys.count <= 1024 else { throw JournalError.storage }
                try whitespace(); try expect(58); try whitespace()
                if retained.contains(key) {
                    output = Data(); try value(depth: 0); fields[key] = output; output = nil
                } else if bodies.contains(key) {
                    guard try peek() == 34 else { throw JournalError.storage }
                    try string(); fields[key] = Data("\"\"".utf8)
                } else { try value(depth: 0) }
                try whitespace()
                if try peek() != 44 { break }
                try expect(44); try whitespace()
            }
        }
        try expect(125); try whitespace()
        guard try peek() == nil else { throw JournalError.storage }
        try verifyUnchanged()
        var result = Data("{".utf8)
        for (offset, key) in fields.keys.sorted().enumerated() {
            if offset > 0 { result.append(44) }
            result.append(try JSONEncoder().encode(key)); result.append(58); result.append(fields[key]!)
            guard result.count <= VoiceMetadataBounds.projectionBytes else { throw VoiceError.limit }
        }
        result.append(125)
        return result
    }

    private func verifyUnchanged() throws {
        var current = stat(), named = stat()
        guard fstat(handle.fileDescriptor, &current) == 0, lstat(url.path, &named) == 0,
              current.st_size == original.st_size, current.st_mtimespec.tv_sec == original.st_mtimespec.tv_sec,
              current.st_mtimespec.tv_nsec == original.st_mtimespec.tv_nsec,
              current.st_ctimespec.tv_sec == original.st_ctimespec.tv_sec,
              current.st_ctimespec.tv_nsec == original.st_ctimespec.tv_nsec,
              named.st_ino == original.st_ino, named.st_dev == original.st_dev else { throw JournalError.storage }
    }

    private func value(depth: Int) throws {
        guard depth < 32 else { throw VoiceError.limit }
        try whitespace()
        guard let byte = try peek() else { throw JournalError.storage }
        switch byte {
        case 34: try string()
        case 123: try object(depth: depth + 1)
        case 91:
            try expect(91); try whitespace()
            if try peek() != 93 {
                while true {
                    try value(depth: depth + 1); try whitespace()
                    if try peek() != 44 { break }
                    try expect(44)
                }
            }
            try expect(93)
        case 116: try literal("true")
        case 102: try literal("false")
        case 110: try literal("null")
        case 45, 48...57: try number()
        default: throw JournalError.storage
        }
    }

    private func object(depth: Int) throws {
        try expect(123); try whitespace()
        var keys = Set<String>()
        if try peek() != 125 {
            while true {
                let key = try stringKey()
                guard keys.insert(key).inserted, keys.count <= 1024 else { throw JournalError.storage }
                try whitespace(); try expect(58); try value(depth: depth); try whitespace()
                if try peek() != 44 { break }
                try expect(44); try whitespace()
            }
        }
        try expect(125)
    }

    private func stringKey() throws -> String {
        let enclosing = output
        output = Data()
        try string(key: true)
        let bytes = output!
        output = enclosing
        if output != nil {
            guard output!.count + bytes.count <= maximumField else { throw VoiceError.limit }
            output!.append(bytes)
        }
        guard let key = try? JSONDecoder().decode(String.self, from: bytes) else { throw JournalError.storage }
        return key
    }

    private func string(key: Bool = false) throws {
        try expect(34)
        var count = 0
        while true {
            let byte = try take(); count += 1
            if key && count > 4096 { throw VoiceError.limit }
            if byte == 34 { return }
            guard byte >= 32 else { throw JournalError.storage }
            if byte == 92 {
                let escape = try take()
                if escape == 117 {
                    let scalar = try hexQuad()
                    if (0xD800...0xDBFF).contains(scalar) {
                        try expect(92); try expect(117)
                        guard (0xDC00...0xDFFF).contains(try hexQuad()) else { throw JournalError.storage }
                    } else if (0xDC00...0xDFFF).contains(scalar) { throw JournalError.storage }
                } else if ![34, 92, 47, 98, 102, 110, 114, 116].contains(escape) { throw JournalError.storage }
            } else if byte >= 128 { try utf8Tail(byte) }
        }
    }

    private func utf8Tail(_ first: UInt8) throws {
        let count: Int, low: UInt8, high: UInt8
        switch first {
        case 0xC2...0xDF: count = 1; low = 0x80; high = 0xBF
        case 0xE0: count = 2; low = 0xA0; high = 0xBF
        case 0xE1...0xEC, 0xEE...0xEF: count = 2; low = 0x80; high = 0xBF
        case 0xED: count = 2; low = 0x80; high = 0x9F
        case 0xF0: count = 3; low = 0x90; high = 0xBF
        case 0xF1...0xF3: count = 3; low = 0x80; high = 0xBF
        case 0xF4: count = 3; low = 0x80; high = 0x8F
        default: throw JournalError.storage
        }
        guard (low...high).contains(try take()) else { throw JournalError.storage }
        for _ in 1..<count { guard (0x80...0xBF).contains(try take()) else { throw JournalError.storage } }
    }

    private func hexQuad() throws -> UInt32 {
        var result: UInt32 = 0
        for _ in 0..<4 {
            let b = try take(), digit: UInt32
            switch b {
            case 48...57: digit = UInt32(b - 48)
            case 65...70: digit = UInt32(b - 55)
            case 97...102: digit = UInt32(b - 87)
            default: throw JournalError.storage
            }
            result = result * 16 + digit
        }
        return result
    }

    private func number() throws {
        if try peek() == 45 { _ = try take() }
        if try peek() == 48 { _ = try take() }
        else { guard let p = try peek(), (49...57).contains(p) else { throw JournalError.storage }; try digits() }
        if try peek() == 46 { _ = try take(); try digits(required: true) }
        if try peek() == 101 || peek() == 69 {
            _ = try take()
            if try peek() == 43 || peek() == 45 { _ = try take() }
            try digits(required: true)
        }
    }
    private func digits(required: Bool = false) throws {
        var any = false
        while let p = try peek(), (48...57).contains(p) { _ = try take(); any = true }
        if required && !any { throw JournalError.storage }
    }
    private func literal(_ text: String) throws { for byte in text.utf8 { try expect(byte) } }
    private func whitespace() throws {
        while let p = try peek(), [9, 10, 13, 32].contains(p) { _ = try take() }
    }
    private func expect(_ byte: UInt8) throws { guard try take() == byte else { throw JournalError.storage } }
    private func peek() throws -> UInt8? {
        if index == buffer.count { buffer = Array(try handle.read(upToCount: 65536) ?? Data()); index = 0 }
        return index < buffer.count ? buffer[index] : nil
    }
    private func take() throws -> UInt8 {
        guard let byte = try peek() else { throw JournalError.storage }
        index += 1
        if output != nil {
            guard output!.count < maximumField else { throw VoiceError.limit }
            output!.append(byte)
        }
        return byte
    }
}

/// A cache entry is reusable only for an unchanged regular-file inode and complete version.
struct VoiceFileVersion: Codable, Equatable {
    let device: Int32
    let inode: UInt64
    let size: Int64
    let modifiedSeconds: Int
    let modifiedNanos: Int
    let changedSeconds: Int
    let changedNanos: Int
    init(_ url: URL) throws {
        var info = stat()
        guard lstat(url.path, &info) == 0, info.st_mode & S_IFMT == S_IFREG else { throw JournalError.storage }
        device = info.st_dev; inode = info.st_ino; size = info.st_size
        modifiedSeconds = info.st_mtimespec.tv_sec; modifiedNanos = info.st_mtimespec.tv_nsec
        changedSeconds = info.st_ctimespec.tv_sec; changedNanos = info.st_ctimespec.tv_nsec
    }
}
struct VoiceEvidenceCacheEntry {
    let version: VoiceFileVersion
    let stamps: [VoiceInsertionStamp]
}
struct VoiceSummaryCacheEntry: Codable {
    let version: VoiceFileVersion?
    let summary: VoiceRecordingSummary?
    var limited: Bool = false
}
struct VoiceSummaryIndex: Codable {
    let namespace: UUID
    let entries: [String: VoiceSummaryCacheEntry]
}
struct VoicePreparationHint: Codable {
    let targetHash: String
    let recordingID: UUID
    let manifestVersion: VoiceFileVersion
    let abandonmentInventory: String
}

enum VoiceMetadataBounds {
    static let stampCount = 10000
    static let projectionBytes = 4 * 1024 * 1024
    static func validate(_ stamps: [VoiceInsertionStamp]) throws {
        guard stamps.count <= stampCount else { throw VoiceError.limit }
        var recordings: [UUID: VoiceInsertionStamp] = [:], insertions: [UUID: VoiceInsertionStamp] = [:]
        for stamp in stamps {
            guard stamp.payloadHash.utf8.count == 64, stamp.payloadHash.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }),
                  recordings[stamp.recordingID].map({ $0 == stamp }) ?? true,
                  insertions[stamp.insertionID].map({ $0 == stamp }) ?? true else { throw JournalError.operationMismatch }
            recordings[stamp.recordingID] = stamp; insertions[stamp.insertionID] = stamp
        }
        guard try JSONEncoder().encode(stamps).count < projectionBytes - 16384 else { throw VoiceError.limit }
    }
}
