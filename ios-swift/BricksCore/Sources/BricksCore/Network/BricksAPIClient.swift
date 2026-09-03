import Foundation

public enum BricksAPIError: LocalizedError {
    case unauthorized
    case invalidCredentials
    case invalidCode
    case notFound
    case serverError(Int, String)
    case networkError(Error)
    case decodingError(Error)
    case invalidURL

    public var errorDescription: String? {
        switch self {
        case .unauthorized: return "Session expired. Please sign in again."
        case .invalidCredentials: return "Incorrect email or password."
        case .invalidCode: return "That code is incorrect or has expired."
        case .notFound: return "Resource not found."
        case .serverError(let code, let msg): return "Server error \(code): \(msg)"
        case .networkError(let e): return e.localizedDescription
        case .decodingError(let e): return "Unexpected response: \(e.localizedDescription)"
        case .invalidURL: return "Invalid API URL."
        }
    }
}

/// App Group shared with widgets. Must match entitlements (`group.com.bricks.go`).
public enum BricksAppGroup {
    public static let suiteName = "group.com.bricks.go"
}

public enum BricksAPIURL {
    /// Build an API URL with `URLComponents` so query strings are never stuffed
    /// into `appendingPathComponent` (which percent-encodes `?`).
    public static func make(base: URL, path: String, query: [URLQueryItem] = []) -> URL {
        var pathPart = path
        var queryItems = query
        if let q = path.firstIndex(of: "?") {
            pathPart = String(path[..<q])
            let qs = String(path[path.index(after: q)...])
            if let parsed = URLComponents(string: "https://invalid.example?\(qs)")?.queryItems {
                queryItems = parsed + query
            }
        }
        pathPart = pathPart.hasPrefix("/") ? String(pathPart.dropFirst()) : pathPart
        while pathPart.hasPrefix("/") { pathPart = String(pathPart.dropFirst()) }

        var components = URLComponents()
        let baseComponents = URLComponents(url: base, resolvingAgainstBaseURL: false)
        components.scheme = baseComponents?.scheme ?? base.scheme
        components.host = baseComponents?.host ?? base.host
        components.port = baseComponents?.port ?? base.port
        let basePath = (baseComponents?.path ?? base.path)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if basePath.isEmpty {
            components.path = "/" + pathPart
        } else {
            components.path = "/" + basePath + "/" + pathPart
        }
        if !queryItems.isEmpty {
            components.queryItems = queryItems
        }
        return components.url ?? base.appendingPathComponent(pathPart)
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

    public func url(path: String, query: [URLQueryItem] = []) -> URL {
        BricksAPIURL.make(base: baseURL, path: path, query: query)
    }

    public func get<T: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        try await request(path: path, method: "GET", body: Optional<Never>.none, query: query)
    }

    public func post<B: Encodable, T: Decodable>(_ path: String, body: B) async throws -> T {
        try await request(path: path, method: "POST", body: body)
    }

    public func patch<B: Encodable, T: Decodable>(_ path: String, body: B) async throws -> T {
        try await request(path: path, method: "PATCH", body: body)
    }

    public func put<B: Encodable, T: Decodable>(_ path: String, body: B) async throws -> T {
        try await request(path: path, method: "PUT", body: body)
    }

    public func postVoid<B: Encodable>(_ path: String, body: B) async throws {
        let _: EmptyResponse = try await request(path: path, method: "POST", body: body)
    }

    // MARK: — Private

    private func request<B: Encodable, T: Decodable>(
        path: String, method: String, body: B?, query: [URLQueryItem] = []
    ) async throws -> T {
        var request = URLRequest(url: url(path: path, query: query))
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
            if T.self == EmptyResponse.self {
                return EmptyResponse() as! T
            }
            do {
                return try decoder.decode(T.self, from: data)
            } catch {
                throw BricksAPIError.decodingError(error)
            }
        case 401:
            throw BricksAPIError.unauthorized
        case 404:
            throw BricksAPIError.notFound
        default:
            let parsed = BricksAPIError.parseBody(data)
            throw BricksAPIError.serverError(http.statusCode, parsed.message)
        }
    }
}

extension BricksAPIError {
    /// FastAPI `{detail: {code, message}}` or `{detail: "…"}` or `{message}/{error}`.
    public static func parseBody(_ data: Data) -> (code: String?, message: String) {
        guard let obj = try? JSONSerialization.jsonObject(with: data) else {
            let raw = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return (nil, raw.isEmpty ? "Unknown error" : raw)
        }
        return parseJSON(obj)
    }

    public static func parseJSON(_ obj: Any) -> (code: String?, message: String) {
        guard let dict = obj as? [String: Any] else {
            return (nil, String(describing: obj))
        }
        var code: String?
        var message: String?
        if let detail = dict["detail"] as? [String: Any] {
            code = detail["code"] as? String
            message = detail["message"] as? String
        } else if let detail = dict["detail"] as? String {
            message = detail
        }
        if code == nil { code = dict["code"] as? String }
        if message == nil { message = dict["message"] as? String ?? dict["error"] as? String }
        return (code, message ?? "Unknown error")
    }
}

private struct EmptyResponse: Decodable {
    init() {}
}

// Shared config — set at app startup before any API call
public enum BricksConfig {
    public static var apiBaseURL: String = ""
}
