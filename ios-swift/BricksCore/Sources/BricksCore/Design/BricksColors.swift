import SwiftUI

public extension Color {
    /// Parses a "#RRGGBB" or "#RRGGBBAA" hex string (web design-token format).
    init(hex: String) {
        var s = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        if s.count == 6 { s += "FF" }
        var v: UInt64 = 0
        Scanner(string: s).scanHexInt64(&v)
        self.init(
            .sRGB,
            red: Double((v >> 24) & 0xFF) / 255,
            green: Double((v >> 16) & 0xFF) / 255,
            blue: Double((v >> 8) & 0xFF) / 255,
            opacity: Double(v & 0xFF) / 255
        )
    }
}

/// Shared design tokens. Values mirror the canonical palette in every other
/// Bricks surface (frontend/src/lib/bricks.js, tailwind.config.js, design_guidelines.json)
/// so all platforms render the same colors -- this file must stay in sync with those.
public enum BricksColors {
    /// Signature brick-red brand colour.
    public static let brick = Color("BricksBrick")
    /// Gold accent — resolved earnings, credits, premium moments.
    public static let gold  = Color("BricksGold")

    public static let brickDeep = Color(hex: "#A8401F")
    public static let brickSoft = Color(hex: "#F4D9CC")
    public static let brickTint = Color(hex: "#FBEFE8")

    // Light ("paper") surface + text tokens
    public static let paper     = Color(hex: "#F4F1EB")
    public static let paperSoft = Color(hex: "#EDE8DF")
    public static let paperDeep = Color(hex: "#E4DED2")
    public static let hair       = Color(hex: "#1411100F")
    public static let hairStrong = Color(hex: "#14111020")
    public static let ink      = Color(hex: "#14110E")
    public static let inkSoft  = Color(hex: "#3B332A")
    public static let inkMute  = Color(hex: "#6B6054")
    public static let inkFaint = Color(hex: "#A39684")

    // Dark ("night") surface + text tokens
    public static let night      = Color(hex: "#141210")
    public static let nightSoft  = Color(hex: "#1C1A17")
    public static let nightDeep  = Color(hex: "#23201C")
    public static let nightHair  = Color(hex: "#F4F1EB14")
    public static let nightHairS = Color(hex: "#F4F1EB26")
    public static let moon      = Color(hex: "#F4F1EB")
    public static let moonSoft  = Color(hex: "#D6CFC2")
    public static let moonMute  = Color(hex: "#8F8678")
    public static let moonFaint = Color(hex: "#5E574C")

    // Status colours
    public static let ok      = Color(hex: "#4A6B3C")
    public static let okTint  = Color(hex: "#E6ECDD")
    public static let warn     = Color(hex: "#B8741F")
    public static let warnTint = Color(hex: "#F6E8D1")
    public static let bad      = Color(hex: "#8C2F24")
    public static let badTint  = Color(hex: "#F1DAD4")
}
