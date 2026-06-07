import Foundation
import ActivityKit

// Live Activity for an active Dispatch job.
// Shown on lock screen + Dynamic Island while photographer is on a shoot.

public struct JobActivityAttributes: ActivityAttributes {
    public typealias ContentState = ContentStateType

    // Static (set once at activity start)
    public let jobId: String
    public let address: String
    public let packageName: String

    public init(jobId: String, address: String, packageName: String) {
        self.jobId = jobId
        self.address = address
        self.packageName = packageName
    }

    // Dynamic (updated as job progresses)
    public struct ContentStateType: Codable, Hashable, Sendable {
        public let status: String          // "assigned" | "in_progress" | "completed"
        public let elapsedMinutes: Int     // time since status changed to in_progress
        public let agentName: String?      // shown if agent no-show check-in needed

        public init(status: String, elapsedMinutes: Int, agentName: String?) {
            self.status = status
            self.elapsedMinutes = elapsedMinutes
            self.agentName = agentName
        }
    }
}

// Live Activity for an active booking (agent-facing).
// Shown when a confirmed booking has a photographer assigned and in transit.

public struct BookingActivityAttributes: ActivityAttributes {
    public typealias ContentState = ContentStateType

    public let bookingId: String
    public let address: String
    public let packageName: String

    public init(bookingId: String, address: String, packageName: String) {
        self.bookingId = bookingId
        self.address = address
        self.packageName = packageName
    }

    public struct ContentStateType: Codable, Hashable, Sendable {
        public let photographerStatus: String   // "assigned" | "en_route" | "arrived" | "shooting"
        public let etaMinutes: Int?             // nil until photographer marks "on_way"
        public let scheduledTime: String        // "10:00 AM"

        public init(photographerStatus: String, etaMinutes: Int?, scheduledTime: String) {
            self.photographerStatus = photographerStatus
            self.etaMinutes = etaMinutes
            self.scheduledTime = scheduledTime
        }
    }
}
