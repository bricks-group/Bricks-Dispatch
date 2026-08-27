import Foundation

public struct ICAgreement: Decodable {
    public let version: String
    public let effectiveDate: String
    public let text: String
}

public struct VendorApplicationRequest: Encodable {
    public let name: String
    public let email: String
    public let phone: String
    public let specialties: [String]
    public let portfolioLinks: [String]
    public let city: String
    public let maxTravelKm: Int
    public let acceptedTerms: Bool
    public let icAgreementVersion: String

    public init(
        name: String,
        email: String,
        phone: String,
        specialties: [String],
        portfolioLinks: [String],
        city: String,
        maxTravelKm: Int,
        acceptedTerms: Bool,
        icAgreementVersion: String
    ) {
        self.name = name
        self.email = email
        self.phone = phone
        self.specialties = specialties
        self.portfolioLinks = portfolioLinks
        self.city = city
        self.maxTravelKm = maxTravelKm
        self.acceptedTerms = acceptedTerms
        self.icAgreementVersion = icAgreementVersion
    }
}

public struct VendorApplicationResponse: Decodable {
    public let success: Bool
    public let message: String
    public let memberId: String
    public let applicationToken: String
}

public enum OnboardingClient {
    public static func fetchAgreement() async throws -> ICAgreement {
        try await BricksAPIClient.shared.get("/api/onboarding/ic-agreement")
    }

    public static func apply(_ request: VendorApplicationRequest) async throws -> VendorApplicationResponse {
        try await BricksAPIClient.shared.post("/api/onboarding/apply", body: request)
    }
}
