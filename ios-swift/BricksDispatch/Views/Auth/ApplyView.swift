import SwiftUI
import BricksCore

struct ApplyView: View {
    @State private var name = ""
    @State private var email = ""
    @State private var phone = ""
    @State private var city = ""
    @State private var portfolioLink = ""
    @State private var acceptedTerms = false
    @State private var confirmationMessage: String?
    @State private var errorMessage: String?
    @State private var isLoading = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                BOver("Apply to join", color: BricksColors.brick)

                if let confirmationMessage {
                    Text(confirmationMessage)
                        .font(.system(size: 15))
                        .foregroundStyle(.primary)
                        .padding(.top, 4)
                } else {
                    VStack(spacing: 14) {
                        BField("Name", placeholder: "Full name", text: $name, textContentType: .name)
                        BField("Email", placeholder: "you@example.com", text: $email, keyboardType: .emailAddress, textContentType: .emailAddress)
                        BField("Phone", placeholder: "(555) 555-5555", text: $phone, keyboardType: .phonePad, textContentType: .telephoneNumber)
                        BField("City", placeholder: "Toronto", text: $city, textContentType: .addressCity)
                        BField("Portfolio link", placeholder: "instagram.com/yourwork", text: $portfolioLink, keyboardType: .URL)
                    }

                    Button {
                        acceptedTerms.toggle()
                    } label: {
                        HStack(alignment: .top, spacing: 10) {
                            Image(systemName: acceptedTerms ? "checkmark.square.fill" : "square")
                                .foregroundStyle(acceptedTerms ? BricksColors.brick : BricksColors.inkFaint)
                            Text("I accept the Bricks Contractor Agreement (Ontario)")
                                .font(.caption)
                                .foregroundStyle(.primary)
                                .multilineTextAlignment(.leading)
                        }
                    }
                    .buttonStyle(.plain)
                    .padding(.top, 4)

                    if let errorMessage {
                        Text(errorMessage)
                            .font(.caption)
                            .foregroundStyle(BricksColors.bad)
                    }

                    BBtn(isLoading ? "Submitting…" : "Submit application", variant: .accent, full: true) {
                        Task { await submit() }
                    }
                    .disabled(isLoading || !canSubmit)
                    .opacity(isLoading || !canSubmit ? 0.5 : 1)
                }
            }
            .padding(28)
        }
        .background(Color(.systemBackground).ignoresSafeArea())
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var canSubmit: Bool {
        !name.isEmpty && !email.isEmpty && !phone.isEmpty && !city.isEmpty && acceptedTerms
    }

    private func submit() async {
        isLoading = true
        errorMessage = nil
        let trimmedLink = portfolioLink.trimmingCharacters(in: .whitespaces)
        let request = VendorApplicationRequest(
            name: name.trimmingCharacters(in: .whitespaces),
            email: email.trimmingCharacters(in: .whitespaces),
            phone: phone.trimmingCharacters(in: .whitespaces),
            specialties: ["photo"],
            portfolioLinks: trimmedLink.isEmpty ? [] : [trimmedLink],
            city: city.trimmingCharacters(in: .whitespaces),
            maxTravelKm: 50,
            acceptedTerms: acceptedTerms
        )
        do {
            let res = try await OnboardingClient.apply(request)
            confirmationMessage = res.message
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }
}
