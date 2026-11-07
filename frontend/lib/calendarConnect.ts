import * as WebBrowser from 'expo-web-browser';
import * as Linking from 'expo-linking';
import { supabase } from './supabase';
import { actorHeaders } from './api';
import { AUTH_CALLBACK_MESSAGES, parseAuthCallback } from './oauthCallback';
import { createCalendarOAuthClient } from './calendarAuthClient';

WebBrowser.maybeCompleteAuthSession();

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';
export const GOOGLE_CAL_SCOPE = 'https://www.googleapis.com/auth/calendar.freebusy';
export const WEB_AVAILABILITY = 'https://go.brxs.ca/availability';

export type CalendarProvider = 'google' | 'microsoft' | 'apple' | 'ics';

export type CalendarConnection = {
  provider: string;
  connected: boolean;
  status: string;
  detail: string;
  last_synced_at?: string | null;
  last_sync_status?: string | null;
};

export type CalendarsResponse = {
  connections: CalendarConnection[];
  oauth?: {
    google_configured?: boolean;
    microsoft_configured?: boolean;
    google_connect_via?: string | null;
  };
  copy?: string;
  used_for_offers?: string;
  nudge?: string;
};

export const CALENDAR_INTRO_TITLE = 'Jobs that fit your schedule';
export const CALENDAR_EDU_LINES = [
  'Connect your calendar so we can avoid times you’ve already booked.',
  'Your working hours still apply, with or without a connected calendar.',
] as const;
export const CALENDAR_PROVIDER_HINTS: Record<string, string> = {
  google: 'Use the calendar you already use with Google.',
  microsoft: 'Use your Outlook or Microsoft 365 calendar.',
  apple: 'Create an app-specific password in your Apple Account security settings.',
  ics: 'Paste a sharing link from your calendar settings.',
};
export const CALENDAR_NUDGE_TITLE = CALENDAR_INTRO_TITLE;
export const CALENDAR_NUDGE_BODY = CALENDAR_EDU_LINES[0];

export function anyCalendarConnected(data: CalendarsResponse | CalendarConnection[] | null | undefined): boolean {
  const rows = Array.isArray(data) ? data : data?.connections;
  return (rows || []).some((c) => c.connected);
}

export function calendarRowSubtitle(c: CalendarConnection | null, title: string): string {
  if (c?.connected) return 'We’ll avoid times you’ve already booked here.';
  if (c?.status === 'needs_env') return 'Calendar connection is not available yet.';
  const provider = c?.provider || (title.includes('Google') ? 'google' : title.includes('Outlook') ? 'microsoft' : '');
  return CALENDAR_PROVIDER_HINTS[provider] || 'Connect the calendar you use to plan your day.';
}

export const PROVIDER_LABELS: Record<string, string> = {
  google: 'Google Calendar',
  microsoft: 'Outlook Calendar',
  apple: 'Apple Calendar',
  ics: 'Calendar link',
};

function detailOf(data: any, fallback: string) {
  const d = data?.detail;
  if (typeof d === 'string' && d) return d;
  if (d?.message) return String(d.message);
  return fallback;
}

