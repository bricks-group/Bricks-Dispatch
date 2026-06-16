import SwiftUI
import BricksCore

struct ProfileEditView: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var vm = ProfileEditViewModel()

    var body: some View {
        NavigationStack {
            Form {
                Section("IDENTITY") {
                    LabeledContent("Name") {
                        TextField("Full name", text: $vm.name)
                            .multilineTextAlignment(.trailing)
                    }
                    LabeledContent("Phone") {
                        TextField("Phone number", text: $vm.phone)
                            .multilineTextAlignment(.trailing)
                            .keyboardType(.phonePad)
                    }
                }

                Section("AVAILABILITY") {
                    Toggle("Available for jobs", isOn: $vm.availability)
                        .tint(BricksColors.brick)
                }

                if let errorMsg = vm.errorMessage {
                    Section {
                        Text(errorMsg)
                            .font(.subheadline)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Edit Profile")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if vm.isSaving {
                        ProgressView()
                    } else {
                        Button("Save") {
                            Task {
                                await vm.save()
                                if vm.errorMessage == nil { dismiss() }
                            }
                        }
                        .fontWeight(.semibold)
                        .tint(BricksColors.brick)
                    }
                }
            }
        }
        .task { await vm.load() }
    }
}

@MainActor
final class ProfileEditViewModel: ObservableObject {
    @Published var name: String = ""
    @Published var phone: String = ""
    @Published var availability: Bool = true
    @Published var isSaving = false
    @Published var errorMessage: String?

    private let api = BricksAPIClient.shared

    func load() async {
        do {
            struct Profile: Decodable {
                let name: String; let phone: String; let availability: Bool
            }
            let p: Profile = try await api.get("/api/profile")
            name = p.name; phone = p.phone; availability = p.availability
        } catch {}
    }

    func save() async {
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }
        do {
            struct ProfileUpdate: Encodable {
                let name: String; let phone: String; let availability: Bool
            }
            struct ProfileResponse: Decodable {}
            let _: ProfileResponse = try await api.patch(
                "/api/profile",
                body: ProfileUpdate(name: name, phone: phone, availability: availability)
            )
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
