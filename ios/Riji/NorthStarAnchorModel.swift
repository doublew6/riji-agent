import Foundation
import Combine
import RijiCore

struct NorthStarReaders {
    let source: String
    let account: String
    let original: Bool
    let goals: () async throws -> [NorthStarDefinition]
    let entries: ([String]) async throws -> [VaultEntry]
    let text: ([String]) async throws -> Data
    var scope: String { NorthStarAnchor.scope(source: source, account: account) }
}

struct NorthStarDisplay: Equatable {
    let title: String
    let detail: String
    let sourceLabel: String
}

struct NorthStarPreview {
    let anchor: NorthStarAnchor
    let display: NorthStarDisplay
}

@MainActor
final class NorthStarAnchorModel: ObservableObject {
    @Published private(set) var current: NorthStarDisplay?
    @Published private(set) var preview: NorthStarPreview?
    @Published private(set) var goals: [NorthStarDefinition] = []
    @Published private(set) var entries: [VaultEntry] = []
    @Published private(set) var path: [String] = []
    @Published private(set) var paragraphs: [NorthStarParagraph] = []
    @Published private(set) var selectedFile: [String]?
    @Published private(set) var error: String?
    @Published private(set) var sourceError: String?
    @Published private(set) var activationID = UUID()
    @Published private(set) var available = false
    @Published private(set) var hasAssociation = false
    @Published private(set) var busy = false
    private let store: NorthStarAnchorStore
    private var readers: NorthStarReaders?
    private var request = UUID()
    private var fileData: Data?
    var original: Bool { readers?.original == true }
    var scope: String? { readers?.scope }

    init(store: NorthStarAnchorStore) { self.store = store }

    func activate(_ readers: NorthStarReaders?) {
        clearSource()
        self.readers = readers
        available = readers != nil
        activationID = UUID()
    }
    func invalidate() {
        clearSource()
        activationID = UUID()
    }
    private func clearSource() {
        request = UUID(); readers = nil
        available = false; hasAssociation = false
        current = nil; preview = nil; goals = []; entries = []; path = []
        paragraphs = []; selectedFile = nil; fileData = nil; error = nil; sourceError = nil; busy = false
    }
    func cancelPreview() { preview = nil }

    func refresh() async {
        guard let readers, !busy else { return }
        let token = begin()
        do {
            let anchor = try await store.read(scope: readers.scope)
            guard accepts(token) else { return }
            hasAssociation = anchor != nil
            let value = try await anchor.mapAsync { try await resolve($0, readers: readers) }
            guard accepts(token) else { return }
            current = value; sourceError = nil
        } catch { if accepts(token) { current = nil; sourceError = message(error) } }
        if accepts(token) { busy = false }
    }

    func recover() async {
        guard !busy else { return }
        let activation = activationID, file = selectedFile, directory = path
        preview = nil
        await refresh()
        guard activation == activationID, !Task.isCancelled else { return }
        if let file { await open(VaultEntry(path: file, kind: .file)) }
        else { await browse(directory) }
    }

    func browseWhenReady() async {
        let activation = activationID
        while busy && activation == activationID {
            do { try await Task.sleep(nanoseconds: 50_000_000) } catch { return }
        }
        guard activation == activationID, !Task.isCancelled else { return }
        await browse()
    }

    func browse(_ path: [String] = []) async {
        guard let readers, !busy else { return }
        let token = begin(); preview = nil
        do {
            if readers.original {
                let entries = try await readers.entries(path)
                guard accepts(token) else { return }
                self.entries = entries.filter { $0.kind == .folder || NorthStarAnchor.validPath($0.path) }
            } else {
                let values = try await readers.goals()
                guard accepts(token) else { return }
                goals = values
            }
            self.path = path; paragraphs = []; selectedFile = nil; fileData = nil; error = nil
        } catch { if accepts(token) { self.error = message(error) } }
        if accepts(token) { busy = false }
    }

    func open(_ entry: VaultEntry) async {
        guard let readers, readers.original, !busy else { return }
        let token = begin(); paragraphs = []; fileData = nil; selectedFile = entry.path; preview = nil
        do {
            let data = try await readers.text(entry.path)
            let values = try NorthStarParagraph.read(data)
            guard accepts(token) else { return }
            fileData = data; paragraphs = values; selectedFile = entry.path; error = nil
        } catch { if accepts(token) { self.error = message(error) } }
        if accepts(token) { busy = false }
    }

