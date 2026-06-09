import SwiftUI
import BricksCore

struct TodayJobsView: View {
    @StateObject private var store = JobStore.shared
    @State private var selectedJob: DispatchJob?

    var body: some View {
        NavigationStack {
            Group {
                if store.isLoading && store.todayJobs.isEmpty {
                    ProgressView()
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if store.todayJobs.isEmpty {
                    emptyState
                } else {
                    jobList
                }
            }
            .navigationTitle("Today")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    HStack(spacing: 5) {
                        Circle()
                            .fill(.green)
                            .frame(width: 6, height: 6)
                            .accessibilityHidden(true)
                        Text("Online")
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundStyle(.secondary)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel("Status: Online")
                }
            }
            .refreshable { await store.loadTodayJobs() }
            .navigationDestination(item: $selectedJob) { job in
                JobDetailView(job: job)
            }
            .fullScreenCover(isPresented: Binding(
                get: { store.pendingOffer != nil },
                set: { if !$0 { store.pendingOffer = nil } }
            )) {
                JobOfferHost(store: store)
            }
        }
    }

    private var jobList: some View {
        List {
            Section {
                earningsCard
                    .listRowInsets(EdgeInsets(top: 4, leading: 16, bottom: 4, trailing: 16))
            }

            if let active = store.todayJobs.first(where: { $0.isActive }) {
                Section("ACTIVE") {
                    JobRowView(job: active, isNext: true)
                        .onTapGesture { selectedJob = active }
                }
            }

            let upcoming = store.todayJobs.filter { $0.isUpcoming }
            if !upcoming.isEmpty {
                Section("UPCOMING") {
                    ForEach(upcoming) { job in
                        JobRowView(job: job)
                            .onTapGesture { selectedJob = job }
                    }
                }
            }

            let done = store.todayJobs.filter { $0.status == .completed || $0.status == .delivered }
            if !done.isEmpty {
                Section("DONE") {
                    ForEach(done) { job in
                        JobRowView(job: job, dimmed: true)
                            .onTapGesture { selectedJob = job }
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
    }

    private var earningsCard: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 4) {
                Text("PROJECTED TODAY")
                    .font(.system(size: 9.5, weight: .semibold, design: .monospaced))
                    .tracking(0.8)
                    .foregroundStyle(.secondary)
                Text(projectedPayout, format: .currency(code: "CAD"))
                    .font(.system(size: 28, weight: .semibold))
                    .foregroundStyle(.primary)
                Text("\(store.todayJobs.count) shoot\(store.todayJobs.count == 1 ? "" : "s")")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 4) {
                Text("EARNED TODAY")
                    .font(.system(size: 9.5, weight: .semibold, design: .monospaced))
                    .tracking(0.8)
                    .foregroundStyle(.secondary)
                Text(earnedPayout, format: .currency(code: "CAD"))
                    .font(.system(size: 20, weight: .semibold))
                    .foregroundStyle(BricksColors.gold)
            }
        }
        .padding(.vertical, 6)
    }

    private var projectedPayout: Double {
        store.todayJobs
            .filter { $0.status != .cancelled }
            .compactMap { $0.vendorPayout }
            .reduce(0, +)
    }

    private var earnedPayout: Double {
        store.todayJobs
            .filter { $0.status == .completed || $0.status == .delivered }
            .compactMap { $0.vendorPayout }
            .reduce(0, +)
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "calendar.badge.checkmark")
                .font(.system(size: 44))
                .foregroundStyle(Color(.tertiaryLabel))
            Text("No jobs today")
                .font(.headline)
                .foregroundStyle(Color(.secondaryLabel))
            Text("New assignments will appear here")
                .font(.subheadline)
                .foregroundStyle(Color(.tertiaryLabel))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: — Job row

struct JobRowView: View {
    let job: DispatchJob
    var dimmed = false
    var isNext = false

    var body: some View {
        HStack(spacing: 14) {
            statusDot
            VStack(alignment: .leading, spacing: 3) {
                if isNext {
                    HStack(spacing: 6) {
                        Text("UP NEXT")
                            .font(.system(size: 9, weight: .bold, design: .monospaced))
                            .tracking(0.6)
                            .foregroundStyle(.white)
                            .padding(.horizontal, 7)
                            .padding(.vertical, 3)
                            .background(BricksColors.brick)
                            .clipShape(RoundedRectangle(cornerRadius: 2))
                        Text(timeLabel)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(BricksColors.brick)
                    }
                    .padding(.bottom, 1)
                }
                Text(job.address)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(dimmed ? Color(.secondaryLabel) : .primary)
                    .lineLimit(1)
                HStack(spacing: 6) {
                    if !isNext {
                        Text(timeLabel)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(Color(.secondaryLabel))
                        Text("·")
                            .foregroundStyle(Color(.tertiaryLabel))
                    }
                    Text(job.packageName ?? job.jobType.capitalized)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(Color(.secondaryLabel))
                }
            }
            Spacer()
            Text(job.formattedPayout)
                .font(.subheadline.monospacedDigit())
                .foregroundStyle(dimmed ? Color(.tertiaryLabel) : BricksColors.brick)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }

    private var timeLabel: String {
        guard let dt = job.scheduledDateTime else { return job.timeWindowStart }
        let fmt = DateFormatter()
        fmt.dateFormat = "h:mm a"
        return fmt.string(from: dt)
    }

    private var statusDot: some View {
        Circle()
            .fill(dotColor)
            .frame(width: 8, height: 8)
            .accessibilityHidden(true)
    }

    private var dotColor: Color {
        switch job.status {
        case .inProgress: return .green
        case .assigned:   return BricksColors.brick
        case .completed, .delivered: return Color(.tertiaryLabel)
        default: return Color(.quaternaryLabel)
        }
    }
}
