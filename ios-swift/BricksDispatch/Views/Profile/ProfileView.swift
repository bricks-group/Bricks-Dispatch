import SwiftUI
import BricksCore
import UniformTypeIdentifiers

/// Live vendor profile — identity, title (Owner vs Photographer IC), skills,
/// Advanced RPAS upload (file URL on the member), service area, availability,
/// and Owner payouts as full package with $0 deduction.
struct ProfileView: View {
    @ObservedObject private var auth: BricksAuth = .shared
    @StateObject private var vm = ProfileViewModel()
    @State private var showImporter = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    identity
                    vault
                    skills
                    rpas
                    availability
                    settings
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 8)
            }
            .background(Color(hex: 0x141210).ignoresSafeArea())
            .navigationTitle("Profile")
            .navigationBarTitleDisplayMode(.large)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .task { await vm.load() }
            .fileImporter(isPresented: $showImporter, allowedContentTypes: [.pdf, .png, .jpeg], allowsMultipleSelection: false) { result in
                if case .success(let urls) = result, let url = urls.first {
                    Task { await vm.uploadRPAS(url) }
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private var identity: some View {
        HStack(spacing: 14) {
            RoundedRectangle(cornerRadius: 2).fill(Color(hex: 0x2C2823))
                .frame(width: 54, height: 54)
                .overlay(Text(vm.initials).font(.system(size: 20, weight: .semibold)).foregroundStyle(BricksColors.gold))
            VStack(alignment: .leading, spacing: 3) {
                Text(vm.name)
                    .font(.system(size: 19, weight: .semibold)).tracking(-0.3).foregroundStyle(Color(hex: 0xF4F1EB))
                Text(vm.subtitle)
                    .font(.system(size: 11.5, design: .monospaced)).foregroundStyle(Color(hex: 0x8F8678))
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var vault: some View {
        ZStack {
            LinearGradient(colors: [Color(hex: 0x231B12), Color(hex: 0x130F0C)],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
            Circle().fill(BricksColors.gold).opacity(0.16).blur(radius: 34)
                .frame(width: 180).offset(x: 120, y: -85)
            HStack(spacing: 20) {
                TierBrick(tier: .keystone, fill: vm.onTimeRate)
                    .frame(width: 104, height: 104)
                    .shadow(color: BricksColors.gold.opacity(0.4), radius: 14, y: 6)
                VStack(alignment: .leading, spacing: 0) {
                    Text(vm.isOwner ? "OWNER" : "VENDOR TIER")
                        .font(.system(size: 10.5, weight: .medium, design: .monospaced))
                        .tracking(2).foregroundStyle(BricksColors.gold)
                    Text(vm.isOwner ? "Owner" : "Tier \(vm.tierLabel)")
                        .font(.system(size: 26, weight: .semibold)).tracking(-0.6)
                        .foregroundStyle(Color(hex: 0xF4F1EB)).padding(.top, 8)
                    Text(vm.payoutLabel)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(Color(hex: 0x8F8678)).padding(.top, 7)
                    Divider().overlay(Color.white.opacity(0.1)).padding(.vertical, 13)
                    Text(vm.isOwner ? "$0 deduction. Full package." : "Fixed rate card — not % of rack")
                        .font(.system(size: 10.5, design: .monospaced))
                        .foregroundStyle(Color(hex: 0x8F8678))
                }
                Spacer(minLength: 0)
            }
            .padding(22)
        }
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(.white.opacity(0.14), lineWidth: 1))
    }

    private var skills: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("SKILLS").font(.system(size: 10.5, weight: .medium, design: .monospaced))
                .tracking(2).foregroundStyle(Color(hex: 0x8F8678))
            HStack(spacing: 8) {
                ForEach(vm.skills, id: \.self) { skill in
                    Text(skill.uppercased())
                        .font(.system(size: 10, weight: .medium, design: .monospaced))
                        .tracking(0.8)
                        .foregroundStyle(Color(hex: 0xF4F1EB))
                        .padding(.horizontal, 10).padding(.vertical, 6)
                        .background(RoundedRectangle(cornerRadius: 2).fill(Color(hex: 0x2C2823)))
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var rpas: some View {
        Button { showImporter = true } label: {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("ADVANCED RPAS")
                        .font(.system(size: 10.5, weight: .medium, design: .monospaced))
                        .tracking(2).foregroundStyle(Color(hex: 0x8F8678))
                    Text(vm.rpasLabel)
                        .font(.system(size: 14, weight: .medium)).foregroundStyle(Color(hex: 0xF4F1EB))
                    if vm.uploading { Text("Uploading…").font(.system(size: 11, design: .monospaced)).foregroundStyle(Color(hex: 0x8F8678)) }
                }
                Spacer()
                Image(systemName: "arrow.up.doc").foregroundStyle(Color(hex: 0xD6CFC2))
            }
            .padding(16)
            .background(RoundedRectangle(cornerRadius: 12).fill(Color(hex: 0x1C1A17))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(.white.opacity(0.08))))
        }
        .buttonStyle(.plain)
    }

    private var availability: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text("AVAILABILITY")
                    .font(.system(size: 10.5, weight: .medium, design: .monospaced))
                    .tracking(2).foregroundStyle(Color(hex: 0x8F8678))
                Text(vm.availability ? "Available for assignments" : "Not available")
                    .font(.system(size: 14, weight: .medium)).foregroundStyle(Color(hex: 0xF4F1EB))
                Text(vm.serviceAreas).font(.system(size: 11, design: .monospaced)).foregroundStyle(Color(hex: 0x8F8678))
            }
            Spacer()
            Toggle("", isOn: $vm.availability)
                .labelsHidden()
                .tint(Color(hex: 0xD2542B))
                .onChange(of: vm.availability) { _, val in
                    Task { await vm.setAvailability(val) }
                }
        }
        .padding(16)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(hex: 0x1C1A17))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(.white.opacity(0.08))))
    }

    private var settings: some View {
        VStack(spacing: 0) {
            row("banknote", "Payout", vm.payoutLabel)
            Divider().overlay(Color.white.opacity(0.08))
            row("checkmark.shield", "IC agreement", "v1.3")
            Divider().overlay(Color.white.opacity(0.08)).padding(.vertical, 4)
            Button(role: .destructive) { auth.signOut() } label: {
                row("rectangle.portrait.and.arrow.right", "Sign out", nil, destructive: true)
            }
        }
        .padding(.horizontal, 16)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(hex: 0x1C1A17))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(.white.opacity(0.08))))
    }

    private func row(_ icon: String, _ label: String, _ value: String?, destructive: Bool = false) -> some View {
        HStack(spacing: 13) {
            Image(systemName: icon).font(.system(size: 16))
                .foregroundStyle(destructive ? Color(hex: 0xC2685C) : Color(hex: 0xD6CFC2)).frame(width: 26)
            Text(label).font(.system(size: 14.5, weight: .medium))
                .foregroundStyle(destructive ? Color(hex: 0xC2685C) : Color(hex: 0xF4F1EB))
            Spacer()
            if let value { Text(value).font(.system(size: 12, design: .monospaced)).foregroundStyle(Color(hex: 0x8F8678)).lineLimit(1) }
            if !destructive { Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold)).foregroundStyle(Color(hex: 0x5E574C)) }
        }
        .padding(.vertical, 15)
        .contentShape(Rectangle())
    }
}

