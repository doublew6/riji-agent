// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RijiCore",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "RijiCore", targets: ["RijiCore"])],
    targets: [
        .target(name: "RijiCore", path: "RijiCore"),
        .testTarget(name: "RijiCoreTests", dependencies: ["RijiCore"], path: "RijiCoreTests"),
        .target(name: "RijiState", dependencies: ["RijiCore"], path: "Riji",
                exclude: ["RijiApp.swift", "TodayDiaryView.swift", "DiaryDirectoryView.swift"],
                sources: ["DiaryViewModel.swift", "MarkdownDocument.swift"]),
        .testTarget(name: "RijiStateTests", dependencies: ["RijiState", "RijiCore"], path: "RijiStateTests")
    ]
)
