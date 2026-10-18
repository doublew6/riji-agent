import Foundation
import Combine
import RijiCore

struct VaultTreeRow: Identifiable {
    let entry: VaultEntry
    let depth: Int
    var id: String { entry.id }
}

@MainActor
final class VaultBrowserModel: ObservableObject {
    @Published private(set) var rootName: String?
    @Published private(set) var rootExpanded = true
    @Published private(set) var children: [String: [VaultEntry]] = [:]
    @Published private(set) var expanded = Set<String>()
    @Published private(set) var loading = Set<String>()
    @Published private(set) var folderErrors: [String: String] = [:]
    @Published private(set) var error: String?
    @Published private(set) var isBusy = false
    @Published private(set) var openingFile: String?
    @Published private(set) var preview: VaultFileContents?
    private let store: VaultBrowserStore
    private var started = false
    private var generation = UUID()
    private var requests: [String: UUID] = [:]

    init(store: VaultBrowserStore? = nil) {
        var directory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        #if DEBUG
        if let value = ProcessInfo.processInfo.environment["RIJI_TEST_CONTAINER"], let id = UUID(uuidString: value) {
            directory.appendPathComponent("UITests-\(id.uuidString)")
        }
        #endif
        self.store = store ?? VaultBrowserStore(stateDirectory: directory.appendingPathComponent("Riji"))
    }

    var rows: [VaultTreeRow] {
        guard rootExpanded else { return [] }
        func descend(_ parent: String, _ depth: Int) -> [VaultTreeRow] {
            (children[parent] ?? []).flatMap { entry -> [VaultTreeRow] in
                let row = VaultTreeRow(entry: entry, depth: depth)
                return [row] + (entry.kind == .folder && expanded.contains(entry.id) ? descend(entry.id, depth + 1) : [])
            }
        }
        return descend("", 0)
    }

    func start() async {
        guard !started else { return }
        started = true; await reload()
    }

    func reload() async {
        guard !isBusy else { return }
        invalidateRequests(); isBusy = true; error = nil
        let owner = generation
        do {
            let root = try await store.restore()
            if owner == generation { apply(root) }
        } catch { if owner == generation { apply(nil); self.error = message(error) } }
        isBusy = false
    }

    func selectFolder(_ url: URL) async {
        guard !isBusy else { return }
        invalidateRequests(); isBusy = true; error = nil
        let owner = generation
        do {
            let root = try await store.connect(url)
            if owner == generation { apply(root) }
        } catch { if owner == generation { self.error = message(error) } }
        isBusy = false
    }

    func pickerFailed() { error = "未能打开文件夹，请重新选择。" }
    func toggleRoot() { rootExpanded.toggle() }

    func toggle(_ entry: VaultEntry) async {
        guard !isBusy, entry.kind == .folder else { return }
        if expanded.contains(entry.id) {
            expanded.remove(entry.id); requests[entry.id] = nil; loading.remove(entry.id)
        } else { expanded.insert(entry.id); await load(entry) }
    }

    func load(_ entry: VaultEntry) async {
        guard !isBusy, entry.kind == .folder, expanded.contains(entry.id) else { return }
        let request = UUID(), owner = generation
        requests[entry.id] = request; loading.insert(entry.id); folderErrors[entry.id] = nil
        do {
            let entries = try await store.entries(path: entry.path)
            guard owner == generation, requests[entry.id] == request else { return }
            children[entry.id] = entries
        } catch {
            guard owner == generation, requests[entry.id] == request else { return }
            children[entry.id] = nil; folderErrors[entry.id] = message(error)
        }
        loading.remove(entry.id); requests[entry.id] = nil
    }

    func open(_ entry: VaultEntry) async {
        guard !isBusy, openingFile == nil else { return }
        let owner = generation
        error = nil; openingFile = entry.id
        do {
            let contents = try await store.read(entry)
            guard owner == generation else { await store.discardPreview(contents); return }
            preview = contents
        } catch {
            guard owner == generation else { return }
            self.error = message(error)
        }
        openingFile = nil
    }

    func closePreview() {
        guard let contents = preview else { return }
        preview = nil
        Task { await store.discardPreview(contents) }
    }

    func directoryClosed() { invalidateRequests(); closePreview(); started = false }

    private func invalidateRequests() {
        generation = UUID(); requests = [:]; loading = []; openingFile = nil
    }

    private func apply(_ root: VaultRoot?) {
        rootName = root?.name; children = root.map { ["": $0.entries] } ?? [:]
        expanded = []; folderErrors = [:]; rootExpanded = true
    }

    private func message(_ error: Error) -> String {
        (error as? VaultReadError)?.localizedDescription ?? (error as? VaultPreviewError)?.localizedDescription ?? VaultReadError.unavailable.localizedDescription
    }
}
