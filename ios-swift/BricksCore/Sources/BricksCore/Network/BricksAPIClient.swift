import Foundation

public enum BricksAPIError: LocalizedError {
    case unauthorized
    case loginFailed
    case notFound
    case serverError(Int, String)
    case networkError(Error)
    case decodingError(Error)

    public var errorDescription: String? {
        switch self {
        case .unauthorized:  return "Session expired. Please sign in again."
        case .loginFailed:   return "Incorrect email or password."
        case .notFound: return "Resource not found."
        case .serverError(let code, let msg): return "Server error \(code): \(msg)"
        case .networkError(let e): return e.localizedDescription
        case .decodingError(let e): return "Unexpected response: \(e.localizedDescription)"
        }
    }
}

public final class BricksAPIClient: @unchecked Sendable {
    public static let shared = BricksAPIClient()

    private let session: URLSession
    private let decoder: JSONDecoder

    private init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        self.session = URLSession(configuration: config)
        self.decoder = JSONDecoder()
        self.decoder.keyDecodingStrategy = .convertFromSnakeCase
        self.decoder.dateDecodingStrategy = .iso8601
    }

    private var baseURL: URL {
        // Dispatch: https://dispatch-api.brxs.ca  Booking: https://booking-api.brxs.ca
        // Set via BricksConfig before first use
        URL(string: BricksConfig.apiBaseURL)!
    }

    public func get<T: Decodable>(_ path: String) async throws -> T {
        try await request(path: path, method: "GET", body: Optional<Never>.none)
    }

    public func post<B: Encodable, T: Decodable>(_ path: String, body: B) async throws -> T {
        try await request(path: path, method: "POST", body: body)
    }

    public func patch<B: Encodable, T: Decodable>(_ path: String, body: B) async throws -> T {
        try await request(path: path, method: "PATCH", body: body)
    }

    public func postVoid<B: Encodable>(_ path: String, body: B) async throws {
        let _: EmptyResponse = try await request(path: path, method: "POST", body: body)
    }

    // MARK: — Private

    private func request<B: Encodable, T: Decodable>(
        path: String, method: String, body: B?
    ) async throws -> T {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        if let token = await BricksAuth.shared.accessToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        if let body {
            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .convertToSnakeCase
            request.httpBody = try encoder.encode(body)
        }

        let (data, response) = try await session.data(for: request)
        let http = response as! HTTPURLResponse

        switch http.statusCode {
        case 200...299:
            return try decoder.decode(T.self, from: data)
        case 401:
            throw BricksAPIError.unauthorized
        case 404:
            throw BricksAPIError.notFound
        default:
            let msg = String(data: data, encoding: .utf8) ?? "Unknown error"
            throw BricksAPIError.serverError(http.statusCode, msg)
        }
    }
}

private struct EmptyResponse: Decodable {}

// Shared config — set at app startup before any API call
public enum BricksConfig {
    public static var apiBaseURL: String = ""
}
