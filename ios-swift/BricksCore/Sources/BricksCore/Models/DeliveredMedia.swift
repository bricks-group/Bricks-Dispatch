import Foundation

/// A single delivered file from the post-production pipeline.
/// Matches the `delivery_urls` JSON array on the `media_delivery` table.
public struct DeliveredPhoto: Codable, Identifiable, Sendable {
    public let id: String           // upload_id from Dispatch
    public let filename: String
    public let url: String          // signed HTTPS URL (24h expiry)
    public let expiresAt: String
    public let qcFlagged: Bool      // flagged (included with warning) vs passed

    public var photoURL: URL? { URL(string: url) }

    enum CodingKeys: String, CodingKey {
        case id = "upload_id", filename, url
        case expiresAt = "expires_at"
        case qcFlagged = "qc_flagged"
    }
}

/// The full delivery payload for a job.
/// Fetched from GET /api/jobs/{id}/delivery (or embedded in job detail response).
public struct JobDelivery: Codable, Sendable {
    public let jobId: String
    public let photos: [DeliveredPhoto]
    public let deliveredAt: String

    enum CodingKeys: String, CodingKey {
        case jobId = "job_id"
        case photos = "delivery_urls"
        case deliveredAt = "delivered_at"
    }
}
