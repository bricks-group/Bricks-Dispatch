import Foundation
import BricksCore
import WatchKit

// Receives job context from the iPhone via WatchConnectivity.
// Posts actions (accept/decline/on_way/arrived) back to the phone.

@MainActor
final class WatchJobStore: ObservableObject {
    @Published var activeJob: WatchJob?
    @Published var nextJob: WatchJob?
    @Published var actionSent = false

    private let connectivity = WatchConnectivityManager.shared

    init() {
        connectivity.addMessageHandler { [weak self] ctx in
            guard let self else { return }
            self.updateFromContext(ctx)
        }
        updateFromContext(connectivity.applicationContext)
    }

    func sendAction(_ action: String, jobId: String) {
        WKInterfaceDevice.current().play(.notification)
        connectivity.sendMessage(["action": action, "jobId": jobId])
        actionSent = true
        // Optimistic update
        if action == WatchAction.accept.rawValue {
            activeJob = nextJob
            nextJob = nil
        } else if action == WatchAction.decline.rawValue {
            nextJob = nil
        }
    }

    private func updateFromContext(_ ctx: [String: Any]) {
        if let id      = ctx[WatchContextKey.activeJobId]      as? String,
           let address = ctx[WatchContextKey.activeJobAddress] as? String,
           let status  = ctx[WatchContextKey.activeJobStatus]  as? String {
            activeJob = WatchJob(id: id, address: address, status: status,
                                 payout: ctx[WatchContextKey.activeJobPayout] as? Double)
        } else {
            activeJob = nil
        }

        if let id      = ctx[WatchContextKey.nextJobId]               as? String,
           let address = ctx[WatchContextKey.nextJobAddress]           as? String,
           let time    = ctx[WatchContextKey.nextJobTimeWindowStart]   as? String,
           let date    = ctx[WatchContextKey.nextJobScheduledDate]     as? String {
            nextJob = WatchJob(id: id, address: address, status: "assigned",
                               payout: nil, timeWindowStart: time, scheduledDate: date)
        } else {
            nextJob = nil
        }
    }
}

struct WatchJob: Identifiable {
    let id: String
    let address: String
    let status: String
    let payout: Double?
    var timeWindowStart: String = ""
    var scheduledDate: String = ""

    var formattedPayout: String {
        guard let p = payout else { return "" }
        return String(format: "$%.0f", p)
    }

    var minutesUntil: Int? {
        guard !scheduledDate.isEmpty, !timeWindowStart.isEmpty else { return nil }
        let combined = "\(scheduledDate)T\(timeWindowStart):00"
        guard let dt = ISO8601DateFormatter().date(from: combined) else { return nil }
        return max(0, Int(dt.timeIntervalSinceNow / 60))
    }
}
