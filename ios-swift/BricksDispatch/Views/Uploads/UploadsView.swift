import SwiftUI
import PhotosUI
import BricksCore

struct UploadsView: View {
    @StateObject private var store = UploadsStore()
    @State private var pickerItems: [PhotosPickerItem] = []

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    // Subtitle
                    Text("Drop the card — we auto-sort by job.")
                        .font(BricksFont.mono(12))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 20)
                        .padding(.top, 4)

                    // Drop zone
                    dropZone
                        .padding(.horizontal, 20)

                    // In Progress
                    if !store.inProgressGroups.isEmpty {
                        uploadSection(label: "IN PROGRESS") {
                            ForEach(store.inProgressGroups) { group in
                                ProgressCard(group: group)
                            }
                        }
                    }

                    // Recent
                    if !store.recentGroups.isEmpty {
                        uploadSection(label: "RECENT UPLOADS") {
                            VStack(spacing: 0) {
                                ForEach(Array(store.recentGroups.enumerated()), id: \.element.id) { idx, group in
                                    RecentRow(group: group, isLast: idx == store.recentGroups.count - 1)
                                }
                            }
                            .background(Color(.secondarySystemBackground))
                            .clipShape(RoundedRectangle(cornerRadius: 6))
                        }
                    }

                    if !store.isLoading && store.inProgressGroups.isEmpty && store.recentGroups.isEmpty {
                        VStack(spacing: 10) {
                            Image(systemName: "arrow.up.doc")
                                .font(.system(size: 40))
                                .foregroundStyle(Color(.tertiaryLabel))
                            Text("No uploads yet")
                                .font(.subheadline)
                                .foregroundStyle(Color(.secondaryLabel))
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.top, 40)
                    }

                    Spacer(minLength: 100)
                }
                .padding(.top, 8)
            }
            .navigationTitle("Upload")
            .navigationBarTitleDisplayMode(.large)
            .refreshable { await store.loadData() }
            .onChange(of: pickerItems) { items in
                guard !items.isEmpty else { return }
                Task {
                    await store.uploadPickerItems(items)
                    pickerItems = []
                }
            }
        }
        .task { await store.loadData() }
    }

    // MARK: — Drop zone

    private var dropZone: some View {
        PhotosPicker(
            selection: $pickerItems,
            maxSelectionCount: 20,
            matching: .any(of: [.images, .videos])
        ) {
            VStack(spacing: 12) {
                if store.isUploading {
                    ProgressView()
                        .tint(BricksColors.brick)
                        .scaleEffect(1.2)
                } else {
                    Image(systemName: "arrow.up.circle")
                        .font(.system(size: 34))
                        .foregroundStyle(BricksColors.brick)
                    Text("Tap to select files")
                        .font(.system(size: 15, weight: .medium))
                    Text("RAW · JPEG · MP4 · up to 20 GB per job")
                        .font(BricksFont.mono(11))
                        .foregroundStyle(.secondary)
                    Text("CHOOSE FILES")
                        .font(BricksFont.monoMedium(11))
                        .tracking(1.2)
                        .foregroundStyle(.white)
                        .padding(.horizontal, 20)
                        .padding(.vertical, 10)
                        .background(BricksColors.brick)
                        .clipShape(RoundedRectangle(cornerRadius: 2))
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 36)
            .overlay(
                RoundedRectangle(cornerRadius: 4)
                    .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6]))
                    .foregroundStyle(Color(.separator))
            )
        }
        .buttonStyle(.plain)
        .disabled(store.isUploading || store.activeJob == nil)
    }

    @ViewBuilder
    private func uploadSection<Content: View>(label: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(label)
                .font(BricksFont.monoMedium(9))
                .tracking(1.8)
                .foregroundStyle(.secondary)
                .padding(.horizontal, 20)
            content()
                .padding(.horizontal, 20)
        }
    }
}

// MARK: — Progress card (in-progress job)

private struct ProgressCard: View {
    let group: UploadGroup

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(group.address.lowercased())
                        .font(.system(size: 14, weight: .medium))
                    Text("\(group.uploads.count) FILES · \(fmtBytes(group.totalBytes))")
                        .font(BricksFont.mono(11))
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if let pct = group.uploadPct {
                    Text("\(pct)%")
                        .font(BricksFont.mono(16))
                        .foregroundStyle(BricksColors.brick)
                }
            }
            if let pct = group.uploadPct {
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        RoundedRectangle(cornerRadius: 2)
                            .fill(Color(.separator))
                            .frame(height: 3)
                        RoundedRectangle(cornerRadius: 2)
                            .fill(BricksColors.brick)
                            .frame(width: geo.size.width * CGFloat(pct) / 100, height: 3)
                    }
                }
                .frame(height: 3)
            }
        }
        .padding(16)
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }
}

