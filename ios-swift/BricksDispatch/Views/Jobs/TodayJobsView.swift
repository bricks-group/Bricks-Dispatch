import SwiftUI
import BricksCore

struct TodayJobsView: View {
    @StateObject private var store = JobStore.shared
    private let th = BricksTheme(dark: true)

    private var dayLabel: String {
        let fmt = DateFormatter()
        fmt.dateFormat = "EEEE · MMM d"
        return fmt.string(from: Date())
    }

    var body: some View {
        ZStack {
            th.bg.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {

                    // ── Header ──
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 4) {
                            BOver(dayLabel, color: th.ink4)
                            Text("\(store.todayJobs.count) shoots.")
                                .font(.system(size: 22, weight: .medium))
                                .tracking(-0.5)
                                .foregroundStyle(th.ink)
                        }
                        Spacer()
                        BAvatar(store.vendorInitials, size: 36, dark: true)
                    }
                    .padding(.horizontal, 22)
                    .padding(.top, 14)
                    .padding(.bottom, 20)

                    // ── Next up card ──
                    if let next = store.nextJob {
                        VStack(alignment: .leading, spacing: 0) {
                            BOver("Next up · \(next.timeUntil)", color: BricksColors.brick)
                            VStack(alignment: .leading, spacing: 0) {
                                Text(next.address)
                                    .font(.system(size: 22, weight: .medium))
                                    .tracking(-0.5)
                                    .foregroundStyle(th.ink)
                                Text("\(next.propertyType?.uppercased() ?? "PROPERTY") · \(next.sqft.map { "\($0) SQFT" } ?? "")")
                                    .font(BricksFont.mono(12))
                                    .foregroundStyle(th.ink3)
                                    .padding(.top, 4)

                                // Stats grid
                                HStack(spacing: 1) {
                                    ForEach([
                                        ("ARRIVE", next.arriveLabel),
                                        ("DRIVE",  next.driveLabel ?? "—"),
                                        ("PACKAGE", next.packageName ?? "—"),
                                    ], id: \.0) { l, v in
                                        VStack(alignment: .leading, spacing: 4) {
                                            BOver(l, color: th.ink4)
                                            Text(v)
                                                .font(BricksFont.mono(14))
                                                .foregroundStyle(th.ink)
                                        }
                                        .padding(.horizontal, 12)
                                        .padding(.vertical, 10)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                        .background(th.surf)
                                    }
                                }
                                .background(th.hairS)
                                .clipShape(RoundedRectangle(cornerRadius: 2))
                                .padding(.top, 14)

                                HStack(spacing: 8) {
                                    Button {
                                        if let url = URL(string: "maps://?daddr=\(next.address.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "")") {
                                            UIApplication.shared.open(url)
                                        }
                                    } label: {
                                        HStack(spacing: 6) {
                                            Image(systemName: "location.fill")
                                            Text("Navigate")
                                        }
                                        .font(.system(size: 14, weight: .medium))
                                        .foregroundStyle(.white)
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, 13)
                                        .background(BricksColors.brick)
                                        .clipShape(RoundedRectangle(cornerRadius: 2))
                                    }

                                    if let phone = next.agentPhone, let url = URL(string: "tel:\(phone)") {
                                        Link(destination: url) {
                                            Image(systemName: "phone")
                                                .font(.system(size: 17))
                                                .foregroundStyle(th.ink)
                                                .padding(13)
                                                .overlay(
                                                    RoundedRectangle(cornerRadius: 2)
                                                        .strokeBorder(th.hairS, lineWidth: 1)
                                                )
                                        }
                                    }
                                }
                                .padding(.top, 14)
                            }
                            .padding(18)
                            .background(th.surf)
                            .overlay(
                                RoundedRectangle(cornerRadius: 2)
                                    .strokeBorder(th.hairS, lineWidth: 1)
                            )
                            .clipShape(RoundedRectangle(cornerRadius: 2))
                        }
                        .padding(.horizontal, 22)
                        .padding(.bottom, 24)
                    }

                    // ── Pre-shoot checklist ──
                    if let next = store.nextJob {
                        VStack(alignment: .leading, spacing: 0) {
                            BOver("Pre-shoot checklist", color: th.ink3)
                                .padding(.horizontal, 22)
                                .padding(.bottom, 10)

                            let items: [(String, Bool)] = [
                                ("Agent confirmed · \(next.agentName ?? "—")", next.agentName != nil),
                                (next.accessCode.map { "Lockbox code · \($0)" } ?? "Lockbox code pending", next.accessCode != nil),
                                ("Drone approved for area", next.droneApproved ?? false),
                            ]

                            ForEach(items, id: \.0) { item, done in
                                HStack(spacing: 12) {
                                    ZStack {
                                        RoundedRectangle(cornerRadius: 2)
                                            .fill(done ? BricksColors.brick : Color.clear)
                                            .frame(width: 18, height: 18)
                                        RoundedRectangle(cornerRadius: 2)
                                            .strokeBorder(done ? BricksColors.brick : th.hairS, lineWidth: 1.5)
                                            .frame(width: 18, height: 18)
                                        if done {
                                            Image(systemName: "checkmark")
                                                .font(.system(size: 10, weight: .bold))
                                                .foregroundStyle(.white)
                                        }
                                    }
                                    Text(item)
                                        .font(.system(size: 13))
                                        .foregroundStyle(done ? th.ink3 : th.ink)
                                        .strikethrough(done, color: th.ink3)
                                    Spacer()
                                }
                                .padding(.vertical, 12)
                                .padding(.horizontal, 22)
                                BHair(dark: true)
                            }
                        }
                        .padding(.bottom, 24)
                    }

                    // ── Later today ──
                    let later = store.todayJobs.dropFirst()
                    if !later.isEmpty {
                        BOver("Later today", color: th.ink3)
                            .padding(.horizontal, 22)
                            .padding(.bottom, 10)

                        VStack(spacing: 10) {
                            ForEach(later) { job in
                                LaterJobRow(job: job, th: th)
                            }
                        }
                        .padding(.horizontal, 22)
                    }

                    Spacer(minLength: 20)
                }
            }
        }
        .task { await store.loadTodayJobs() }
    }
}

private struct LaterJobRow: View {
    let job: DispatchJob
    let th: BricksTheme

    var body: some View {
        HStack(spacing: 14) {
            Text(job.arriveLabel)
                .font(BricksFont.mono(13))
                .foregroundStyle(th.ink)
                .frame(width: 48, alignment: .leading)

            Rectangle()
                .fill(th.hair)
                .frame(width: 1, height: 28)

            VStack(alignment: .leading, spacing: 2) {
                Text(job.address)
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(th.ink)
                Text(job.propertyType ?? "Property")
                    .font(.system(size: 11))
                    .foregroundStyle(th.ink3)
            }

            Spacer()

            Text(job.payout.map { "$\(Int($0))" } ?? "—")
                .font(BricksFont.mono(13))
                .foregroundStyle(th.ink)
        }
        .padding(14)
        .background(th.surf.opacity(0))
        .overlay(
            RoundedRectangle(cornerRadius: 2)
                .strokeBorder(th.hair, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 2))
    }
}
