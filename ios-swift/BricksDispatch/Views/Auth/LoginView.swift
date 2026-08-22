import SwiftUI
import BricksCore

// Dispatch has no password auth -- vendors sign in with a one-time email
// code (see SupabaseAuth.requestOTP/verifyOTP, which call the backend's
// /api/vendor-os/auth endpoints, not raw Supabase).
struct LoginView: View {
    @ObservedObject private var auth: BricksAuth = .shared
    @State private var email = ""
    @State private var code = ""
    @State private var step: Step = .email
    @State private var errorMessage: String?
    @State private var isLoading = false

    private enum Step { case email, code }
    private enum Destination: Identifiable {
        case apply
        var id: Self { self }
    }
    @State private var destination: Destination?

    var body: some View {
        VStack(spacing: 0) {
            Spacer()
            VStack(spacing: 24) {
                BricksLogoMark(size: 4)
                    .padding(.bottom, 8)

                Text("bricks dispatch")
                    .font(.system(size: 20, weight: .medium))
                    .foregroundStyle(BricksColors.brick)

                switch step {
                case .email:
                    BField("Email", placeholder: "you@example.com", text: $email, keyboardType: .emailAddress, textContentType: .emailAddress)
                case .code:
                    VStack(alignment: .leading, spacing: 8) {
                        Text("We sent a code to \(email).")
                            .font(.caption)
                            .foregroundStyle(BricksColors.inkMute)
                        BField("Code", placeholder: "6-digit code", text: $code, keyboardType: .numberPad)
                    }
                }

                if let errorMessage {
                    Text(errorMessage)
                        .font(.caption)
                        .foregroundStyle(BricksColors.bad)
                        .multilineTextAlignment(.center)
                }

                BBtn(primaryLabel, variant: .accent, full: true) {
                    Task { await primaryAction() }
                }
                .disabled(isLoading || primaryDisabled)
                .opacity(isLoading || primaryDisabled ? 0.5 : 1)

                if step == .code {
                    Button("Use a different email") {
                        step = .email
                        code = ""
                        errorMessage = nil
                    }
                    .font(.caption)
                    .foregroundStyle(BricksColors.inkMute)
                }

                Button {
                    destination = .apply
                } label: {
                    (Text("Not a vendor yet?  ").foregroundStyle(BricksColors.inkFaint)
                     + Text("Apply to join").foregroundStyle(BricksColors.inkSoft))
                        .font(.caption)
                }
                .padding(.top, 4)
            }
            .padding(.horizontal, 32)
            Spacer()
        }
        .background(Color(.systemBackground).ignoresSafeArea())
        .navigationDestination(item: $destination) { dest in
            switch dest {
            case .apply: ApplyView()
            }
        }
    }

    private var primaryLabel: String {
        if isLoading { return step == .email ? "Sending code…" : "Verifying…" }
        return step == .email ? "Send code" : "Sign in"
    }

    private var primaryDisabled: Bool {
        step == .email ? email.isEmpty : code.isEmpty
    }

    private func primaryAction() async {
        switch step {
        case .email: await sendCode()
        case .code: await verify()
        }
    }

    private func sendCode() async {
        isLoading = true
        errorMessage = nil
        do {
            try await auth.requestOTP(email: email.trimmingCharacters(in: .whitespaces))
            step = .code
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    private func verify() async {
        isLoading = true
        errorMessage = nil
        do {
            try await auth.verifyOTP(email: email.trimmingCharacters(in: .whitespaces), code: code.trimmingCharacters(in: .whitespaces))
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }
}
