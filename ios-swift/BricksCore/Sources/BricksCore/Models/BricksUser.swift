import Foundation

public struct BricksUser: Codable, Sendable {
    public let userId: String
    public let email: String
    public let name: String
    public let role: String          // "agent" | "vendor" | "admin"
    public let plan: String?         // "clay" | "keystone" | "marble" | "diamond"
    public let subscriptionType: String?  // "solo" | "team" | "brokerage"
    public let cardLast4: String?
    public let cardBrand: String?
}

public struct CoverageResponse: Decodable, Sendable {
    public let coverageType: String
    public let label: String
    public let hasPaymentMethod: Bool
    public let allocation: Int?
    public let used: Int?
    public let credits: Int?
    public let cardLast4: String?
    public let cardBrand: String?

    public var creditsRemaining: Int {
        guard let c = credits, let u = used else { return 0 }
        return max(0, c - u)
    }

    public var isCovered: Bool {
        coverageType == "brokerage" || coverageType == "subscription"
    }
}

public struct Package: Codable, Identifiable, Sendable {
    public let id: String
    public let name: String
    public let price: Int        // CAD dollars (not cents — API returns dollars here)
    public let description: String
    public let features: [String]
    public let recommended: Bool
}
