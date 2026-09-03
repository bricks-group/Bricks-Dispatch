import SwiftUI
import BricksCore

enum CaptureChrome {
    static let night = Color(hex: "#0C0B09")
    static let moon = Color(hex: "#F4F1EB")
    static var brick: Color { BricksColors.brick }
}

struct CaptureCloseChip: View {
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Image(systemName: "xmark")
                .font(.body.weight(.semibold))
                .foregroundStyle(CaptureChrome.moon)
                .frame(width: 34, height: 34)
                .overlay(
                    RoundedRectangle(cornerRadius: 2)
                        .stroke(CaptureChrome.moon.opacity(0.19), lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Close")
    }
}

struct CaptureGhostChip: View {
    let title: String
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Text(title)
                .font(BricksFont.mono(11))
                .foregroundStyle(CaptureChrome.moon)
                .frame(width: 44, height: 44)
                .overlay(
                    RoundedRectangle(cornerRadius: 2)
                        .stroke(CaptureChrome.moon.opacity(0.19), lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
    }
}

struct CaptureShutterButton: View {
    var pressed: Bool
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .stroke(CaptureChrome.moon, lineWidth: 3)
                    .frame(width: 74, height: 74)
                Circle()
                    .fill(CaptureChrome.brick)
                    .frame(width: 58, height: 58)
            }
            .scaleEffect(pressed ? 0.92 : 1)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Shutter")
    }
}

struct CoverageDomeView: View {
    var captured: Set<Int>
    var total: Int = CaptureDome.total
    var headingTick: Int

    var body: some View {
        Canvas { ctx, size in
            let cx = size.width / 2
            let cy = size.height / 2
            let R = min(size.width, size.height) / 2 - 10
            var inner = Path()
            inner.addEllipse(in: CGRect(x: cx - (R - 22), y: cy - (R - 22), width: (R - 22) * 2, height: (R - 22) * 2))
            ctx.stroke(inner, with: .color(CaptureChrome.moon.opacity(0.10)), lineWidth: 1)
            var outer = Path()
            outer.addEllipse(in: CGRect(x: cx - (R + 6), y: cy - (R + 6), width: (R + 6) * 2, height: (R + 6) * 2))
            ctx.stroke(outer, with: .color(CaptureChrome.moon.opacity(0.07)), lineWidth: 1)

            for i in 0..<total {
                let ang = (Double(i) / Double(total)) * .pi * 2 - .pi / 2
                let on = captured.contains(i)
                let aim = headingTick == i
                let x1 = cx + Cos(ang) * (R - 12)
                let y1 = cy + Sin(ang) * (R - 12)
                let x2 = cx + Cos(ang) * R
                let y2 = cy + Sin(ang) * R
                var tick = Path()
                tick.move(to: CGPoint(x: x1, y: y1))
                tick.addLine(to: CGPoint(x: x2, y: y2))
                let color: Color = aim ? CaptureChrome.moon : (on ? CaptureChrome.brick : CaptureChrome.moon.opacity(0.19))
                ctx.stroke(tick, with: .color(color), style: StrokeStyle(lineWidth: (aim || on) ? 3 : 2, lineCap: .round))
            }

            var cross = Path()
            cross.move(to: CGPoint(x: cx - 10, y: cy))
            cross.addLine(to: CGPoint(x: cx + 10, y: cy))
            cross.move(to: CGPoint(x: cx, y: cy - 10))
            cross.addLine(to: CGPoint(x: cx, y: cy + 10))
            ctx.stroke(cross, with: .color(CaptureChrome.moon.opacity(0.33)), lineWidth: 1)
        }
        .frame(width: 220, height: 220)
    }

    private func Cos(_ a: Double) -> CGFloat { CGFloat(cos(a)) }
    private func Sin(_ a: Double) -> CGFloat { CGFloat(sin(a)) }
}

struct FloorplanCornerBox: View {
    var captured: Int
    var total: Int = CaptureDome.floorplanCorners

    var body: some View {
        Canvas { ctx, size in
            let on: (Int) -> Bool = { $0 < min(captured, total) }
            let rect = CGRect(x: 18, y: 12, width: 124, height: 86)
            var box = Path(rect)
            ctx.stroke(box, with: .color(CaptureChrome.moon.opacity(0.13)), lineWidth: 1.5)
            strokeCorner(&ctx, on: on(0), a: CGPoint(x: 18, y: 12), b: CGPoint(x: 36, y: 12), c: CGPoint(x: 18, y: 30))
            strokeCorner(&ctx, on: on(1), a: CGPoint(x: 142, y: 12), b: CGPoint(x: 124, y: 12), c: CGPoint(x: 142, y: 30))
            strokeCorner(&ctx, on: on(2), a: CGPoint(x: 142, y: 98), b: CGPoint(x: 124, y: 98), c: CGPoint(x: 142, y: 80))
            strokeCorner(&ctx, on: on(3), a: CGPoint(x: 18, y: 98), b: CGPoint(x: 36, y: 98), c: CGPoint(x: 18, y: 80))
        }
        .frame(width: 160, height: 110)
    }

    private func strokeCorner(_ ctx: inout GraphicsContext, on: Bool, a: CGPoint, b: CGPoint, c: CGPoint) {
        var p = Path()
        p.move(to: b)
        p.addLine(to: a)
        p.addLine(to: c)
        ctx.stroke(p, with: .color(on ? CaptureChrome.brick : CaptureChrome.moon.opacity(0.25)), style: StrokeStyle(lineWidth: on ? 4 : 2, lineCap: .square, lineJoin: .miter))
    }
}

struct CaptureGuidanceBanner: View {
    let title: String
    let bodyText: String
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.white)
            Text(bodyText)
                .font(.system(size: 11))
                .foregroundStyle(.white.opacity(0.8))
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(CaptureChrome.brick)
        .clipShape(RoundedRectangle(cornerRadius: 2))
        .padding(.horizontal, 20)
    }
}

struct CaptureErrorBanner: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.system(size: 11))
            .foregroundStyle(CaptureChrome.moon)
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity)
            .padding(8)
            .background(BricksColors.bad)
            .clipShape(RoundedRectangle(cornerRadius: 2))
            .padding(.horizontal, 20)
    }
}

struct CapturePermGate: View {
    let message: String
    var buttonTitle: String = "Allow camera"
    var onAllow: (() -> Void)?
    var onClose: () -> Void

    var body: some View {
        ZStack {
            CaptureChrome.night.ignoresSafeArea()
            VStack(spacing: 20) {
                HStack {
                    CaptureCloseChip(action: onClose)
                    Spacer()
                }
                .padding(.horizontal, 20)
                Spacer()
                Text(message)
                    .font(.system(size: 14))
                    .foregroundStyle(CaptureChrome.moon)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
                if let onAllow {
                    Button(action: onAllow) {
                        Text(buttonTitle)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.white)
                            .padding(.horizontal, 24)
                            .padding(.vertical, 12)
                            .background(CaptureChrome.brick)
                            .clipShape(RoundedRectangle(cornerRadius: 2))
                    }
                    .buttonStyle(.plain)
                }
                Spacer()
            }
        }
    }
}
