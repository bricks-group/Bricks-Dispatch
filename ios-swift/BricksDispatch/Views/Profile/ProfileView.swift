import SwiftUI
import BricksCore

struct ProfileView: View {
    @ObservedObject private var auth: BricksAuth = .shared

    var body: some View {
        NavigationStack {
            List {
                // Header section
                Section {
                    HStack(spacing: 14) {
                        ZStack {
                            Circle()
                                .fill(BricksColors.brick.opacity(0.12))
                                .frame(width: 56, height: 56)
                            Image(systemName: "camera.fill")
                                .font(.title2)
                                .foregroundStyle(BricksColors.brick)
                        }
                        .accessibilityHidden(true)

                        VStack(alignment: .leading, spacing: 5) {
                            Text(auth.session?.email ?? "Photographer")
                                .font(.headline)
                            Text("VENDOR MEMBER")
                                .font(.system(size: 9, weight: .semibold, design: .monospaced))
                                .tracking(0.6)
                                .foregroundStyle(BricksColors.brick)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(BricksColors.brick.opacity(0.08))
                                .clipShape(RoundedRectangle(cornerRadius: 2))
                        }
                        Spacer()
                    }
                    .padding(.vertical, 6)
                }

                Section("ACCOUNT") {
                    NavigationLink {
                        ComingSoonView(title: "Availability")
                    } label: {
                        Label("Availability", systemImage: "clock")
                    }
                    NavigationLink {
                        ComingSoonView(title: "Documents")
                    } label: {
                        Label("Documents", systemImage: "doc.text")
                    }
                    NavigationLink {
                        ComingSoonView(title: "Bank & Payouts")
                    } label: {
                        Label("Bank & Payouts", systemImage: "banknote")
                    }
                }

                Section("SUPPORT") {
                    Link(destination: URL(string: "https://dispatch.brxs.ca")!) {
                        Label("Help Centre", systemImage: "questionmark.circle")
                    }
                    .foregroundStyle(.primary)
                }

                Section {
                    Button(role: .destructive) {
                        auth.signOut()
                    } label: {
                        Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                    }
                    .accessibilityHint("Signs you out of the Bricks Dispatch app")
                }
            }
            .navigationTitle("Profile")
        }
    }
}

private struct ComingSoonView: View {
    let title: String
    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "wrench.and.screwdriver")
                .font(.system(size: 40))
                .foregroundStyle(Color(.tertiaryLabel))
            Text(title)
                .font(.headline)
            Text("Coming soon")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }
}
