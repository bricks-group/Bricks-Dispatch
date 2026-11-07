/**
 * Phone OAuth return for PKCE (Google sign-in and Google Calendar).
 *
 * Production builds use the app scheme in app.json. Supabase Auth → URL
 * Configuration must allow-list exactly:
 *   bricks-go://auth-callback
 * Implicit hash tokens are ignored. Only a ?code= on the redirect we opened
 * is exchanged.
 */
export const BRICKS_GO_AUTH_CALLBACK = 'bricks-go://auth-callback';

function sameRedirect(actual, expected) {
  if (actual.protocol !== expected.protocol) return false;
  if ((actual.hostname || '') !== (expected.hostname || '')) return false;
  const path = (u) => (u.pathname || '/').replace(/\/+$/, '') || '/';
  return path(actual) === path(expected);
}

/**
 * @param {string} callbackUrl URL returned by the auth session
 * @param {string} expectedRedirect redirect passed to the provider
 * @returns {string | null} authorization code, or null if the URL is not that callback
 */
export function codeFromAuthCallback(callbackUrl, expectedRedirect) {
  if (!callbackUrl || !expectedRedirect) return null;
  let actual;
  let expected;
  try {
    actual = new URL(callbackUrl);
    expected = new URL(expectedRedirect);
  } catch {
    return null;
  }
  if (!sameRedirect(actual, expected)) return null;
  if (actual.username || actual.password) return null;
  if (actual.searchParams.get('error')) return null;
  const code = actual.searchParams.get('code');
  if (!code || code.length > 2048) return null;
  return code;
}
