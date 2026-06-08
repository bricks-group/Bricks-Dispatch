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

    public init(id: String, filename: String, url: String, expiresAt: String, qcFlagged: Bool) {
        self.id = id; self.filename = filename; self.url = url
        self.expiresAt = expiresAt; self.qcFlagged = qcFlagged
    }

    enum CodingKeys: String, CodingKey {
        case id = "upload_id", filename, url
        case expiresAt = "expires_at"
        case qcFlagged = "qc_flagged"
    }
}

/// The full delivery payload for a job.
/// Fetched from GET /api/bookings/{id}/delivery (Booking API, reads shared media_delivery table).
public struct JobDelivery: Codable, Sendable {
    public let jobId: String
    public let photos: [DeliveredPhoto]
    public let deliveredAt: String
    /// Signed URL to the .splat Gaussian splat file — non-nil for Premium/Luxury/Estate jobs
    /// where a 3D capture was performed and passed post-production.
    public let splatURL: String?

    public var splatFileURL: URL? { splatURL.flatMap(URL.init) }

    enum CodingKeys: String, CodingKey {
        case jobId      = "job_id"
        case photos     = "delivery_urls"
        case deliveredAt = "delivered_at"
        case splatURL   = "splat_url"
    }
}
