import SwiftUI
import UIKit

enum MentorPalette {
    static let paper = Color(red: 30/255, green: 39/255, blue: 34/255)
    static let forest = Color(red: 29/255, green: 42/255, blue: 34/255)
    static let surface = Color(red: 48/255, green: 56/255, blue: 44/255)
    static let text = Color(red: 242/255, green: 235/255, blue: 220/255)
    static let secondary = Color(red: 190/255, green: 191/255, blue: 169/255)
    static let border = Color(red: 67/255, green: 83/255, blue: 67/255)
    static let action = Color(red: 177/255, green: 199/255, blue: 182/255)
    static let onAction = Color(red: 23/255, green: 52/255, blue: 37/255)
}

struct MentorBackdrop: View {
    var body: some View {
        ZStack {
            MentorPalette.paper
            LinearGradient(colors: [MentorPalette.forest, MentorPalette.paper, MentorPalette.forest],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
            RadialGradient(colors: [MentorPalette.border.opacity(0.42), .clear],
                           center: UnitPoint(x: 0.5, y: 0.35), startRadius: 20, endRadius: 390)
            Canvas { context, size in
                for index in 0..<48 {
                    let x = CGFloat((index * 73 + 17) % 997) / 997 * size.width
                    let y = CGFloat((index * 113 + 37) % 991) / 991 * size.height
                    let radius: CGFloat = index.isMultiple(of: 7) ? 1 : 0.5
                    context.fill(Path(ellipseIn: CGRect(x: x, y: y, width: radius, height: radius)),
                                 with: .color(MentorPalette.text.opacity(index.isMultiple(of: 7) ? 0.42 : 0.2)))
                }
            }
        }.ignoresSafeArea().accessibilityHidden(true).allowsHitTesting(false)
    }
}

/// Static depth behind the existing portrait assets; it never carries interaction or state.
struct MentorStageDepth: View {
    var body: some View {
        GeometryReader { proxy in
            ZStack {
                RadialGradient(colors: [MentorPalette.secondary.opacity(0.16), .clear],
                               center: UnitPoint(x: 0.5, y: 0.78), startRadius: 4, endRadius: proxy.size.width * 0.65)
                Canvas { context, size in
                    for layer in 0..<3 {
                        var path = Path()
                        let baseline = size.height * (0.38 + CGFloat(layer) * 0.17)
                        path.move(to: CGPoint(x: 0, y: size.height))
                        path.addLine(to: CGPoint(x: 0, y: baseline))
                        for step in 0...16 {
                            let x = size.width * CGFloat(step) / 16
                            let rise = CGFloat((step * 13 + layer * 7) % 11) * 3
                            path.addLine(to: CGPoint(x: x, y: baseline + rise))
                        }
                        path.addLine(to: CGPoint(x: size.width, y: size.height)); path.closeSubpath()
                        context.fill(path, with: .color(MentorPalette.forest.opacity(0.28 + Double(layer) * 0.17)))
                    }
                    for index in 0..<5 {
                        let rect = CGRect(x: 0, y: size.height * 0.78 + CGFloat(index) * 9,
                                          width: size.width, height: 0.7)
                        context.fill(Path(rect), with: .color(MentorPalette.secondary.opacity(0.06)))
                    }
                }
                Circle().fill(RadialGradient(colors: [MentorPalette.border, MentorPalette.forest],
                    center: .topLeading, startRadius: 0, endRadius: 33))
                    .frame(width: 34, height: 34).opacity(0.65)
                    .position(x: proxy.size.width * 0.87, y: proxy.size.height * 0.2)
            }
        }.mask(LinearGradient(stops: [.init(color: .clear, location: 0), .init(color: .black, location: 0.08),
                                     .init(color: .black, location: 0.92), .init(color: .clear, location: 1)],
                              startPoint: .leading, endPoint: .trailing))
            .mask(LinearGradient(stops: [.init(color: .black, location: 0), .init(color: .black, location: 0.82),
                                         .init(color: .clear, location: 1)], startPoint: .top, endPoint: .bottom))
            .accessibilityHidden(true).allowsHitTesting(false)
    }
}

struct MentorNorthStar: View {
    var body: some View {
        Image(systemName: "sparkle").font(.system(size: 25, weight: .light))
            .foregroundStyle(MentorPalette.text)
            .shadow(color: MentorPalette.text.opacity(0.22), radius: 10)
            .accessibilityHidden(true)
    }
}

struct MentorPortrait: View {
    let actor: String
    var topCrop: CGFloat = 0.35
    private var index: Int? { ["gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"].firstIndex(of: actor) }
    var body: some View {
        GeometryReader { proxy in
            if let index, let atlas = UIImage(named: "MentorPortraitAtlas") {
                Image(uiImage: atlas).resizable()
                    .frame(width: proxy.size.width * 4, height: proxy.size.width * 2)
                    .offset(x: -CGFloat(index) * proxy.size.width, y: -proxy.size.width * topCrop)
            } else if let portrait = UIImage(named: "PublicMentor_\(actor)") {
                Image(uiImage: portrait).resizable().scaledToFill()
                    .frame(width: proxy.size.width, height: proxy.size.height)
            } else {
                Image(systemName: actor == "host" ? "sparkle" : "person.crop.circle")
                    .resizable().scaledToFit().padding(8).foregroundStyle(MentorPalette.secondary)
            }
        }.clipped().accessibilityHidden(true).allowsHitTesting(false)
    }
}

struct MentorStage: View {
    let mentor: String?
    let activeActor: String?
    let compact: Bool
    var participants: [String] = []
    var showsHost = true
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private let actors = ["gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"]
    private let names = ["温柔回顾者", "直率教练", "未来的我", "王阳明"]
    var body: some View {
        if !compact {
            GeometryReader { proxy in
                ZStack(alignment: .bottom) {
                    MentorStageDepth()
                    if let mentor {
                        MentorPortrait(actor: mentor, topCrop: 0.44)
                            .frame(width: min(proxy.size.width * 0.82, 340), height: 280).mentorPortraitFade()
                    } else {
                        HStack(alignment: .bottom, spacing: 0) {
                            ForEach(Array(actors.enumerated()).filter { showsHost || participants.contains($0.element) }, id: \.element) { index, actor in
                                VStack(spacing: 0) {
                                    MentorPortrait(actor: actor, topCrop: 0.42)
                                        .frame(height: proxy.size.width * 0.39).mentorPortraitFade()
                                    Text(names[index]).font(.caption2)
                                        .foregroundStyle(participants.contains(actor) ? MentorPalette.text : MentorPalette.secondary)
                                        .padding(.bottom, index == 1 || index == 2 ? 15 : 0)
                                }.frame(maxWidth: .infinity)
                                    .overlay(alignment: .bottom) {
                                        if activeActor == actor {
                                            Capsule().fill(MentorPalette.action).frame(width: 22, height: 2)
                                                .transition(.opacity)
                                        }
                                    }
                                    .animation(reduceMotion ? nil : .easeInOut(duration: 0.2), value: activeActor)
                            }
                        }.padding(.bottom, 56)
                        Ellipse().fill(LinearGradient(colors: [MentorPalette.surface.opacity(0.85), MentorPalette.forest],
                                                      startPoint: .top, endPoint: .bottom)).frame(height: 54)
                        Ellipse().stroke(MentorPalette.secondary.opacity(0.44), lineWidth: 0.7).frame(height: 54)
                        Ellipse().stroke(MentorPalette.secondary.opacity(0.18), lineWidth: 0.5)
                            .frame(width: proxy.size.width * 0.68, height: 32).padding(.bottom, 11)
                        if showsHost { Text("主持人").font(.caption).padding(.horizontal, 14).padding(.vertical, 7)
                            .background(MentorPalette.forest, in: Capsule())
                            .overlay(Capsule().stroke(MentorPalette.border)).padding(.bottom, 12)
                            .overlay(alignment: .bottom) {
                                if activeActor == "host" {
                                    Capsule().fill(MentorPalette.action).frame(width: 22, height: 2)
                                        .transition(.opacity)
                                }
                            }
                            .animation(reduceMotion ? nil : .easeInOut(duration: 0.2), value: activeActor)
                        }
                    }
                }.frame(width: proxy.size.width, height: proxy.size.height, alignment: .bottom)
            }.frame(height: mentor == nil ? 224 : 280).accessibilityHidden(true)
                .accessibilityIdentifier("mentorStageArtwork")
                .allowsHitTesting(false)
        }
    }
}

struct MentorSpeakerCard<Content: View, Accessory: View>: View {
    let actor: String
    let name: String
    let kind: String
    var featured = false
    var compact = false
    @Environment(\.dynamicTypeSize) private var typeSize
    @ViewBuilder let accessory: () -> Accessory
    @ViewBuilder let content: () -> Content
    var body: some View {
        Group {
            if featured && !compact && !typeSize.isAccessibilitySize {
                MentorFeaturedLayout {
                    dialogue
                    MentorPortrait(actor: actor, topCrop: 0.44).frame(width: 124, height: 174).mentorPortraitFade()
                }
            } else { dialogue }
        }.padding(16).frame(maxWidth: .infinity, alignment: .leading)
            .background {
                RoundedRectangle(cornerRadius: 18).fill(LinearGradient(
                    colors: [MentorPalette.surface.opacity(actor == "user" ? 0.5 : 0.96), MentorPalette.forest.opacity(0.92)],
                    startPoint: .topLeading, endPoint: .bottomTrailing))
            }
            .overlay(RoundedRectangle(cornerRadius: 18).stroke(MentorPalette.border.opacity(0.75), lineWidth: 0.6))
    }
    private var dialogue: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 10) {
                if actor != "user" && !featured {
                    MentorPortrait(actor: actor, topCrop: 0.42).frame(width: 48, height: 56)
                        .background(MentorPalette.forest).clipShape(RoundedRectangle(cornerRadius: 14))
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(name).font(.headline)
                    Text(kind).font(.caption).foregroundStyle(MentorPalette.secondary)
                }.frame(maxWidth: .infinity, alignment: .leading)
                accessory()
            }
            content().lineSpacing(5).fixedSize(horizontal: false, vertical: true)
        }
    }
}