    func prepare(_ goal: NorthStarDefinition) async {
        guard let readers, !readers.original, !busy else { return }
        do {
            let anchor = try NorthStarAnchor(scope: readers.scope, source: .definition(goal.id, hash: NorthStarAnchor.definitionHash(goal)))
            await prepare(anchor)
        } catch { self.error = message(error) }
    }

    func prepare(_ paragraph: NorthStarParagraph) async {
        guard let readers, let path = selectedFile, let data = fileData,
              paragraphs.contains(paragraph), !busy else { return }
        do {
            let anchor = try NorthStarAnchor(scope: readers.scope, source: .paragraph(
                path: path, start: paragraph.start, end: paragraph.end, hash: SourceHash.digest(data)))
            await prepare(anchor)
        } catch { self.error = message(error) }
    }

    private func prepare(_ anchor: NorthStarAnchor) async {
        guard let readers else { return }
        let token = begin(); preview = nil
        do {
            let display = try await resolve(anchor, readers: readers)
            guard accepts(token) else { return }
            preview = NorthStarPreview(anchor: anchor, display: display); error = nil
        } catch { if accepts(token) { self.error = message(error) } }
        if accepts(token) { busy = false }
    }

    func confirm() async -> Bool {
        guard let readers, let preview, !busy else { return false }
        let token = begin()
        do {
            _ = try await resolve(preview.anchor, readers: readers)
            guard accepts(token) else { return false }
            try await store.save(preview.anchor)
            guard accepts(token) else { return false }
            hasAssociation = true
            // Reread after persistence; a stored association is not proof of a current source.
            let verified = try await resolve(preview.anchor, readers: readers)
            guard accepts(token) else { return false }
            current = verified
            self.preview = nil; error = nil; sourceError = nil; busy = false
            return true
        } catch {
            if accepts(token) { current = nil; self.error = message(error); busy = false }
            return false
        }
    }

    func unlink() async {
        guard let readers, !busy else { return }
        let token = begin()
        do {
            try await store.remove(scope: readers.scope)
            guard accepts(token) else { return }
            current = nil; preview = nil; hasAssociation = false; error = nil; sourceError = nil
        } catch { if accepts(token) { self.error = message(error) } }
        if accepts(token) { busy = false }
    }

    private func resolve(_ anchor: NorthStarAnchor, readers: NorthStarReaders) async throws -> NorthStarDisplay {
        try anchor.validate()
        guard anchor.scope == readers.scope else { throw NorthStarAnchorError.invalid }
        switch anchor.source {
        case .definition(let id, let hash):
            guard !readers.original, let goal = try await readers.goals().first(where: { $0.id == id }),
                  try NorthStarAnchor.definitionHash(goal) == hash else { throw NorthStarAnchorError.stale }
            return NorthStarDisplay(title: goal.name,
                detail: goal.definition + "\n目标：" + goal.target + " " + goal.unit + "\n周期：" + goal.period + "\n阶段：" + goal.stage
                    + (goal.measurement.map { "\n当前测量：" + $0 } ?? "")
                    + (goal.measurementSource.map { "\n测量来源及截止时间：" + $0 } ?? ""),
                sourceLabel: "本地北极星 · " + goal.day.value)
        case .paragraph(let path, _, _, _):
            guard readers.original else { throw NorthStarAnchorError.invalid }
            let text = try NorthStarParagraph.resolve(anchor, in: await readers.text(path))
            return NorthStarDisplay(title: text, detail: text, sourceLabel: path.joined(separator: "/"))
        }
    }
    private func begin() -> UUID { request = UUID(); busy = true; return request }
    private func accepts(_ token: UUID) -> Bool { request == token && readers != nil }
    private func message(_ error: Error) -> String {
        if let error = error as? VaultReadError, case .tooLarge = error { return NorthStarAnchorError.tooLarge.localizedDescription }
        return (error as? NorthStarAnchorError)?.localizedDescription ?? NorthStarAnchorError.unavailable.localizedDescription
    }
}

private extension Optional {
    func mapAsync<T>(_ transform: (Wrapped) async throws -> T) async rethrows -> T? {
        guard let value = self else { return nil }
        return try await transform(value)
    }
}
