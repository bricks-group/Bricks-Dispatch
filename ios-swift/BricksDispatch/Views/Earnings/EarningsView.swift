import SwiftUI
import BricksCore
import WidgetKit

struct EarningsResponse: Decodable {
    let weekTotal: Double
    let pendingAmount: Double
    let pendingTripFeesTotal: Double
    let weekJobCount: Int
    let payoutDay: String
    let recentJobs: [EarningsJobItem]
}

struct EarningsJobItem: Decodable, Identifiable {
    let id: String
    let address: String
    let scheduledDate: String
    let status: String
    let payoutAmount: Double
    let payoutStatus: String
}

struct EarningsView: View {
    @StateObject private var vm = EarningsViewModel()

    var body: some View {
        NavigationStack {
            Group {
                if vm.isLoading && vm.response == nil {
                    ProgressView()
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let r = vm.response {
                    List {
                        Section {
                            HStack {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text("This week")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                    Text(r.weekTotal, format: .currency(code: "CAD"))
                                        .font(.title2.bold())
                                }
                                Spacer()
                                VStack(alignment: .trailing, spacing: 4) {
                                    Text("Pending")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                    Text(r.pendingAmount, format: .currency(code: "CAD"))
                                        .font(.headline)
                                        .foregroundStyle(.orange)
                                }
                            }
                            .padding(.vertical, 4)

                            if r.pendingTripFeesTotal > 0 {
                                Label {
                                    Text("Trip fees: \(r.pendingTripFeesTotal, format: .currency(code: "CAD"))")
                                        .font(.subheadline)
                                } icon: {
                                    Image(systemName: "car.fill")
                                        .foregroundStyle(BricksColors.brick)
                                }
                            }
                        }

                        Section("Recent Jobs") {
                            ForEach(r.recentJobs) { job in
                                HStack {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(job.address)
                                            .font(.subheadline)
                                            .lineLimit(1)
                                        Text(job.scheduledDate)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    VStack(alignment: .trailing, spacing: 2) {
                                        Text(job.payoutAmount, format: .currency(code: "CAD"))
                                            .font(.subheadline.bold())
                                        Text(job.payoutStatus)
                                            .font(.caption2)
                                            .foregroundStyle(job.payoutStatus == "paid" ? .green : .secondary)
                                    }
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Earnings")
            .task { await vm.load() }
            .refreshable { await vm.load() }
        }
    }
}

@MainActor
final class EarningsViewModel: ObservableObject {
    @Published var response: EarningsResponse?
    @Published var isLoading = false

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            response = try await BricksAPIClient.shared.get("/api/vendor-os/earnings")
            if let r = response { syncToWidget(r) }
        } catch {
            // Keep stale data
        }
    }

    private func syncToWidget(_ r: EarningsResponse) {
        let d = UserDefaults(suiteName: "group.com.bricks.dispatch")
        d?.set(r.weekTotal,     forKey: "dispatch.widget.todayEarnings")
        d?.set(r.pendingAmount, forKey: "dispatch.widget.pendingEarnings")
        d?.set(r.weekJobCount,  forKey: "dispatch.widget.weekJobCount")
        d?.set(r.payoutDay,     forKey: "dispatch.widget.payoutDay")
        WidgetCenter.shared.reloadTimelines(ofKind: "EarningsWidget")
    }
}
