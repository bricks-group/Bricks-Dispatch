import { supabase } from './supabase';

export const GOOGLE_CAL_CALLBACK = 'https://go.brxs.ca/auth/google-calendar';
export const GOOGLE_CAL_SCOPE =
  'https://www.googleapis.com/auth/calendar.freebusy';

export type GoogleConnectStart = {
  mode?: string | null;
  connect_via?: string | null;
  oauth_url?: string | null;
  redirect_uri?: string | null;
  scope?: string | null;
  query_params?: Record<string, string>;
  status?: string;
  detail?: string;
};

/** Start Google Calendar OAuth — Supabase-mediated (default) or direct authorize URL. */
export async function startGoogleCalendarOAuth(
  startBody: GoogleConnectStart,
): Promise<{ redirected?: boolean; error?: string }> {
  if (startBody?.oauth_url) {
    window.location.href = startBody.oauth_url;
    return { redirected: true };
  }
  if (startBody?.mode === 'supabase' || startBody?.connect_via === 'supabase') {
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo: startBody.redirect_uri || GOOGLE_CAL_CALLBACK,
        scopes: startBody.scope || GOOGLE_CAL_SCOPE,
        queryParams: {
          access_type: 'offline',
          prompt: 'consent',
          ...(startBody.query_params || {}),
        },
      },
    });
    if (error) {
      return { error: error.message || 'Could not start Google Calendar connect.' };
    }
    return { redirected: true };
  }
  return { error: startBody?.detail || 'Google Calendar is not ready to connect on this environment.' };
}
