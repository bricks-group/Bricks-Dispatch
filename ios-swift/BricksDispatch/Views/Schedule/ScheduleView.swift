import SwiftUI
import BricksCore

struct ScheduleView: View {
    @StateObject private var store = ScheduleStore()
    @State private var selectedDate: Date = Calendar.current.startOfDay(for: .now)
    @State private var weekOffset: Int = 0
    private let th = BricksTheme(dark: true)

    var body: some View {
        ZStack {
            th.bg.ignoresSafeArea()
            VStack(spacing: 0) {
                Text("Schedule")
                    .font(.system(size: 22, weight: .medium))
                    .tracking(-0.5)
                    .foregroundStyle(th.ink)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 20)
                    .padding(.top, 14)
                    .padding(.bottom, 8)
                ScrollView {
                    LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders]) {
                        Section(header: weekHeader) {
                            dayTimeline
                        }
                    }
                }
                .refreshable { await store.loadAllJobs() }
            }
        }
        .task { await store.loadAllJobs() }
    }

    // MARK: — Week header (sticky)

    private var weekHeader: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Week \(weekNumber)")
                    .font(.system(size: 22, weight: .medium))
                Spacer()
                HStack(spacing: 6) {
                    Button {
                        weekOffset -= 1
                        snapSelectedDate()
                    } label: {
                        Image(systemName: "chevron.left")
                            .font(.system(size: 13, weight: .medium))
                            .frame(width: 32, height: 32)
                            .background(th.surf)
                            .clipShape(RoundedRectangle(cornerRadius: 2))
                    }
                    .buttonStyle(.plain)
                    Button {
                        weekOffset += 1
                        snapSelectedDate()
                    } label: {
                        Image(systemName: "chevron.right")
                            .font(.system(size: 13, weight: .medium))
                            .frame(width: 32, height: 32)
                            .background(th.surf)
                            .clipShape(RoundedRectangle(cornerRadius: 2))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 10)
            .padding(.bottom, 10)

            HStack(spacing: 5) {
                ForEach(Array(weekDays.enumerated()), id: \.offset) { _, day in
                    let iso = isoString(day)
                    let isSelected = Calendar.current.isDate(day, inSameDayAs: selectedDate)
                    let isToday = Calendar.current.isDateInToday(day)
                    let count = store.jobsByDate[iso]?.count ?? 0

                    Button {
                        selectedDate = day
                    } label: {
                        VStack(spacing: 3) {
                            Text(shortDayName(day))
                                .font(BricksFont.monoMedium(8))
                                .tracking(0.5)
                                .foregroundStyle(isSelected ? BricksColors.paper : th.ink3)
                            Text("\(Calendar.current.component(.day, from: day))")
                                .font(BricksFont.mono(14))
                                .foregroundStyle(
                                    isSelected ? BricksColors.paper
                                    : isToday ? BricksColors.brick
                                    : th.ink
                                )
                            if count > 0 {
                                HStack(spacing: 2) {
                                    ForEach(0..<min(count, 4), id: \.self) { _ in
                                        Circle()
                                            .fill(isSelected ? BricksColors.paper : BricksColors.brick)
                                            .frame(width: 3, height: 3)
                                    }
                                }
                            } else {
                                Spacer().frame(height: 3)
                            }
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                        .background(isSelected ? BricksColors.brick : th.surf)
                        .clipShape(RoundedRectangle(cornerRadius: 2))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 10)
            .background(Color(.systemBackground))
        }
    }

    // MARK: — Day timeline

    private var dayTimeline: some View {
        let iso = isoString(selectedDate)
        let dayFmt = DateFormatter()
        let _ = { dayFmt.dateFormat = "EEEE, MMMM d" }()
        let jobs = store.jobsByDate[iso] ?? []
        let label = dayFmt.string(from: selectedDate).uppercased()

        return Group {
            HStack {
                Text("\(label) · \(jobs.count) SHOOT\(jobs.count == 1 ? "" : "S")")
                    .font(BricksFont.monoMedium(9))
                    .tracking(1.2)
                    .foregroundStyle(.secondary)
                Spacer()
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 14)

            if jobs.isEmpty {
                VStack(spacing: 10) {
                    Image(systemName: "calendar")
                        .font(.system(size: 36))
                        .foregroundStyle(Color(.tertiaryLabel))
                    Text("No jobs on this day")
                        .font(.subheadline)
                        .foregroundStyle(Color(.secondaryLabel))
                }
                .frame(maxWidth: .infinity)
                .padding(.top, 60)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(jobs.enumerated()), id: \.element.id) { idx, job in
                        NavigationLink {
                            JobDetailView(job: job)
                        } label: {
                            TimelineRow(job: job, isLast: idx == jobs.count - 1)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 20)
            }
        }
    }

    // MARK: — Helpers

    private var weekDays: [Date] {
        let cal = Calendar.current
        let today = cal.startOfDay(for: .now)
        let todayWeekday = cal.component(.weekday, from: today)
        let daysToMonday = (todayWeekday == 1) ? -6 : 2 - todayWeekday
        let monday = cal.date(byAdding: .day, value: daysToMonday + (weekOffset * 7), to: today)!
        return (0..<7).compactMap { cal.date(byAdding: .day, value: $0, to: monday) }
    }

    private var weekNumber: Int {
        Calendar.current.component(.weekOfYear, from: weekDays.first ?? .now)
    }

    private func isoString(_ date: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: date)
    }

    private func shortDayName(_ date: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "EEE"
        return String(f.string(from: date).prefix(3)).uppercased()
    }

    private func snapSelectedDate() {
        if let first = weekDays.first { selectedDate = first }
    }
}

