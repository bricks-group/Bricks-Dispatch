import { useEffect, useState } from 'react';
import { api } from '../lib/api';

type Settings = {
  company_name: string;
  service_areas: string[];
  support_email: string;
};

type StripeConnectStatus = {
  connected: boolean;
  connect_status: string;
};

function StripeConnectSection() {
  const [status, setStatus] = useState<StripeConnectStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');

  const load = () => {
    api.get<StripeConnectStatus>('/api/vendor-os/stripe-connect/status').then(setStatus).catch(() => setStatus(null));
  };

  useEffect(() => {
    load();
    // Coming back from Stripe's hosted onboarding lands here with ?stripe=return —
    // re-check status so a completed onboarding shows as connected right away.
    if (new URLSearchParams(window.location.search).get('stripe')) {
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  const connect = async () => {
    setStarting(true);
    setError('');
    try {
      const returnUrl = `${window.location.origin}/settings?stripe=return`;
      const res = await api.post<{ url: string }>('/api/vendor-os/stripe-connect/onboard', { return_url: returnUrl });
      window.location.href = res.url;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start Stripe onboarding');
      setStarting(false);
    }
  };

  return (
    <section className="card full">
      <h3>Payouts</h3>
      <div className="stack">
        {!status && <p className="muted">Loading…</p>}
        {status && (
          <div className="row">
            <span className={`badge ${status.connected ? 'badge-green' : 'badge-gray'}`}>
              {status.connected ? 'Connected' : 'Not connected'}
            </span>
            <span className="muted">
              {status.connected
                ? 'Stripe is connected — job payouts go to this account.'
                : 'Connect a Stripe account to receive job payouts. Owner payouts are the full package with $0 deduction.'}
            </span>
          </div>
        )}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button type="button" onClick={connect} disabled={starting}>
            {status?.connected ? 'Update Stripe account' : 'Connect Stripe'}
          </button>
          {error && <span className="error">{error}</span>}
        </div>
      </div>
    </section>
  );
}

export function SettingsPage() {
  const [data, setData] = useState<Settings | null>(null);
  const [serviceAreasRaw, setServiceAreasRaw] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.get<Settings>('/api/vendor-os/settings').then((res) => {
      setData(res);
      setServiceAreasRaw(res.service_areas.join(', '));
    });
  }, []);

  const save = async () => {
    if (!data) return;
    const payload = {
      ...data,
      service_areas: serviceAreasRaw.split(',').map((v) => v.trim()).filter(Boolean),
    };
    await api.put('/api/vendor-os/settings', payload);
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  return (
    <div className="page-content">
      <div className="page-header">
        <div>
          <h1>Settings</h1>
          <p>Company name, service areas, and contact</p>
        </div>
      </div>

      <StripeConnectSection />

      {!data && <p className="muted">Loading…</p>}

      {data && (
        <section className="card full">
          <h3>Vendor profile</h3>
          <div className="stack">
            <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
              Company name
              <input value={data.company_name} onChange={(e) => setData({ ...data, company_name: e.target.value })} style={{ width: 320 }} />
            </label>
            <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
              Service areas
              <input value={serviceAreasRaw} onChange={(e) => setServiceAreasRaw(e.target.value)} placeholder="City, City, …" style={{ width: 480 }} />
              <span className="muted">Comma-separated</span>
            </label>
            <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
              Support email
              <input value={data.support_email} onChange={(e) => setData({ ...data, support_email: e.target.value })} type="email" style={{ width: 320 }} />
            </label>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <button type="submit" onClick={save}>Save</button>
              {saved && <span className="muted">Saved</span>}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
