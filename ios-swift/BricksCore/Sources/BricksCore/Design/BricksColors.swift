import SwiftUI

public enum BricksColors {
    // Paper (light surfaces — Booking)
    public static let paper     = Color(hex: "F4F1EB")
    public static let paperSoft = Color(hex: "EDE8DF")
    public static let paperDeep = Color(hex: "E4DED2")

    // Ink (light text)
    public static let ink       = Color(hex: "14110E")
    public static let inkSoft   = Color(hex: "3B332A")
    public static let inkMute   = Color(hex: "6B6054")
    public static let inkFaint  = Color(hex: "A39684")

    // Night (dark surfaces — Dispatch)
    public static let night     = Color(hex: "141210")
    public static let nightSoft = Color(hex: "1C1A17")
    public static let nightDeep = Color(hex: "23201C")

    // Moon (dark text)
    public static let moon      = Color(hex: "F4F1EB")
    public static let moonSoft  = Color(hex: "D6CFC2")
    public static let moonMute  = Color(hex: "8F8678")
    public static let moonFaint = Color(hex: "5E574C")

    // Accent
    public static let brick     = Color(hex: "D2542B")
    public static let brickDeep = Color(hex: "A8401F")
    public static let brickSoft = Color(hex: "F4D9CC")
    public static let brickTint = Color(hex: "FBEFE8")

    // Semantic
    public static let ok        = Color(hex: "4A6B3C")
    public static let okTint    = Color(hex: "E6ECDD")
    public static let warn      = Color(hex: "B8741F")
    public static let warnTint  = Color(hex: "F6E8D1")

    // Hairlines (low-opacity borders)
    public static let hair       = Color(hex: "14110E").opacity(0.06)
    public static let hairStrong = Color(hex: "14110E").opacity(0.13)
    public static let nightHair  = Color(hex: "F4F1EB").opacity(0.08)
    public static let nightHairS = Color(hex: "F4F1EB").opacity(0.15)

    // Legacy aliases
    public static let accent = brick
    public static let moon_legacy = moon
}

public extension Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let r = Double((int >> 16) & 0xFF) / 255
        let g = Double((int >> 8)  & 0xFF) / 255
        let b = Double(int         & 0xFF) / 255
        self.init(red: r, green: g, blue: b)
    }
}
