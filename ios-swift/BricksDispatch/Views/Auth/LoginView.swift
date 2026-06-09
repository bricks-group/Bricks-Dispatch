import SwiftUI
import BricksCore

struct LoginView: View {
    @ObservedObject private var auth: BricksAuth = .shared
    @State private var email = ""
    @State private var password = ""
    @State private var errorMessage: String?
    @State private var isLoading = false
    @State private var showForm = false

    private let night = Color(red: 0.078, green: 0.071, blue: 0.063)

    var body: some View {
        ZStack {
            night.ignoresSafeArea()

            // Gradient vignette — simulates property exterior darkness at edges
            LinearGradient(
                stops: [
                    .init(color: .black.opacity(0.55), location: 0),
                    .init(color: .clear, location: 0.32),
                    .init(color: night.opacity(0.45), location: 0.65),
                    .init(color: night, location: 1),
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()

            VStack(alignment: .leading, spacing: 0) {
                // Wordmark
                HStack {
                    BricksLogoMark(size: 4)
                    Text("bricks")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundStyle(.white)
                    Spacer()
                }
                .padding(.horizontal, 28)
                .padding(.top, 18)

                Spacer()

                VStack(alignment: .leading, spacing: 0) {
                    Text("DISPATCHED TO YOUR DOOR")
                        .font(.system(size: 10, weight: .semibold, design: .monospaced))
                        .tracking(1.8)
                        .foregroundStyle(BricksColors.brick)
                        .padding(.bottom, 14)

                    Text("Professional media,\non your schedule.")
                        .font(.system(size: 38, weight: .semibold))
                        .foregroundStyle(.white)
                        .lineSpacing(2)
                        .padding(.bottom, 16)

                    Text("Accept jobs, navigate to properties, deliver\nwork — all from your pocket.")
                        .font(.system(size: 15.5))
                        .foregroundStyle(.white.opacity(0.55))
                        .lineSpacing(3)
                        .padding(.bottom, 32)

                    if showForm {
                        formSection
                            .transition(.move(edge: .bottom).combined(with: .opacity))
                    } else {
                        Button {
                            withAnimation(.spring(response: 0.42, dampingFraction: 0.8)) {
                                showForm = true
                            }
                        } label: {
                            Text("Sign in with email")
                                .font(.system(size: 15.5, weight: .semibold))
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 16)
                                .background(.white)
                                .foregroundStyle(night)
                                .clipShape(RoundedRectangle(cornerRadius: 2))
                        }
                        .padding(.bottom, 12)
                        .transition(.opacity)

                        Text("Photography vendors only. New applicants: apply at dispatch.brxs.ca")
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(.white.opacity(0.3))
                            .lineSpacing(3)
                            .padding(.bottom, 4)
                    }
                }
                .padding(.horizontal, 28)
                .padding(.bottom, 44)
            }
        }
        .preferredColorScheme(.dark)
    }

    private var formSection: some View {
        VStack(spacing: 10) {
            TextField("Email", text: $email)
                .textContentType(.emailAddress)
                .keyboardType(.emailAddress)
                .autocapitalization(.none)
                .autocorrectionDisabled()
                .padding(14)
                .background(.white.opacity(0.08))
                .overlay(
                    RoundedRectangle(cornerRadius: 2)
                        .strokeBorder(.white.opacity(0.15), lineWidth: 1)
                )
                .foregroundStyle(.white)
                .clipShape(RoundedRectangle(cornerRadius: 2))

            SecureField("Password", text: $password)
                .textContentType(.password)
                .padding(14)
                .background(.white.opacity(0.08))
                .overlay(
                    RoundedRectangle(cornerRadius: 2)
                        .strokeBorder(.white.opacity(0.15), lineWidth: 1)
                )
                .foregroundStyle(.white)
                .clipShape(RoundedRectangle(cornerRadius: 2))

            if let msg = errorMessage {
                Text(msg)
                    .font(.caption)
                    .foregroundStyle(.red.opacity(0.9))
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            Button {
                Task { await signIn() }
            } label: {
                Group {
                    if isLoading {
                        ProgressView().tint(night)
                    } else {
                        Text("Sign In")
                            .font(.system(size: 15.5, weight: .semibold))
                    }
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 16)
                .background(email.isEmpty || password.isEmpty || isLoading
                    ? Color.white.opacity(0.35)
                    : Color.white)
                .foregroundStyle(night)
                .clipShape(RoundedRectangle(cornerRadius: 2))
            }
            .disabled(isLoading || email.isEmpty || password.isEmpty)
        }
    }

    private func signIn() async {
        isLoading = true
        errorMessage = nil
        do {
            try await auth.signIn(email: email.trimmingCharacters(in: .whitespaces), password: password)
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }
}
