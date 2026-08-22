import Foundation

public struct VendorApplicationRequest: Encodable {
    public let name: String
    public let email: String
    public let phone: String
    public let specialties: [String]
    public let portfolioLinks: [String]
    public let city: String
    public let maxTravelKm: Int
    public let acceptedTerms: Bool

    public init(name: String, email: String, phone: String, specialties: [String], portfolioLinks: [String], city: String, maxTravelKm: Int, acceptedTerms: Bool) {
        self.name = name
        self.email = email
        self.phone = phone
        self.specialties = specialties
        self.portfolioLinks = portfolioLinks
        self.city = city
        self.maxTravelKm = maxTravelKm
        self.acceptedTerms = acceptedTerms
    }
}

public struct VendorApplicationResponse: Decodable {
    public let success: Bool
    public let message: String
    public let memberId: String
    public let applicationToken: String
}

public enum OnboardingClient {
    public static func apply(_ request: VendorApplicationRequest) async throws -> VendorApplicationResponse {
        try await BricksAPIClient.shared.post("/api/onboarding/apply", body: request)
    }
}
