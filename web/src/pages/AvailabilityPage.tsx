import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { startGoogleCalendarOAuth } from '../lib/googleCalendarConnect';
import BricksLoader from '../components/BricksLoader';
import { CALENDAR_EDU_LINES, CALENDAR_INTRO_TITLE, CALENDAR_PROVIDER_HINTS } from '../lib/calendarCopy';

type Availability = {
  work_start: string;
  work_end: string;
  jobs_per_day: number;
  available: boolean;
  blocked_windows: Array<{ start: string; end: string }>;
};

type CalStatus = {
  provider: string;
  connected: boolean;
  status: string;
  detail: string;
  last_synced_at?: string | null;
  last_sync_status?: string | null;
};

function syncedLabel(c: CalStatus): string {
  if (c.last_sync_status === 'error') return 'We couldn’t check your calendar. Please try again.';
  if (!c.last_synced_at) return 'We’ll check your calendar shortly.';
  const mins = Math.max(0, Math.round((Date.now() - new Date(c.last_synced_at).getTime()) / 60000));
  if (mins < 1) return 'Your calendar was checked just now.';
  if (mins < 60) return `Your calendar was checked ${mins} minutes ago.`;
  return `Your calendar was checked ${Math.round(mins / 60)} hours ago.`;
}

type CalResponse = {
  connections: CalStatus[];
  oauth: { google_configured: boolean; microsoft_configured?: boolean; google_connect_via?: string | null };
  copy: string;
};

const PROVIDER_LABELS: Record<string, string> = {
  google: 'Google Calendar',
  microsoft: 'Outlook Calendar',
  apple: 'Apple Calendar',
  ics: 'Calendar link',
};

const CONNECT_PROVIDERS = new Set(['google', 'microsoft', 'apple', 'ics']);

