import Foundation
import WatchConnectivity

// WatchConnectivityManager — runs on both iOS and watchOS targets.
// iOS: sends job/booking context to watch, receives watch actions
// watchOS: receives context, sends user actions back to phone

public typealias WatchMessageHandler = ([String: Any]) -> Void

@MainActor
public final class WatchConnectivityManager: NSObject, ObservableObject {
    public static let shared = WatchConnectivityManager()

    @Published public private(set) var isReachable = false
    @Published public private(set) var applicationContext: [String: Any] = [:]

    private var messageHandlers: [WatchMessageHandler] = []

    private override init() {
        super.init()
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    // Send a message that requires immediate response (watch must be reachable)
    public func sendMessage(_ message: [String: Any]) {
        guard WCSession.default.isReachable else { return }
        WCSession.default.sendMessage(message, replyHandler: nil)
    }

    // Update application context (queued, delivered when watch opens)
    public func updateContext(_ context: [String: Any]) {
        try? WCSession.default.updateApplicationContext(context)
    }

    // Register a handler for incoming messages from the counterpart
    public func addMessageHandler(_ handler: @escaping WatchMessageHandler) {
        messageHandlers.append(handler)
    }
}

extension WatchConnectivityManager: WCSessionDelegate {
    public nonisolated func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        Task { @MainActor in
            self.isReachable = session.isReachable
        }
    }

    public nonisolated func sessionReachabilityDidChange(_ session: WCSession) {
        Task { @MainActor in
            self.isReachable = session.isReachable
        }
    }

    public nonisolated func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
        Task { @MainActor in
            for handler in self.messageHandlers { handler(message) }
        }
    }

    public nonisolated func session(_ session: WCSession, didReceiveApplicationContext context: [String: Any]) {
        Task { @MainActor in
            self.applicationContext = context
            for handler in self.messageHandlers { handler(context) }
        }
    }

    // iOS-only delegate methods
    #if os(iOS)
    public nonisolated func sessionDidBecomeInactive(_ session: WCSession) {}
    public nonisolated func sessionDidDeactivate(_ session: WCSession) {
        session.activate()
    }
    #endif
}

// Typed context keys shared between iOS and watchOS
public enum WatchContextKey {
    public static let activeJobId = "activeJobId"
    public static let activeJobAddress = "activeJobAddress"
    public static let activeJobStatus = "activeJobStatus"
    public static let activeJobPayout = "activeJobPayout"
    public static let nextJobId = "nextJobId"
    public static let nextJobAddress = "nextJobAddress"
    public static let nextJobTimeWindowStart = "nextJobTimeWindowStart"
    public static let nextJobScheduledDate = "nextJobScheduledDate"
    // Booking
    public static let nextBookingId = "nextBookingId"
    public static let nextBookingAddress = "nextBookingAddress"
    public static let nextBookingDate = "nextBookingDate"
    public static let nextBookingTime = "nextBookingTime"
    public static let creditsRemaining = "creditsRemaining"
}

