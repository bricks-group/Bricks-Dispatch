import SwiftUI
import CoreText

public enum BricksFont {

    public static func registerFonts() {
        registerFont(named: "GeneralSans-Regular.otf")
        registerFont(named: "GeneralSans-Medium.otf")
        registerFont(named: "GeneralSans-Semibold.otf")
        registerFont(named: "IoskeleyMono-Regular.ttf")
        registerFont(named: "IoskeleyMono-Medium.ttf")
    }

    // MARK: - Body / Display (General Sans)

    public static func body(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        switch weight {
        case .medium:   return .custom("GeneralSans-Medium",   size: size)
        case .semibold: return .custom("GeneralSans-Semibold", size: size)
        default:        return .custom("GeneralSans-Regular",  size: size)
        }
    }

    // MARK: - Mono / Data (IoskeleyMono)

    public static func mono(_ size: CGFloat) -> Font {
        .custom("IoskeleyMono-Regular", size: size)
    }

    public static func monoMedium(_ size: CGFloat) -> Font {
        .custom("IoskeleyMono-Medium", size: size)
    }

    public static func overline(_ size: CGFloat = 10.5) -> Font {
        .custom("IoskeleyMono-Regular", size: size)
    }

    public static func kpi(_ size: CGFloat = 36) -> Font {
        .custom("IoskeleyMono-Regular", size: size)
    }

    // MARK: - Private

    private static func registerFont(named fileName: String) {
        guard let url = Bundle.module.url(forResource: fileName, withExtension: nil) else {
            return
        }
        CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
    }
}
