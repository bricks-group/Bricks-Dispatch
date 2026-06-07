import ActivityKit
import WidgetKit
import SwiftUI
import BricksCore

// MARK: — Widget declaration

struct JobLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: JobActivityAttributes.self) { context in
            JobLockScreenView(context: context)
                .activityBackgroundTint(Color.black)
                .activitySystemActionForegroundColor(.white)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    ExpandedLeading(context: context)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    ExpandedTrailing(context: context)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    ExpandedBottom(context: context)
                }
            } compactLeading: {
                Image(systemName: "camera.fill")
                    .foregroundStyle(Color("BricksGold"))
                    .font(.caption2.weight(.semibold))
            } compactTrailing: {
                Text(elapsedLabel(context.state.elapsedMinutes))
                    .font(.caption2.weight(.medium).monospacedDigit())
                    .foregroundStyle(.primary)
            } minimal: {
                Image(systemName: statusIcon(context.state.status))
                    .foregroundStyle(statusColor(context.state.status))
                    .font(.caption2.weight(.semibold))
            }
            .keylineTint(Color("BricksGold"))
        }
    }
}

// MARK: — Lock Screen

private struct JobLockScreenView: View {
    let context: ActivityViewContext<JobActivityAttributes>

    var body: some View {
        HStack(alignment: .center, spacing: 16) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    StatusBadge(status: context.state.status)
                    Spacer()
                    Text(context.attributes.packageName.capitalized)
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(.white.opacity(0.45))
                }

                Text(context.attributes.address)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)

                if let agent = context.state.agentName {
                    Text("Agent: \(agent)")
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.5))
                }
            }

            Spacer(minLength: 0)

            VStack(spacing: 2) {
                Image(systemName: "camera.fill")
                    .font(.title3)
                    .foregroundStyle(Color("BricksGold"))
                Text(elapsedLabel(context.state.elapsedMinutes))
                    .font(.caption2.monospacedDigit().weight(.medium))
                    .foregroundStyle(.white.opacity(0.7))
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
    }
}

// MARK: — Dynamic Island expanded regions

private struct ExpandedLeading: View {
    let context: ActivityViewContext<JobActivityAttributes>

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 5) {
                Image(systemName: statusIcon(context.state.status))
                    .foregroundStyle(statusColor(context.state.status))
                    .font(.caption.weight(.semibold))
                Text(statusLabel(context.state.status))
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.white)
            }
            Text(context.attributes.packageName.capitalized)
                .font(.caption2)
                .foregroundStyle(.white.opacity(0.5))
        }
        .padding(.leading, 4)
    }
}

private struct ExpandedTrailing: View {
    let context: ActivityViewContext<JobActivityAttributes>

    var body: some View {
        VStack(alignment: .trailing, spacing: 2) {
            Text(elapsedLabel(context.state.elapsedMinutes))
                .font(.title3.weight(.bold).monospacedDigit())
                .foregroundStyle(Color("BricksGold"))
            Text("elapsed")
                .font(.caption2)
                .foregroundStyle(.white.opacity(0.4))
        }
        .padding(.trailing, 4)
    }
}

private struct ExpandedBottom: View {
    let context: ActivityViewContext<JobActivityAttributes>

    var body: some View {
        HStack {
            Image(systemName: "mappin.circle.fill")
                .foregroundStyle(.white.opacity(0.5))
                .font(.caption)
            Text(context.attributes.address)
                .font(.caption)
                .foregroundStyle(.white.opacity(0.75))
                .lineLimit(1)
            Spacer()
        }
        .padding(.horizontal, 4)
        .padding(.bottom, 4)
    }
}

// MARK: — Status badge

private struct StatusBadge: View {
    let status: String

    var body: some View {
        HStack(spacing: 4) {
            Circle()
                .fill(statusColor(status))
                .frame(width: 5, height: 5)
            Text(statusLabel(status))
                .font(.caption2.weight(.semibold))
                .foregroundStyle(statusColor(status))
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .background(statusColor(status).opacity(0.15), in: Capsule())
    }
}

// MARK: — Helpers

private func elapsedLabel(_ minutes: Int) -> String {
    if minutes < 60 { return "\(minutes)m" }
    let h = minutes / 60
    let m = minutes % 60
    return m > 0 ? "\(h)h \(m)m" : "\(h)h"
}

private func statusLabel(_ status: String) -> String {
    switch status {
    case "assigned":    return "Assigned"
    case "in_progress": return "Shooting"
    case "completed":   return "Done"
    default:            return status.capitalized
    }
}

private func statusIcon(_ status: String) -> String {
    switch status {
    case "assigned":    return "clock.fill"
    case "in_progress": return "camera.fill"
    case "completed":   return "checkmark.circle.fill"
    default:            return "camera"
    }
}

private func statusColor(_ status: String) -> Color {
    switch status {
    case "assigned":    return Color("BricksGold")
    case "in_progress": return .green
    case "completed":   return Color(.systemGray2)
    default:            return .white.opacity(0.6)
    }
}
