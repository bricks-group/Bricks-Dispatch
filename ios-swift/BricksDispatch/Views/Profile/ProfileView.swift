import SwiftUI
import BricksCore

/// Vendor profile — night-native. The glass brick carries the photographer's
/// pro-tier material; fill tracks the on-time rate that keeps them at tier.
struct ProfileView: View {
    @ObservedObject private var auth: BricksAuth = .shared

    // TODO: bind to vendor_members — tier, payout %, on-time rate.
    private let onTimeRate = 0.98

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    identity
                    vault
                    stats
                    settings
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 8)
            }
            .background(Color(hex: 0x141210).ignoresSafeArea())
            .navigationTitle("Profile")
            .navigationBarTitleDisplayMode(.large)
            .toolbarColorScheme(.dark, for: .navigationBar)
        }
        .preferredColorScheme(.dark)
    }

    private var identity: some View {
        HStack(spacing: 14) {
            RoundedRectangle(cornerRadius: 2).fill(Color(hex: 0x2C2823))
                .frame(width: 54, height: 54)
                .overlay(Text("HR").font(.system(size: 20, weight: .semibold)).foregroundStyle(BricksColors.gold))
            VStack(alignment: .leading, spacing: 3) {
                Text(auth.session?.email.map { String($0.prefix(while: { $0 != "@" })) } ?? "Hassan Reyes")
                    .font(.system(size: 19, weight: .semibold)).tracking(-0.3).foregroundStyle(Color(hex: 0xF4F1EB))
                Text("Photographer · RPAS Advanced")
                    .font(.system(size: 11.5, design: .monospaced)).foregroundStyle(Color(hex: 0x8F8678))
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var vault: some View {
        ZStack {
            LinearGradient(colors: [Color(hex: 0x231B12), Color(hex: 0x130F0C)],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
            Circle().fill(BricksColors.gold).opacity(0.16).blur(radius: 34)
                .frame(width: 180).offset(x: 120, y: -85)
            HStack(spacing: 20) {
                TierBrick(tier: .gold, fill: onTimeRate)
                    .frame(width: 104, height: 104)
                    .shadow(color: BricksColors.gold.opacity(0.4), radius: 14, y: 6)
                VStack(alignment: .leading, spacing: 0) {
                    Text("VENDOR TIER")
                        .font(.system(size: 10.5, weight: .medium, design: .monospaced))
                        .tracking(2).foregroundStyle(BricksColors.gold)
                    Text("Tier A")
                        .font(.system(size: 26, weight: .semibold)).tracking(-0.6)
                        .foregroundStyle(Color(hex: 0xF4F1EB)).padding(.top, 8)
                    Text("Elite · 55% payout · full routing")
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(Color(hex: 0x8F8678)).padding(.top, 7)
                    Divider().overlay(Color.white.opacity(0.1)).padding(.vertical, 13)
                    Text("\(Int(onTimeRate * 100))% on-time keeps you at Tier A")
                        .font(.system(size: 10.5, design: .monospaced))
                        .foregroundStyle(Color(hex: 0x8F8678))
                }
                Spacer(minLength: 0)
            }
            .padding(22)
        }
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(.white.opacity(0.14), lineWidth: 1))
    }

    private var stats: some View {
        HStack(spacing: 10) {
            stat("342", "SHOOTS")
            stat("4.9", "RATING")
            stat("98%", "ON TIME")
        }
    }
    private func stat(_ v: String, _ l: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(v).font(.system(size: 22, weight: .semibold)).tracking(-0.5).foregroundStyle(Color(hex: 0xF4F1EB))
            Text(l).font(.system(size: 9.5, weight: .medium, design: .monospaced))
                .tracking(0.5).foregroundStyle(Color(hex: 0x8F8678))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(hex: 0x1C1A17))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(.white.opacity(0.08))))
    }

    private var settings: some View {
        VStack(spacing: 0) {
            row("banknote", "Payout · Stripe", "Weekly")
            Divider().overlay(Color.white.opacity(0.08))
            row("checkmark.shield", "IC agreement", "v1.1")
            Divider().overlay(Color.white.opacity(0.08))
            row("airplane", "RPAS certificate", "Advanced")
            Divider().overlay(Color.white.opacity(0.08)).padding(.vertical, 4)
            Button(role: .destructive) { auth.signOut() } label: {
                row("rectangle.portrait.and.arrow.right", "Sign out", nil, destructive: true)
            }
        }
        .padding(.horizontal, 16)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(hex: 0x1C1A17))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(.white.opacity(0.08))))
    }
    private func row(_ icon: String, _ label: String, _ value: String?, destructive: Bool = false) -> some View {
        HStack(spacing: 13) {
            Image(systemName: icon).font(.system(size: 16))
                .foregroundStyle(destructive ? Color(hex: 0xC2685C) : Color(hex: 0xD6CFC2)).frame(width: 26)
            Text(label).font(.system(size: 14.5, weight: .medium))
                .foregroundStyle(destructive ? Color(hex: 0xC2685C) : Color(hex: 0xF4F1EB))
            Spacer()
            if let value { Text(value).font(.system(size: 12, design: .monospaced)).foregroundStyle(Color(hex: 0x8F8678)) }
            if !destructive { Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold)).foregroundStyle(Color(hex: 0x5E574C)) }
        }
        .padding(.vertical, 15)
        .contentShape(Rectangle())
    }
}
