import Foundation
import SwiftUI
import UniformTypeIdentifiers
import RijiCore
#if DEBUG && targetEnvironment(simulator)
import OSLog
#endif

struct MarkdownDocument: FileDocument {
    static var readableContentTypes: [UTType] { [.init(filenameExtension: "md") ?? .plainText] }
    let filename: String
    var data: Data
    let includesOnlySavedVersion: Bool
    let isRecoveryDraft: Bool

    init(day: JournalDay, data: Data, onlySavedVersion: Bool = false, recoveryDraft: Bool = false) {
        filename = "\(day.value)" + (recoveryDraft ? "-recovery.md" : ".md")
        isRecoveryDraft = recoveryDraft
        self.data = data
        includesOnlySavedVersion = onlySavedVersion
    }

    init(configuration: ReadConfiguration) throws {
        filename = configuration.file.preferredFilename ?? "Diary.md"
        data = configuration.file.regularFileContents ?? Data()
        includesOnlySavedVersion = false
        isRecoveryDraft = false
    }

    func makeFileWrapper() -> FileWrapper { FileWrapper(regularFileWithContents: data) }
    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        #if DEBUG && targetEnvironment(simulator)
        let trace = MarkdownExportTrace.Metadata(bytes: data.count,
            filenameValid: MarkdownExportTrace.validFilename(filename),
            typeMatches: Self.readableContentTypes.contains(configuration.contentType))
        MarkdownExportTrace.record(.writerCalled, trace)
        #endif
        let wrapper = makeFileWrapper()
        #if DEBUG && targetEnvironment(simulator)
        MarkdownExportTrace.record(.writerReturned, trace)
        #endif
        return wrapper
    }
}

#if DEBUG && targetEnvironment(simulator)
enum MarkdownExportTrace {
    enum Stage: String, Sendable {
        case requestPrepared, childAppeared, presentationChanged
        case childDisappeared, writerCalled, writerReturned, finished
    }

    enum Outcome: String, Sendable { case none, success, cancellation, cocoaFailure, otherFailure }

    struct Metadata: Sendable {
        var request = 0
        var bytes = -1
        var filenameValid = false
        var typeMatches = false
        var started = false
        var presented = false
        var requestMatches = false
        var outcome = Outcome.none
    }

    private final class State: @unchecked Sendable {
        let enabled: Bool
        private let lock = NSLock()
        private let output = DispatchQueue(label: "com.riji.ci-probe.markdown-export")
        private let logger = Logger(subsystem: "com.riji.ci-probe", category: "markdown-export")
        private var emitted = 0
        private var requests = 0

        init() {
            let environment = ProcessInfo.processInfo.environment
            enabled = environment["RIJI_TEST_CONTAINER"].flatMap(UUID.init(uuidString:)) != nil
                && environment["RIJI_TEST_EXPORT_TRACE"] == "1"
        }

        func prepare(_ metadata: Metadata) -> Int {
            guard enabled else { return 0 }
            lock.lock()
            defer { lock.unlock() }
            guard emitted < 16 else { return 0 }
            requests += 1
            var value = metadata
            value.request = requests
            enqueue(.requestPrepared, value)
            return requests
        }

        func record(_ stage: Stage, _ metadata: Metadata) {
            guard enabled else { return }
            lock.lock()
            defer { lock.unlock() }
            enqueue(stage, metadata)
        }

        private func enqueue(_ stage: Stage, _ metadata: Metadata) {
            guard emitted < 16 else { return }
            emitted += 1
            let sequence = emitted
            output.async { [self] in
                logger.notice("seq=\(sequence) stage=\(stage.rawValue, privacy: .public) request=\(metadata.request) bytes=\(metadata.bytes) filenameValid=\(metadata.filenameValid) typeMatches=\(metadata.typeMatches) started=\(metadata.started) presented=\(metadata.presented) requestMatches=\(metadata.requestMatches) outcome=\(metadata.outcome.rawValue, privacy: .public)")
            }
        }
    }

    private static let state = State()

    static func validFilename(_ filename: String) -> Bool {
        !filename.isEmpty && filename.hasSuffix(".md") && !filename.contains("/") && !filename.contains("\\")
    }

    static func prepare(_ document: MarkdownDocument) -> Int {
        state.prepare(.init(bytes: document.data.count, filenameValid: validFilename(document.filename),
                            typeMatches: !MarkdownDocument.readableContentTypes.isEmpty))
    }

    static func record(_ stage: Stage, _ metadata: Metadata = .init()) { state.record(stage, metadata) }

    static func outcome(_ result: Result<URL, Error>?) -> Outcome {
        guard let result else { return .cancellation }
        switch result {
        case .success: return .success
        case .failure(let error):
            let value = error as NSError
            if value.domain == NSCocoaErrorDomain && value.code == NSUserCancelledError { return .cancellation }
            return value.domain == NSCocoaErrorDomain ? .cocoaFailure : .otherFailure
        }
    }
}
#endif
