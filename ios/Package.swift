// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RijiCore",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "RijiCore", targets: ["RijiCore"])],
    targets: [
        .target(name: "RijiICloudProbeSupport", path: "ProbeSupport"),
        .testTarget(name: "RijiICloudProbeSupportTests", dependencies: ["RijiICloudProbeSupport"], path: "ProbeSupportTests"),
        .target(name: "RijiCore", path: "RijiCore",
                sources: ["NorthStarAnchor.swift", "MobileRoundtableModels.swift", "MobileRoundtableCache.swift", "AIResultStore.swift", "MobileOverviewModels.swift", "MobileOverviewCache.swift", "MarkdownVersionDiff.swift", "MobileInstallation.swift", "MobileModels.swift", "MobileTransport.swift", "MobilePersistence.swift", "CloudVersions.swift", "CloudRecoveryStore.swift", "OriginalVaultAccess.swift", "LocalVaultReader.swift", "MarkdownPresentation.swift", "MarkdownTableEditing.swift", "BodyRevisionStore.swift", "DailyTemplate.swift", "GoalReviewStore.swift", "JournalFileSystem.swift", "JournalModels.swift", "LocalJournalStore.swift", "MarkdownSectionAppender.swift", "VoiceAbandonmentStore.swift", "VoiceDerivedAudioStore.swift", "VoiceEvidenceReader.swift", "VoiceHistoryStore.swift", "VoiceInsertionStore.swift", "VoiceModels.swift", "VoiceStore.swift", "WorkflowModels.swift", "WorkflowStore.swift"]),
        .testTarget(name: "RijiCoreTests", dependencies: ["RijiCore"], path: "RijiCoreTests"),
        .target(name: "RijiState", dependencies: ["RijiCore"], path: "Riji",
                exclude: ["ApprovedPageStyle.swift", "ApprovedSettingsViews.swift", "MentorDialogueStyle.swift", "NorthStarAnchorViews.swift", "MobileRoundtableViews.swift", "MobileOverviewViews.swift", "MarkdownDiffView.swift", "CloudVersionReviewView.swift", "MobileViews.swift", "MobileSettingsViews.swift", "RijiNavigationView.swift", "VaultFileView.swift", "MarkdownTextView.swift", "DiaryBodyEditor.swift", "RijiApp.swift", "TodayDiaryView.swift", "DiaryDirectoryView.swift", "WorkflowViews.swift", "VoiceViews.swift", "NativeVoiceServices.swift", "ContinuousVoiceWriter.swift", "Info.plist"],
                sources: ["NorthStarAnchorModel.swift", "MobileRoundtableState.swift", "MobileRoundtableFixture.swift", "MobileOverviewState.swift", "MobileClientModel.swift", "MobileKeychain.swift", "VaultBrowserStore.swift", "VaultBrowserModel.swift", "DiaryViewModel.swift", "MarkdownDocument.swift", "WorkflowViewModel.swift", "VoiceViewModel.swift"]),
        .testTarget(name: "RijiStateTests", dependencies: ["RijiState", "RijiCore"], path: "RijiStateTests")
    ]
)
