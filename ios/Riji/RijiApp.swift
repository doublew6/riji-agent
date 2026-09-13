import SwiftUI
import Combine

@main
struct RijiApp: App {
    @StateObject private var model: DiaryViewModel
    @Environment(\.scenePhase) private var scenePhase

    init() {
        var clock: () -> Date = Date.init
        #if DEBUG
        if let value = ProcessInfo.processInfo.environment["RIJI_TEST_DATE"],
           let date = ISO8601DateFormatter().date(from: value) { clock = { date } }
        #endif
        _model = StateObject(wrappedValue: DiaryViewModel(now: clock))
    }

    var body: some Scene {
        WindowGroup {
            TodayDiaryView(model: model)
                .task { model.observeDayChanges(); await model.start() }
                .onReceive(Timer.publish(every: 60, on: .main, in: .common).autoconnect()) { _ in
                    model.foregroundTick(isActive: scenePhase == .active)
                }
                .onChange(of: scenePhase) { _, phase in
                    if phase == .active { model.resume() }
                }
        }
    }
}
