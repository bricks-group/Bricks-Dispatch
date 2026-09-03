import SwiftUI
import BricksCore

/// Honest 3D-tour stub. Native shutter is not wired here.
/// Owned Gaussian splat capture runs in GO Expo: Start Capture → 3D tour.
struct SplatCaptureStubView: View {
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
                Text("3D TOUR")
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
                Text("Native splat shutter is not wired in this build.")
                    .font(.subheadline.weight(.medium))
                Text("Path-confined 3D tour frames run in GO (Expo) — Start Capture → 3D tour. Not a virtual visit. Professional HDR stills stay on the vendor camera.")
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