// MARK: — Timeline row

private struct TimelineRow: View {
    let job: DispatchJob
    let isLast: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            Text(shortTime)
                .font(BricksFont.mono(12))
                .foregroundStyle(.secondary)
                .frame(width: 44, alignment: .leading)
                .padding(.top, 2)

            ZStack(alignment: .top) {
                if !isLast {
                    Rectangle()
                        .fill(Color(.separator))
                        .frame(width: 1)
                        .frame(maxHeight: .infinity)
                        .offset(x: 0)
                }
                Circle()
                    .fill(statusDotColor)
                    .frame(width: 7, height: 7)
                    .padding(.top, 5)
            }
            .frame(width: 1)

            VStack(alignment: .leading, spacing: 4) {
                Text(job.address.lowercased())
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(.primary)
                Text(job.jobType.replacingOccurrences(of: "_", with: " "))
                    .font(BricksFont.mono(11))
                    .foregroundStyle(.secondary)
                statusPill
                    .padding(.bottom, 16)
            }
        }
    }

    private var shortTime: String {
        let parts = job.timeWindowStart.split(separator: ":").map(String.init)
        guard parts.count >= 2, let h = Int(parts[0]), let m = Int(parts[1]) else { return job.timeWindowStart }
        let ampm = h >= 12 ? "PM" : "AM"
        let h12 = h == 0 ? 12 : h > 12 ? h - 12 : h
        return m == 0 ? "\(h12)\(ampm)" : "\(h12):\(parts[1])\(ampm)"
    }

    private var statusDotColor: Color {
        switch job.status {
        case .completed, .delivered: return Color(.tertiaryLabel)
        case .inProgress: return .green
        default: return BricksColors.brick
        }
    }

    @ViewBuilder
    private var statusPill: some View {
        let (label, bg, fg): (String, Color, Color) = {
            switch job.status {
            case .completed, .delivered: return ("Delivered", .green.opacity(0.15), .green)
            case .inProgress: return ("Active", BricksColors.brick.opacity(0.15), BricksColors.brick)
            default: return ("Scheduled", th.surf, Color(.secondaryLabel))
            }
        }()
        Text(label.uppercased())
            .font(BricksFont.monoMedium(9))
            .tracking(1.2)
            .foregroundStyle(fg)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(bg)
            .clipShape(RoundedRectangle(cornerRadius: 2))
    }
}

// MARK: — Store

@MainActor
final class ScheduleStore: ObservableObject {
    @Published var jobsByDate: [String: [DispatchJob]] = [:]

    private let api = BricksAPIClient.shared

    func loadAllJobs() async {
        do {
            struct JobsWrapper: Decodable { let jobs: [DispatchJob] }
            let res: JobsWrapper = try await api.get("/api/jobs")
            var map: [String: [DispatchJob]] = [:]
            for job in res.jobs {
                map[job.scheduledDate, default: []].append(job)
            }
            for key in map.keys {
                map[key]?.sort { ($0.timeWindowStart) < ($1.timeWindowStart) }
            }
            jobsByDate = map
        } catch {}
    }
}