// Measure the dialogue with its real column width so long text wraps before sizing the card.
private struct MentorFeaturedLayout: Layout {
    private let portrait = CGSize(width: 124, height: 174)
    private let spacing: CGFloat = 8
    private func horizontal(_ width: CGFloat) -> Bool { width >= 330 }
    private func textSize(_ subviews: Subviews, width: CGFloat) -> CGSize {
        subviews[0].sizeThatFits(ProposedViewSize(
            width: horizontal(width) ? width - portrait.width - spacing : width, height: nil))
    }
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 360
        let text = textSize(subviews, width: width)
        return CGSize(width: width, height: horizontal(width) ? max(text.height, portrait.height) : text.height + spacing + portrait.height)
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let text = textSize(subviews, width: bounds.width)
        subviews[0].place(at: CGPoint(x: bounds.minX, y: horizontal(bounds.width) ? bounds.maxY - text.height : bounds.minY),
                          anchor: .topLeading, proposal: ProposedViewSize(text))
        subviews[1].place(at: CGPoint(x: bounds.maxX - portrait.width, y: bounds.maxY - portrait.height),
                          anchor: .topLeading, proposal: ProposedViewSize(portrait))
    }
}

struct MentorControlStyle: ButtonStyle {
    var prominent = false
    @Environment(\.isEnabled) private var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.padding(.horizontal, 16).padding(.vertical, 10)
            .frame(minWidth: 44, minHeight: 44)
            .foregroundStyle(prominent ? MentorPalette.onAction : MentorPalette.text)
            .background(prominent ? MentorPalette.action : MentorPalette.surface, in: Capsule())
            .overlay(Capsule().stroke(MentorPalette.border, lineWidth: 0.7))
            .opacity(enabled ? (configuration.isPressed ? 0.7 : 1) : 0.45)
    }
}

private struct MentorDestinationStyle: ViewModifier {
    func body(content: Content) -> some View {
        content.foregroundStyle(MentorPalette.text).tint(MentorPalette.action)
            .background { MentorBackdrop() }
            .toolbarBackground(MentorPalette.forest, for: .navigationBar, .tabBar)
            .toolbarBackground(.visible, for: .navigationBar, .tabBar)
            .toolbarColorScheme(.dark, for: .navigationBar, .tabBar)
            .environment(\.colorScheme, .dark)
    }
}

extension View {
    func mentorPortraitFade() -> some View {
        mask(LinearGradient(stops: [.init(color: .black, location: 0), .init(color: .black, location: 0.74),
                                    .init(color: .clear, location: 1)], startPoint: .top, endPoint: .bottom))
    }
    func mentorDestinationStyle() -> some View { modifier(MentorDestinationStyle()) }
    func mentorInputStyle() -> some View {
        padding(12).background(MentorPalette.surface, in: RoundedRectangle(cornerRadius: 16))
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(MentorPalette.border, lineWidth: 0.7))
    }
}
