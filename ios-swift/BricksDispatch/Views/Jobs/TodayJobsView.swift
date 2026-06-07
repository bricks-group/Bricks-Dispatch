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
                    Button { Task { await store.loadTodayJobs() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
            }
            .refreshable { await store.loadTodayJobs() }
            .navigationDestination(item: $selectedJob) { job in
                JobDetailView(job: job)
            }
        }
    }

    private var jobList: some View {
        List {
            if let active = store.todayJobs.first(where: { $0.isActive }) {
                Section("ACTIVE") {
                    JobRowView(job: active)
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

struct JobRowView: View {
    let job: DispatchJob
    var dimmed = false

    var body: some View {
        HStack(spacing: 14) {
            statusDot
            VStack(alignment: .leading, spacing: 3) {
                Text(job.address)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(dimmed ? Color(.secondaryLabel) : .primary)
                    .lineLimit(1)
                HStack(spacing: 6) {
                    Text(timeLabel)
                        .font(.caption)
                        .foregroundStyle(Color(.secondaryLabel))
                    Text("·")
                        .foregroundStyle(Color(.tertiaryLabel))
                    Text(job.packageName ?? job.jobType.capitalized)
                        .font(.caption)
                        .foregroundStyle(Color(.secondaryLabel))
                }
            }
            Spacer()
            Text(job.formattedPayout)
                .font(.subheadline.monospacedDigit())
                .foregroundStyle(dimmed ? Color(.tertiaryLabel) : BricksColors.brick)
        }
        .padding(.vertical, 2)
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
