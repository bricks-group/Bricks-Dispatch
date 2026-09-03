import SwiftUI
import BricksCore

/// Honest RoomPlan stub. RoomPlan / LiDAR is not wired in this native build.
/// In-app floor-plan capture (corners + walls) runs in GO Expo:
/// Start Capture → Floor plan.
struct FloorPlanCaptureView: View {
    let job: DispatchJob
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Button { dismiss() } label: {
                    Image(systemName: "xmark")
                        .font(.body.weight(.semibold))
                        .foregroundStyle(.primary)
                        .frame(width: 34, height: 34)
                        .overlay(
                            RoundedRectangle(cornerRadius: 2)
                                .stroke(Color.primary.opacity(0.2), lineWidth: 1)
                        )
                }
                Spacer()
                Text("FLOOR PLAN")
                    .font(.caption.monospaced())
                    .tracking(1.6)
                    .foregroundStyle(BricksColors.brick)
                Spacer()
                Color.clear.frame(width: 34, height: 34)
            }
            .padding(.horizontal, 20)
            .padding(.top, 16)

            VStack(alignment: .leading, spacing: 14) {
                Text(job.address)
                    .font(.title3.weight(.semibold))
                Text("RoomPlan is not wired. LiDAR is not running.")
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.primary)
                Text("Capture corners and walls in GO (Expo) — Start Capture → Floor plan. Phone frames are tracing inputs, not HDR stills, and are not sent to BoxBrownie.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(22)

            Spacer()

            Button { dismiss() } label: {
                Text("Close")
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(BricksColors.brick)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 2))
            }
            .padding(22)
        }
        .background(Color(.systemBackground))
    }
}
