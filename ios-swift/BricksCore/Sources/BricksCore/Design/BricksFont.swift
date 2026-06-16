import SwiftUI
import CoreText

public enum BricksFont {

    public static func registerFonts() {
        registerFont(named: "IoskeleyMono-Regular.ttf")
        registerFont(named: "IoskeleyMono-Medium.ttf")
    }

    // MARK: - Semantic wrappers

    public static func mono(_ size: CGFloat) -> Font {
        .custom("Ioskeley-Mono", size: size)
    }

    public static func monoMedium(_ size: CGFloat) -> Font {
        .custom("Ioskeley-Mono-Medium", size: size)
    }

    /// Overline / label style — mono 10pt, uppercase applied at call site
    public static func overline(_ size: CGFloat = 10) -> Font {
        .custom("Ioskeley-Mono", size: size)
    }

    /// KPI large value — mono 28pt (field dashboard)
    public static func kpi(_ size: CGFloat = 28) -> Font {
        .custom("Ioskeley-Mono", size: size)
    }

    // MARK: - Private

    private static func registerFont(named fileName: String) {
        guard let url = Bundle.module.url(forResource: fileName, withExtension: nil) else {
            return
        }
        CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
    }
}
