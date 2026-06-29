import SwiftUI

// MARK: — Tier model

/// The Bricks subscription ladder. Each tier has a *material* that fills the
/// glass brick gauge. clay → gold → platinum → diamond.
///
/// The brick is the brand: an empty glass "b" that fills, bottom-up, with the
/// material of the member's tier. Fill level = credits remaining this cycle.
public enum BricksTier: String, CaseIterable, Sendable {
    case clay, gold, platinum, diamond

    public var displayName: String {
        switch self {
        case .clay: "Clay"; case .gold: "Gold"
        case .platinum: "Platinum"; case .diamond: "Diamond"
        }
    }

    /// Monthly credit allotment (resets monthly, no rollover).
    public var monthlyCredits: Int {
        switch self { case .clay: 0; case .gold: 1; case .platinum: 2; case .diamond: 4 }
    }

    /// Monthly price in CAD, billed annually.
    public var monthlyPrice: Int {
        switch self { case .clay: 0; case .gold: 179; case .platinum: 329; case .diamond: 599 }
    }

    public var tagline: String {
        switch self {
        case .clay: "Pay as you go"
        case .gold, .platinum, .diamond: "\(monthlyCredits) credit\(monthlyCredits == 1 ? "" : "s") / mo"
        }
    }

    /// Vertical material gradient (top → bottom).
    public var material: [Color] {
        switch self {
        case .clay:     [Color(hex: 0xD69163), Color(hex: 0xB5663B), Color(hex: 0x8A4724)]
        case .gold:     [Color(hex: 0xF2D687), Color(hex: 0xD6A94A), Color(hex: 0x9A7022)]
        case .platinum: [Color(hex: 0xF1F3F6), Color(hex: 0xC6CCD5), Color(hex: 0x8B93A1)]
        case .diamond:  [Color(hex: 0xEAF4FC), Color(hex: 0xAAD3EF), Color(hex: 0x6BA6D8)]
        }
    }

    /// Bright edge / glow colour for the tier.
    public var edge: Color {
        switch self {
        case .clay: Color(hex: 0xC8744A); case .gold: Color(hex: 0xE7C572)
        case .platinum: Color(hex: 0xD8DDE4); case .diamond: Color(hex: 0xBFE0F5)
        }
    }

    /// Specular strength of the material's sheen.
    public var shine: Double {
        switch self { case .clay: 0.16; case .gold: 0.5; case .platinum: 0.62; case .diamond: 0.82 }
    }
}

// MARK: — The glass brick gauge

/// The canonical Bricks "b" — three rounded bricks — rendered as glass and
/// filled, bottom-up, with the member's tier material. An ambient specular
/// sweep gives it a living, glassy quality. Diamond gets facets; platinum a
/// brushed grain.
public struct TierBrick: View {
    public let tier: BricksTier
    /// 0…1 — how full the brick is (credits remaining ÷ allotment).
    public var fill: Double
    public var animated: Bool

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var appear: Date = .now

    public init(tier: BricksTier, fill: Double = 1, animated: Bool = true) {
        self.tier = tier
        self.fill = max(0, min(1, fill))
        self.animated = animated
    }

    // 24-grid brick rects: top-wide, bottom-left, bottom-right → reads as "b"
    private let bricks: [(CGFloat, CGFloat, CGFloat, CGFloat)] =
        [(4, 5, 11, 7), (4, 13, 6, 7), (11, 13, 10, 7)]
    private let topY: CGFloat = 5, botY: CGFloat = 20

    public var body: some View {
        TimelineView(.animation(paused: !animated || reduceMotion)) { tl in
            Canvas { ctx, size in
                draw(&ctx, size, now: tl.date)
            }
        }
        .aspectRatio(1, contentMode: .fit)
        .accessibilityElement()
        .accessibilityLabel("\(tier.displayName) tier")
        .accessibilityValue("\(Int((fill * Double(tier.monthlyCredits)).rounded())) of \(tier.monthlyCredits) credits remaining")
    }

    private func brickPaths(_ size: CGSize) -> [Path] {
        let s = min(size.width, size.height)
        let ox = (size.width - s) / 2, oy = (size.height - s) / 2
        func r(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat) -> Path {
            Path(roundedRect: CGRect(x: ox + x / 24 * s, y: oy + y / 24 * s,
                                     width: w / 24 * s, height: h / 24 * s),
                 cornerRadius: 1.4 / 24 * s)
        }
        return bricks.map { r($0.0, $0.1, $0.2, $0.3) }
    }

