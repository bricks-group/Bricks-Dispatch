import SwiftUI
import BricksCore

struct ProfileView: View {
    @ObservedObject private var auth: BricksAuth = .shared
    @StateObject private var vm = ProfileViewModel()
    @State private var showEditSheet = false

    var body: some View {
        NavigationStack {
            List {
                // Header
                Section {
                    HStack(spacing: 14) {
                        ZStack {
                            Circle()
                                .fill(BricksColors.brick.opacity(0.12))
                                .frame(width: 56, height: 56)
                            Text(initials(vm.name ?? auth.session?.email ?? "?"))
                                .font(.system(size: 20, weight: .semibold))
                                .foregroundStyle(BricksColors.brick)
                        }
                        .accessibilityHidden(true)

                        VStack(alignment: .leading, spacing: 5) {
                            Text(vm.name ?? auth.session?.email ?? "Photographer")
                                .font(.headline)
                            if let role = vm.role {
                                Text(role.uppercased())
                                    .font(BricksFont.monoMedium(9))
                                    .tracking(0.6)
                                    .foregroundStyle(BricksColors.brick)
                                    .padding(.horizontal, 6)
                                    .padding(.vertical, 2)
                                    .background(BricksColors.brick.opacity(0.08))
                                    .clipShape(RoundedRectangle(cornerRadius: 2))
                            }
                            if let stats = vm.stats {
                                HStack(spacing: 16) {
                                    statBadge(label: "SHOOTS", value: "\(stats.totalJobsCompleted)")
                                    if let rating = stats.rating {
                                        statBadge(label: "RATING", value: String(format: "%.1f", rating))
                                    }
                                    statBadge(label: "SINCE", value: stats.memberSince)
                                }
                                .padding(.top, 4)
                            }
                        }
                        Spacer()
                    }
                    .padding(.vertical, 8)
                }

                Section("ACCOUNT") {
                    Button {
                        showEditSheet = true
                    } label: {
                        Label("Edit Profile", systemImage: "pencil")
                    }
                    .foregroundStyle(.primary)

                    Toggle(isOn: $vm.availability) {
                        Label("Available for Jobs", systemImage: "clock")
                    }
                    .tint(BricksColors.brick)
                    .onChange(of: vm.availability) { val in
                        Task { await vm.setAvailability(val) }
                    }

                    NavigationLink {
                        SettingsView()
                    } label: {
                        Label("Settings", systemImage: "gearshape")
                    }

                    NavigationLink {
                        NotificationsView()
                    } label: {
                        Label("Notifications", systemImage: "bell")
                    }
                }

                Section("SUPPORT") {
                    Link(destination: URL(string: "https://dispatch.brxs.ca")!) {
                        Label("Help Centre", systemImage: "questionmark.circle")
                    }
                    .foregroundStyle(.primary)
                    Link(destination: URL(string: "https://brxs.ca/privacy")!) {
                        Label("Privacy Policy", systemImage: "lock.shield")
                    }
                    .foregroundStyle(.primary)
                }

                Section {
                    Button(role: .destructive) {
                        auth.signOut()
                    } label: {
                        Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                    }
                    .accessibilityHint("Signs you out of the Bricks GO app")
                }
            }
            .navigationTitle("Me")
            .sheet(isPresented: $showEditSheet) {
                ProfileEditView()
                    .onDisappear { Task { await vm.load() } }
            }
        }
        .task { await vm.load() }
    }

    @ViewBuilder
    private func statBadge(label: String, value: String) -> some View {
        VStack(spacing: 2) {
            Text(value)
                .font(BricksFont.mono(13))
                .foregroundStyle(.primary)
            Text(label)
                .font(BricksFont.monoMedium(8))
                .tracking(0.6)
                .foregroundStyle(.secondary)
        }
    }

    private func initials(_ str: String) -> String {
        let parts = str.split(separator: " ").map(String.init)
        if parts.count >= 2 {
            return String((parts[0].first ?? "?")) + String((parts[1].first ?? "?"))
        }
        return String(str.prefix(2)).uppercased()
    }
}

// MARK: — ViewModel

@MainActor
final class ProfileViewModel: ObservableObject {
    @Published var name: String?
    @Published var role: String?
    @Published var availability: Bool = true
    @Published var stats: ProfileStats?

    struct ProfileStats {
        let totalJobsCompleted: Int
        let rating: Double?
        let memberSince: String
    }

    private let api = BricksAPIClient.shared

    func load() async {
        do {
            struct Profile: Decodable {
                let name: String; let role: String; let availability: Bool
                let totalJobsCompleted: Int; let rating: Double?; let memberSince: String
            }
            let p: Profile = try await api.get("/api/profile")
            name = p.name
            role = p.role
            availability = p.availability
            let fmt = DateFormatter()
            fmt.dateFormat = "yyyy-MM-dd'T'HH:mm:ssZ"
            let since: String
            if let date = fmt.date(from: p.memberSince) {
                let out = DateFormatter()
                out.dateFormat = "MMM yyyy"
                since = out.string(from: date)
            } else {
                since = p.memberSince
            }
            stats = ProfileStats(totalJobsCompleted: p.totalJobsCompleted, rating: p.rating, memberSince: since)
        } catch {}
    }

    func setAvailability(_ val: Bool) async {
        do {
            struct Upd: Encodable { let availability: Bool }
            struct Resp: Decodable {}
            let _: Resp = try await api.patch("/api/profile", body: Upd(availability: val))
        } catch {
            availability = !val
        }
    }
}
