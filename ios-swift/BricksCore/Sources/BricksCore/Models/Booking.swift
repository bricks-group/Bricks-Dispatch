import Foundation

public struct Booking: Codable, Identifiable, Sendable {
    public let id: String
    public let address: String
    public let city: String?
    public let propertyType: String
    public let packageId: String
    public let packageName: String
    public let packagePrice: Int            // cents CAD
    public let taxAmount: Int              // cents CAD
    public let scheduledDate: String       // "YYYY-MM-DD"
    public let scheduledTime: String       // "10:00 AM" — 12h format from Booking API
    public var status: BookingStatus
    public let billingType: String         // "covered" | "overage"
    public let dispatchJobId: String?
    public let mlsNumber: String?
    public let createdAt: String

    public var totalCents: Int { packagePrice + taxAmount }

    public var formattedTotal: String {
        String(format: "$%.2f", Double(totalCents) / 100)
    }

    // "10:00 AM" → "10:00" (24h) for display + dispatch
    public var scheduledTime24h: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "h:mm a"
        guard let date = formatter.date(from: scheduledTime) else { return scheduledTime }
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }

    public var scheduledDateTime: Date? {
        let combined = "\(scheduledDate)T\(scheduledTime24h):00"
        return ISO8601DateFormatter().date(from: combined)
    }

    public var isUpcoming: Bool {
        guard let dt = scheduledDateTime else { return false }
        return dt > Date() && (status == .pending || status == .confirmed || status == .inProgress)
    }
}

public enum BookingStatus: String, Codable, Sendable {
    case pending, confirmed
    case inProgress = "in_progress"
    case completed, delivered
    case cancelled
    case agentNoShow = "agent_no_show"
    case dispatchFailed = "dispatch_failed"
}

// Minimal record used by watch quick-list
public struct RecentBooking: Codable, Identifiable, Sendable {
    public let id: String
    public let address: String
    public let packageId: String
    public let packageName: String
    public let scheduledDate: String
    public let status: String
}

// Fast-book request sent from watch
public struct FastBookRequest: Encodable {
    public let address: String
    public let packageId: String
    public let scheduledDate: String       // tomorrow at 10:00 AM by default

    public init(address: String, packageId: String) {
        self.address = address
        self.packageId = packageId
        let tomorrow = Calendar.current.date(byAdding: .day, value: 1, to: Date())!
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd"
        self.scheduledDate = fmt.string(from: tomorrow)
    }
}

public struct FastBookResponse: Decodable {
    public let bookingId: String
    public let message: String
}