export async function fetchVendorCalendars(): Promise<CalendarsResponse> {
  const headers = await actorHeaders();
  const res = await fetch(`${API}/api/vendor-os/calendars`, { headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(detailOf(data, `HTTP ${res.status}`));
  return data as CalendarsResponse;
}

export function connectionFor(
  data: CalendarsResponse | null,
  provider: CalendarProvider,
): CalendarConnection | null {
  return (data?.connections || []).find((c) => c.provider === provider) || null;
}

export function googleConnection(data: CalendarsResponse | null): CalendarConnection | null {
  return connectionFor(data, 'google');
}

export async function disconnectVendorCalendar(provider: string): Promise<void> {
  const headers = await actorHeaders();
  const res = await fetch(`${API}/api/vendor-os/calendars/${encodeURIComponent(provider)}`, {
    method: 'DELETE',
    headers,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(detailOf(data, `HTTP ${res.status}`));
  if (data.connected) throw new Error(data.detail || 'Could not disconnect');
}

async function syncCalendarsSoft() {
  try {
    const headers = await actorHeaders({ 'Content-Type': 'application/json' });
    await fetch(`${API}/api/vendor-os/calendars/sync`, { method: 'POST', headers, body: '{}' });
  } catch {
    /* sync is best-effort; GET status is the source of truth */
  }
}

async function postConnect(provider: string, body: Record<string, string> = {}) {
  const headers = await actorHeaders({ 'Content-Type': 'application/json' });
  const res = await fetch(`${API}/api/vendor-os/calendars/${provider}/connect`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(detailOf(data, `HTTP ${res.status}`));
  return data;
}

async function finishOauthInBrowser(oauthUrl: string, provider: CalendarProvider) {
  // Never intercept the API callback: it must validate and consume the signed state first.
  await WebBrowser.openAuthSessionAsync(oauthUrl, WEB_AVAILABILITY);
  const after = await fetchVendorCalendars();
  const row = connectionFor(after, provider);
  if (row?.connected) {
    await syncCalendarsSoft();
    return { connected: true as const };
  }
  return {
    connected: false as const,
    error: row?.detail || 'Finish calendar connect, then return here.',
  };
}

export const CALENDAR_SIGN_IN_AGAIN = 'Your sign-in changed while connecting your calendar. Please sign in again.';
const GOOGLE_CONNECT_FAILED = 'Could not finish Google Calendar connect. Please try again.';

type ConnectResult = { connected: boolean; error?: string };

/**
 * The vendor's own session must still be the one we started with. If it is
 * not, sign out locally and stop: never continue or report success under
 * another account.
 */
async function vendorSessionIntact(originalUserId: string): Promise<boolean> {
  try {
    const { data, error } = await supabase.auth.getSession();
    if (!error && data?.session?.user?.id === originalUserId) return true;
  } catch {
    /* fall through to sign out */
  }
  try {
    await supabase.auth.signOut({ scope: 'local' });
  } catch {
    /* best effort; the caller still fails closed */
  }
  return false;
}

let googleConnectInFlight: Promise<ConnectResult> | null = null;

/**
 * Honest Google Calendar Connect — Connected only if the API ping succeeded.
 * A second tap while one connect is open joins it instead of starting another.
 */
export async function connectGoogleCalendar(): Promise<ConnectResult> {
  if (!googleConnectInFlight) {
    googleConnectInFlight = runGoogleCalendarConnect().finally(() => {
      googleConnectInFlight = null;
    });
  }
  return googleConnectInFlight;
}

async function runGoogleCalendarConnect(): Promise<ConnectResult> {
  const { data: { session: original } } = await supabase.auth.getSession();
  if (!original?.access_token || !original.user?.id) return { connected: false, error: 'Sign in first' };
  const originalUserId = original.user.id;

  const start = await postConnect('google');
  if (start.connected) return { connected: true };
  if (start.status === 'needs_env' || start.status === 'unavailable') {
    return { connected: false, error: 'Google Calendar connection is not available yet.' };
  }

  if (start.oauth_url) {
    return finishOauthInBrowser(start.oauth_url, 'google');
  }

  if (start.mode === 'supabase' || start.connect_via === 'supabase') {
    // Separate in-memory client: the vendor's session and PKCE verifier are never touched.
    const oauth = createCalendarOAuthClient();
    let providerToken = '';
    let providerRefresh = '';
    try {
      const redirectTo = Linking.createURL('auth-callback');
      const { data, error } = await oauth.auth.signInWithOAuth({
        provider: 'google',
        options: {
          redirectTo,
          skipBrowserRedirect: true,
          scopes: start.scope || GOOGLE_CAL_SCOPE,
          queryParams: {
            access_type: 'offline',
            prompt: 'consent',
            ...(start.query_params || {}),
          },
        },
      });
      if (error || !data?.url) {
        await WebBrowser.openBrowserAsync(WEB_AVAILABILITY);
        return {
          connected: false,
          error: 'Opened Availability on the web to finish Connect. Return here after Google confirms.',
        };
      }
      const result = await WebBrowser.openAuthSessionAsync(data.url, redirectTo);
      if (result.type !== 'success' || !('url' in result) || !result.url) {
        if (result.type === 'cancel') return { connected: false, error: 'Connect cancelled' };
        return { connected: false, error: GOOGLE_CONNECT_FAILED };
      }
      // Exchange only a code returned on exactly the redirect we opened.
      const callback = parseAuthCallback(result.url, redirectTo);
      if (!callback.ok) {
        return { connected: false, error: AUTH_CALLBACK_MESSAGES[callback.reason] };
      }
      const { data: exchanged, error: exErr } = await oauth.auth.exchangeCodeForSession(callback.code);
      providerToken = exchanged?.session?.provider_token || '';
      providerRefresh = exchanged?.session?.provider_refresh_token || '';
      if (exErr || !providerRefresh) {
        return {
          connected: false,
          error: 'Google Calendar permission is required. Approve calendar access and try again.',
        };
      }
    } catch {
      return { connected: false, error: GOOGLE_CONNECT_FAILED };
    } finally {
      // Drop the temporary Google session; it only existed in this client's memory.
      await oauth.auth.signOut({ scope: 'local' }).catch(() => undefined);
    }

    if (!(await vendorSessionIntact(originalUserId))) {
      return { connected: false, error: CALENDAR_SIGN_IN_AGAIN };
    }

    try {
      const completeRes = await fetch(`${API}/api/vendor-os/calendars/google/complete-session`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${original.access_token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          provider_token: providerToken,
          provider_refresh_token: providerRefresh,
          scope: start.scope || GOOGLE_CAL_SCOPE,
        }),
      });
      const complete = await completeRes.json().catch(() => ({}));
      if (complete.connected || complete.status === 'connected') {
        await syncCalendarsSoft();
        return { connected: true };
      }
      return { connected: false, error: complete.detail || 'Could not verify Google Calendar access' };
    } catch {
      return { connected: false, error: GOOGLE_CONNECT_FAILED };
    }
  }

  return { connected: false, error: start.detail || 'Google Calendar is not ready to connect on this environment.' };
}

/** Honest Outlook Connect — API callback pings Graph before Connected. */
export async function connectMicrosoftCalendar(): Promise<{ connected: boolean; error?: string }> {
  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) return { connected: false, error: 'Sign in first' };
  const start = await postConnect('microsoft');
  if (start.connected) return { connected: true };
  if (start.status === 'needs_env' || start.status === 'unavailable') {
    return { connected: false, error: 'Outlook Calendar connection is not available yet.' };
  }
  if (start.oauth_url) {
    return finishOauthInBrowser(start.oauth_url, 'microsoft');
  }
  return { connected: false, error: start.detail || 'Outlook is not ready to connect on this environment.' };
}

export async function connectAppleCalendar(input: {
  caldav_url: string;
  app_password: string;
  username?: string;
}): Promise<{ connected: boolean; error?: string }> {
  const data = await postConnect('apple', {
    caldav_url: input.caldav_url,
    app_password: input.app_password,
    username: input.username || '',
  });
  if (data.connected) {
    await syncCalendarsSoft();
    return { connected: true };
  }
  return { connected: false, error: data.detail || 'Could not connect Apple Calendar' };
}

export async function connectIcsCalendar(icsUrl: string): Promise<{ connected: boolean; error?: string }> {
  const data = await postConnect('ics', { ics_url: icsUrl });
  if (data.connected) {
    await syncCalendarsSoft();
    return { connected: true };
  }
  return { connected: false, error: data.detail || 'Could not connect that calendar link' };
}
