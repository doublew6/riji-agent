import Foundation

public enum VoiceError: Error, LocalizedError, Equatable {
    case verifying, unavailable, permission, missing, changedMedia, deleted, invalidTarget, emptyTranscript, limit, interrupted
    public var errorDescription: String? {
        switch self {
        case .verifying: return "正在分批核验本地保存记录。原数据保留，请重试以继续核验。"
        case .interrupted: return "录音被打断。未删除已有文件，请核对可用片段后明确点继续。"
        case .unavailable: return "此设备暂不支持离线中文转写。录音已保留，可回放并手动输入。"
        case .permission: return "未获得所需权限。可以继续打字，或前往系统设置。"
        case .missing: return "录音状态暂不可读。原文件保留，请重试。"
        case .changedMedia: return "音频缺失、尚未完成或版本发生变化。已停止操作，未删除已有文件。"
        case .deleted: return "原音频已申请删除，旧回放和转写预览已失效。文字仍保留。"
        case .invalidTarget: return "请返回录音绑定的日期与区块，核对当前日记后重新预览。"
        case .emptyTranscript: return "没有识别到文字。录音已保留，可重试或手动输入。"
        case .limit: return "本次处理超过本地安全上限。已有文件保留。"
        }
    }
}

public struct VoiceTarget: Codable, Equatable, Sendable {
    public let namespace: UUID
    public let day: JournalDay
    public let section: String
    public let sourceHash: String?
    public let templateHash: String
    public static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.namespace == rhs.namespace && lhs.day == rhs.day && lhs.sourceHash == rhs.sourceHash &&
            lhs.templateHash == rhs.templateHash && lhs.section.utf8.elementsEqual(rhs.section.utf8)
    }
}

public struct VoiceSegment: Codable, Identifiable, Equatable, Sendable {
    public let id: UUID
    public let fileExtension: String
    public var hash: String?
    public var byteCount: Int64?
    public var duration: Double?
    public var finalized: Bool
    public var captureEvidence: VoiceCaptureEvidence?
    public var captureExpected: Bool?
    public var captureEvidenceUnknown: Bool?
    public var relativePath: String { "\(id.uuidString).\(fileExtension)" }
}

public struct VoiceMediaReference: Codable, Equatable, Sendable {
    public let segmentID: UUID
    public let hash: String
    public let byteCount: Int64
    public let relativeLink: String
}

public struct VoiceTranscript: Codable, Identifiable, Equatable, Sendable {
    public let id: UUID
    public let media: [VoiceMediaReference]
    public let text: String
    public var skipped: [VoiceMediaReference]?
    public var inventory: [VoiceSourcePiece]?
    public init(id: UUID, media: [VoiceMediaReference], text: String) {
        self.id = id; self.media = media; self.text = text
    }
    public static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.id == rhs.id && lhs.media == rhs.media && lhs.skipped == rhs.skipped && lhs.inventory == rhs.inventory && lhs.text.utf8.elementsEqual(rhs.text.utf8)
    }
}

public struct VoiceCorrection: Codable, Identifiable, Equatable, Sendable {
    public let id: UUID
    public let text: String
    public var origin: VoiceCorrectionOrigin?
    public init(id: UUID = UUID(), text: String, origin: VoiceCorrectionOrigin? = nil) { self.id = id; self.text = text; self.origin = origin }
    public static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.id == rhs.id && lhs.origin == rhs.origin && lhs.text.utf8.elementsEqual(rhs.text.utf8)
    }
}

public struct VoiceRecording: Codable, Identifiable, Equatable, Sendable {
    public let id: UUID
    public let target: VoiceTarget
    public let createdAt: Date
    public var segments: [VoiceSegment]
    public var transcripts: [VoiceTranscript]
    public var corrections: [VoiceCorrection]
    public var segmentTranscripts: [VoiceSegmentTranscript]?
    public var insertionTarget: VoiceTarget?
    public var correctionDraft: VoiceCorrection?
    public var correctionReferences: [VoiceCorrectionReference]?
    public var currentCorrectionReference: VoiceCorrectionReference?
    public var skippedTranscripts: [VoiceMediaReference]?
    public var transcriptReferences: [VoiceTextReference]?
    public var segmentTranscriptReferences: [VoiceTextReference]?
    public var currentCorrectionUnavailable: Bool?
    public var unavailableHistory: [UUID]?
    public var ended: Bool
    public var deletionRequested: Bool
    public var duration: Double { segments.compactMap(\.duration).reduce(0, +) }
    public var correctedText: String { correctionDraft?.text ?? corrections.last?.text ?? "" }
    public var effectiveCorrection: VoiceCorrection { correctionDraft ?? corrections.last ?? VoiceCorrection(id: id, text: "") }
    public var lastPlayable: VoiceSegment? { segments.last { $0.finalized } }
}

