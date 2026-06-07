import SwiftUI
import BricksCore

struct ProfileView: View {
    @ObservedObject private var auth: BricksAuth = .shared

    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack(spacing: 12) {
                        Image(systemName: "person.circle.fill")
                            .font(.system(size: 44))
                            .foregroundStyle(BricksColors.brick)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(auth.session?.email ?? "Photographer")
                                .font(.headline)
                            Text("Vendor member")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.vertical, 6)
                }

                Section("Account") {
                    NavigationLink {
                        Text("Availability settings coming soon")
                    } label: {
                        Label("Availability", systemImage: "clock")
                    }
                    NavigationLink {
                        Text("Documents coming soon")
                    } label: {
                        Label("Documents", systemImage: "doc.text")
                    }
                    NavigationLink {
                        Text("Bank & Payouts coming soon")
                    } label: {
                        Label("Bank & Payouts", systemImage: "banknote")
                    }
                }

                Section {
                    Button(role: .destructive) {
                        auth.signOut()
                    } label: {
                        Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                    }
                }
            }
            .navigationTitle("Profile")
        }
    }
}
