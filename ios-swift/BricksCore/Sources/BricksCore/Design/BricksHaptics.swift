import CoreHaptics
import UIKit

/// Semantic haptic vocabulary for both Bricks apps.
/// All events have names that describe intent, not mechanism.
@MainActor
public final class BricksHaptics {
    public static let shared = BricksHaptics()
    private var engine: CHHapticEngine?
    private var proximityPlayer: CHHapticAdvancedPatternPlayer?

    private init() { prepare() }

    private func prepare() {
        guard CHHapticEngine.capabilitiesForHardware().supportsHaptics else { return }
        do {
            let e = try CHHapticEngine()
            e.isAutoShutdownEnabled = false
            e.resetHandler = { [weak self] in try? self?.engine?.start() }
            e.stoppedHandler = { _ in }
            try e.start()
            engine = e
        } catch {}
    }

    // MARK: — Dispatch events

    /// New job offer arrives — medium impact, anticipatory
    public func jobOffer() {
        impact(.medium)
    }

    /// Photographer swiped-accepted — decisive success
    public func jobAccepted() {
        UINotificationFeedbackGenerator().notificationOccurred(.success)
    }

    /// Swipe-declined — minimal, no weight
    public func jobDeclined() {
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
    }

    /// Status milestone: arrived / in_progress / completed — rigid, confident
    public func statusAdvanced() {
        UIImpactFeedbackGenerator(style: .rigid).impactOccurred()
    }

    /// A delivered photo settles into place — soft, satisfying
    public func photoSettle() {
        transient(intensity: 0.42, sharpness: 0.22)
    }

    /// Earnings number fully resolves — weighted success: two strikes, rising
    public func earningsResolved() {
        playPattern([
            (0.00, 0.45, 0.25),
            (0.09, 0.95, 0.65),
            (0.16, 0.58, 0.40),
        ])
    }

    // MARK: — Booking events

    /// Booking confirmed — money moved, shot booked — weighted success
    public func bookingConfirmed() {
        UINotificationFeedbackGenerator().notificationOccurred(.success)
    }

    /// MapKit camera begins cinematic fly-to — light cue
    public func cameraFlightStart() {
        transient(intensity: 0.28, sharpness: 0.18)
    }

    /// Camera lands on property — heavier settle
    public func cameraFlightLand() {
        transient(intensity: 0.68, sharpness: 0.35)
    }

    // MARK: — Continuous proximity pulse
    // Intensity rises as photographer approaches the property (0.0 → 1.0 fraction)

    public func startProximityPulse(fraction: Float) {
        guard let engine else { return }
        let intensity = max(0.18, min(1.0, fraction))
        do {
            let event = CHHapticEvent(
                eventType: .hapticContinuous,
                parameters: [
                    CHHapticEventParameter(parameterID: .hapticIntensity, value: intensity),
                    CHHapticEventParameter(parameterID: .hapticSharpness, value: 0.28),
                ],
                relativeTime: 0,
                duration: 100
            )
            let pattern = try CHHapticPattern(events: [event], parameters: [])
            let player = try engine.makeAdvancedPlayer(with: pattern)
            try player.start(atTime: CHHapticTimeImmediate)
            proximityPlayer = player
        } catch {}
    }

    public func updateProximityPulse(fraction: Float) {
        let intensity = max(0.18, min(1.0, fraction))
        try? proximityPlayer?.sendParameters(
            [CHHapticDynamicParameter(parameterID: .hapticIntensityControl, value: intensity, relativeTime: 0)],
            atTime: CHHapticTimeImmediate
        )
    }

    public func stopProximityPulse() {
        try? proximityPlayer?.stop(atTime: CHHapticTimeImmediate)
        proximityPlayer = nil
    }

    // MARK: — Primitives

    private func impact(_ style: UIImpactFeedbackGenerator.FeedbackStyle) {
        UIImpactFeedbackGenerator(style: style).impactOccurred()
    }

    private func transient(intensity: Float, sharpness: Float) {
        guard let engine else { impact(.medium); return }
        let event = CHHapticEvent(
            eventType: .hapticTransient,
            parameters: [
                CHHapticEventParameter(parameterID: .hapticIntensity, value: intensity),
                CHHapticEventParameter(parameterID: .hapticSharpness, value: sharpness),
            ],
            relativeTime: 0
        )
        do {
            let pattern = try CHHapticPattern(events: [event], parameters: [])
            try engine.makePlayer(with: pattern).start(atTime: CHHapticTimeImmediate)
        } catch { impact(.medium) }
    }

    private func playPattern(_ events: [(TimeInterval, Float, Float)]) {
        guard let engine else { return }
        let hapticEvents = events.map {
            CHHapticEvent(
                eventType: .hapticTransient,
                parameters: [
                    CHHapticEventParameter(parameterID: .hapticIntensity, value: $0.1),
                    CHHapticEventParameter(parameterID: .hapticSharpness, value: $0.2),
                ],
                relativeTime: $0.0
            )
        }
        do {
            let pattern = try CHHapticPattern(events: hapticEvents, parameters: [])
            try engine.makePlayer(with: pattern).start(atTime: CHHapticTimeImmediate)
        } catch {}
    }
}