@MainActor
final class ProfileViewModel: ObservableObject {
    @Published var name = "Photographer"
    @Published var title = "Photographer IC"
    @Published var skills: [String] = ["photo"]
    @Published var availability = true
    @Published var onTimeRate = 1.0
    @Published var tierLabel = "C"
    @Published var payoutLabel = "Fixed rate card"
    @Published var rpasURL: String?
    @Published var rpasLevel: String?
    @Published var serviceAreas = ""
    @Published var uploading = false

    var isOwner: Bool { title == "Owner" }
    var initials: String {
        let parts = name.split(separator: " ")
        return String(parts.prefix(2).compactMap { $0.first }).uppercased()
    }
    var subtitle: String {
        let rpas = (rpasLevel?.capitalized).map { "RPAS \($0)" } ?? "RPAS"
        return "\(title) · \(rpas)"
    }
    var rpasLabel: String {
        if let url = rpasURL, !url.isEmpty { return "On file · tap to replace" }
        return "Upload Advanced certificate"
    }

    private let api = BricksAPIClient.shared

    struct ProfileDTO: Decodable {
        let name: String?
        let title: String?
        let role: String?
        let skills: [String]?
        let availability: Bool?
        let onTimeRate: Double?
        let tierLabel: String?
        let payoutLabel: String?
        let rpasCertificateUrl: String?
        let rpasCertificateLevel: String?
        let serviceAreas: [String]?
        let payoutModel: String?
    }

    func load() async {
        do {
            let p: ProfileDTO = try await api.get("/api/vendor-os/profile")
            name = p.name ?? name
            title = p.title ?? p.role ?? title
            skills = p.skills ?? skills
            availability = p.availability ?? true
            onTimeRate = p.onTimeRate ?? 1
            tierLabel = p.tierLabel ?? "C"
            payoutLabel = p.payoutLabel ?? (title == "Owner" ? "Full package · $0 deduction" : "Fixed rate card")
            rpasURL = p.rpasCertificateUrl
            rpasLevel = p.rpasCertificateLevel
            serviceAreas = (p.serviceAreas ?? []).joined(separator: ", ")
        } catch {}
    }

    func setAvailability(_ val: Bool) async {
        struct Body: Encodable { let availability: Bool }
        struct Resp: Decodable {}
        _ = try? await api.put("/api/vendor-os/profile", body: Body(availability: val)) as Resp
    }

    func uploadRPAS(_ url: URL) async {
        uploading = true
        defer { uploading = false }
        guard url.startAccessingSecurityScopedResource() else { return }
        defer { url.stopAccessingSecurityScopedResource() }
        guard let data = try? Data(contentsOf: url) else { return }
        var req = URLRequest(url: api.url(path: "/api/vendor-os/profile/rpas"))
        req.httpMethod = "POST"
        let boundary = "Boundary-\(UUID().uuidString)"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        if let token = await BricksAuth.shared.accessToken {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"level\"\r\n\r\nadvanced\r\n".data(using: .utf8)!)
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        let filename = url.lastPathComponent
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: application/octet-stream\r\n\r\n".data(using: .utf8)!)
        body.append(data)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        req.httpBody = body
        _ = try? await URLSession.shared.data(for: req)
        await load()
    }
}