public struct VoiceLibrary: Sendable {
    public let recordings: [VoiceRecording]
    public let unreadableCount: Int
}

public struct VoiceInsertionStamp: Codable, Equatable, Sendable {
    public let recordingID: UUID
    public let insertionID: UUID
    public let payloadHash: String
}

public struct VoiceInsertionCandidate: Codable, Equatable, Sendable {
    public let id: UUID
    public let recordingID: UUID
    public let target: VoiceTarget
    public let correction: VoiceCorrection
    public let media: [VoiceMediaReference]
    public let baseDraftID: UUID
    public let baseDraftHash: String
    public let preview: String
    public var inventory: [VoiceSourcePiece]?
    public static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.id == rhs.id && lhs.recordingID == rhs.recordingID && lhs.target == rhs.target &&
            lhs.correction == rhs.correction && lhs.media == rhs.media && lhs.baseDraftID == rhs.baseDraftID &&
            lhs.baseDraftHash == rhs.baseDraftHash && lhs.inventory == rhs.inventory && lhs.preview.utf8.elementsEqual(rhs.preview.utf8)
    }
}

struct VoiceInsertionIntent: Codable {
    let candidate: VoiceInsertionCandidate
    let mergedDraft: JournalDraft
}

public enum VoiceInsertionResult: Equatable, Sendable {
    case inserted, alreadyInserted
}

/// An immutable successful recognition of one exact original, independent of corrections.
public struct VoiceSegmentTranscript: Codable, Equatable, Sendable {
    public let media: VoiceMediaReference
    public let text: String
    public init(media: VoiceMediaReference, text: String) { self.media = media; self.text = text }
    public static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.media == rhs.media && lhs.text.utf8.elementsEqual(rhs.text.utf8)
    }
}

/// Captured frames identify ordinary boundaries, without promising physical-device continuity.
public struct VoiceCaptureEvidence: Codable, Equatable, Sendable {
    public let firstFrame: Int64
    public let frameCount: Int64
    public let sampleRate: Double
    public var streamID: UUID?
    public var rejectedFrames: Int64?
    public var unassignedFrames: Int64?
    public init(firstFrame: Int64, frameCount: Int64, sampleRate: Double, streamID: UUID? = nil) {
        self.firstFrame = firstFrame; self.frameCount = frameCount; self.sampleRate = sampleRate; self.streamID = streamID
    }
}

public struct VoiceCorrectionReference: Codable, Identifiable, Equatable, Sendable {
    public let id: UUID
    public let hash: String
    public let byteCount: Int
    public var temporaryDraft: Bool?
}
struct VoiceCorrectionRecord: Codable {
    let recordingID: UUID
    let target: VoiceTarget
    let correction: VoiceCorrection
}

public struct VoiceRecordingSummary: Codable, Identifiable, Sendable {
    public let id: UUID
    public let day: JournalDay
    public let sectionLabel: String
    public let createdAt: Date
    public let duration: Double
    public let deletionRequested: Bool
    public let segmentCount: Int
    init(_ value: VoiceRecording) {
        id = value.id; day = value.target.day; sectionLabel = String(value.target.section.prefix(128))
        createdAt = value.createdAt; duration = value.duration
        deletionRequested = value.deletionRequested; segmentCount = value.segments.count
    }
}
public struct VoiceLibraryPage: Sendable {
    public let recordings: [VoiceRecordingSummary]
    public let unreadableCount: Int
    public let limitedCount: Int
    public let nextCursor: String?
    public var rebuildingCount: Int = 0
    public var scannedManifestBytes: Int = 0
    public var scannedManifestCount: Int = 0
}
public struct VoiceCorrectionPage: Sendable {
    public let references: [VoiceCorrectionReference]
    public let nextOffset: Int?
    public var rebuildingCount: Int = 0
    public var unavailableCount: Int = 0
    public var scannedBytes: Int = 0
    public var scannedCount: Int = 0
    public var snapshot: String?
}
struct VoiceAbandonment: Codable, Equatable {
    let candidate: VoiceInsertionCandidate
}

/// Every retained capture slot has an identity, even if no usable original was created.
public struct VoiceSourcePiece: Codable, Equatable, Sendable {
    public var skipped: Bool?
    public let segmentID: UUID
    public let relativeLink: String
    public let finalized: Bool
    public let hash: String?
    public let byteCount: Int64?
    public var unavailable: Bool?
    public var observedHash: String?
    public var observedByteCount: Int64?
    public var unavailableState: String?
    public var integrity: VoiceCaptureEvidence?
    public var integrityUnknown: Bool?
    public var usable: Bool { finalized && unavailable != true }
}

