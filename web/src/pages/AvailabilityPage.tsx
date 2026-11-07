import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { startGoogleCalendarOAuth } from '../lib/googleCalendarConnect';
import BricksLoader from '../components/BricksLoader';
import { CalendarConnectNudge } from '../components/CalendarConnectNudge';
import { CALENDAR_EDU_LINES, CALENDAR_USED_FOR_OFFERS } from '../lib/calendarCopy';

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
  if (c.last_sync_status === 'error') return 'Couldn’t read this calendar on the last sync';
  if (!c.last_synced_at) return 'Syncing free/busy…';
  const mins = Math.max(0, Math.round((Date.now() - new Date(c.last_synced_at).getTime()) / 60000));
  if (mins < 1) return 'Free/busy synced just now';
  if (mins < 60) return `Free/busy synced ${mins} min ago`;
  return `Free/busy synced ${Math.round(mins / 60)} h ago`;
}

type CalResponse = {
  connections: CalStatus[];
  oauth: { google_configured: boolean; microsoft_configured?: boolean; google_connect_via?: string | null };
  copy: string;
};

const PROVIDER_LABELS: Record<string, string> = {
  google: 'Google Calendar',
  microsoft: 'Outlook / Microsoft 365',
  apple: 'Apple Calendar (iCloud CalDAV)',
  ics: 'Other calendar (ICS link)',
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
      if (res.status === 'needs_env' || res.status === 'needs_app_password' || res.status === 'noscope') {
        setErr(res.detail || 'Not connected');
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

  const calendarsLoaded = cals !== null;
  const anyConnected = (cals?.connections || []).some((c) => c.connected);

  return (
    <div className="page-content">
      <div className="page-header">
        <div>
          <h1>Availability</h1>
          <p>Working hours, capacity, and calendars that feed auto-assign</p>
        </div>
      </div>

      <section className="card full" data-testid="calendar-edu">
        <h3>How auto-assign uses your calendar</h3>
        <ul className="muted" style={{ margin: 0, padding: '14px 20px 18px 36px', maxWidth: 680, fontSize: 13, lineHeight: 1.55 }}>
          {CALENDAR_EDU_LINES.map((line) => (
            <li key={line} style={{ marginBottom: 8 }}>{line}</li>
          ))}
        </ul>
      </section>

      {calendarsLoaded && !anyConnected && <CalendarConnectNudge linkToAvailability={false} />}

      {!data && <p className="muted"><BricksLoader variant="assemble" size={40} style={{ display: 'block', marginBottom: 12 }} />Loading…</p>}
      {err && <p className="error">{err}</p>}

      {data && (
        <section className="card full">
          <h3>Working hours &amp; capacity</h3>
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
                Max jobs / day
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
            { provider: 'google', connected: false, status: 'not_connected', detail: 'Connect Google Calendar' },
            { provider: 'microsoft', connected: false, status: 'not_connected', detail: 'Connect Outlook / Microsoft 365' },
            { provider: 'apple', connected: false, status: 'needs_app_password', detail: 'Apple needs app-specific password' },
            { provider: 'ics', connected: false, status: 'not_connected', detail: 'Paste a public ICS URL' },
          ])
            .filter((c) => CONNECT_PROVIDERS.has(c.provider) || c.connected)
            .map((c) => (
            <li key={c.provider} className="list-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <div>
                  <strong>{PROVIDER_LABELS[c.provider] || c.provider}</strong>
                  <p className="muted" style={{ margin: '4px 0 0', padding: 0 }}>
                    {CALENDAR_USED_FOR_OFFERS}
                  </p>
                  <p className="muted" style={{ margin: '4px 0 0', padding: 0 }}>
                    {c.connected ? `Connected · ${syncedLabel(c)}` : c.detail}
                    {c.status === 'needs_env' ? ' · OAuth not configured on this environment' : ''}
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
                      onClick={() => connect(c.provider)}
                    >
                      {busy === c.provider ? '…' : c.provider === 'google' ? 'Connect Google' : 'Connect Outlook'}
                    </button>
                  ) : null}
                </div>
              </div>
              {c.provider === 'apple' && !c.connected ? (
                <div className="row" style={{ padding: 0, gap: 8, flexWrap: 'wrap' }}>
                  <input
                    placeholder="CalDAV URL (iCloud)"
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
                    Save Apple
                  </button>
                </div>
              ) : null}
              {c.provider === 'ics' && !c.connected ? (
                <div className="row" style={{ padding: 0, gap: 8 }}>
                  <input
                    placeholder="https://…/calendar.ics"
                    value={icsUrl}
                    onChange={(e) => setIcsUrl(e.target.value)}
                    style={{ flex: 1, minWidth: 220 }}
                  />
                  <button
                    type="button"
                    disabled={busy === 'ics' || !icsUrl}
                    onClick={() => connect('ics', { ics_url: icsUrl })}
                  >
                    Save ICS
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
