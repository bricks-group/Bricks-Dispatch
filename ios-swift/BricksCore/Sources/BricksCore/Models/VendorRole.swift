import Foundation

/// Same gate as frontend/lib/vendor.ts and web JobsPage.
public enum VendorRole {
    /// Admin if role or title contains owner/admin/manager, or id (lowercased) starts with 0c3fa7c2 (Kam).
    public static func isVendorAdmin(id: String? = nil, role: String? = nil, title: String? = nil) -> Bool {
        let blob = "\(role ?? "") \(title ?? "")".lowercased()
        if blob.contains("owner") || blob.contains("admin") || blob.contains("manager") { return true }
        return (id ?? "").lowercased().hasPrefix("0c3fa7c2")
    }

    /// Active photographers only. Owner/Kam always included; `assignable == false` (Aman) excluded.
    public static func isPickerMember(_ m: TeamMember) -> Bool {
        if !m.isActive { return false }
        if m.id.lowercased().hasPrefix("0c3fa7c2") { return true }
        let t = "\(m.title ?? "") \(m.role ?? "")".lowercased()
        if t.contains("owner") { return true }
        if m.assignable == false { return false }
        return true
    }
}

public struct TeamMember: Codable, Identifiable, Hashable, Sendable {
    public let id: String
    public let name: String
    public let email: String?
    public let role: String?
    public let title: String?
    public let isActive: Bool
    public let assignable: Bool?
    public let assignableReason: String?

    public init(
        id: String, name: String, email: String? = nil, role: String? = nil, title: String? = nil,
        isActive: Bool = true, assignable: Bool? = nil, assignableReason: String? = nil
    ) {
        self.id = id; self.name = name; self.email = email; self.role = role; self.title = title
        self.isActive = isActive; self.assignable = assignable; self.assignableReason = assignableReason
    }

    enum CodingKeys: String, CodingKey {
        case id, name, email, role, title, isActive, assignable, assignableReason
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(String.self, forKey: .id) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        email = try c.decodeIfPresent(String.self, forKey: .email)
        role = try c.decodeIfPresent(String.self, forKey: .role)
        title = try c.decodeIfPresent(String.self, forKey: .title)
        assignable = try c.decodeIfPresent(Bool.self, forKey: .assignable)
        assignableReason = try c.decodeIfPresent(String.self, forKey: .assignableReason)
        // GET /team does not return is_active; `assignable` already requires an
        // active, approved, unpaused member.
        isActive = try c.decodeIfPresent(Bool.self, forKey: .isActive) ?? (assignable ?? false)
    }

    public var pickerLabel: String { title ?? role ?? "Photographer" }
}
