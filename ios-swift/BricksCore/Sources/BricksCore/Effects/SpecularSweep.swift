import SwiftUI

/// A specular highlight that sweeps diagonally across a view when triggered.
/// Implemented as a blended LinearGradient overlay — no Metal compiler required,
/// visually equivalent for the delivery reveal use case.
///
/// Usage:
///   AsyncImage(url: url)
///       .specularSweep(trigger: photoDidSettle)
public struct SpecularSweep: ViewModifier {
    /// Toggle this bool to trigger a single sweep. Each new toggle fires one pass.
    let trigger: Bool
    @State private var phase: CGFloat = -1.6
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    public func body(content: Content) -> some View {
        content
            .overlay(alignment: .topLeading) {
                if !reduceMotion {
                    GeometryReader { geo in
                        let w = geo.size.width
                        let h = geo.size.height
                        // Diagonal specular band
                        LinearGradient(
                            stops: [
                                .init(color: .clear,               location: 0.0),
                                .init(color: .white.opacity(0.0),  location: 0.38),
                                .init(color: .white.opacity(0.26), location: 0.48),
                                .init(color: .white.opacity(0.36), location: 0.50),
                                .init(color: .white.opacity(0.26), location: 0.52),
                                .init(color: .white.opacity(0.0),  location: 0.62),
                                .init(color: .clear,               location: 1.0),
                            ],
                            startPoint: .topLeading,
                            endPoint:   .bottomTrailing
                        )
                        // 2× width so it enters and exits cleanly
                        .frame(width: w * 2, height: h * 2)
                        .offset(x: phase * w, y: phase * h * 0.5)
                    }
                    .clipped()
                    .blendMode(.screen)
                    .allowsHitTesting(false)
                }
            }
            .onChange(of: trigger) { _, _ in
                guard !reduceMotion else { return }
                phase = -1.6
                withAnimation(.easeOut(duration: 0.82)) {
                    phase = 1.6
                }
            }
    }
}

public extension View {
    func specularSweep(trigger: Bool) -> some View {
        modifier(SpecularSweep(trigger: trigger))
    }
}
