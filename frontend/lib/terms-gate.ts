// Remembers, per signed-in user, that the contractor agreement step is done,
// so the root layout asks /auth/me once per session instead of on every screen.
let clearedFor: string | null = null;

export function termsCleared(userId: string | null | undefined): boolean {
  return !!userId && clearedFor === userId;
}

export function markTermsCleared(userId: string | null | undefined) {
  clearedFor = userId || null;
}

export function resetTermsGate() {
  clearedFor = null;
}
