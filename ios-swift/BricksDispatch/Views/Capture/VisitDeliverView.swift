import SwiftUI
import UniformTypeIdentifiers
import BricksCore

/// Deliver Virtual Visit onto the listing file — mirrors GO web VisitCapturePage
/// and Expo `/(capture)/visit`. Not a shutter mode: Polycam URL or .ply/.splat/.spz.
struct VisitDeliverView: View {
    let job: DispatchJob
    @Environment(\.dismiss) private var dismiss

    @State private var urlText = ""
    @State private var pickedName: String?
    @State private var pickedData: Data?
    @State private var busy = false
    @State private var error: String?
    @State private var done: String?
    @State private var showImporter = false

    private var canSubmit: Bool {
        guard !busy else { return false }
        if pickedData != nil { return true }
        return urlText.trimmingCharacters(in: .whitespacesAndNewlines).count > 8
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Text("VIRTUAL VISIT")
                        .font(.system(size: 11, weight: .semibold, design: .monospaced))
                        .tracking(2)
                        .foregroundStyle(BricksColors.brick)

                    Text("Deliver Virtual Visit")
                        .font(.title2.weight(.semibold))

                    Text("Free-roam splat of the space — not the path-confined 3D tour. Paste a Polycam share URL, or upload a .sog / .spz / .ply / .splat. Owned phone-video Visit capture is next.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)

                    VStack(alignment: .leading, spacing: 6) {
                        Text("POLYCAM / SHARE URL")
                            .font(.system(size: 10, weight: .medium, design: .monospaced))
                            .tracking(1.2)
                            .foregroundStyle(.secondary)
                        TextField("https://poly.cam/…", text: $urlText)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .keyboardType(.URL)
                            .padding(12)
                            .background(Color(.secondarySystemBackground))
                            .overlay(RoundedRectangle(cornerRadius: 2).stroke(Color(.separator), lineWidth: 1))
                            .clipShape(RoundedRectangle(cornerRadius: 2))
                            .disabled(busy || pickedData != nil)
                            .onChange(of: urlText) { _, next in
                                if !next.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                                    pickedData = nil
                                    pickedName = nil
                                }
                            }
                    }

                    Text("or")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity)

                    VStack(alignment: .leading, spacing: 8) {
                        Text(".PLY / .SPLAT / .SPZ FILE")
                            .font(.system(size: 10, weight: .medium, design: .monospaced))
                            .tracking(1.2)
                            .foregroundStyle(.secondary)
                        Button {
                            showImporter = true
                        } label: {
                            HStack {
                                Image(systemName: "doc.badge.plus")
                                Text(pickedName ?? "Choose visit file")
                                    .lineLimit(1)
                                Spacer()
                            }
                            .padding(12)
                            .frame(maxWidth: .infinity)
                            .background(Color(.secondarySystemBackground))
                            .overlay(RoundedRectangle(cornerRadius: 2).stroke(Color(.separator), lineWidth: 1))
                            .clipShape(RoundedRectangle(cornerRadius: 2))
                        }
                        .buttonStyle(.plain)
                        .disabled(busy || !urlText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                        if pickedName != nil {
                            Button("Clear file") {
                                pickedData = nil
                                pickedName = nil
                            }
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(BricksColors.brick)
                            .disabled(busy)
                        }
                    }

                    if let error {
                        Text(error)
                            .font(.subheadline)
                            .foregroundStyle(BricksColors.bad)
                    }
                    if let done {
                        Text(done)
                            .font(.subheadline)
                            .foregroundStyle(BricksColors.ok)
                    }

                    Button {
                        Task { await deliver() }
                    } label: {
                        Group {
                            if busy {
                                ProgressView()
                                    .tint(.white)
                            } else {
                                Text("Deliver to listing")
                                    .fontWeight(.semibold)
                            }
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                    }
                    .buttonStyle(BricksPrimaryButton())
                    .disabled(!canSubmit)
                    .opacity(canSubmit ? 1 : 0.45)
                }
                .padding(22)
            }
            .background(Color(.systemBackground))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { dismiss() } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 14, weight: .semibold))
                    }
                }
            }
            .fileImporter(
                isPresented: $showImporter,
                allowedContentTypes: Self.visitTypes,
                allowsMultipleSelection: false
            ) { result in
                switch result {
                case .failure(let err):
                    error = err.localizedDescription
                case .success(let urls):
                    guard let fileURL = urls.first else { return }
                    ingestFile(fileURL)
                }
            }
        }
    }

    private static var visitTypes: [UTType] {
        var types: [UTType] = [.data, .item]
        if let ply = UTType(filenameExtension: "ply") { types.insert(ply, at: 0) }
        if let splat = UTType(filenameExtension: "splat") { types.insert(splat, at: 0) }
        if let spz = UTType(filenameExtension: "spz") { types.insert(spz, at: 0) }
        if let sog = UTType(filenameExtension: "sog") { types.insert(sog, at: 0) }
        return types
    }

    private func ingestFile(_ fileURL: URL) {
        let name = fileURL.lastPathComponent
        let ext = fileURL.pathExtension.lowercased()
        guard ["ply", "splat", "spz", "sog"].contains(ext) else {
            error = "Only .sog, .spz, .ply, or .splat files are supported"
            return
        }
        let accessed = fileURL.startAccessingSecurityScopedResource()
        defer { if accessed { fileURL.stopAccessingSecurityScopedResource() } }
        do {
            let data = try Data(contentsOf: fileURL)
            guard !data.isEmpty else {
                error = "File is empty"
                return
            }
            pickedData = data
            pickedName = name
            urlText = ""
            error = nil
            done = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func deliver() async {
        busy = true
        error = nil
        done = nil
        defer { busy = false }

        let result: CaptureAPIClient.VisitDeliverResult
        if let data = pickedData, let name = pickedName {
            result = await CaptureAPIClient.shared.uploadAndDeliverVisitFile(
                jobId: job.id,
                filename: name,
                fileData: data
            )
        } else {
            result = await CaptureAPIClient.shared.deliverVisit(
                jobId: job.id,
                polycamURL: urlText
            )
        }

        if result.ok {
            done = pickedData != nil
                ? "Virtual Visit file is on the listing."
                : "Virtual Visit link is on the listing."
            if pickedData != nil {
                pickedData = nil
                pickedName = nil
            } else {
                urlText = ""
            }
        } else {
            error = result.error ?? "Could not deliver visit"
        }
    }
}
