import Foundation
import SwiftUI
import UniformTypeIdentifiers
import RijiCore

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
    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper { makeFileWrapper() }
}
