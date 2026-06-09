import SwiftUI
import WatchKit
import BricksCore

// Shown when a new job is assigned — primary watch interaction point.
// One-tap accept with digital crown confirmation prevents accidental taps.

struct UpcomingJobView: View {
    let job: WatchJob
    @EnvironmentObject var store: WatchJobStore
    @State private var confirmAccept = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                // Time until job
                if let mins = job.minutesUntil {
                    HStack(spacing: 4) {
                        Image(systemName: "clock.fill")
                            .font(.caption2)
                            .foregroundStyle(.orange)
                        Text(minsLabel(mins))
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.orange)
                    }
                }

                // Address
                Text(job.address)
                    .font(.footnote.bold())
                    .lineLimit(2)

                // Payout
                if let p = job.formattedPayout as String?, !p.isEmpty {
                    Text(p)
                        .font(.title3.monospacedDigit().bold())
                        .foregroundStyle(BricksColors.brick)
                }

                Divider()

                // Accept / Decline — side by side
                HStack(spacing: 6) {
                    Button {
                        store.sendAction(WatchAction.accept.rawValue, jobId: job.id)
                        WKInterfaceDevice.current().play(.success)
                    } label: {
                        Label("Accept", systemImage: "checkmark")
                            .font(.caption.bold())
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.green)

                    Button(role: .destructive) {
                        store.sendAction(WatchAction.decline.rawValue, jobId: job.id)
                        WKInterfaceDevice.current().play(.failure)
                    } label: {
                        Label("Decline", systemImage: "xmark")
                            .font(.caption.bold())
                    }
                    .buttonStyle(.bordered)
                }
            }
            .padding(.horizontal, 4)
        }
        .navigationTitle("New Job")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func minsLabel(_ mins: Int) -> String {
        if mins < 60 { return "in \(mins) min" }
        let h = mins / 60
        let m = mins % 60
        return m == 0 ? "in \(h)h" : "in \(h)h \(m)m"
    }
}

struct ActiveJobView: View {
    let job: WatchJob
    @EnvironmentObject var store: WatchJobStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                // Status indicator
                HStack(spacing: 6) {
                    Circle().fill(.green).frame(width: 7, height: 7)
                    Text("IN PROGRESS")
                        .font(.caption2.bold())
                        .foregroundStyle(.green)
                }

                Text(job.address)
                    .font(.footnote.bold())
                    .lineLimit(2)

                if let p = job.formattedPayout as String?, !p.isEmpty {
                    Text(p)
                        .font(.callout.monospacedDigit().bold())
                        .foregroundStyle(BricksColors.brick)
                }

                Divider()

                VStack(spacing: 6) {
                    Button {
                        store.sendAction(WatchAction.onWay.rawValue, jobId: job.id)
                        WKInterfaceDevice.current().play(.click)
                    } label: {
                        Label("On my way", systemImage: "car.fill")
                            .font(.caption.bold())
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(BricksColors.brick)

                    Button {
                        store.sendAction(WatchAction.arrived.rawValue, jobId: job.id)
                        WKInterfaceDevice.current().play(.success)
                    } label: {
                        Label("Arrived", systemImage: "mappin.circle.fill")
                            .font(.caption.bold())
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                }
            }
            .padding(.horizontal, 4)
        }
        .navigationTitle("Active Job")
        .navigationBarTitleDisplayMode(.inline)
    }
}
