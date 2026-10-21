import SwiftUI
import Combine
import UIKit

@main
struct RijiApp: App {
    @StateObject private var model: DiaryViewModel
    @Environment(\.scenePhase) private var scenePhase

    init() {
        #if DEBUG && targetEnvironment(simulator)
        SyntheticTouchTrace.installIfRequested()
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
                    #if DEBUG && targetEnvironment(simulator)
                    SyntheticTouchTrace.mark(.foregroundTickBegin, available: scenePhase == .active)
                    #endif
                    model.foregroundTick(isActive: scenePhase == .active)
                    #if DEBUG && targetEnvironment(simulator)
                    SyntheticTouchTrace.mark(.foregroundTickEnd, available: scenePhase == .active)
                    #endif
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
