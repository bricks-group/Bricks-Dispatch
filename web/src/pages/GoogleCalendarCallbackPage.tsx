import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { supabase } from '../lib/supabase';

/** Land here after Supabase Google OAuth with calendar scopes (go.brxs.ca/auth/google-calendar). */

async function consumeSupabaseSession() {
  const { data: { session: existing } } = await supabase.auth.getSession();
  if (existing?.provider_token || existing?.provider_refresh_token) return existing;

  const search = new URLSearchParams(window.location.search);
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''));

  const pkceCode = search.get('code');
  const state = search.get('state') || '';
  // Direct Google state is a bare member uuid (no dot) — leave that to API callback.
  if (pkceCode && !/^[0-9a-f-]{36}$/i.test(state)) {
    const { data, error } = await supabase.auth.exchangeCodeForSession(pkceCode);
    if (!error && data?.session) return data.session;
  }

  const access_token = hash.get('access_token');
  const refresh_token = hash.get('refresh_token');
  if (access_token && refresh_token) {
    const { data, error } = await supabase.auth.setSession({ access_token, refresh_token });
    if (!error && data?.session) return data.session;
  }

  for (let i = 0; i < 10; i++) {
    const { data } = await supabase.auth.getSession();
    const s = data?.session;
    if (s?.provider_token || s?.provider_refresh_token) return s;
    if (s?.access_token && i > 3) return s;
    await new Promise((r) => setTimeout(r, 200));
  }
  return existing || null;
}

export function GoogleCalendarCallbackPage() {
  const hasProcessed = useRef(false);
  const navigate = useNavigate();

  useEffect(() => {
    if (hasProcessed.current) return;
    hasProcessed.current = true;

    const go = (q: string) => navigate(`/availability?${q}`, { replace: true });

    (async () => {
      const search = new URLSearchParams(window.location.search);
      const err = search.get('error');
      if (err) return go(`calendar_error=${encodeURIComponent(err)}`);

      const session = await consumeSupabaseSession();
      if (!session?.access_token) {
        return go(`calendar_error=${encodeURIComponent('Could not finish Google sign-in session')}`);
      }

      const providerToken = session.provider_token || '';
      const providerRefresh = session.provider_refresh_token || '';
      if (!providerRefresh) {
        return go(
          `calendar_error=${encodeURIComponent(
            'Google Calendar permission is required. Approve calendar access and try Connect again.',
          )}`,
        );
      }

      try {
        const res = await api.post<{ connected?: boolean; status?: string; detail?: string }>(
          '/api/vendor-os/calendars/google/complete-session',
          {
            provider_token: providerToken,
            provider_refresh_token: providerRefresh,
          },
        );
        if (res.connected || res.status === 'connected') {
          return go('calendar_connected=google');
        }
        return go(
          `calendar_error=${encodeURIComponent(res.detail || 'Could not connect Google Calendar')}`,
        );
      } catch (e) {
        return go(`calendar_error=${encodeURIComponent((e as Error).message || 'Connect failed')}`);
      }
    })();
  }, [navigate]);

  return (
    <p style={{ padding: 28, color: 'var(--c-ink-mute)' }}>Finishing Google Calendar connect…</p>
  );
}
