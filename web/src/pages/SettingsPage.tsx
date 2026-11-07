import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { supabase } from '../lib/supabase';
import { requestAccountDeletion, DELETION_INFO_URL, SUPPORT_EMAIL, type DeletionResult } from '../lib/web-account';
import BricksLoader from '../components/BricksLoader';

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
        {!status && <p className="muted"><BricksLoader variant="assemble" size={40} style={{ display: 'block', marginBottom: 12 }} />Loading…</p>}
        {status && (
          <div className="row">
            <span className={`badge ${status.connected ? 'badge-green' : 'badge-attn'}`}>
              {status.connected
                ? 'Connected'
                : status.connect_status === 'pending' || status.connect_status === 'restricted'
                  ? (status.connect_status === 'restricted' ? 'Restricted' : 'Pending')
                  : 'Not connected'}
            </span>
            <span className="muted">
              {status.connected
                ? 'Payouts for your jobs go to this Stripe account.'
                : status.connect_status === 'pending'
                  ? 'Finish Stripe setup to turn on payouts.'
                  : status.connect_status === 'restricted'
                    ? 'Stripe needs more details before it can pay out. Open Stripe to finish.'
                    : 'Connect a Stripe account to receive job payouts. Owner payouts are the full package with $0 deduction.'}
            </span>
          </div>
        )}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button type="button" onClick={connect} disabled={starting}>
            {status?.connected
              ? 'Update Stripe account'
              : status?.connect_status === 'pending' || status?.connect_status === 'restricted'
                ? 'Finish Stripe setup'
                : 'Connect Stripe'}
          </button>
          {error && <span className="error">{error}</span>}
        </div>
      </div>
    </section>
  );
}

/** Same request the native GO app files (Settings → Delete account). */
function DeleteAccountSection() {
  const [step, setStep] = useState<'idle' | 'confirm' | 'sending'>('idle');
  const [reason, setReason] = useState('');
  const [result, setResult] = useState<DeletionResult | null>(null);

  const send = async () => {
    setStep('sending');
    const { data } = await supabase.auth.getUser();
    const r = await requestAccountDeletion(supabase as never, data.user ? { id: data.user.id, email: data.user.email } : null, reason);
    setResult(r);
    setStep('idle');
  };

  return (
    <section className="card full" data-testid="delete-account">
      <h3>Delete account</h3>
      <div className="stack">
        <p className="muted" style={{ margin: 0 }}>
          Removes your Bricks login, profile and payout details within 30 days. Records Bricks must keep for tax and
          client delivery (completed jobs, invoices) are kept as required by law. <a href={DELETION_INFO_URL} target="_blank" rel="noreferrer">Details</a>
        </p>
        {result?.ok && (
          <p data-testid="delete-account-done" style={{ margin: 0 }}>
            {result.already ? 'Your deletion request is already in. ' : 'Request received. '}
            We will email you when it is done. Questions: {SUPPORT_EMAIL}
          </p>
        )}
        {result && !result.ok && (
          <p className="error" style={{ padding: 0 }}>
            {result.error} <a href={result.mailto}>Email {SUPPORT_EMAIL}</a>
          </p>
        )}
        {!result?.ok && step === 'idle' && (
          <div>
            <button type="button" className="btn-danger" data-testid="delete-account-start" onClick={() => setStep('confirm')}>
              Delete my account
            </button>
          </div>
        )}
        {step !== 'idle' && (
          <>
            <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
              Reason (optional)
              <input value={reason} onChange={(e) => setReason(e.target.value)} style={{ width: '100%', maxWidth: 480 }} />
            </label>
            <div className="row">
              <button type="button" className="btn-danger" data-testid="delete-account-confirm" onClick={send} disabled={step === 'sending'}>
                {step === 'sending' && <BricksLoader variant="cycle" size={16} color="currentColor" style={{ marginRight: 8 }} />}{step === 'sending' ? 'Sending…' : 'Yes, delete my account'}
              </button>
              <button type="button" onClick={() => setStep('idle')} disabled={step === 'sending'}>Cancel</button>
            </div>
          </>
        )}
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

      {!data && <p className="muted"><BricksLoader variant="assemble" size={40} style={{ display: 'block', marginBottom: 12 }} />Loading…</p>}

      {data && (
        <section className="card full">
          <h3>Company</h3>
          <div className="stack">
            <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
              Company name
              <input value={data.company_name} onChange={(e) => setData({ ...data, company_name: e.target.value })} style={{ width: '100%', maxWidth: 320 }} />
            </label>
            <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
              Service areas
              <input value={serviceAreasRaw} onChange={(e) => setServiceAreasRaw(e.target.value)} placeholder="City, City, …" style={{ width: '100%', maxWidth: 480 }} />
              <span className="muted">Comma-separated</span>
            </label>
            <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
              Support email
              <input value={data.support_email} onChange={(e) => setData({ ...data, support_email: e.target.value })} type="email" style={{ width: '100%', maxWidth: 320 }} />
            </label>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <button type="submit" className="btn-primary" onClick={save}>Save</button>
              {saved && <span className="muted">Saved</span>}
            </div>
          </div>
        </section>
      )}

      <DeleteAccountSection />
    </div>
  );
}
