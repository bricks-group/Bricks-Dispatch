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
    @Published private(set) var isAdmin = false
    @Published private(set) var team: [TeamMember] = []
    @Published private(set) var pickers: [TeamMember] = []
    @Published private(set) var listingHits: [OfficeListing] = []
    /// Non-nil when a newly-assigned job is waiting for the photographer to accept or decline.
    /// Set by push notification handler or poll. Cleared by acceptOffer/declineOffer.
    @Published var pendingOffer: DispatchJob?

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

    func job(id: String) -> DispatchJob? {
        todayJobs.first { $0.id == id }
    }

    // MARK: — Load

    func loadTodayJobs() async {
        isLoading = true
        defer { isLoading = false }
        do {
            struct JobsResponse: Decodable { let items: [DispatchJob] }
            // assignee_id=all: Owner sees unassigned default_vendor jobs (IC still scoped server-side).
            let res: JobsResponse = try await api.get("/api/vendor-os/jobs", query: [
                URLQueryItem(name: "assignee_id", value: "all"),
            ])
            let today = DispatchJob.torontoToday()
            todayJobs = res.items
                .filter { $0.scheduledDate.prefix(10) == today }
                .sorted { ($0.scheduledDateTime ?? .distantFuture) < ($1.scheduledDateTime ?? .distantFuture) }
            syncToWatch()
            syncToWidget()
            await refreshAdmin()
        } catch {
            self.error = error.localizedDescription
        }
    }

    func loadTeam() async {
        await refreshAdmin()
        guard isAdmin else {
            team = []
            pickers = []
            return
        }
        do {
            struct TeamResponse: Decodable { let items: [TeamMember] }
            let res: TeamResponse = try await api.get("/api/vendor-os/team", query: [
                URLQueryItem(name: "page", value: "1"),
                URLQueryItem(name: "page_size", value: "200"),
            ])
            team = res.items
            pickers = res.items.filter(VendorRole.isPickerMember)
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// PUT /api/vendor-os/jobs/{id}/assign  {"member_id": "<uuid>"|"unassign"|"auto"}
    func assign(jobId: String, memberId: String) async {
        struct Body: Encodable { let memberId: String }
        let payload = memberId.isEmpty ? "unassign" : memberId
        do {
            let updated: DispatchJob = try await api.put(
                "/api/vendor-os/jobs/\(jobId)/assign",
                body: Body(memberId: payload)
            )
            upsertTodayJob(updated, replacing: jobId)
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// GET /api/vendor-os/listings?q=&job_id=
    func searchListings(q: String, jobId: String) async {
        let query = q.trimmingCharacters(in: .whitespacesAndNewlines)
        if query.isEmpty {
            listingHits = []
            return
        }
        do {
            struct ListingsResponse: Decodable { let items: [OfficeListing] }
            let res: ListingsResponse = try await api.get("/api/vendor-os/listings", query: [
                URLQueryItem(name: "q", value: query),
                URLQueryItem(name: "job_id", value: jobId),
            ])
            listingHits = res.items.filter { !$0.id.isEmpty }
        } catch {
            listingHits = []
            self.error = error.localizedDescription
        }
    }

    /// PATCH /api/vendor-os/jobs/{id}/listing  {"transaction_id": uuid|null}
    func attachListing(jobId: String, transactionId: String?) async {
        struct Body: Encodable {
            let transactionId: String?
            func encode(to encoder: Encoder) throws {
                var c = encoder.container(keyedBy: CodingKeys.self)
                try c.encode(transactionId, forKey: .transactionId)
            }
            enum CodingKeys: String, CodingKey { case transactionId }
        }
        do {
            let updated: DispatchJob = try await api.patch(
                "/api/vendor-os/jobs/\(jobId)/listing",
                body: Body(transactionId: transactionId)
            )
            upsertTodayJob(updated, replacing: jobId, listingPatch: true)
            listingHits = []
        } catch {
            self.error = error.localizedDescription
        }
    }

    // MARK: — Offer accept / decline (called from JobOfferCard)

    func acceptOffer(_ job: DispatchJob) async {
        pendingOffer = nil
        await markInProgress(job)
    }

    func declineOffer(_ job: DispatchJob) async {
        pendingOffer = nil
        await markDeclined(job)
    }

    /// Called when a "job_assigned" push arrives. Surfaces the offer card.
    func receivePushOffer(jobId: String) async {
        // If the job is already in todayJobs use it, otherwise reload
        if let job = todayJobs.first(where: { $0.id == jobId }) {
            pendingOffer = job
        } else {
            await loadTodayJobs()
            pendingOffer = todayJobs.first(where: { $0.id == jobId })
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
        switch WatchAction(rawValue: action) {
        case .accept:    await markInProgress(job)
        case .decline:   await markDeclined(job)
        case .onWay:     await updateStatus(job, to: .inProgress)
        case .arrived:   break  // no status change — just GPS confirmation
        case nil:        break
        }
    }

    // MARK: — Private

    /// Keep the returned job in todayJobs after assign/attach. Never drop it.
    private func upsertTodayJob(_ incoming: DispatchJob, replacing id: String, listingPatch: Bool = false) {
        let merged: DispatchJob
        if let existing = todayJobs.first(where: { $0.id == id }) {
            var next = existing.applyingAssignment(from: incoming)
            if incoming.transactionId != nil || incoming.listingAddress != nil || incoming.id == id {
                next = next.applyingListing(
                    transactionId: incoming.transactionId,
                    listingAddress: incoming.listingAddress
                )
            }
            // Prefer the decoded incoming job when it has a real id; fill blanks from existing.
            merged = incoming.id.isEmpty ? next : fillBlanks(incoming, from: existing, listingPatch: listingPatch)
        } else {
            merged = incoming
        }
        if let idx = todayJobs.firstIndex(where: { $0.id == id || $0.id == merged.id }) {
            todayJobs[idx] = merged
        } else {
            todayJobs.append(merged)
            todayJobs.sort { ($0.scheduledDateTime ?? .distantFuture) < ($1.scheduledDateTime ?? .distantFuture) }
        }
        syncToWatch()
        syncToWidget()
    }

    /// Assign/listing responses are often a partial job row — keep Today fields we already have.
    private func fillBlanks(_ incoming: DispatchJob, from existing: DispatchJob, listingPatch: Bool = false) -> DispatchJob {
        DispatchJob(
            id: incoming.id.isEmpty ? existing.id : incoming.id,
            bookingId: incoming.bookingId.isEmpty ? existing.bookingId : incoming.bookingId,
            address: incoming.address.isEmpty ? existing.address : incoming.address,
            city: incoming.city.isEmpty ? existing.city : incoming.city,
            state: incoming.state.isEmpty ? existing.state : incoming.state,
            zipCode: incoming.zipCode.isEmpty ? existing.zipCode : incoming.zipCode,
            lat: incoming.lat == 0 ? existing.lat : incoming.lat,
            lng: incoming.lng == 0 ? existing.lng : incoming.lng,
            timeWindowStart: incoming.timeWindowStart.isEmpty ? existing.timeWindowStart : incoming.timeWindowStart,
            timeWindowEnd: incoming.timeWindowEnd.isEmpty ? existing.timeWindowEnd : incoming.timeWindowEnd,
            preferredWindow: {
                let p = (incoming.preferredWindow ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                return p.isEmpty ? existing.preferredWindow : incoming.preferredWindow
            }(),
            scheduledDate: incoming.scheduledDate.isEmpty ? existing.scheduledDate : incoming.scheduledDate,
            jobType: incoming.jobType.isEmpty ? existing.jobType : incoming.jobType,
            status: incoming.status,
            packageName: incoming.packageName ?? existing.packageName,
            packagePrice: incoming.packagePrice ?? existing.packagePrice,
            vendorPayout: incoming.vendorPayout ?? existing.vendorPayout,
            agentName: incoming.agentName ?? existing.agentName,
            agentPhone: incoming.agentPhone ?? existing.agentPhone,
            agentEmail: incoming.agentEmail ?? existing.agentEmail,
            clientInstructions: incoming.clientInstructions ?? existing.clientInstructions,
            checklist: incoming.checklist.isEmpty ? existing.checklist : incoming.checklist,
            complexityScore: incoming.complexityScore ?? existing.complexityScore,
            createdAt: incoming.createdAt.isEmpty ? existing.createdAt : incoming.createdAt,
            updatedAt: incoming.updatedAt.isEmpty ? existing.updatedAt : incoming.updatedAt,
            assigneeId: incoming.assigneeId,
            assigneeName: incoming.assigneeName ?? existing.assigneeName,
            transactionId: listingPatch ? incoming.transactionId : (incoming.transactionId ?? existing.transactionId),
            listingAddress: listingPatch ? incoming.listingAddress : (incoming.listingAddress ?? existing.listingAddress)
        )
    }

    private func refreshAdmin() async {
        struct Me: Decodable {
            let id: String?
            let role: String?
            let title: String?
        }
        if let me: Me = try? await api.get("/api/vendor-os/profile") {
            isAdmin = VendorRole.isVendorAdmin(id: me.id, role: me.role, title: me.title)
        }
    }

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
        let d = UserDefaults(suiteName: BricksAppGroup.suiteName)
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
