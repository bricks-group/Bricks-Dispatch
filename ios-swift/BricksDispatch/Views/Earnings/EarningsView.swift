import SwiftUI
import BricksCore
import WidgetKit

struct EarningsView: View {
    @StateObject private var vm = EarningsViewModel()
    private let th = BricksTheme(dark: true)

    var body: some View {
        ZStack {
            th.bg.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {

                    Text("Payouts")
                        .font(.system(size: 22, weight: .medium))
                        .tracking(-0.5)
                        .foregroundStyle(th.ink)
                        .padding(.horizontal, 22)
                        .padding(.top, 14)
                        .padding(.bottom, 16)

                    // ── This week card ──
                    VStack(alignment: .leading, spacing: 0) {
                        BOver("This week", color: th.ink3)

                        Text("$\(Int(vm.weekTotal))")
                            .font(BricksFont.mono(36))
                            .kerning(-0.8)
                            .foregroundStyle(th.ink)
                            .padding(.top, 10)

                        Text("Pays out Monday · \(vm.weekJobCount) jobs")
                            .font(.system(size: 11))
                            .foregroundStyle(th.ink3)
                            .padding(.top, 4)

                        GeometryReader { geo in
                            HStack(spacing: 0) {
                                Rectangle()
                                    .fill(BricksColors.brick)
                                    .frame(width: geo.size.width * vm.paidFraction)
                                Rectangle()
                                    .fill(th.ink4)
                                    .frame(width: geo.size.width * vm.pendingFraction)
                                Spacer()
                            }
                        }
                        .frame(height: 3)
                        .background(th.hair)
                        .clipShape(RoundedRectangle(cornerRadius: 2))
                        .padding(.top, 16)

                        HStack {
                            Text("PAID $\(Int(vm.paidTotal))")
                                .font(BricksFont.mono(10))
                                .foregroundStyle(th.ink3)
                            Spacer()
                            Text("PENDING $\(Int(vm.pendingTotal))")
                                .font(BricksFont.mono(10))
                                .foregroundStyle(th.ink3)
                        }
                        .padding(.top, 8)
                    }
                    .padding(20)
                    .background(th.surf)
                    .overlay(
                        RoundedRectangle(cornerRadius: 2)
                            .strokeBorder(th.hairS, lineWidth: 1)
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 2))
                    .padding(.horizontal, 22)
                    .padding(.bottom, 22)

                    // ── Recent jobs ──
                    BOver("Recent jobs", color: th.ink3)
                        .padding(.horizontal, 22)
                        .padding(.bottom, 10)

                    VStack(spacing: 0) {
                        ForEach(vm.recentJobs.indices, id: \.self) { i in
                            let job = vm.recentJobs[i]
                            HStack(spacing: 10) {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(job.address)
                                        .font(.system(size: 13, weight: .medium))
                                        .foregroundStyle(th.ink)
                                    Text(job.dateLabel)
                                        .font(BricksFont.mono(11))
                                        .foregroundStyle(th.ink3)
                                }
                                Spacer()
                                VStack(alignment: .trailing, spacing: 2) {
                                    Text(job.amount)
                                        .font(BricksFont.mono(13))
                                        .foregroundStyle(th.ink)
                                    Text(job.status.uppercased())
                                        .font(BricksFont.mono(10))
                                        .foregroundStyle(job.status == "Paid" ? BricksColors.ok : BricksColors.warn)
                                }
                            }
                            .padding(14)
                            if i < vm.recentJobs.count - 1 { BHair(dark: true) }
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

struct DispatchPayoutJob {
    let address: String
    let dateLabel: String
    let amount: String
    let status: String
}

@MainActor
final class EarningsViewModel: ObservableObject {
    @Published var weekTotal: Double = 0
    @Published var paidTotal: Double = 0
    @Published var pendingTotal: Double = 0
    @Published var weekJobCount: Int = 0
    @Published var recentJobs: [DispatchPayoutJob] = []

    var paidFraction: Double { weekTotal > 0 ? paidTotal / weekTotal : 0 }
    var pendingFraction: Double { weekTotal > 0 ? pendingTotal / weekTotal : 0 }

    func load() async {
        do {
            struct Resp: Decodable {
                let weekTotal: Double?
                let paidTotal: Double?
                let pendingTotal: Double?
                let weekJobCount: Int?
                struct Job: Decodable {
                    let address: String
                    let date: String?
                    let payout: Double?
                    let status: String?
                }
                let recentJobs: [Job]?
            }
            let r: Resp = try await BricksAPIClient.shared.get("/api/earnings")
            weekTotal    = r.weekTotal ?? 0
            paidTotal    = r.paidTotal ?? 0
            pendingTotal = r.pendingTotal ?? 0
            weekJobCount = r.weekJobCount ?? 0
            recentJobs   = (r.recentJobs ?? []).map {
                DispatchPayoutJob(
                    address: $0.address,
                    dateLabel: $0.date ?? "—",
                    amount: "$\(Int($0.payout ?? 0))",
                    status: $0.status?.capitalized ?? "—"
                )
            }
            syncToWidget()
        } catch {}
    }

    private func syncToWidget() {
        let d = UserDefaults(suiteName: "group.com.bricks.go")
        d?.set(weekTotal,    forKey: "dispatch.widget.todayEarnings")
        d?.set(pendingTotal, forKey: "dispatch.widget.pendingEarnings")
        d?.set(weekJobCount, forKey: "dispatch.widget.weekJobCount")
        WidgetCenter.shared.reloadTimelines(ofKind: "EarningsWidget")
    }
}