    private func draw(_ ctx: inout GraphicsContext, _ size: CGSize, now: Date) {
        let s = min(size.width, size.height)
        let oy = (size.height - s) / 2
        let paths = brickPaths(size)
        var bPath = Path(); paths.forEach { bPath.addPath($0) }

        let contentTop = oy + topY / 24 * s
        let contentBot = oy + botY / 24 * s

        // appear ramp (ease-out over 0.9s) × target fill
        let t = now.timeIntervalSince(appear)
        let ramp = (animated && !reduceMotion) ? min(1, max(0, t / 0.9)) : 1
        let p = (1 - pow(1 - ramp, 3)) * fill

        let fillTopY = contentBot - (contentBot - contentTop) * p

        // empty glass: faint tint
        ctx.fill(bPath, with: .color(.white.opacity(0.04)))

        // material fill, clipped to brick silhouette
        ctx.drawLayer { lc in
            lc.clip(to: bPath)
            let band = CGRect(x: 0, y: fillTopY, width: size.width, height: contentBot - fillTopY + 1)
            lc.fill(Path(band), with: .linearGradient(
                Gradient(colors: tier.material),
                startPoint: CGPoint(x: 0, y: contentTop),
                endPoint: CGPoint(x: 0, y: contentBot)))

            // meniscus surface highlight
            if p > 0.02 && p < 0.99 {
                lc.fill(Path(CGRect(x: 0, y: fillTopY, width: size.width, height: max(0.8, s * 0.5 / 24))),
                        with: .color(.white.opacity(0.55)))
            }

            // diamond facets / platinum grain
            if p > 0.1 {
                if tier == .diamond {
                    lc.fill(facet(s, oy, [(4,14),(9,11),(7,16)]), with: .color(.white.opacity(0.22)))
                    lc.fill(facet(s, oy, [(12,15),(17,13),(15,19)]), with: .color(.white.opacity(0.16)))
                } else if tier == .platinum {
                    func g(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: x / 24 * s, y: oy + y / 24 * s) }
                    var grain = Path()
                    grain.move(to: g(3, 15)); grain.addLine(to: g(20, 14.4))
                    grain.move(to: g(3, 17.4)); grain.addLine(to: g(20, 17))
                    lc.stroke(grain, with: .color(.white.opacity(0.3)), lineWidth: s * 0.18 / 24)
                }
            }

            // ambient specular sweep — a skewed light band crossing the glass
            if animated && !reduceMotion {
                let phase = (now.timeIntervalSinceReferenceDate * 0.22)
                    .truncatingRemainder(dividingBy: 1)
                let cx = (-0.25 + 1.5 * phase) * size.width
                let bw = s * 0.26, skew = s * 0.3
                var band = Path()
                band.move(to: CGPoint(x: cx, y: -s))
                band.addLine(to: CGPoint(x: cx + bw, y: -s))
                band.addLine(to: CGPoint(x: cx + bw - skew, y: size.height + s))
                band.addLine(to: CGPoint(x: cx - skew, y: size.height + s))
                band.closeSubpath()
                let a = tier.shine * (1 - abs(phase - 0.3) * 2).clamped()
                lc.fill(band, with: .linearGradient(
                    Gradient(colors: [.white.opacity(0), .white.opacity(a), .white.opacity(0)]),
                    startPoint: CGPoint(x: cx, y: 0), endPoint: CGPoint(x: cx + bw, y: 0)))
            }
        }

        // glass edges + top sheen drawn on top so brick seams read through material
        let edgeColor: Color = p > 0.5 ? .white.opacity(0.3) : .black.opacity(0.28)
        for path in paths {
            ctx.stroke(path, with: .color(edgeColor), lineWidth: s * 0.5 / 24)
        }
    }

    private func facet(_ s: CGFloat, _ oy: CGFloat, _ pts: [(CGFloat, CGFloat)]) -> Path {
        var p = Path()
        for (i, c) in pts.enumerated() {
            let q = CGPoint(x: c.0 / 24 * s, y: oy + c.1 / 24 * s)
            if i == 0 { p.move(to: q) } else { p.addLine(to: q) }
        }
        p.closeSubpath(); return p
    }
}

// MARK: — Tier chip (the ladder)

public struct TierChip: View {
    public let tier: BricksTier
    public let active: Bool
    public init(tier: BricksTier, active: Bool) { self.tier = tier; self.active = active }

    public var body: some View {
        VStack(spacing: 8) {
            TierBrick(tier: tier, fill: 1, animated: false)
                .frame(width: 38, height: 38)
            Text(tier.displayName)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(active ? .white : .white.opacity(0.45))
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 12)
        .background(
            RoundedRectangle(cornerRadius: 4)
                .fill(active ? Color.white.opacity(0.06) : .clear)
                .overlay(RoundedRectangle(cornerRadius: 4)
                    .stroke(active ? tier.edge : .white.opacity(0.12), lineWidth: 1))
        )
    }
}

// MARK: — Color hex helper

public extension Color {
    init(hex: UInt32) {
        self.init(.sRGB,
                  red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255,
                  opacity: 1)
    }
}

private extension Double {
    func clamped() -> Double { Swift.max(0, Swift.min(1, self)) }
}
