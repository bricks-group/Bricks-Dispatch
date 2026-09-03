import SwiftUI
import MapKit
import BricksCore

struct JobDetailView: View {
    let job: DispatchJob
    @StateObject private var store = JobStore.shared
    @State private var cameraPosition: MapCameraPosition
    @State private var showNavigation = false
    @State private var pick = ""
    @State private var listingQuery = ""
    @State private var assigning = false
    @State private var listingBusy = false
    @State private var showFloorPlanCapture = false
    @State private var showSplatCapture = false

    init(job: DispatchJob) {
        self.job = job
        let coord = CLLocationCoordinate2D(latitude: job.lat, longitude: job.lng)
        self._cameraPosition = State(initialValue: .region(
            MKCoordinateRegion(center: coord, latitudinalMeters: 500, longitudinalMeters: 500)
        ))
        self._pick = State(initialValue: job.assigneeId ?? "")
    }

    private var live: DispatchJob {
        store.job(id: job.id) ?? job
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                // MapKit preview — native, turn-by-turn capable
                Map(position: $cameraPosition) {
                    Annotation(live.address, coordinate: CLLocationCoordinate2D(latitude: live.lat, longitude: live.lng)) {
                        ZStack {
                            Circle().fill(BricksColors.brick).frame(width: 32, height: 32)
                            Image(systemName: "camera.fill").foregroundStyle(.white).font(.caption.bold())
                        }
                    }
                }
                .frame(height: 220)
                .clipShape(RoundedRectangle(cornerRadius: 0))

                VStack(alignment: .leading, spacing: 20) {
                    // Header
                    VStack(alignment: .leading, spacing: 4) {
                        Text(live.address)
                            .font(.title3.bold())
                        Text(live.city + (live.state.isEmpty ? "" : ", \(live.state)"))
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }

                    // Status + payout row
                    HStack {
                        StatusPill(status: live.status)
                        Spacer()
                        VStack(alignment: .trailing, spacing: 2) {
                            Text(live.formattedPayout)
                                .font(.title3.monospacedDigit().bold())
                                .foregroundStyle(BricksColors.brick)
                            Text("your payout")
                                .font(.caption2)
                                .foregroundStyle(.tertiary)
                        }
                    }

                    Divider()

                    // Details grid
                    detailsGrid

                    Divider()

                    assignedSection

                    Divider()

                    officeListingSection

                    // Agent contact
                    if let name = live.agentName {
                        agentSection(name: name, phone: live.agentPhone)
                    }

                    // Checklist
                    if !live.checklist.isEmpty {
                        checklistSection
                    }

                    // Action buttons
                    actionButtons
                }
                .padding(20)
            }
        }
        .navigationTitle("Job Detail")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            pick = live.assigneeId ?? ""
            await store.loadTeam()
        }
        .onChange(of: live.assigneeId) { _, new in
            pick = new ?? ""
        }
        .fullScreenCover(isPresented: $showFloorPlanCapture) {
            FloorPlanCaptureView(job: live)
        }
        .fullScreenCover(isPresented: $showSplatCapture) {
            TourCaptureView(job: live)
        }
    }

    private var detailsGrid: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
            DetailCell(label: "Package", value: live.packageName ?? live.jobType.capitalized)
            DetailCell(label: "Scheduled", value: scheduledLabel)
            DetailCell(label: "Type", value: live.jobType.capitalized)
            if let instructions = live.clientInstructions, !instructions.isEmpty {
                DetailCell(label: "Notes", value: instructions)
            }
        }
    }

    private var scheduledLabel: String {
        guard let dt = live.scheduledDateTime else { return "\(live.scheduledDate) \(live.timeWindowStart)" }
        let fmt = DateFormatter()
        fmt.dateStyle = .short
        fmt.timeStyle = .short
        return fmt.string(from: dt)
    }

    // MARK: — Assigned to (Owner picker / IC name only)

    private var assignedSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("ASSIGNED TO")
                .font(.caption.bold())
                .foregroundStyle(.secondary)
            Text(live.displayAssignee)
                .font(.subheadline.weight(.medium))

            if store.isAdmin {
                Text("Active photographers. Pending applicants are not listed.")
                    .font(.caption)
                    .foregroundStyle(.tertiary)

                pickerRow(id: "", title: "Unassigned", subtitle: nil)

                ForEach(store.pickers) { member in
                    pickerRow(id: member.id, title: member.name, subtitle: member.pickerLabel)
                }

                if let err = store.error, assigning {
                    Text(err).font(.caption).foregroundStyle(.red)
                }

                HStack(spacing: 8) {
                    Button {
                        Task {
                            assigning = true
                            await store.assign(jobId: live.id, memberId: pick)
                            assigning = false
                        }
                    } label: {
                        if assigning {
                            ProgressView().tint(.white)
                        } else {
                            Text(live.assigneeId == nil ? "Assign" : "Reassign")
                        }
                    }
                    .buttonStyle(SharpControlButton(filled: true))
                    .disabled(assigning)

                    Button {
                        Task {
                            assigning = true
                            await store.assign(jobId: live.id, memberId: "auto")
                            assigning = false
                        }
                    } label: {
                        Text("Auto")
                    }
                    .buttonStyle(SharpControlButton(filled: false))
                    .disabled(assigning)
                }
            }
        }
    }

    private func pickerRow(id: String, title: String, subtitle: String?) -> some View {
        let on = pick == id
        return Button {
            pick = id
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.primary)
                if let subtitle {
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 10)
            .padding(.vertical, 10)
            .background(on ? BricksColors.brick.opacity(0.08) : Color(.secondarySystemBackground))
            .overlay(
                RoundedRectangle(cornerRadius: 2)
                    .stroke(on ? BricksColors.brick : Color(.separator), lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 2))
        }
        .buttonStyle(.plain)
    }

    // MARK: — Office listing attach / detach

    private var officeListingSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("OFFICE")
                .font(.caption.bold())
                .foregroundStyle(.secondary)
            if let addr = live.listingAddress, !addr.isEmpty {
                Text(addr)
                    .font(.subheadline.weight(.medium))
            } else {
                Text("Not attached")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            TextField("Search office listings or paste id", text: $listingQuery)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(Color(.secondarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 2))
                .onSubmit { Task { await runSearch() } }

            HStack(spacing: 8) {
                Button {
                    Task { await runSearch() }
                } label: {
                    if listingBusy {
                        ProgressView().tint(.white)
                    } else {
                        Text("Search")
                    }
                }
                .buttonStyle(SharpControlButton(filled: true))
                .disabled(listingBusy)

                if live.transactionId != nil {
                    Button {
                        Task {
                            listingBusy = true
                            await store.attachListing(jobId: live.id, transactionId: nil)
                            listingBusy = false
                        }
                    } label: {
                        Text("Detach")
                    }
                    .buttonStyle(SharpControlButton(filled: false))
                    .disabled(listingBusy)
                }
            }

            ForEach(store.listingHits) { hit in
                Button {
                    Task {
                        listingBusy = true
                        await store.attachListing(jobId: live.id, transactionId: hit.id)
                        listingQuery = ""
                        listingBusy = false
                    }
                } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(hit.address)
                            .font(.subheadline.weight(.medium))
                            .foregroundStyle(.primary)
                        Text(hit.subtitle)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 10)
                }
                .buttonStyle(.plain)
                .disabled(listingBusy)
            }
        }
    }

    private func runSearch() async {
        listingBusy = true
        await store.searchListings(q: listingQuery, jobId: live.id)
        listingBusy = false
    }

    private func agentSection(name: String, phone: String?) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("AGENT")
                .font(.caption.bold())
                .foregroundStyle(.secondary)
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(name).font(.subheadline.weight(.medium))
                    if let phone { Text(phone).font(.caption).foregroundStyle(.secondary) }
                }
                Spacer()
                if let phone {
                    Button {
                        guard let url = URL(string: "tel://\(phone.filter { $0.isNumber })") else { return }
                        UIApplication.shared.open(url)
                    } label: {
                        Image(systemName: "phone.fill")
                            .foregroundStyle(BricksColors.brick)
                            .padding(10)
                            .background(BricksColors.brick.opacity(0.12))
                            .clipShape(RoundedRectangle(cornerRadius: 2))
                    }
                }
            }
        }
    }

    private var checklistSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("CHECKLIST")
                .font(.caption.bold())
                .foregroundStyle(.secondary)
            ForEach(live.checklist) { item in
                HStack(spacing: 10) {
                    Image(systemName: item.completed ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(item.completed ? .green : Color(.tertiaryLabel))
                    Text(item.label)
                        .font(.subheadline)
                        .foregroundStyle(item.completed ? .secondary : .primary)
                }
            }
        }
    }

    @ViewBuilder
    private var actionButtons: some View {
        switch live.status {
        case .assigned:
            VStack(spacing: 10) {
                // In-app turn-by-turn navigation
                Button { showNavigation = true } label: {
                    Label("Navigate to Property", systemImage: "location.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(BricksPrimaryButton())
                .fullScreenCover(isPresented: $showNavigation) {
                    JobNavigationView(job: live)
                }

                Button {
                    Task { await store.markInProgress(live) }
                } label: {
                    Label("I'm on my way", systemImage: "car.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(BricksSecondaryButton())

                captureModeButtons
            }

        case .inProgress:
            VStack(spacing: 10) {
                Button {
                    Task { await store.markCompleted(live) }
                } label: {
                    Label("Mark shoot complete", systemImage: "checkmark.circle.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(BricksPrimaryButton())

                captureModeButtons
            }

        default:
            EmptyView()
        }
    }

    private var captureModeButtons: some View {
        let modes = live.captureModes
        return VStack(spacing: 10) {
            if modes.tour {
                Button { showSplatCapture = true } label: {
                    Label("3D tour capture", systemImage: "cube.transparent")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(BricksSecondaryButton())
            }
            if modes.floorplan {
                Button { showFloorPlanCapture = true } label: {
                    Label("Floor plan capture", systemImage: "square.split.bottomrightquarter")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(BricksSecondaryButton())
            }
            if !modes.hasPhoneMode {
                Text(modes.neitherCopy)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

/// 2px controls for assign / office listing — not Capsule pills.
struct SharpControlButton: ButtonStyle {
    var filled: Bool
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(
                filled
                    ? BricksColors.brick.opacity(configuration.isPressed ? 0.85 : 1)
                    : Color(.secondarySystemBackground)
            )
            .foregroundStyle(filled ? Color.white : Color.primary)
            .clipShape(RoundedRectangle(cornerRadius: 2))
    }
}

struct StatusPill: View {
    let status: JobStatus

    var body: some View {
        Text(label)
            .font(.caption.bold())
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(color.opacity(0.15))
            .foregroundStyle(color)
            .clipShape(RoundedRectangle(cornerRadius: 2))
    }

    private var label: String {
        switch status {
        case .assigned: return "UPCOMING"
        case .inProgress: return "IN PROGRESS"
        case .completed: return "COMPLETED"
        case .delivered: return "DELIVERED"
        case .declined: return "DECLINED"
        case .pendingAssignment: return "UNASSIGNED"
        default: return status.rawValue.uppercased()
        }
    }

    private var color: Color {
        switch status {
        case .inProgress: return .green
        case .assigned: return BricksColors.brick
        case .pendingAssignment, .pending: return .orange
        case .completed, .delivered: return .secondary
        default: return .secondary
        }
    }
}

struct DetailCell: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label.uppercased())
                .font(.caption2.bold())
                .foregroundStyle(.tertiary)
            Text(value)
                .font(.subheadline)
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 2))
    }
}

struct BricksPrimaryButton: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .padding(.vertical, 14)
            .background(BricksColors.brick.opacity(configuration.isPressed ? 0.85 : 1))
            .foregroundStyle(.white)
            .clipShape(RoundedRectangle(cornerRadius: 2))
    }
}

struct BricksSecondaryButton: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.medium))
            .padding(.vertical, 14)
            .background(Color(.secondarySystemBackground))
            .foregroundStyle(.primary)
            .clipShape(RoundedRectangle(cornerRadius: 2))
    }
}
