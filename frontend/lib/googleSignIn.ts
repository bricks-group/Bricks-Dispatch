import { parseAuthCallback, AUTH_CALLBACK_MESSAGES } from './oauthCallback';

export type AuthSessionResult = { type: string; url?: string };
export type CodeExchanger = {
  exchangeCodeForSession: (code: string) => Promise<{ error: unknown }>;
};

export const GOOGLE_SIGN_IN_FAILED = 'Google sign-in didn’t finish. Please try again.';

/**
 * Finish Google sign-in after the auth session returns. Exchanges the code only
 * when the return URL is exactly the redirect we opened. `message` is set when
 * the vendor should see why it stopped; a plain cancel has no message.
 */
export async function completeGoogleSignIn(
  auth: CodeExchanger,
  result: AuthSessionResult,
  redirectTo: string,
): Promise<{ ok: true } | { ok: false; message?: string }> {
  if (result.type !== 'success' || !result.url) return { ok: false };
  const callback = parseAuthCallback(result.url, redirectTo);
  if (!callback.ok) return { ok: false, message: AUTH_CALLBACK_MESSAGES[callback.reason] };
  try {
    const { error } = await auth.exchangeCodeForSession(callback.code);
    if (error) return { ok: false, message: GOOGLE_SIGN_IN_FAILED };
  } catch {
    return { ok: false, message: GOOGLE_SIGN_IN_FAILED };
  }
  return { ok: true };
}