public struct VoiceTextReference: Codable, Identifiable, Equatable, Sendable {
    public let id: UUID
    public let hash: String
    public let byteCount: Int
    public var segment: Bool?
}
struct VoiceTextRecord: Codable {
    let recordingID: UUID
    let target: VoiceTarget
    let proposal: VoiceTranscript?
    let piece: VoiceSegmentTranscript?
    var pieceInventory: [VoiceSourcePiece]?
}
public struct VoiceTextPage: Sendable {
    public let references: [VoiceTextReference]
    public let nextOffset: Int?
}
struct VoiceCaptureRecord: Codable {
    let recordingID: UUID
    let segmentID: UUID
    let hash: String
    let byteCount: Int64
    let evidence: VoiceCaptureEvidence
}

/// Duration and fingerprint belong to the same immutable decoder input.
public struct VoiceMediaValidation: Equatable, Sendable {
    public let hash: String
    public let byteCount: Int64
    public let duration: Double
    public init(data: Data, duration: Double) {
        hash = SourceHash.digest(data); byteCount = Int64(data.count); self.duration = duration
    }
}
public struct VoiceRecognitionSource: Sendable {
    public let media: VoiceMediaReference
    public let data: Data
    public init(media: VoiceMediaReference, data: Data) throws {
        guard Int64(data.count) == media.byteCount, SourceHash.digest(data) == media.hash,
              data.count <= 32 * 1024 * 1024 else { throw VoiceError.changedMedia }
        self.media = media; self.data = data
    }
}

/// Shared by fresh Markdown previews and all historical proposal presentations.
public enum VoiceSourceNotices {
    public static func text(_ inventory: [VoiceSourcePiece]) -> String {
        if inventory.isEmpty { return "录音资料不完整：没有可用原音频；本段为手动校正文字。" }
        let missing = inventory.enumerated().filter { !$0.element.usable }.map { String($0.offset + 1) }.joined(separator: "、")
        let skipped = inventory.enumerated().filter { $0.element.skipped == true }.map { String($0.offset + 1) }.joined(separator: "、")
        var notices: [String] = []
        if !missing.isEmpty || !skipped.isEmpty {
            notices.append("录音资料不完整：" + (missing.isEmpty ? "" : "第 \(missing) 段尚不可用。") +
                (skipped.isEmpty ? "" : "第 \(skipped) 段已选择略过。") + "请人工核对转写完整性。")
        }
        for (index, piece) in inventory.enumerated() {
            if let evidence = piece.integrity, (evidence.rejectedFrames ?? 0) > 0 || (evidence.unassignedFrames ?? 0) > 0 {
                notices.append("录音完整性警告：第 \(index + 1) 段采集拒收 \(evidence.rejectedFrames ?? 0) 帧、已接收但未写入 \(evidence.unassignedFrames ?? 0) 帧；请人工核对。")
            }
            if piece.integrityUnknown == true { notices.append("录音完整性警告：第 \(index + 1) 段恢复后的采集证据不可用，可播放不代表全部输入已保留。") }
        }
        return notices.joined(separator: "\n")
    }
}

/// Adoption provenance describes the retained original, independently of later manual edits/current audio.
public struct VoiceCorrectionOrigin: Codable, Equatable, Sendable {
    public static let unavailableNotice = "历史原稿文件目前不可读；已保留的来源说明与手动校正文稿仍可继续使用。"
    public let recordingID: UUID
    public let target: VoiceTarget
    public let versionID: UUID
    public let segment: Bool?
    public let versionHash: String
    public let inventory: [VoiceSourcePiece]?
    public let skipped: [VoiceMediaReference]?

    public init(recordingID: UUID, target: VoiceTarget, proposal: VoiceTranscript) throws {
        self.recordingID = recordingID; self.target = target; versionID = proposal.id; segment = nil
        versionHash = try Self.digest(proposal); inventory = proposal.inventory; skipped = proposal.skipped
    }
    public init(recordingID: UUID, target: VoiceTarget, piece: VoiceSegmentTranscript, inventory: [VoiceSourcePiece]? = nil) throws {
        self.recordingID = recordingID; self.target = target; versionID = piece.media.segmentID; segment = true
        versionHash = try Self.digest(piece); self.inventory = inventory; skipped = nil
    }
    public static func digest<T: Encodable>(_ value: T) throws -> String {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        return SourceHash.digest(try encoder.encode(value))
    }
    public var notice: String {
        let detail = inventory.map(VoiceSourceNotices.text) ?? ((skipped ?? []).isEmpty ? "" : "原稿曾略过部分录音，请人工核对完整性。")
        return "校正文字的历史来源：采用\(segment == true ? "逐段" : "汇总")原稿 \(versionID.uuidString.prefix(6))。后续编辑仍为手动校正。" +
            (detail.isEmpty ? "" : "\n历史原稿说明：" + detail)
    }
}
