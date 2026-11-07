/**
 * Phone OAuth return handling for PKCE (Google sign-in and Google Calendar).
 *
 * Only a `?code=` on exactly the redirect we opened is exchanged. Scheme, host,
 * port and path must all match the redirect passed to the provider. Provider
 * errors, missing codes, implicit hash tokens and any other URL are rejected and
 * must never reach exchangeCodeForSession.
 *
 * Production builds use the app scheme in app.json, so Supabase Auth URL
 * Configuration must allow-list exactly: bricks-go://auth-callback
 *
 * Kept free of imports so it runs under node:test without a bundler.
 */
export const BRICKS_GO_AUTH_CALLBACK = 'bricks-go://auth-callback';

export type AuthCallbackRejection = 'unexpected_callback' | 'provider_error' | 'missing_code';

export type AuthCallbackResult =
  | { ok: true; code: string }
  | { ok: false; reason: AuthCallbackRejection };

/** Friendly, plain-language messages shown to the vendor. */
export const AUTH_CALLBACK_MESSAGES: Record<AuthCallbackRejection, string> = {
  unexpected_callback: 'We couldn’t confirm that Google sent you back to Bricks. Please try again.',
  provider_error: 'Google didn’t finish. Approve access when asked, then try again.',
  missing_code: 'Google didn’t send back a confirmation. Please try again.',
};

const MAX_CODE_LENGTH = 2048;

function normalPath(pathname: string): string {
  return (pathname || '/').replace(/\/+$/, '') || '/';
}

function sameRedirect(actual: URL, expected: URL): boolean {
  return (
    actual.protocol === expected.protocol &&
    actual.hostname.toLowerCase() === expected.hostname.toLowerCase() &&
    // Exact port match: an expected redirect with no port rejects any port.
    actual.port === expected.port &&
    normalPath(actual.pathname) === normalPath(expected.pathname)
  );
}

function hashParams(url: URL): URLSearchParams {
  return new URLSearchParams(url.hash.startsWith('#') ? url.hash.slice(1) : url.hash);
}

/**
 * Validate an auth-session return URL against the redirect we opened.
 * Returns the authorization code only when every check passes.
 */
export function parseAuthCallback(callbackUrl: string, expectedRedirect: string): AuthCallbackResult {
  if (!callbackUrl || !expectedRedirect) return { ok: false, reason: 'unexpected_callback' };
  let actual: URL;
  let expected: URL;
  try {
    actual = new URL(callbackUrl);
    expected = new URL(expectedRedirect);
  } catch {
    return { ok: false, reason: 'unexpected_callback' };
  }
  if (!sameRedirect(actual, expected)) return { ok: false, reason: 'unexpected_callback' };
  if (actual.username || actual.password) return { ok: false, reason: 'unexpected_callback' };

  const hash = hashParams(actual);
  const errored = ['error', 'error_code', 'error_description'].some(
    (k) => actual.searchParams.has(k) || hash.has(k),
  );
  if (errored) return { ok: false, reason: 'provider_error' };

  const codes = actual.searchParams.getAll('code');
  if (codes.length > 1) return { ok: false, reason: 'unexpected_callback' };
  const code = codes[0];  // undefined when absent
  if (!code || code.length > MAX_CODE_LENGTH) return { ok: false, reason: 'missing_code' };
  return { ok: true, code };
}
