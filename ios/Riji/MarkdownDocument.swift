import Foundation
import SwiftUI
import UniformTypeIdentifiers
import RijiCore

struct MarkdownDocument: FileDocument {
    static var readableContentTypes: [UTType] { [.init(filenameExtension: "md") ?? .plainText] }
    let filename: String
    var data: Data

    init(day: JournalDay, data: Data) {
        filename = "\(day.value).md"
        self.data = data
    }

    init(configuration: ReadConfiguration) throws {
        filename = configuration.file.preferredFilename ?? "Diary.md"
        data = configuration.file.regularFileContents ?? Data()
    }

    func makeFileWrapper() -> FileWrapper { FileWrapper(regularFileWithContents: data) }
    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper { makeFileWrapper() }
}
