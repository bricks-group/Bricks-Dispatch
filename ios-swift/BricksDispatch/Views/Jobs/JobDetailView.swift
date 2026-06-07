import SwiftUI
import MapKit
import BricksCore

struct JobDetailView: View {
    let job: DispatchJob
    @StateObject private var store = JobStore.shared
    @State private var cameraPosition: MapCameraPosition
    @State private var showNavigation = false

    init(job: DispatchJob) {
        self.job = job
        let coord = CLLocationCoordinate2D(latitude: job.lat, longitude: job.lng)
        self._cameraPosition = State(initialValue: .region(
            MKCoordinateRegion(center: coord, latitudinalMeters: 500, longitudinalMeters: 500)
        ))
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                // MapKit preview — native, turn-by-turn capable
                Map(position: $cameraPosition) {
                    Annotation(job.address, coordinate: CLLocationCoordinate2D(latitude: job.lat, longitude: job.lng)) {
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
                        Text(job.address)
                            .font(.title3.bold())
                        Text(job.city + (job.state.isEmpty ? "" : ", \(job.state)"))
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }

                    // Status + payout row
                    HStack {
                        StatusPill(status: job.status)
                        Spacer()
                        VStack(alignment: .trailing, spacing: 2) {
                            Text(job.formattedPayout)
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

                    // Agent contact
                    if let name = job.agentName {
                        agentSection(name: name, phone: job.agentPhone)
                    }

                    // Checklist
                    if !job.checklist.isEmpty {
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
    }

    private var detailsGrid: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
            DetailCell(label: "Package", value: job.packageName ?? job.jobType.capitalized)
            DetailCell(label: "Scheduled", value: scheduledLabel)
            DetailCell(label: "Type", value: job.jobType.capitalized)
            if let instructions = job.clientInstructions, !instructions.isEmpty {
                DetailCell(label: "Notes", value: instructions)
            }
        }
    }

    private var scheduledLabel: String {
        guard let dt = job.scheduledDateTime else { return "\(job.scheduledDate) \(job.timeWindowStart)" }
        let fmt = DateFormatter()
        fmt.dateStyle = .short
        fmt.timeStyle = .short
        return fmt.string(from: dt)
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
                            .clipShape(Circle())
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
            ForEach(job.checklist) { item in
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
        switch job.status {
        case .assigned:
            VStack(spacing: 10) {
                // In-app turn-by-turn navigation
                Button { showNavigation = true } label: {
                    Label("Navigate to Property", systemImage: "location.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(BricksPrimaryButton())
                .fullScreenCover(isPresented: $showNavigation) {
                    JobNavigationView(job: job)
                }

                Button {
                    Task { await store.markInProgress(job) }
                } label: {
                    Label("I'm on my way", systemImage: "car.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(BricksSecondaryButton())
            }

        case .inProgress:
            Button {
                Task { await store.markCompleted(job) }
            } label: {
                Label("Mark shoot complete", systemImage: "checkmark.circle.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(BricksPrimaryButton())

        default:
            EmptyView()
        }
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
            .clipShape(Capsule())
    }

    private var label: String {
        switch status {
        case .assigned: return "UPCOMING"
        case .inProgress: return "IN PROGRESS"
        case .completed: return "COMPLETED"
        case .delivered: return "DELIVERED"
        case .declined: return "DECLINED"
        default: return status.rawValue.uppercased()
        }
    }

    private var color: Color {
        switch status {
        case .inProgress: return .green
        case .assigned: return BricksColors.brick
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
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct BricksPrimaryButton: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .padding(.vertical, 14)
            .background(BricksColors.brick.opacity(configuration.isPressed ? 0.85 : 1))
            .foregroundStyle(.white)
            .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct BricksSecondaryButton: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.medium))
            .padding(.vertical, 14)
            .background(Color(.secondarySystemBackground))
            .foregroundStyle(.primary)
            .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}
