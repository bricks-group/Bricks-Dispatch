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
    @State private var agreement: ICAgreement?
    @State private var agreementError: String?

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

                    agreementBlock

                    Button {
                        guard agreement != nil else { return }
                        acceptedTerms.toggle()
                    } label: {
                        HStack(alignment: .top, spacing: 10) {
                            Image(systemName: acceptedTerms ? "checkmark.square.fill" : "square")
                                .foregroundStyle(acceptedTerms ? BricksColors.brick : BricksColors.inkFaint)
                            Text(acceptLabel)
                                .font(.caption)
                                .foregroundStyle(.primary)
                                .multilineTextAlignment(.leading)
                        }
                    }
                    .buttonStyle(.plain)
                    .disabled(agreement == nil)
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
        .task { await loadAgreement() }
    }

    private var acceptLabel: String {
        if let version = agreement?.version {
            return "I accept the Bricks Contractor Agreement v\(version) (Ontario)"
        }
        return "I accept the Bricks Contractor Agreement (Ontario)"
    }

    @ViewBuilder
    private var agreementBlock: some View {
        VStack(alignment: .leading, spacing: 8) {
            BOver("Contractor agreement", color: BricksColors.inkMute)
            if let agreement {
                Text("v\(agreement.version) · effective \(agreement.effectiveDate)")
                    .font(.caption2)
                    .foregroundStyle(BricksColors.inkFaint)
                ScrollView {
                    Text(agreement.text)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(.primary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
                .frame(maxHeight: 280)
                .padding(10)
                .background(RoundedRectangle(cornerRadius: 2).fill(Color(.secondarySystemBackground)))
                .overlay(RoundedRectangle(cornerRadius: 2).stroke(BricksColors.hairStrong, lineWidth: 1))
            } else if let agreementError {
                Text(agreementError)
                    .font(.caption)
                    .foregroundStyle(BricksColors.bad)
                Button("Retry") { Task { await loadAgreement() } }
                    .font(.caption)
                    .foregroundStyle(BricksColors.brick)
            } else {
                Text("Loading contractor agreement…")
                    .font(.caption)
                    .foregroundStyle(BricksColors.inkFaint)
            }
        }
        .padding(.top, 8)
    }

    private var canSubmit: Bool {
        !name.isEmpty && !email.isEmpty && !phone.isEmpty && !city.isEmpty && acceptedTerms && agreement != nil
    }

    private func loadAgreement() async {
        agreementError = nil
        do {
            agreement = try await OnboardingClient.fetchAgreement()
        } catch {
            agreement = nil
            acceptedTerms = false
            agreementError = "Could not load the contractor agreement. Check your connection and retry."
        }
    }

    private func submit() async {
        guard let agreement else {
            errorMessage = "The contractor agreement must be loaded before you can apply."
            return
        }
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
            acceptedTerms: acceptedTerms,
            icAgreementVersion: agreement.version
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
