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
                            .minimumScaleFactor(0.55)
                            .lineLimit(1)
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
                WeekBarChart(dailyEarnings: weeklyEarnings(from: r.recentJobs))
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

    private func weeklyEarnings(from jobs: [EarningsJobItem]) -> [Double] {
        var daily = Array(repeating: 0.0, count: 7)
        let calendar = Calendar.current
        let today = Date()
        let weekday = calendar.component(.weekday, from: today)
        let todayIdx = (weekday + 5) % 7
        guard let monday = calendar.date(
            byAdding: .day,
            value: -todayIdx,
            to: calendar.startOfDay(for: today)
        ) else { return daily }
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd"
        fmt.locale = Locale(identifier: "en_US_POSIX")
        for job in jobs where job.status != "cancelled" {
            guard let date = fmt.date(from: job.scheduledDate) else { continue }
            let offset = calendar.dateComponents([.day], from: monday, to: calendar.startOfDay(for: date)).day ?? -1
            guard offset >= 0 && offset < 7 else { continue }
            daily[offset] += job.payoutAmount
        }
        return daily
    }
}

// MARK: — Week bar chart

private struct WeekBarChart: View {
    let dailyEarnings: [Double]   // 7 values, index 0=Mon … 6=Sun
    @State private var revealedCount = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private let dayLabels = ["M", "T", "W", "T", "F", "S", "S"]
    private let maxBarHeight: CGFloat = 52
    private let minBarHeight: CGFloat = 8

    private var todayIndex: Int {
        let weekday = Calendar.current.component(.weekday, from: Date())
        return (weekday + 5) % 7
    }

    private var maxEarning: Double { dailyEarnings.max() ?? 1 }

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            ForEach(0..<7, id: \.self) { i in
                VStack(spacing: 5) {
                    RoundedRectangle(cornerRadius: 3)
                        .fill(barColor(for: i))
                        .frame(height: i < revealedCount ? targetHeight(for: i) : minBarHeight)
                        .animation(
                            reduceMotion ? .none :
                                .spring(response: 0.6, dampingFraction: 0.7)
                                .delay(Double(i) * 0.065),
                            value: revealedCount
                        )
                    Text(dayLabels[i])
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(i == todayIndex ? BricksColors.gold : .secondary)
                }
                .frame(maxWidth: .infinity)
            }
        }
        .frame(height: 72)
        .padding(.vertical, 4)
        .onAppear { startReveal() }
        .onChange(of: dailyEarnings) { _, _ in revealedCount = 0; startReveal() }
        .accessibilityLabel(chartAccessibilityLabel)
    }

    private func targetHeight(for i: Int) -> CGFloat {
        if i > todayIndex { return minBarHeight }
        let e = dailyEarnings[i]
        guard e > 0, maxEarning > 0 else { return minBarHeight }
        let proportion = CGFloat(e / maxEarning)
        return minBarHeight + (maxBarHeight - minBarHeight) * max(proportion, 0.14)
    }

    private func barColor(for i: Int) -> AnyShapeStyle {
        if i == todayIndex { return AnyShapeStyle(BricksColors.gold) }
        if i > todayIndex { return AnyShapeStyle(Color(.tertiarySystemFill)) }
        return AnyShapeStyle(BricksColors.brick.opacity(0.30))
    }

    private func startReveal() {
        if reduceMotion { revealedCount = 7; return }
        for step in 1...7 {
            DispatchQueue.main.asyncAfter(deadline: .now() + Double(step - 1) * 0.065) {
                withAnimation(.spring(response: 0.6, dampingFraction: 0.7)) {
                    revealedCount = step
                }
            }
        }
    }

    private var chartAccessibilityLabel: String {
        let labels = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        let parts = (0...todayIndex).map { i -> String in
            let e = dailyEarnings[i]
            return e > 0 ? "\(labels[i]): $\(String(format: "%.0f", e))" : "\(labels[i]): no earnings"
        }
        return "Weekly earnings chart. " + parts.joined(separator: ", ")
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
