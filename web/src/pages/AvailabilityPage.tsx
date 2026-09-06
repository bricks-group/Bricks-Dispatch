import { useEffect, useState } from 'react';
import { api } from '../lib/api';

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
};

type CalResponse = {
  connections: CalStatus[];
  oauth: { google_configured: boolean; microsoft_configured: boolean };
  copy: string;
};

const PROVIDER_LABELS: Record<string, string> = {
  google: 'Google Calendar',
  microsoft: 'Outlook / Microsoft 365',
  apple: 'Apple Calendar (CalDAV)',
  ics: 'ICS URL',
};

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
      }>(`/api/vendor-os/calendars/${provider}/connect`, body || {});
      if (res.oauth_url) {
        window.open(res.oauth_url, '_blank', 'noopener,noreferrer');
      }
      if (res.status === 'needs_env' || res.status === 'needs_app_password') {
        setErr(res.detail || 'Not connected');
      }
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

  const anyConnected = (cals?.connections || []).some((c) => c.connected);
  const assignable = !!data?.available;

  return (
    <div className="page-content">
      <div className="page-header">
        <div>
          <h1>Availability</h1>
          <p>Working hours, capacity, and calendars that feed auto-assign</p>
        </div>
      </div>

      <section className="card full">
        <h3>How auto-assign uses your calendar</h3>
        <p style={{ margin: '8px 0 0', maxWidth: 640 }}>
          {cals?.copy ||
            'Connecting a calendar tells Bricks when you’re free so auto-assign only offers jobs that fit your real schedule. Busy on any connected calendar = you won’t get that slot. Manual hours still apply if no calendar is connected.'}
        </p>
      </section>

      {assignable && !anyConnected && (
        <section className="card full" style={{ borderColor: 'var(--brick, #c45)' }}>
          <h3>Connect a calendar</h3>
          <p className="muted" style={{ margin: '8px 0 0' }}>
            You’re marked available, but no calendar is connected yet. Connect Google, Outlook, Apple, or an ICS feed
            so auto-assign can skip slots you’re already busy.
          </p>
        </section>
      )}

      {!data && <p className="muted">Loading…</p>}
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
              <button type="submit" onClick={save}>Save</button>
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
          ]).map((c) => (
            <li key={c.provider} className="list-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <div>
                  <strong>{PROVIDER_LABELS[c.provider] || c.provider}</strong>
                  <p className="muted" style={{ margin: '4px 0 0', padding: 0 }}>
                    {c.connected ? 'Connected' : c.detail}
                    {c.status === 'needs_env' ? ' · OAuth not configured on this environment' : ''}
                  </p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  {c.connected ? (
                    <button type="button" disabled={busy === c.provider} onClick={() => disconnect(c.provider)}>
                      Disconnect
                    </button>
                  ) : c.provider === 'google' || c.provider === 'microsoft' ? (
                    <button type="button" disabled={busy === c.provider} onClick={() => connect(c.provider)}>
                      {busy === c.provider ? '…' : `Connect ${c.provider === 'google' ? 'Google' : 'Outlook'}`}
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
