import SwiftUI
import BricksCore

struct ProfileView: View {
    @ObservedObject private var auth: BricksAuth = .shared
    @StateObject private var vm = DispatchProfileViewModel()
    private let th = BricksTheme(dark: true)

    private var displayName: String { vm.name ?? auth.session?.email ?? "Photographer" }

    private var initials: String {
        let parts = displayName.split(separator: " ").map(String.init)
        if parts.count >= 2 { return String(parts[0].prefix(1)) + String(parts[1].prefix(1)) }
        return String(displayName.prefix(2)).uppercased()
    }

    private var currentYearMonth: String {
        let c = Calendar.current; let now = Date()
        return String(format: "%04d-%02d", c.component(.year, from: now), c.component(.month, from: now))
    }

    var body: some View {
        ZStack {
            th.bg.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {

                    Text("Profile")
                        .font(.system(size: 22, weight: .medium))
                        .tracking(-0.5)
                        .foregroundStyle(th.ink)
                        .padding(.horizontal, 22)
                        .padding(.top, 14)
                        .padding(.bottom, 20)

                    // ── Identity card ──
                    HStack(spacing: 14) {
                        BAvatar(initials, size: 56, dark: true)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(displayName)
                                .font(.system(size: 17, weight: .medium))
                                .foregroundStyle(th.ink)
                            Text("Freelance · Photographer")
                                .font(BricksFont.body(12))
                                .foregroundStyle(th.ink3)
                        }
                        Spacer()
                        Image(systemName: "chevron.right")
                            .font(.system(size: 14))
                            .foregroundStyle(th.ink3)
                    }
                    .padding(.horizontal, 22)
                    .padding(.vertical, 14)

                    // ── Stats strip ──
                    HStack(spacing: 1) {
                        BStatCell("JOBS", "\(vm.totalJobs)", dark: true)
                        BStatCell("THIS MO.", "\(vm.jobsThisMonth)", dark: true)
                        BStatCell("RATING", vm.rating, dark: true)
                    }
                    .background(BricksColors.nightHair)
                    .overlay(
                        RoundedRectangle(cornerRadius: 2)
                            .strokeBorder(BricksColors.nightHair, lineWidth: 1)
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 2))
                    .padding(.horizontal, 22)
                    .padding(.bottom, 22)

                    // ── Account section ──
                    BOver("Account", color: th.ink3)
                        .padding(.horizontal, 22)
                        .padding(.bottom, 10)

                    VStack(spacing: 0) {
                        let rows: [(String, String)] = [
                            ("person", "Edit profile"),
                            ("creditcard", "Payout method"),
                            ("doc.text", "Tax documents"),
                            ("gearshape", "Settings"),
                        ]
                        ForEach(rows.indices, id: \.self) { i in
                            HStack(spacing: 12) {
                                Image(systemName: rows[i].0)
                                    .font(.system(size: 17))
                                    .foregroundStyle(th.ink2)
                                    .frame(width: 20)
                                Text(rows[i].1)
                                    .font(.system(size: 14))
                                    .foregroundStyle(th.ink)
                                Spacer()
                                Image(systemName: "chevron.right")
                                    .font(.system(size: 14))
                                    .foregroundStyle(th.ink3)
                            }
                            .padding(14)
                            if i < rows.count - 1 { BHair(dark: true) }
                        }
                    }
                    .background(th.surf)
                    .overlay(
                        RoundedRectangle(cornerRadius: 2)
                            .strokeBorder(th.hair, lineWidth: 1)
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 2))
                    .padding(.horizontal, 22)
                    .padding(.bottom, 22)

                    // ── Support section ──
                    BOver("Support", color: th.ink3)
                        .padding(.horizontal, 22)
                        .padding(.bottom, 10)

                    VStack(spacing: 0) {
                        HStack(spacing: 12) {
                            Image(systemName: "phone")
                                .font(.system(size: 17))
                                .foregroundStyle(th.ink2)
                                .frame(width: 20)
                            Text("Contact support")
                                .font(.system(size: 14))
                                .foregroundStyle(th.ink)
                            Spacer()
                        }
                        .padding(14)

                        BHair(dark: true)

                        Button(role: .destructive) { auth.signOut() } label: {
                            HStack(spacing: 12) {
                                Image(systemName: "rectangle.portrait.and.arrow.right")
                                    .font(.system(size: 17))
                                    .foregroundStyle(BricksColors.brick)
                                    .frame(width: 20)
                                Text("Sign out")
                                    .font(.system(size: 14))
                                    .foregroundStyle(BricksColors.brick)
                                Spacer()
                            }
                            .padding(14)
                        }
                    }
                    .background(th.surf)
                    .overlay(
                        RoundedRectangle(cornerRadius: 2)
                            .strokeBorder(th.hair, lineWidth: 1)
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 2))
                    .padding(.horizontal, 22)

                    Spacer(minLength: 20)
                }
            }
        }
        .task { await vm.load() }
    }
}

@MainActor
final class DispatchProfileViewModel: ObservableObject {
    @Published var name: String?
    @Published var totalJobs: Int = 0
    @Published var jobsThisMonth: Int = 0
    @Published var rating: String = "—"

    func load() async {
        do {
            struct Resp: Decodable {
                let name: String?
                let totalJobs: Int?
                let jobsThisMonth: Int?
                let rating: Double?
            }
            let r: Resp = try await BricksAPIClient.shared.get("/api/profile")
            name = r.name
            totalJobs = r.totalJobs ?? 0
            jobsThisMonth = r.jobsThisMonth ?? 0
            if let rat = r.rating { rating = String(format: "%.1f", rat) }
        } catch {}
    }
}
