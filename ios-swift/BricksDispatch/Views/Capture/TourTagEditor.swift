import SwiftUI
import BricksCore

/// Amenity / feature tags on a 3D tour. Same capture tags API as Expo and GO web.
/// Phone types yaw/pitch — no 360 look-strip drag placer (that lives on GO web).
struct TourTagEditor: View {
    let jobId: String
    let session: GOCaptureSession
    var onSession: (GOCaptureSession) -> Void

    @State private var stationIndex: Int = 0
    @State private var label = ""
    @State private var note = ""
    @State private var yawText = "0"
    @State private var pitchText = "0"
    @State private var busy = false
    @State private var msg: String?

    private var sessionId: String { session.sessionId ?? "" }
    private var stations: [CaptureStationInfo] { session.stations }
    private var tags: [TourTag] { session.allTags }

    private var stationOptions: [CaptureStationInfo] {
        if stations.isEmpty {
            return [CaptureStationInfo(
                stationIndex: 0, room: "Station 0", panCount: 0,
                hasEquirect: false, heading: nil, contentType: nil
            )]
        }
        return stations
    }

    var body: some View {
        if sessionId.isEmpty {
            EmptyView()
        } else {
            VStack(alignment: .leading, spacing: 8) {
                Text("Feature tags")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(Color(hex: "#14110E"))

                Text("Pick a station, set look (yaw / pitch), then add a short label. Same API as web — tags compile into the listing tour player. Phone does not drag a 360 look strip; type degrees (yaw −180…180, pitch −80…80).")
                    .font(.system(size: 12))
                    .foregroundStyle(Color(hex: "#6B6054"))
                    .fixedSize(horizontal: false, vertical: true)

                fieldLabel("Station")
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(stationOptions, id: \.stationIndex) { st in
                            let active = st.stationIndex == stationIndex
                            Button {
                                stationIndex = st.stationIndex
                            } label: {
                                Text(stationLabel(st.stationIndex))
                                    .font(.system(size: 12, weight: active ? .semibold : .regular))
                                    .foregroundStyle(Color(hex: active ? "#14110E" : "#3B332A"))
                                    .padding(.horizontal, 12)
                                    .padding(.vertical, 8)
                                    .overlay(
                                        RoundedRectangle(cornerRadius: 2)
                                            .stroke(active ? CaptureChrome.brick : Color(hex: "#14111020"), lineWidth: 1)
                                    )
                                    .background(active ? CaptureChrome.brick.opacity(0.12) : Color.clear)
                            }
                            .buttonStyle(.plain)
                            .disabled(busy)
                        }
                    }
                }

                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 4) {
                        fieldLabel("Yaw °")
                        TextField("0", text: $yawText)
                            .keyboardType(.numbersAndPunctuation)
                            .textFieldStyle(TagFieldStyle())
                            .disabled(busy)
                    }
                    VStack(alignment: .leading, spacing: 4) {
                        fieldLabel("Pitch °")
                        TextField("0", text: $pitchText)
                            .keyboardType(.numbersAndPunctuation)
                            .textFieldStyle(TagFieldStyle())
                            .disabled(busy)
                    }
                }

                fieldLabel("Label")
                TextField("Walk-in closet", text: $label)
                    .textFieldStyle(TagFieldStyle())
                    .disabled(busy)
                    .onChange(of: label) { _, v in
                        if v.count > 80 { label = String(v.prefix(80)) }
                    }

                fieldLabel("Note (optional)")
                TextField("Cedar lining", text: $note)
                    .textFieldStyle(TagFieldStyle())
                    .disabled(busy)
                    .onChange(of: note) { _, v in
                        if v.count > 240 { note = String(v.prefix(240)) }
                    }

                Button {
                    Task { await addTag() }
                } label: {
                    Text(busy ? "Saving…" : "Add tag")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)
                        .background(CaptureChrome.brick.opacity(busy || label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? 0.5 : 1))
                        .clipShape(RoundedRectangle(cornerRadius: 2))
                }
                .buttonStyle(.plain)
                .disabled(busy || label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .padding(.top, 6)

                if let msg {
                    Text(msg)
                        .font(.system(size: 12))
                        .foregroundStyle(msgOk(msg) ? BricksColors.ok : BricksColors.bad)
                }

                Text(tags.isEmpty
                     ? "No feature tags yet — none are invented."
                     : "\(tags.count) tag\(tags.count == 1 ? "" : "s") on this tour")
                    .font(BricksFont.mono(11))
                    .foregroundStyle(Color(hex: "#6B6054"))
                    .padding(.top, 6)

                ForEach(tags) { t in
                    HStack(alignment: .center, spacing: 10) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(t.note.map { "\(t.label) — \($0)" } ?? t.label)
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundStyle(Color(hex: "#14110E"))
                            Text("\(stationLabel(t.stationIndex)) · \(Int(t.yaw.rounded()))° / \(Int(t.pitch.rounded()))°")
                                .font(BricksFont.mono(11))
                                .foregroundStyle(Color(hex: "#6B6054"))
                        }
                        Spacer()
                        Button {
                            Task { await deleteTag(t.id) }
                        } label: {
                            Text("Remove")
                                .font(.system(size: 12))
                                .foregroundStyle(Color(hex: "#3B332A"))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 8)
                                .overlay(
                                    RoundedRectangle(cornerRadius: 2)
                                        .stroke(Color(hex: "#14111020"), lineWidth: 1)
                                )
                        }
                        .buttonStyle(.plain)
                        .disabled(busy)
                    }
                    .padding(.vertical, 10)
                    .overlay(alignment: .top) {
                        Rectangle().fill(Color(hex: "#14111020")).frame(height: 1)
                    }
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(hex: "#F4F1EB"))
            .overlay(
                RoundedRectangle(cornerRadius: 2)
                    .stroke(Color(hex: "#14111020"), lineWidth: 1)
            )
            .onAppear {
                if let first = stations.first { stationIndex = first.stationIndex }
            }
        }
    }

    private func fieldLabel(_ text: String) -> some View {
        Text(text.uppercased())
            .font(BricksFont.mono(10))
            .tracking(1.5)
            .foregroundStyle(Color(hex: "#6B6054"))
            .padding(.top, 4)
    }

    private func stationLabel(_ idx: Int) -> String {
        if let st = stations.first(where: { $0.stationIndex == idx }), let room = st.room, !room.isEmpty {
            return "\(room) · \(idx)"
        }
        return "Station \(idx)"
    }

    private func msgOk(_ m: String) -> Bool {
        m.hasPrefix("Tag on") || m.hasPrefix("Tag saved")
    }

    private func parseDeg(_ raw: String, fallback: Double = 0) -> Double {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if let n = Double(trimmed), n.isFinite { return n }
        return fallback
    }

    private func addTag() async {
        let lab = label.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !lab.isEmpty else {
            msg = "Tag needs a label — e.g. Walk-in closet. Empty amenities are not invented."
            return
        }
        guard !sessionId.isEmpty else {
            msg = "No capture session"
            return
        }
        let yaw = TourTag.wrapYaw(parseDeg(yawText, fallback: 0))
        let pitch = TourTag.clampPitch(parseDeg(pitchText, fallback: 0))
        busy = true
        msg = nil
        let result = await CaptureAPIClient.shared.addTourTag(
            jobId: jobId,
            sessionId: sessionId,
            stationIndex: stationIndex,
            label: lab,
            note: note,
            yaw: yaw,
            pitch: pitch
        )
        busy = false
        if let s = result.session { onSession(s) }
        if !result.ok {
            msg = result.error ?? "Could not add tag"
            return
        }
        label = ""
        note = ""
        if result.rebuilt == false {
            msg = "Tag saved. Rebuild the tour player so the listing iframe picks it up."
        } else {
            msg = "Tag on the tour. Hard-refresh the listing iframe to see it."
        }
    }

    private func deleteTag(_ tagId: String) async {
        guard !sessionId.isEmpty else { return }
        busy = true
        msg = nil
        let result = await CaptureAPIClient.shared.deleteTourTag(jobId: jobId, sessionId: sessionId, tagId: tagId)
        busy = false
        if let s = result.session { onSession(s) }
        if !result.ok { msg = result.error ?? "Could not remove tag" }
    }
}

private struct TagFieldStyle: TextFieldStyle {
    func _body(configuration: TextField<Self._Label>) -> some View {
        configuration
            .font(.system(size: 14))
            .foregroundStyle(Color(hex: "#14110E"))
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(Color(hex: "#FAF8F4"))
            .overlay(
                RoundedRectangle(cornerRadius: 2)
                    .stroke(Color(hex: "#14111020"), lineWidth: 1)
            )
    }
}