// MARK: — Recent row

private struct RecentRow: View {
    let group: UploadGroup
    let isLast: Bool

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "doc.fill")
                .font(.system(size: 16))
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 3) {
                Text(group.address.lowercased())
                    .font(.system(size: 13, weight: .medium))
                Text("\(group.uploads.count) files · \(fmtBytes(group.totalBytes))")
                    .font(BricksFont.mono(11))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            statusPill(for: group.status)
        }
        .padding(14)
        .overlay(
            isLast ? nil :
            Rectangle()
                .fill(Color(.separator))
                .frame(height: 1)
                .frame(maxWidth: .infinity)
                .padding(.leading, 14),
            alignment: .bottom
        )
    }

    @ViewBuilder
    private func statusPill(for status: JobStatus) -> some View {
        let (label, bg, fg): (String, Color, Color) = {
            switch status {
            case .completed, .delivered: return ("Delivered", .green.opacity(0.15), .green)
            case .inProgress: return ("In Progress", BricksColors.brick.opacity(0.15), BricksColors.brick)
            default: return ("Scheduled", Color(.tertiarySystemBackground), Color(.secondaryLabel))
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

// MARK: — Models

struct UploadRecord: Identifiable, Decodable {
    let id: String
    let jobId: String
    let filename: String
    let fileSize: Int
    let mimeType: String
    let createdAt: String
}

struct UploadJob: Identifiable, Decodable {
    let id: String
    let address: String
    var status: JobStatus
    let deliverablesCount: Int
    let uploadedCount: Int
}

struct UploadGroup: Identifiable {
    let id: String
    let address: String
    let status: JobStatus
    let uploads: [UploadRecord]

    var totalBytes: Int { uploads.reduce(0) { $0 + $1.fileSize } }

    var uploadPct: Int? {
        guard status == .inProgress else { return nil }
        return nil // populated if API returns deliverables_count
    }
}

// MARK: — Store

@MainActor
final class UploadsStore: ObservableObject {
    @Published var inProgressGroups: [UploadGroup] = []
    @Published var recentGroups: [UploadGroup] = []
    @Published var isLoading = false
    @Published var isUploading = false
    @Published var activeJob: UploadJob?

    private let api = BricksAPIClient.shared

    func loadData() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let (uploads, jobs): ([UploadRecord], [UploadJob]) = try await (
                api.get("/api/uploads"),
                api.get("/api/jobs")
            )
            activeJob = jobs.first { $0.status == .inProgress }
            let jobMap = Dictionary(uniqueKeysWithValues: jobs.map { ($0.id, $0) })
            var groupMap: [String: (job: UploadJob, uploads: [UploadRecord])] = [:]
            for u in uploads {
                guard let job = jobMap[u.jobId] else { continue }
                groupMap[u.jobId, default: (job, [])].uploads.append(u)
            }
            let groups = groupMap.values.map {
                UploadGroup(id: $0.job.id, address: $0.job.address, status: $0.job.status, uploads: $0.uploads)
            }
            inProgressGroups = groups.filter { $0.status == .inProgress }
            recentGroups = groups.filter { $0.status != .inProgress }
                .sorted { $0.uploads.first?.createdAt ?? "" > $1.uploads.first?.createdAt ?? "" }
        } catch {}
    }

    func uploadPickerItems(_ items: [PhotosPickerItem]) async {
        guard let job = activeJob else { return }
        isUploading = true
        defer { isUploading = false }
        for item in items {
            guard let data = try? await item.loadTransferable(type: Data.self) else { continue }
            let filename = "upload_\(Int(Date().timeIntervalSince1970)).jpg"
            struct UploadBody: Encodable {
                let jobId: String; let filename: String
                let fileSize: Int; let mimeType: String; let fileData: String
            }
            let body = UploadBody(
                jobId: job.id, filename: filename,
                fileSize: data.count, mimeType: "image/jpeg",
                fileData: data.base64EncodedString()
            )
            _ = try? await api.post("/api/uploads", body: body) as EmptyResponse
        }
        await loadData()
    }
}

private struct EmptyResponse: Decodable {}

private func fmtBytes(_ bytes: Int) -> String {
    if bytes == 0 { return "—" }
    let gb = Double(bytes) / 1_073_741_824
    if gb >= 1 { return String(format: "%.1f GB", gb) }
    let mb = Double(bytes) / 1_048_576
    return String(format: "%.1f MB", mb)
}
