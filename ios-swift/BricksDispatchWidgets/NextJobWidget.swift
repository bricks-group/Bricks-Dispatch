import WidgetKit
import SwiftUI

// MARK: — Shared UserDefaults keys (written by JobStore in main app)
private enum DispatchWidgetKey {
    static let suite        = "group.com.bricks.dispatch"
    static let hasJob       = "dispatch.widget.nextJob.exists"
    static let address      = "dispatch.widget.nextJob.address"
    static let date         = "dispatch.widget.nextJob.date"
    static let time         = "dispatch.widget.nextJob.time"
    static let payout       = "dispatch.widget.nextJob.payout"
    static let jobType      = "dispatch.widget.nextJob.type"
}

// MARK: — Entry

struct NextJobEntry: TimelineEntry {
    let date: Date
    let hasJob: Bool
    let address: String
    let jobDate: String
    let jobTime: String
    let payout: Double
    let jobType: String

    static let placeholder = NextJobEntry(
        date: Date(),
        hasJob: true,
        address: "123 King St W",
        jobDate: "2026-06-08",
        jobTime: "10:00",
        payout: 269.55,
        jobType: "photo"
    )

    static let empty = NextJobEntry(
        date: Date(), hasJob: false,
        address: "", jobDate: "", jobTime: "", payout: 0, jobType: ""
    )
}

// MARK: — Provider

struct NextJobProvider: TimelineProvider {
    func placeholder(in context: Context) -> NextJobEntry { .placeholder }

    func getSnapshot(in context: Context, completion: @escaping (NextJobEntry) -> Void) {
        completion(context.isPreview ? .placeholder : entry())
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<NextJobEntry>) -> Void) {
        let e = entry()
        let refresh = Calendar.current.date(byAdding: .minute, value: 20, to: Date())!
        completion(Timeline(entries: [e], policy: .after(refresh)))
    }

    private func entry() -> NextJobEntry {
        let d = UserDefaults(suiteName: DispatchWidgetKey.suite)
        guard d?.bool(forKey: DispatchWidgetKey.hasJob) == true else { return .empty }
        return NextJobEntry(
            date: Date(),
            hasJob: true,
            address:  d?.string(forKey: DispatchWidgetKey.address)  ?? "",
            jobDate:  d?.string(forKey: DispatchWidgetKey.date)     ?? "",
            jobTime:  d?.string(forKey: DispatchWidgetKey.time)     ?? "",
            payout:   d?.double(forKey: DispatchWidgetKey.payout)   ?? 0,
            jobType:  d?.string(forKey: DispatchWidgetKey.jobType)  ?? "photo"
        )
    }
}

// MARK: — Views

struct NextJobWidgetView: View {
    let entry: NextJobEntry
    @Environment(\.widgetFamily) private var family

    var body: some View {
        if entry.hasJob {
            jobView
        } else {
            emptyView
        }
    }

    private var jobView: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header
            HStack(spacing: 4) {
                Image(systemName: jobIcon)
                    .font(.caption2.bold())
                Text("NEXT JOB")
                    .font(.system(size: 9, weight: .bold))
                    .tracking(0.5)
            }
            .foregroundStyle(brick)

            Spacer(minLength: 4)

            // Address
            Text(shortAddress)
                .font(.system(size: family == .systemSmall ? 13 : 15, weight: .bold))
                .lineLimit(family == .systemSmall ? 2 : 1)
                .foregroundStyle(.primary)

            Spacer(minLength: 2)

            if family == .systemMedium {
                Text(entry.address)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                Spacer(minLength: 4)
            }

            // Time + payout row
            HStack {
                HStack(spacing: 3) {
                    Image(systemName: "clock")
                        .font(.system(size: 9))
                    Text(formattedTime)
                        .font(.system(size: 11, weight: .medium).monospacedDigit())
                }
                .foregroundStyle(.secondary)

                Spacer()

                Text(payout)
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(brick)
            }
        }
        .padding(14)
        .containerBackground(Color(.systemBackground), for: .widget)
    }

    private var emptyView: some View {
        VStack(spacing: 6) {
            Image(systemName: "camera")
                .font(.title3)
                .foregroundStyle(brick.opacity(0.5))
            Text("No jobs today")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .containerBackground(Color(.systemBackground), for: .widget)
    }

    private var brick: Color { Color(red: 0.82, green: 0.33, blue: 0.17) }

    private var shortAddress: String {
        entry.address.components(separatedBy: ",").first ?? entry.address
    }

    private var formattedTime: String {
        let fmt = DateFormatter(); fmt.dateFormat = "HH:mm"
        guard let d = fmt.date(from: entry.jobTime) else { return entry.jobTime }
        fmt.dateFormat = "h:mm a"; return fmt.string(from: d)
    }

    private var payout: String {
        String(format: "$%.0f", entry.payout)
    }

    private var jobIcon: String {
        switch entry.jobType {
        case "drone":   return "airplane"
        case "video":   return "video"
        default:        return "camera"
        }
    }
}

// MARK: — Widget

struct NextJobWidget: Widget {
    let kind = "NextJobWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: NextJobProvider()) { entry in
            NextJobWidgetView(entry: entry)
        }
        .configurationDisplayName("Next Job")
        .description("See your next scheduled shoot at a glance.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}
