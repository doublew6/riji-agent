import SwiftUI
import UIKit

/// Approved diary, memory and settings palette. Mentor screens own their separate palette.
enum RijiPalette {
    static let paper = adaptive(0xFAF4EA, 0x1E2722)
    static let warmSurface = adaptive(0xF0E3D2, 0x30382C)
    static let selectedSurface = adaptive(0xE7EBE2, 0x303B30)
    static let forestSurface = adaptive(0x253D32, 0x1D2A22)
    static let onForest = adaptive(0xFAF4EA, 0xFAF4EA)
    static let onForestSecondary = adaptive(0xCCDCCD, 0xCCDCCD)
    static let brand = adaptive(0x2D5342, 0xABC3B0)
    static let action = adaptive(0x355C4B, 0xB1C7B6)
    static let text = adaptive(0x30372F, 0xF2EBDC)
    static let secondary = adaptive(0x676554, 0xBEBFA9)
    static let border = adaptive(0xD9D7C7, 0x435343)
    static let onAction = adaptive(0xFFF9F0, 0x173425)
    static let warning = adaptive(0x82572E, 0xE9BC8A)
    static let error = adaptive(0x9C4335, 0xEFB4A3)

    private static func adaptive(_ light: UInt32, _ dark: UInt32) -> UIColor {
        UIColor { traits in
            let hex = traits.userInterfaceStyle == .dark ? dark : light
            return UIColor(red: CGFloat((hex >> 16) & 255) / 255,
                           green: CGFloat((hex >> 8) & 255) / 255,
                           blue: CGFloat(hex & 255) / 255, alpha: 1)
        }
    }
}

extension View {
    func rijiEmphasisSurface(featured: Bool = false) -> some View {
        foregroundStyle(Color(uiColor: featured ? RijiPalette.onForest : RijiPalette.text))
            .padding(18).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(uiColor: featured ? RijiPalette.forestSurface : RijiPalette.warmSurface),
                        in: RoundedRectangle(cornerRadius: 18))
    }

    func rijiPage() -> some View {
        self.foregroundStyle(Color(uiColor: RijiPalette.text))
            .tint(Color(uiColor: RijiPalette.action))
            .scrollContentBackground(.hidden)
            .background(Color(uiColor: RijiPalette.paper))
            .toolbarBackground(Color(uiColor: RijiPalette.paper), for: .navigationBar, .tabBar)
            .toolbarBackground(.visible, for: .navigationBar, .tabBar)
    }
}

struct RijiList<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        List { content.listRowBackground(Color(uiColor: RijiPalette.paper)) }
            .listStyle(.insetGrouped).rijiPage()
            .environment(\.defaultMinListRowHeight, 44)
    }
}

struct RijiForm<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        Form { content.listRowBackground(Color(uiColor: RijiPalette.warmSurface)) }
            .rijiPage().environment(\.defaultMinListRowHeight, 44)
    }
}

struct RijiRow: View {
    let title: String
    let detail: String
    let symbol: String
    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: symbol).foregroundStyle(Color(uiColor: RijiPalette.brand))
                .frame(minWidth: 24).padding(.top, 3)
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.headline)
                Text(detail).font(.subheadline).foregroundStyle(Color(uiColor: RijiPalette.secondary))
            }.fixedSize(horizontal: false, vertical: true)
        }.frame(minHeight: 44, alignment: .leading).padding(.vertical, 6)
    }
}

struct RijiUnavailable: View {
    let title: String
    let detail: String
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: "clock").font(.headline)
            Text(detail).font(.callout).foregroundStyle(Color(uiColor: RijiPalette.secondary))
        }.fixedSize(horizontal: false, vertical: true).padding(.vertical, 8)
    }
}

struct RijiPrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.body.weight(.semibold))
            .frame(maxWidth: .infinity, minHeight: 44).padding(.horizontal, 16).padding(.vertical, 4)
            .foregroundStyle(Color(uiColor: isEnabled ? RijiPalette.onAction : RijiPalette.secondary))
            .background(Color(uiColor: isEnabled ? RijiPalette.action : RijiPalette.selectedSurface).opacity(configuration.isPressed && isEnabled ? 0.8 : 1),
                        in: RoundedRectangle(cornerRadius: 14))
    }
}
