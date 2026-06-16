import SwiftUI
import BricksCore

private struct Slide {
    let eye: String
    let head: String
    let body: String
    let img: String
}

private let slides: [Slide] = [
    Slide(eye: "FOR SHOOTERS", head: "Your day, queued up.",   body: "Every job on the books — addresses, gate codes, packages, contact.",                    img: "VENDOR LOADING VAN"),
    Slide(eye: "NAVIGATE",     head: "Drive time baked in.",   body: "Bricks only books jobs you can actually make. Tap for directions.",                      img: "DASHBOARD MAP"),
    Slide(eye: "UPLOAD",       head: "Shoot. Upload. Done.",   body: "Drop the SD card in. Brokerage gets notified when editing starts.",                      img: "SD CARD + LAPTOP"),
]

struct WelcomeView: View {
    @AppStorage("welcome_seen") private var welcomeSeen = false
    @State private var current = 0

    private let night = Color(red: 0.078, green: 0.071, blue: 0.063)
    private let nightSoft = Color(red: 0.110, green: 0.102, blue: 0.090)
    private let nightHair = Color.white.opacity(0.08)

    var body: some View {
        ZStack {
            night.ignoresSafeArea()

            VStack(spacing: 0) {
                // Top bar
                HStack {
                    HStack(spacing: 8) {
                        BricksLogoMark(size: 3)
                        Text("bricks")
                            .font(.system(size: 15, weight: .medium))
                            .foregroundStyle(.white)
                    }
                    Spacer()
                    Button("Skip") { finish() }
                        .font(BricksFont.mono(13))
                        .foregroundStyle(.white.opacity(0.45))
                }
                .padding(.horizontal, 22)
                .padding(.top, 10)
                .padding(.bottom, 16)

                // Image placeholder
                ZStack {
                    nightSoft
                    RoundedRectangle(cornerRadius: 2)
                        .strokeBorder(nightHair, lineWidth: 1)
                    Text(slides[current].img)
                        .font(BricksFont.monoMedium(9))
                        .tracking(1.6)
                        .textCase(.uppercase)
                        .foregroundStyle(.white.opacity(0.3))
                }
                .frame(height: 300)
                .clipShape(RoundedRectangle(cornerRadius: 2))
                .padding(.horizontal, 22)

                // Copy
                VStack(alignment: .leading, spacing: 0) {
                    Text(slides[current].eye)
                        .font(BricksFont.monoMedium(10))
                        .tracking(1.8)
                        .textCase(.uppercase)
                        .foregroundStyle(BricksColors.brick)
                        .padding(.bottom, 10)

                    Text(slides[current].head)
                        .font(.system(size: 28, weight: .medium))
                        .foregroundStyle(.white)
                        .tracking(-0.8)
                        .lineSpacing(3)
                        .padding(.bottom, 10)

                    Text(slides[current].body)
                        .font(.system(size: 14))
                        .foregroundStyle(.white.opacity(0.55))
                        .lineSpacing(5)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 22)
                .padding(.top, 28)

                Spacer()

                // Footer: progress + CTA
                VStack(spacing: 20) {
                    HStack(spacing: 6) {
                        ForEach(0..<slides.count, id: \.self) { i in
                            Capsule()
                                .fill(i == current ? BricksColors.brick : Color.white.opacity(0.2))
                                .frame(height: 2)
                                .frame(maxWidth: i == current ? .infinity : nil)
                                .frame(minWidth: 0)
                        }
                    }

                    Button(action: advance) {
                        Text(current == slides.count - 1 ? "Get started" : "Next")
                            .font(.system(size: 14, weight: .medium))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(BricksColors.brick)
                            .foregroundStyle(.white)
                            .clipShape(RoundedRectangle(cornerRadius: 2))
                    }
                }
                .padding(.horizontal, 22)
                .padding(.bottom, 16)
            }
        }
        .preferredColorScheme(.dark)
        .animation(.easeInOut(duration: 0.22), value: current)
    }

    private func advance() {
        if current < slides.count - 1 {
            current += 1
        } else {
            finish()
        }
    }

    private func finish() {
        welcomeSeen = true
    }
}
