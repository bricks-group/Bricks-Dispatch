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
                    earningsList(r)
                }
            }
            .navigationTitle("Earnings")
            .task { await vm.load() }
            .refreshable { await vm.load() }
        }
    }

    @ViewBuilder
    private func earningsList(_ r: EarningsResponse) -> some View {
        List {
            // Big number header
            Section {
                VStack(alignment: .leading, spacing: 10) {
                    Text("THIS WEEK")
                        .font(.system(size: 9.5, weight: .semibold, design: .monospaced))
                        .tracking(0.8)
                        .foregroundStyle(.secondary)

                    HStack(alignment: .lastTextBaseline, spacing: 4) {
                        AnimatedDollar(target: r.weekTotal, delay: 0.1)
                            .font(.system(size: 46, weight: .semibold))
                            .foregroundStyle(.primary)
                        Text("CAD")
                            .font(.system(size: 14, design: .monospaced))
                            .foregroundStyle(.secondary)
                            .padding(.bottom, 6)
                    }

                    Text("\(r.weekJobCount) shoot\(r.weekJobCount == 1 ? "" : "s") this week")
                        .font(.system(size: 12, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 6)

                // 7-day bar chart
                WeekBarChart(weekTotal: r.weekTotal)
            }

            // Next payout card
            Section {
                HStack(alignment: .center) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("NEXT PAYOUT")
                            .font(.system(size: 9.5, weight: .semibold, design: .monospaced))
                            .tracking(0.8)
                            .foregroundStyle(.secondary)
                        Text(r.pendingAmount, format: .currency(code: "CAD"))
                            .font(.system(size: 22, weight: .semibold))
                        Text(r.payoutDay)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    if r.pendingTripFeesTotal > 0 {
                        VStack(alignment: .trailing, spacing: 4) {
                            Text("TRIP FEES")
                                .font(.system(size: 9.5, weight: .semibold, design: .monospaced))
                                .tracking(0.8)
                                .foregroundStyle(.secondary)
                            Label {
                                Text(r.pendingTripFeesTotal, format: .currency(code: "CAD"))
                                    .font(.system(size: 15, weight: .semibold))
                                    .foregroundStyle(BricksColors.brick)
                            } icon: {
                                Image(systemName: "car.fill")
                                    .foregroundStyle(BricksColors.brick)
                                    .font(.caption)
                            }
                        }
                    }
                }
                .padding(.vertical, 4)
            }

            Section("RECENT SHOOTS") {
                ForEach(r.recentJobs) { job in
                    HStack(spacing: 12) {
                        Image(systemName: "camera")
                            .font(.subheadline)
                            .foregroundStyle(BricksColors.gold)
                            .frame(width: 34, height: 34)
                            .background(BricksColors.gold.opacity(0.1))
                            .clipShape(RoundedRectangle(cornerRadius: 6))

                        VStack(alignment: .leading, spacing: 2) {
                            Text(job.address)
                                .font(.subheadline.weight(.medium))
                                .lineLimit(1)
                            Text(job.scheduledDate)
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 2) {
                            Text(job.payoutAmount, format: .currency(code: "CAD"))
                                .font(.subheadline.weight(.semibold))
                            Text(job.payoutStatus.uppercased())
                                .font(.system(size: 9.5, weight: .bold, design: .monospaced))
                                .tracking(0.5)
                                .foregroundStyle(job.payoutStatus == "paid" ? .green : .secondary)
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
        }
    }
}

// MARK: — Week bar chart

private struct WeekBarChart: View {
    let weekTotal: Double
    private let dayLabels = ["M", "T", "W", "T", "F", "S", "S"]

    private var todayIndex: Int {
        let weekday = Calendar.current.component(.weekday, from: Date())
        return (weekday + 5) % 7
    }

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            ForEach(0..<7, id: \.self) { i in
                VStack(spacing: 5) {
                    RoundedRectangle(cornerRadius: 3)
                        .fill(i == todayIndex
                            ? AnyShapeStyle(BricksColors.gold)
                            : AnyShapeStyle(Color(.tertiarySystemFill))
                        )
                        .frame(height: barHeight(for: i))
                    Text(dayLabels[i])
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(i == todayIndex ? BricksColors.gold : .secondary)
                }
                .frame(maxWidth: .infinity)
            }
        }
        .frame(height: 72)
        .padding(.vertical, 4)
    }

    private func barHeight(for index: Int) -> CGFloat {
        if index > todayIndex { return 12 }
        if index == todayIndex { return 56 }
        let base: [CGFloat] = [32, 24, 44, 38, 50, 56, 20]
        return base[index % base.count]
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
