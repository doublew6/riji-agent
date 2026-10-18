import UIKit
import UniformTypeIdentifiers

@MainActor
final class ProbeFolderPicker: NSObject, UIDocumentPickerDelegate {
    private var continuation: CheckedContinuation<URL, Error>?
    private var timeout: Task<Void, Never>?
    private var picker: UIDocumentPickerViewController?

    func select(folderName: String) async throws -> URL {
        let windows = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap(\.windows)
        guard let root = windows.first(where: \.isKeyWindow)?.rootViewController else { throw ProbeFailure.noPresenter }
        var presenter = root
        while let presented = presenter.presentedViewController { presenter = presented }
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: [.folder], asCopy: false)
        picker.allowsMultipleSelection = false
        picker.delegate = self
        picker.title = "仅选择合成测试目录"
        let instruction = UILabel()
        instruction.text = "请选择 \(folderName)\n不要选择私人日记库。取消会记录未完成。"
        instruction.numberOfLines = 0
        instruction.font = .preferredFont(forTextStyle: .footnote)
        instruction.textAlignment = .center
        instruction.backgroundColor = .secondarySystemBackground
        instruction.translatesAutoresizingMaskIntoConstraints = false
        picker.view.addSubview(instruction)
        NSLayoutConstraint.activate([
            instruction.leadingAnchor.constraint(equalTo: picker.view.leadingAnchor, constant: 12),
            instruction.trailingAnchor.constraint(equalTo: picker.view.trailingAnchor, constant: -12),
            instruction.bottomAnchor.constraint(equalTo: picker.view.safeAreaLayoutGuide.bottomAnchor),
            instruction.heightAnchor.constraint(greaterThanOrEqualToConstant: 64)
        ])
        self.picker = picker
        return try await withCheckedThrowingContinuation { continuation in
            self.continuation = continuation
            presenter.present(picker, animated: true)
            timeout = Task { [weak self] in
                do { try await Task.sleep(nanoseconds: 180_000_000_000) } catch { return }
                self?.finish(.failure(ProbeFailure.pickerTimeout))
            }
        }
    }

    func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
        guard urls.count == 1, let url = urls.first else { finish(.failure(ProbeFailure.cancelled)); return }
        finish(.success(url))
    }

    func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) { finish(.failure(ProbeFailure.cancelled)) }

    private func finish(_ result: Result<URL, Error>) {
        guard let continuation else { return }
        self.continuation = nil
        timeout?.cancel(); timeout = nil
        picker?.dismiss(animated: true); picker = nil
        continuation.resume(with: result)
    }
}
