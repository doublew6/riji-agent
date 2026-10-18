import SwiftUI
import UIKit
import UniformTypeIdentifiers

struct RijiNavigationView: View {
    @ObservedObject var model: DiaryViewModel
    @StateObject private var mobile = MobileClientModel.live()
    @Environment(\.scenePhase) private var scenePhase
    @State private var selected = Destination.diary
    @State private var isSwitching = false

    private enum Destination { case diary, mentors, memory, settings }

    var body: some View {
        TabView(selection: Binding(get: { selected }, set: select)) {
            TodayDiaryView(model: model)
                .tabItem { Label("日记", systemImage: "book") }.tag(Destination.diary)
            MobileMentorsView(model: mobile, diary: model)
                .tabItem { Label("导师", systemImage: "person.2") }.tag(Destination.mentors)
                .toolbarBackground(MentorPalette.paper, for: .tabBar)
                .toolbarColorScheme(.dark, for: .tabBar)
            MobileMemoriesView(model: mobile)
                .tabItem { Label("记忆", systemImage: "brain") }.tag(Destination.memory)
            NavigationStack { DiarySettingsView(model: model, mobile: mobile) }
                .tabItem { Label("设置", systemImage: "gearshape") }.tag(Destination.settings)
        }
        .tint(selected == .mentors ? MentorPalette.action : Color(uiColor: RijiPalette.action))
        #if DEBUG
        .preferredColorScheme(testAppearance)
        #endif
        .task { await mobile.start() }
        .task(id: "\(mobile.epoch)-\(mobile.foreground)-\(mobile.connected)") { await mobile.pollChatInbox() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await mobile.resume() } }
            else { mobile.suspend() }
        }
    }

    #if DEBUG
    private var testAppearance: ColorScheme? {
        let environment = ProcessInfo.processInfo.environment
        guard environment["RIJI_TEST_CONTAINER"].flatMap(UUID.init(uuidString:)) != nil,
              let appearance = environment["RIJI_TEST_APPEARANCE"] else { return nil }
        if selected == .mentors { return .dark }
        return appearance == "dark" ? .dark : appearance == "light" ? .light : nil
    }
    #endif

    private func select(_ destination: Destination) {
        guard destination != selected, !isSwitching else { return }
        guard selected == .diary else {
            guard destination == .diary else { selected = destination; return }
            isSwitching = true
            Task {
                await model.directoryClosed()
                selected = destination
                isSwitching = false
            }
            return
        }
        isSwitching = true
        for scene in UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }) {
            for window in scene.windows { TodayDiaryView.commitEditing(in: window) }
        }
        Task {
            if await model.prepareMainDestination() { selected = destination }
            isSwitching = false
        }
    }
}

struct DiaryFilesSettingsView: View {
    @ObservedObject var model: DiaryViewModel
    @ObservedObject var mobile: MobileClientModel
    @State private var showFolderPicker = false

    var body: some View {
        RijiList {
            Section("目录与日期") {
                LabeledContent("日记时区", value: model.timeZone.identifier).accessibilityIdentifier("settingsTimeZone")
                Text(model.sourceLabel).accessibilityIdentifier("settingsDiarySource")
                if let error = model.errorMessage {
                    Text(error).foregroundStyle(Color(uiColor: RijiPalette.error)).font(.caption).accessibilityIdentifier("settingsSourceError")
                }
                Text(model.isOriginalSource ? "正文修改自动保存在原 Markdown 文件中。iCloud 由系统同步，本机保存不代表其他设备已收到。原有 App 数据仍保留。" : "原有 App 日记保留在本地；选择原目录不会迁移旧数据。")
                    .font(.caption).foregroundStyle(Color(uiColor: RijiPalette.secondary))
                Button("选择原日记文件夹") { showFolderPicker = true }.disabled(model.isBusy).accessibilityIdentifier("settingsChooseVault")
                if model.isOriginalSource {
                    Button("打开原有 App 本地日记") { Task { await model.useLocalDiaries() } }.accessibilityIdentifier("settingsLocalDiaries")
                } else {
                    Button("重新打开已选原目录") { Task { await model.useOriginalDiaries() } }.accessibilityIdentifier("settingsOriginalDiaries")
                }
            }
            if let workflows = model.workflows {
                Section("模板与日期偏好") {
                    NavigationLink("日 / 周 / 月模板与星期语言") { TemplatesView(model: workflows) }
                        .accessibilityIdentifier("settingsTemplates")
                }
            }
        }.navigationTitle("文件与模板").navigationBarTitleDisplayMode(.inline)
            .fileImporter(isPresented: $showFolderPicker, allowedContentTypes: [.folder]) { result in
                switch result {
                case .success(let url): Task { await model.selectOriginalFolder(url) }
                case .failure(let error): model.sourcePickerFailed(error)
                }
            }
    }

}
