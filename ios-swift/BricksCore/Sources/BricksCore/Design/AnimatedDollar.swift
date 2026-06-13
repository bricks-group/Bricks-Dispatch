import SwiftUI

/// A dollar amount that counts up from 0 to `target` with a spring timing curve.
/// Used in the delivery reveal earnings resolution moment.
///
/// Usage:
///   AnimatedDollar(target: 164.45)
///       .font(.system(size: 52, weight: .bold, design: .rounded))
///       .foregroundStyle(BricksTokens.gold)
public struct AnimatedDollar: View {
    let target: Double
    let prefix: String
    let delay: Double

    @State private var displayed: Double = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    public init(target: Double, prefix: String = "$", delay: Double = 0.2) {
        self.target = target
        self.prefix = prefix
        self.delay  = delay
    }

    public var body: some View {
        Text("\(prefix)\(displayed, specifier: "%.2f")")
            .contentTransition(.numericText(value: displayed))
            .onAppear {
                if reduceMotion {
                    displayed = target
                } else {
                    withAnimation(
                        .spring(response: 1.6, dampingFraction: 0.72)
                        .delay(delay)
                    ) {
                        displayed = target
                    }
                }
            }
    }
}

/// A whole-number counter (for photo count, job count, etc.)
public struct AnimatedCount: View {
    let target: Int
    let delay: Double

    @State private var displayed: Int = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    public init(target: Int, delay: Double = 0) {
        self.target = target
        self.delay  = delay
    }

    public var body: some View {
        Text("\(displayed)")
            .contentTransition(.numericText(countsDown: false))
            .onAppear {
                if reduceMotion {
                    displayed = target
                } else {
                    withAnimation(
                        .spring(response: 1.1, dampingFraction: 0.8)
                        .delay(delay)
                    ) {
                        displayed = target
                    }
                }
            }
    }
}
