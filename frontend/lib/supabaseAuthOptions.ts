/**
 * Supabase auth options for the GO app (native and Expo web).
 *
 * PKCE is required: Google sign-in and Google Calendar connect wait for a
 * `?code=` on bricks-go://auth-callback and exchange it. auth-js defaults to
 * the implicit flow, which returns tokens in a URL hash that never yields a
 * code, so native Connect could not finish.
 *
 * The PKCE code verifier is kept in the same storage as the session
 * (AsyncStorage on iOS/Android, localStorage on web), so it survives the trip
 * through the system browser. detectSessionInUrl stays off: the app reads and
 * validates the auth-session URL itself (see oauthCallback.ts).
 *
 * Kept free of imports so it runs under node:test without a bundler.
 */
export type AuthStorage = {
  getItem: (key: string) => string | null | Promise<string | null>;
  setItem: (key: string, value: string) => void | Promise<void>;
  removeItem: (key: string) => void | Promise<void>;
};

export function supabaseAuthOptions(storage: AuthStorage) {
  return {
    storage,
    autoRefreshToken: true,
    persistSession: true,
    detectSessionInUrl: false,
    flowType: 'pkce' as const,
  };
}

/** Server render (Expo web static output): no storage, no session, still PKCE. */
export const SSR_AUTH_OPTIONS = {
  autoRefreshToken: false,
  persistSession: false,
  detectSessionInUrl: false,
  flowType: 'pkce' as const,
};

/**
 * Throwaway client for Google Calendar connect. It runs its own PKCE round trip
 * with in-memory storage under a unique storage key, so the vendor's signed-in
 * session (and its PKCE verifier) is never read, replaced or restored, and two
 * attempts never share a verifier.
 */
export function calendarOAuthAuthOptions(storageKey: string) {
  return {
    storageKey,
    autoRefreshToken: false,
    persistSession: false,
    detectSessionInUrl: false,
    flowType: 'pkce' as const,
  };
}
