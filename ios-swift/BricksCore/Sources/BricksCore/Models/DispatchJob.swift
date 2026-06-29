import Foundation

public struct DispatchJob: Codable, Identifiable, Sendable {
    public let id: String
    public let bookingId: String
    public let address: String
    public let city: String
    public let state: String
    public let zipCode: String
    public let lat: Double
    public let lng: Double
    // Stored as "HH:MM" — always combine with scheduledDate before Date math
    public let timeWindowStart: String
    public let timeWindowEnd: String
    public let scheduledDate: String         // "YYYY-MM-DD"
    public let jobType: String               // "photo" | "drone" | "video"
    public var status: JobStatus
    public let packageName: String?
    public let packagePrice: Int?            // cents CAD
    public let vendorPayout: Double?         // CAD — photographer's cut
    public let agentName: String?
    public let agentPhone: String?
    public let agentEmail: String?
    public let clientInstructions: String?
    public var checklist: [ChecklistItem]
    public let complexityScore: Int?
    public let createdAt: String
    public let updatedAt: String

    // Derived helpers

    public var scheduledDateTime: Date? {
        let combined = "\(scheduledDate)T\(timeWindowStart):00"
        return ISO8601DateFormatter().date(from: combined)
    }

    public var minutesUntilStart: Int? {
        guard let dt = scheduledDateTime else { return nil }
        return Int(dt.timeIntervalSinceNow / 60)
    }

    public var formattedPayout: String {
        guard let p = vendorPayout else { return "—" }
        return String(format: "$%.2f", p)
    }

    public var isActive: Bool { status == .inProgress }
    public var isUpcoming: Bool { status == .assigned }
}

public enum JobStatus: String, Codable, Sendable {
    case pending, assigned
    case inProgress = "in_progress"
    case completed, delivered
    case declined, cancelled
    case dispatchFailed = "dispatch_failed"
}

public struct ChecklistItem: Codable, Identifiable, Sendable {
    public let id: String
    public let label: String
    public var completed: Bool
}

public struct JobStatusUpdate: Encodable {
    public let status: String
    public let notes: String?
    public let completedAt: String?
    public init(status: JobStatus, notes: String? = nil) {
        self.status = status.rawValue
        self.notes = notes
        self.completedAt = status == .completed ? ISO8601DateFormatter().string(from: Date()) : nil
    }
}

public struct WatchActionRequest: Encodable {
    public let action: String
    public init(action: WatchAction) { self.action = action.rawValue }
}

public enum WatchAction: String {
    case accept, decline, onWay = "on_way", arrived
}