export function AvailabilityPage() {
  const [data, setData] = useState<Availability | null>(null);
  const [cals, setCals] = useState<CalResponse | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState('');
  const [icsUrl, setIcsUrl] = useState('');
  const [appleUrl, setAppleUrl] = useState('');
  const [applePass, setApplePass] = useState('');
  const [err, setErr] = useState('');

  const load = () => {
    api.get<Availability>('/api/vendor-os/availability').then(setData).catch((e) => setErr(e.message));
    api.get<CalResponse>('/api/vendor-os/calendars').then(setCals).catch(() => setCals(null));
  };

  useEffect(() => { load(); }, []);

  /** Pull free/busy right away so a fresh connection counts before the hourly sync. */
  const syncNow = () => {
    api.post('/api/vendor-os/calendars/sync', {}).then(load).catch(() => {});
  };

  // Surface OAuth callback query params (calendar_connected / calendar_error)
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const connected = params.get('calendar_connected');
      const calErr = params.get('calendar_error');
      if (calErr) {
        setErr(calErr);
      } else if (connected) {
        setSaved(true);
        setTimeout(() => setSaved(false), 4000);
        syncNow();
      }
      if (connected || calErr) {
        const url = new URL(window.location.href);
        url.searchParams.delete('calendar_connected');
        url.searchParams.delete('calendar_error');
        window.history.replaceState({}, '', url.pathname + (url.search || '') + url.hash);
      }
    } catch {
      /* ignore */
    }
  }, []);

  const save = async () => {
    if (!data) return;
    await api.put('/api/vendor-os/availability', data);
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  const connect = async (provider: string, body?: Record<string, string>) => {
    setBusy(provider);
    setErr('');
    try {
      const res = await api.post<{
        connected?: boolean;
        status?: string;
        detail?: string;
        oauth_url?: string | null;
        mode?: string | null;
        connect_via?: string | null;
        redirect_uri?: string | null;
        scope?: string | null;
        query_params?: Record<string, string>;
      }>(`/api/vendor-os/calendars/${provider}/connect`, body || {});
      if (provider === 'google' && (res.mode === 'supabase' || res.connect_via === 'supabase' || res.oauth_url)) {
        const started = await startGoogleCalendarOAuth(res);
        if (started.error) setErr(started.error);
        if (started.redirected) return; // navigating away
      } else if (res.oauth_url) {
        window.location.href = res.oauth_url;
        return;
      }
      if (!res.connected && !res.oauth_url) {
        setErr(res.status === 'needs_env' ? 'Calendar connection is not available yet.' : res.detail || 'We couldn’t connect your calendar. Please try again.');
      }
      if (res.connected) syncNow();
      load();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  const disconnect = async (provider: string) => {
    setBusy(provider);
    try {
      await api.delete(`/api/vendor-os/calendars/${provider}`);
      load();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="page-content calendar-availability">
      <div className="page-header">
        <div>
          <h1>Availability</h1>
          <p>Set your working hours and connect the calendars you use.</p>
        </div>
      </div>

      <section className="card full" data-testid="calendar-edu">
        <h3>{CALENDAR_INTRO_TITLE}</h3>
        <p className="muted calendar-intro">{CALENDAR_EDU_LINES.join(' ')}</p>
      </section>

      {!data && <p className="muted"><BricksLoader variant="assemble" size={40} style={{ display: 'block', marginBottom: 12 }} />Loading</p>}
      {err && <p className="error">{err}</p>}

      {data && (
        <section className="card full">
          <h3>Working hours and capacity</h3>
          <div className="stack">
            <label>
              <input
                type="checkbox"
                checked={data.available}
                onChange={(e) => setData({ ...data, available: e.target.checked })}
              />
              Available for assignments
            </label>
            <div className="row" style={{ padding: 0 }}>
              <label>
                Start
                <input type="time" value={data.work_start} onChange={(e) => setData({ ...data, work_start: e.target.value })} />
              </label>
              <label>
                End
                <input type="time" value={data.work_end} onChange={(e) => setData({ ...data, work_end: e.target.value })} />
              </label>
              <label>
                Daily job limit
                <input type="number" min={1} value={data.jobs_per_day} onChange={(e) => setData({ ...data, jobs_per_day: Number(e.target.value) })} style={{ width: 80 }} />
              </label>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <button type="submit" className="btn-primary" onClick={save}>Save</button>
              {saved && <span className="muted">Saved</span>}
            </div>
          </div>
        </section>
      )}

      <section className="card full">
        <h3>Calendars</h3>
        <ul className="list">
          {(cals?.connections || [
            { provider: 'google', connected: false, status: 'not_connected', detail: '' },
            { provider: 'microsoft', connected: false, status: 'not_connected', detail: '' },
            { provider: 'apple', connected: false, status: 'needs_app_password', detail: '' },
            { provider: 'ics', connected: false, status: 'not_connected', detail: '' },
          ])
            .filter((c) => CONNECT_PROVIDERS.has(c.provider) || c.connected)
            .map((c) => (
            <li key={c.provider} className="list-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <div>
                  <strong>{PROVIDER_LABELS[c.provider] || c.provider}</strong>
                  <p className="muted calendar-provider-hint">
                    {c.connected ? syncedLabel(c) : c.status === 'needs_env'
                      ? 'Calendar connection is not available yet.' : CALENDAR_PROVIDER_HINTS[c.provider]}
                  </p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  {c.connected ? (
                    <button type="button" disabled={busy === c.provider} onClick={() => disconnect(c.provider)}>
                      Disconnect
                    </button>
                  ) : c.provider === 'google' || c.provider === 'microsoft' ? (
                    <button
                      type="button"
                      disabled={busy === c.provider || c.status === 'needs_env'}
                      aria-label={`Connect ${PROVIDER_LABELS[c.provider]}`}
                      onClick={() => connect(c.provider)}
                    >
                      {busy === c.provider ? 'Connecting' : 'Connect'}
                    </button>
                  ) : null}
                </div>
              </div>
              {c.provider === 'apple' && !c.connected ? (
                <div className="row" style={{ padding: 0, gap: 8, flexWrap: 'wrap' }}>
                  <input
                    aria-label="iCloud calendar address"
                    placeholder="iCloud calendar address"
                    value={appleUrl}
                    onChange={(e) => setAppleUrl(e.target.value)}
                    style={{ flex: 1, minWidth: 200 }}
                  />
                  <input
                    type="password"
                    placeholder="App-specific password"
                    value={applePass}
                    onChange={(e) => setApplePass(e.target.value)}
                    style={{ flex: 1, minWidth: 160 }}
                  />
                  <button
                    type="button"
                    disabled={busy === 'apple' || !appleUrl || !applePass}
                    onClick={() => connect('apple', { caldav_url: appleUrl, app_password: applePass })}
                  >
                    Connect
                  </button>
                </div>
              ) : null}
              {c.provider === 'ics' && !c.connected ? (
                <div className="row calendar-feed-form" style={{ padding: 0, gap: 8 }}>
                  <input
                    aria-label="Calendar link"
                    placeholder="Paste your calendar link"
                    value={icsUrl}
                    onChange={(e) => setIcsUrl(e.target.value)}
                    style={{ flex: 1, minWidth: 0 }}
                  />
                  <button
                    type="button"
                    disabled={busy === 'ics' || !icsUrl}
                    onClick={() => connect('ics', { ics_url: icsUrl })}
                  >
                    Connect
                  </button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
