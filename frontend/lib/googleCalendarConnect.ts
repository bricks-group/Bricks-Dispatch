import * as WebBrowser from 'expo-web-browser';
import * as Linking from 'expo-linking';
import { supabase } from './supabase';
import { actorHeaders } from './api';

WebBrowser.maybeCompleteAuthSession();

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';
export const GOOGLE_CAL_SCOPE = 'https://www.googleapis.com/auth/calendar.freebusy';
export const WEB_AVAILABILITY = 'https://go.brxs.ca/availability';

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
  oauth?: { google_configured?: boolean; google_connect_via?: string | null };
  copy?: string;
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

export function googleConnection(data: CalendarsResponse | null): CalendarConnection | null {
  return (data?.connections || []).find((c) => c.provider === 'google') || null;
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

/** Honest Google Calendar Connect — Connected only if the API ping succeeded. */
export async function connectGoogleCalendar(): Promise<{ connected: boolean; error?: string }> {
  const { data: { session: original } } = await supabase.auth.getSession();
  if (!original?.access_token) return { connected: false, error: 'Sign in first' };

  const headers = await actorHeaders({ 'Content-Type': 'application/json' });
  const startRes = await fetch(`${API}/api/vendor-os/calendars/google/connect`, {
    method: 'POST',
    headers,
    body: '{}',
  });
  const start = await startRes.json().catch(() => ({}));
  if (!startRes.ok) {
    return { connected: false, error: detailOf(start, `HTTP ${startRes.status}`) };
  }
  if (start.connected) return { connected: true };
  if (start.status === 'needs_env' || start.status === 'unavailable') {
    return { connected: false, error: start.detail || 'Google Calendar is not configured on this environment.' };
  }

  if (start.oauth_url) {
    await WebBrowser.openAuthSessionAsync(start.oauth_url, start.redirect_uri || WEB_AVAILABILITY);
    const after = await fetchVendorCalendars();
    const g = googleConnection(after);
    if (g?.connected) {
      await syncCalendarsSoft();
      return { connected: true };
    }
    return { connected: false, error: g?.detail || 'Finish Google Calendar connect, then return here.' };
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
        // Always put the vendor session back — complete-session authenticates with it.
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
