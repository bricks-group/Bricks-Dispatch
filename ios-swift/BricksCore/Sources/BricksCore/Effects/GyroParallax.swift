import SwiftUI
import CoreMotion

// One CMMotionManager per app — Apple's recommendation. All parallax views share this.
@MainActor
public final class GyroManager: ObservableObject {
    public static let shared = GyroManager()

    @Published public var pitch: Double = 0
    @Published public var roll:  Double = 0

    private let motion = CMMotionManager()
    private let queue  = OperationQueue()

    private init() { queue.maxConcurrentOperationCount = 1 }

    public func start() {
        guard motion.isDeviceMotionAvailable, !motion.isDeviceMotionActive else { return }
        motion.deviceMotionUpdateInterval = 1.0 / 60.0
        motion.startDeviceMotionUpdates(to: queue) { [weak self] data, _ in
            guard let self, let data else { return }
            Task { @MainActor in
                // Low-pass blend (α = 0.15) smooths jitter while keeping responsiveness
                let α = 0.15
                self.pitch = self.pitch * (1 - α) + data.gravity.y * α
                self.roll  = self.roll  * (1 - α) + data.gravity.x * α
            }
        }
    }

    public func stop() {
        motion.stopDeviceMotionUpdates()
    }
}

public struct GyroParallaxModifier: ViewModifier {
    @ObservedObject private var gyro = GyroManager.shared
    let magnitude: CGFloat

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    public func body(content: Content) -> some View {
        content
            .offset(
                x: reduceMotion ? 0 : CGFloat(gyro.roll)  * magnitude,
                y: reduceMotion ? 0 : CGFloat(gyro.pitch) * magnitude
            )
            .animation(.interactiveSpring(response: 0.35, dampingFraction: 0.85), value: gyro.roll)
            .animation(.interactiveSpring(response: 0.35, dampingFraction: 0.85), value: gyro.pitch)
            .onAppear  { GyroManager.shared.start() }
            .onDisappear { GyroManager.shared.stop() }
    }
}

public extension View {
    /// Adds a subtle gyroscope-driven parallax offset. Default magnitude 8pt.
    /// No-ops when Reduce Motion is enabled.
    func gyroParallax(magnitude: CGFloat = 8) -> some View {
        modifier(GyroParallaxModifier(magnitude: magnitude))
    }
}
