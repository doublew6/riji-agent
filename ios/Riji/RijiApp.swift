import SwiftUI
import Combine
import UIKit
#if DEBUG && targetEnvironment(simulator)
import ObjectiveC.runtime
#endif

@main
struct RijiApp: App {
    @StateObject private var model: DiaryViewModel
    @Environment(\.scenePhase) private var scenePhase

    init() {
        #if DEBUG && targetEnvironment(simulator)
        SyntheticMenuEventTrace.installIfRequested()
        #endif
        var clock: () -> Date = Date.init
        #if DEBUG
        if let value = ProcessInfo.processInfo.environment["RIJI_TEST_DATE"],
           let date = ISO8601DateFormatter().date(from: value) { clock = { date } }
        #endif
        var original = true
        #if DEBUG
        // Existing isolated regression fixtures explicitly exercise old sandbox diaries.
        if ProcessInfo.processInfo.environment["RIJI_TEST_CONTAINER"].flatMap(UUID.init(uuidString:)) != nil {
            original = ProcessInfo.processInfo.environment["RIJI_TEST_ORIGINAL_VAULT"] == "1"
        }
        #endif
        _model = StateObject(wrappedValue: DiaryViewModel(now: clock, useOriginalCollection: original))
    }

    var body: some Scene {
        WindowGroup {
            RijiNavigationView(model: model)
                .task { model.observeDayChanges(); await model.start() }
                .onReceive(Timer.publish(every: 3, on: .main, in: .common).autoconnect()) { _ in
                    model.foregroundTick(isActive: scenePhase == .active)
                }
                .onChange(of: scenePhase) { _, phase in
                    handleScenePhase(phase)
                }
        }
    }

    private func handleScenePhase(_ phase: ScenePhase) {
        if phase == .active { model.resume(); return }
        var background = UIBackgroundTaskIdentifier.invalid
        background = UIApplication.shared.beginBackgroundTask(withName: "Preserve diary input") {
            if background != .invalid { UIApplication.shared.endBackgroundTask(background); background = .invalid }
        }
        let flush = model.scenePhaseChanged(phase, commitEditing: TodayDiaryView.commitVisibleInput)
        Task { @MainActor in
            await flush?.value
            if background != .invalid { UIApplication.shared.endBackgroundTask(background); background = .invalid }
        }
    }
}

#if DEBUG && targetEnvironment(simulator)
// Temporary diagnosis for explicitly opted-in, isolated synthetic UI tests only.
@MainActor private enum SyntheticMenuEventTrace {
    private static var installed = false
    private static var remainingRecords = 256

    static func installIfRequested() {
        let environment = ProcessInfo.processInfo.environment
        guard !installed, environment["RIJI_TEST_MENU_EVENT_TRACE"] == "1",
              environment["RIJI_TEST_CONTAINER"].flatMap(UUID.init(uuidString:)) != nil,
              environment["RIJI_TEST_MOBILE"] == "roundtable",
              let original = class_getInstanceMethod(UIWindow.self, #selector(UIWindow.sendEvent(_:))),
              let trace = class_getInstanceMethod(UIWindow.self, #selector(UIWindow.riji_traceMenuEvent(_:))) else { return }
        method_exchangeImplementations(original, trace)
        installed = true
        SyntheticMenuActionTrace.enableAfterEventHookInstallation()
    }

    struct Record {
        let touch: UITouch
        let ancestors: [UIView]
        let before: String
    }

    static func capture(_ event: UIEvent, in window: UIWindow) -> [Record] {
        guard remainingRecords > 0, event.type == .touches else { return [] }
        var records: [Record] = []
        for touch in event.allTouches ?? [] where touch.window === window {
            guard remainingRecords > 0 else { break }
            var ancestors: [UIView] = []
            var view = touch.view
            while let current = view, ancestors.count < 24 {
                ancestors.append(current); view = current.superview
            }
            let menuRelated = ancestors.contains { view in
                className(view).contains("Menu") || (view.gestureRecognizers ?? []).contains {
                    className($0).contains("Menu")
                }
            }
            guard menuRelated else { continue }
            records.append(Record(touch: touch, ancestors: ancestors,
                                  before: metadata(touch, ancestors: ancestors, window: window)))
            remainingRecords -= 1
        }
        return records
    }

    static func emit(_ records: [Record], in window: UIWindow) {
        for record in records {
            // Emit after dispatch so console I/O cannot delay the original handler.
            print("[DEBUG-menu-dispatch] before \(record.before)")
            print("[DEBUG-menu-dispatch] after \(metadata(record.touch, ancestors: record.ancestors, window: window))")
        }
    }

    private static func metadata(_ touch: UITouch, ancestors: [UIView], window: UIWindow) -> String {
        let point = touch.location(in: window)
        let chain = ancestors.map { view in
            let gestures = (view.gestureRecognizers ?? []).prefix(8).map {
                "\(className($0)):\($0.state.rawValue)"
            }.joined(separator: ",")
            return "\(className(view))[\(gestures)]"
        }.joined(separator: ">")
        return "time=\(touch.timestamp) phase=\(touch.phase.rawValue) point=(\(point.x),\(point.y)) window=\(className(window)) target=\(touch.view.map(className) ?? "none") ancestors=\(chain)"
    }

    private static func className(_ object: NSObject) -> String {
        String(NSStringFromClass(type(of: object)).prefix(160))
    }
}

private extension UIWindow {
    @objc func riji_traceMenuEvent(_ event: UIEvent) {
        let records = SyntheticMenuEventTrace.capture(event, in: self)
        // After exchange this selector invokes the original implementation exactly once.
        riji_traceMenuEvent(event)
        SyntheticMenuEventTrace.emit(records, in: self)
    }
}
#endif
