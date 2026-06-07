import Foundation
import BricksCore
import ActivityKit
import WidgetKit

@MainActor
final class JobStore: ObservableObject {
    static let shared = JobStore()

    @Published private(set) var todayJobs: [DispatchJob] = []
    @Published private(set) var isLoading = false
    @Published private(set) var error: String?

    private let api = BricksAPIClient.shared
    private let watch = WatchConnectivityManager.shared

    private init() {
        // Listen for accept/decline actions from the watch
        watch.addMessageHandler { [weak self] msg in
            guard let self, let action = msg["action"] as? String,
                  let jobId = msg["jobId"] as? String else { return }
            Task { await self.handleWatchAction(action, jobId: jobId) }
        }
    }

    // MARK: — Load

    func loadTodayJobs() async {
        isLoading = true
        defer { isLoading = false }
        do {
            struct JobsResponse: Decodable { let jobs: [DispatchJob] }
            let res: JobsResponse = try await api.get("/api/vendor-os/jobs")
            todayJobs = res.jobs
                .filter { job in
                    let today = ISO8601DateFormatter().string(from: Date()).prefix(10)
                    return job.scheduledDate == String(today)
                }
                .sorted { ($0.scheduledDateTime ?? .distantFuture) < ($1.scheduledDateTime ?? .distantFuture) }
            syncToWatch()
            syncToWidget()
        } catch {
            self.error = error.localizedDescription
        }
    }

    // MARK: — Status transitions

    func markInProgress(_ job: DispatchJob) async {
        await updateStatus(job, to: .inProgress)
        startLiveActivity(for: job)
    }

    func markCompleted(_ job: DispatchJob) async {
        await updateStatus(job, to: .completed)
        endLiveActivity(jobId: job.id)
    }

    func markDeclined(_ job: DispatchJob) async {
        await updateStatus(job, to: .declined)
    }

    func handleWatchAction(_ action: String, jobId: String) async {
        guard let job = todayJobs.first(where: { $0.id == jobId }) else { return }
        switch action {
        case WatchAction.accept:    await markInProgress(job)
        case WatchAction.decline:   await markDeclined(job)
        case WatchAction.onWay:     await updateStatus(job, to: .inProgress)
        case WatchAction.arrived:   break  // no status change — just GPS confirmation
        default:                    break
        }
    }

    // MARK: — Private

    private func updateStatus(_ job: DispatchJob, to status: JobStatus) async {
        let body = JobStatusUpdate(status: status)
        do {
            struct StatusResponse: Decodable { let status: String }
            let _: StatusResponse = try await api.patch(
                "/api/vendor-os/jobs/\(job.id)/status", body: body)
            if let idx = todayJobs.firstIndex(where: { $0.id == job.id }) {
                todayJobs[idx].status = status
            }
            syncToWatch()
            syncToWidget()
        } catch {
            self.error = error.localizedDescription
        }
    }

    // MARK: — Widget sync

    private func syncToWidget() {
        let d = UserDefaults(suiteName: "group.com.bricks.dispatch")
        let next = todayJobs.first { $0.isUpcoming || $0.isActive }
        d?.set(next != nil, forKey: "dispatch.widget.nextJob.exists")
        if let n = next {
            d?.set(n.address,           forKey: "dispatch.widget.nextJob.address")
            d?.set(n.scheduledDate,     forKey: "dispatch.widget.nextJob.date")
            d?.set(n.timeWindowStart,   forKey: "dispatch.widget.nextJob.time")
            d?.set(n.vendorPayout ?? 0, forKey: "dispatch.widget.nextJob.payout")
            d?.set(n.jobType,           forKey: "dispatch.widget.nextJob.type")
        }
        WidgetCenter.shared.reloadTimelines(ofKind: "NextJobWidget")
    }

    // MARK: — Watch sync

    private func syncToWatch() {
        let active = todayJobs.first(where: { $0.isActive })
        let next   = todayJobs.first(where: { $0.isUpcoming })
        var ctx: [String: Any] = [:]

        if let a = active {
            ctx[WatchContextKey.activeJobId]      = a.id
            ctx[WatchContextKey.activeJobAddress] = a.address
            ctx[WatchContextKey.activeJobStatus]  = a.status.rawValue
            ctx[WatchContextKey.activeJobPayout]  = a.vendorPayout ?? 0
        }
        if let n = next {
            ctx[WatchContextKey.nextJobId]                = n.id
            ctx[WatchContextKey.nextJobAddress]           = n.address
            ctx[WatchContextKey.nextJobTimeWindowStart]   = n.timeWindowStart
            ctx[WatchContextKey.nextJobScheduledDate]     = n.scheduledDate
        }
        watch.updateContext(ctx)
    }

    // MARK: — Live Activities

    private var activeActivity: Activity<JobActivityAttributes>?

    private func startLiveActivity(for job: DispatchJob) {
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { return }
        let attrs = JobActivityAttributes(jobId: job.id, address: job.address, packageName: job.packageName ?? "")
        let state = JobActivityAttributes.ContentStateType(status: "in_progress", elapsedMinutes: 0, agentName: job.agentName)
        let content = ActivityContent(state: state, staleDate: nil)
        activeActivity = try? Activity.request(attributes: attrs, content: content)
    }

    private func endLiveActivity(jobId: String) {
        Task {
            let state = JobActivityAttributes.ContentStateType(status: "completed", elapsedMinutes: 0, agentName: nil)
            let content = ActivityContent(state: state, staleDate: nil)
            await activeActivity?.end(content, dismissalPolicy: .after(.now + 300))
            activeActivity = nil
        }
    }
}
