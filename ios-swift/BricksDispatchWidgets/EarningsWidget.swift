import WidgetKit
import SwiftUI

// MARK: — Entry

struct EarningsEntry: TimelineEntry {
    let date: Date
    let todayEarnings: Double
    let pendingEarnings: Double
    let jobCount: Int
    let payoutDay: String

    static let placeholder = EarningsEntry(
        date: Date(),
        todayEarnings: 539.10,
        pendingEarnings: 269.55,
        jobCount: 3,
        payoutDay: "Sunday"
    )
}

// MARK: — Provider

struct EarningsProvider: TimelineProvider {
    func placeholder(in context: Context) -> EarningsEntry { .placeholder }

    func getSnapshot(in context: Context, completion: @escaping (EarningsEntry) -> Void) {
        completion(context.isPreview ? .placeholder : entry())
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<EarningsEntry>) -> Void) {
        let e = entry()
        let refresh = Calendar.current.date(byAdding: .minute, value: 30, to: Date())!
        completion(Timeline(entries: [e], policy: .after(refresh)))
    }

    private func entry() -> EarningsEntry {
        let d = UserDefaults(suiteName: "group.com.bricks.go")
        return EarningsEntry(
            date: Date(),
            todayEarnings:   d?.double(forKey: "dispatch.widget.todayEarnings")   ?? 0,
            pendingEarnings: d?.double(forKey: "dispatch.widget.pendingEarnings") ?? 0,
            jobCount:        d?.integer(forKey: "dispatch.widget.weekJobCount")   ?? 0,
            payoutDay:       d?.string(forKey: "dispatch.widget.payoutDay")       ?? "Sunday"
        )
    }
}

// MARK: — View

struct EarningsWidgetView: View {
    let entry: EarningsEntry
    private var brick: Color { Color(red: 0.82, green: 0.33, blue: 0.17) }

    var body: some View {
        HStack(spacing: 0) {
            // Left: headline earnings
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 4) {
                    Image(systemName: "banknote")
                        .font(.caption2.bold())
                    Text("TODAY")
                        .font(.system(size: 9, weight: .bold))
                        .tracking(0.5)
                }
                .foregroundStyle(brick)

                Spacer(minLength: 0)

                Text(formatted(entry.todayEarnings))
                    .font(.system(size: 26, weight: .bold, design: .rounded))
                    .foregroundStyle(.primary)
                    .minimumScaleFactor(0.7)

                Text("\(entry.jobCount) job\(entry.jobCount == 1 ? "" : "s")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Divider()
                .padding(.vertical, 4)
                .padding(.horizontal, 12)

            // Right: pending + payout day
            VStack(alignment: .leading, spacing: 8) {
                statRow(
                    icon: "clock",
                    label: "Pending",
                    value: formatted(entry.pendingEarnings),
                    valueColor: entry.pendingEarnings > 0 ? .orange : .secondary
                )
                statRow(
                    icon: "arrow.up.circle",
                    label: "Payout",
                    value: entry.payoutDay,
                    valueColor: .primary
                )
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(14)
        .containerBackground(Color(.systemBackground), for: .widget)
    }

    private func statRow(icon: String, label: String, value: String, valueColor: Color) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: 3) {
                Image(systemName: icon).font(.system(size: 9))
                Text(label).font(.system(size: 9, weight: .medium))
            }
            .foregroundStyle(.secondary)
            Text(value)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(valueColor)
        }
    }

    private func formatted(_ value: Double) -> String {
        String(format: "$%.0f", value)
    }
}

// MARK: — Widget

struct EarningsWidget: Widget {
    let kind = "EarningsWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: EarningsProvider()) { entry in
            EarningsWidgetView(entry: entry)
        }
        .configurationDisplayName("Earnings")
        .description("Today's earnings and pending payout.")
        .supportedFamilies([.systemMedium])
    }
}
