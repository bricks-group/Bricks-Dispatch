import Foundation

// Supabase ECC P-256 JWT session management.
// The backend uses supabase.auth.get_user(token) — never decode JWT client-side.
// We store the session in the Keychain and restore it on launch.

public struct SupabaseSession: Codable, Sendable {
    public let accessToken: String
    public let refreshToken: String
    public let expiresAt: Date
    public let userId: String
    public let email: String

    public var isExpired: Bool { Date() >= expiresAt }
}

public struct AuthResponse: Decodable {
    public let accessToken: String
    public let refreshToken: String
    public let expiresIn: Int
    public let user: AuthUser
}

public struct AuthUser: Decodable {
    public let id: String
    public let email: String
}

@MainActor
public final class BricksAuth: ObservableObject {
    public static let shared = BricksAuth()

    @Published public private(set) var session: SupabaseSession?
    @Published public private(set) var isLoading = true

    private let supabaseURL: String
    private let supabaseAnonKey: String
    private let keychain = KeychainHelper.shared

    private init() {
        self.supabaseURL = BricksAuthConfig.supabaseURL
        self.supabaseAnonKey = BricksAuthConfig.supabaseAnonKey
    }

    public var accessToken: String? { session?.accessToken }
    public var isAuthenticated: Bool { session != nil }

    public func restoreSession() async {
        isLoading = true
        defer { isLoading = false }

        guard let stored = keychain.load(key: "bricks.session"),
              let decoded = try? JSONDecoder().decode(SupabaseSession.self, from: stored)
        else { return }

        if decoded.isExpired {
            await refreshSession(refreshToken: decoded.refreshToken)
        } else {
            session = decoded
        }
    }

    public func signIn(email: String, password: String) async throws {
        let url = URL(string: "\(supabaseURL)/auth/v1/token?grant_type=password")!
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(supabaseAnonKey, forHTTPHeaderField: "apikey")

        let body = ["email": email, "password": password]
        req.httpBody = try JSONEncoder().encode(body)

        let (data, response) = try await URLSession.shared.data(for: req)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw BricksAPIError.unauthorized
        }

        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let auth = try decoder.decode(AuthResponse.self, from: data)
        let s = SupabaseSession(
            accessToken: auth.accessToken,
            refreshToken: auth.refreshToken,
            expiresAt: Date().addingTimeInterval(TimeInterval(auth.expiresIn)),
            userId: auth.user.id,
            email: auth.user.email
        )
        persist(s)
    }

    public func signOut() {
        session = nil
        keychain.delete(key: "bricks.session")
    }

    // MARK: — Private

    private func refreshSession(refreshToken: String) async {
        let url = URL(string: "\(supabaseURL)/auth/v1/token?grant_type=refresh_token")!
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(supabaseAnonKey, forHTTPHeaderField: "apikey")
        req.httpBody = try? JSONEncoder().encode(["refresh_token": refreshToken])

        guard let (data, response) = try? await URLSession.shared.data(for: req),
              (response as? HTTPURLResponse)?.statusCode == 200 else {
            signOut()
            return
        }

        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        guard let auth = try? decoder.decode(AuthResponse.self, from: data) else {
            signOut()
            return
        }

        let s = SupabaseSession(
            accessToken: auth.accessToken,
            refreshToken: auth.refreshToken,
            expiresAt: Date().addingTimeInterval(TimeInterval(auth.expiresIn)),
            userId: auth.user.id,
            email: auth.user.email
        )
        persist(s)
    }

    private func persist(_ s: SupabaseSession) {
        session = s
        if let data = try? JSONEncoder().encode(s) {
            keychain.save(data, key: "bricks.session")
        }
    }
}

// Minimal Keychain wrapper — no third-party dependency
final class KeychainHelper: @unchecked Sendable {
    static let shared = KeychainHelper()
    private let service = "ca.brxs.bricks"

    func save(_ data: Data, key: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecValueData as String: data,
        ]
        SecItemDelete(query as CFDictionary)
        SecItemAdd(query as CFDictionary, nil)
    }

    func load(key: String) -> Data? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        SecItemCopyMatching(query as CFDictionary, &result)
        return result as? Data
    }

    func delete(key: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
        SecItemDelete(query as CFDictionary)
    }
}

public enum BricksAuthConfig {
    public static var supabaseURL: String = "https://ctqwxwrcxwruaqojohpf.supabase.co"
    public static var supabaseAnonKey: String = ""   // set at app launch from Info.plist
}
