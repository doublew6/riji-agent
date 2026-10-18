# Riji app icon

The owner selected concept 11, **Memory Pages**, on 2026-09-16. The mark combines overlapping diary pages, a bookmark and a conversation tail. The approved palette uses warm ivory, ochre and muted olive.

- [Original selected artwork](riji-memory-pages-source.png): transparent 1254 × 1254 PNG, preserved from the design exploration.
- [Opaque production master](riji-memory-pages-master.png): 1254 × 1254 PNG prepared using built-in imagegen with the selected artwork as the edit target.
- [Shipping app icon](../../ios/Riji/Assets.xcassets/AppIcon.appiconset/AppIcon.png): opaque 1024 × 1024 PNG exported from the master using `sips --resampleHeightWidth 1024 1024`.

The Xcode target uses `Assets.xcassets/AppIcon.appiconset` in both Debug and Release. Xcode derives the smaller system icons from the single iOS image. iOS applies the outer corner mask; the source image has square corners. This asset does not change the app identifier, signing team, diary storage or backend configuration.

References: [Apple asset-catalog icon configuration](https://developer.apple.com/documentation/xcode/configuring-your-app-icon).

## Production edit prompt

Tool: built-in imagegen. Input: the original selected artwork linked above.

Edit target: the attached approved Riji icon concept 11. Prepare it for an iPhone home-screen app icon. Only composite the existing transparent artwork onto one absolutely solid opaque muted olive background, RGB 65,69,45 (#41452D), extending to all four square corners. Preserve the two overlapping diary pages, exact shapes and composition, warm ivory front page, ochre back page, green bookmark and two green writing lines, speech-tail contour, proportions, scale and placement. Do not redraw, redesign, relight, add texture, add shadow, sharpen stylistically, change colors, add text, or add decorative elements. Keep the existing square framing and generous padding. Output a flat square PNG app icon with opaque background and no alpha/transparency, ideally 1024x1024. No outside border and no baked-in rounded corners. The olive background is part of the final image; do not remove it as background.
