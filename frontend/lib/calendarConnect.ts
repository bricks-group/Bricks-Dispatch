import * as WebBrowser from 'expo-web-browser';
import * as Linking from 'expo-linking';
import { supabase } from './supabase';
import { actorHeaders } from './api';

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
};

export const PROVIDER_LABELS: Record<string, string> = {
  google: 'Google Calendar',
  microsoft: 'Outlook / Microsoft 365',
  apple: 'Apple Calendar',
  ics: 'Other calendar (ICS)',
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

async function restoreSession(original: { access_token: string; refresh_token?: string | null }) {
  await supabase.auth.setSession({
    access_token: original.access_token,
    refresh_token: original.refresh_token || original.access_token,
  });
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

async function finishOauthInBrowser(oauthUrl: string, redirectUri: string | undefined, provider: CalendarProvider) {
  await WebBrowser.openAuthSessionAsync(oauthUrl, redirectUri || WEB_AVAILABILITY);
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

/** Honest Google Calendar Connect — Connected only if the API ping succeeded. */
export async function connectGoogleCalendar(): Promise<{ connected: boolean; error?: string }> {
  const { data: { session: original } } = await supabase.auth.getSession();
  if (!original?.access_token) return { connected: false, error: 'Sign in first' };

  const start = await postConnect('google');
  if (start.connected) return { connected: true };
  if (start.status === 'needs_env' || start.status === 'unavailable') {
    return { connected: false, error: start.detail || 'Google Calendar is not configured on this environment.' };
  }

  if (start.oauth_url) {
    return finishOauthInBrowser(start.oauth_url, start.redirect_uri, 'google');
  }

  if (start.mode === 'supabase' || start.connect_via === 'supabase') {
    const redirectTo = Linking.createURL('auth-callback');
    const { data, error } = await supabase.auth.signInWithOAuth({
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
      await restoreSession(original);
      if (result.type === 'cancel') return { connected: false, error: 'Connect cancelled' };
      return { connected: false, error: 'Could not finish Google Calendar connect' };
    }
    try {
      const url = new URL(result.url);
      const code = url.searchParams.get('code');
      if (!code) {
        return { connected: false, error: 'Google did not return an auth code' };
      }
      let providerToken = '';
      let providerRefresh = '';
      try {
        const { data: exchanged, error: exErr } = await supabase.auth.exchangeCodeForSession(code);
        providerToken = exchanged?.session?.provider_token || '';
        providerRefresh = exchanged?.session?.provider_refresh_token || '';
        if (exErr || !providerRefresh) {
          return {
            connected: false,
            error: 'Google Calendar permission is required. Approve calendar access and try again.',
          };
        }
      } finally {
        await restoreSession(original);
      }
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
    } catch (e: any) {
      await restoreSession(original);
      return { connected: false, error: e?.message || 'Could not finish Google Calendar connect' };
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
    return { connected: false, error: start.detail || 'Outlook is not configured on this environment.' };
  }
  if (start.oauth_url) {
    return finishOauthInBrowser(start.oauth_url, start.redirect_uri, 'microsoft');
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
  return { connected: false, error: data.detail || 'Could not connect that ICS calendar' };
}
