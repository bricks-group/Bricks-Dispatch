import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { api, startPayoutSetup } from '../lib/api';

type Status = { payout_required?: boolean; connected: boolean; connect_status: string };

/**
 * "Connect payouts" banner for anyone Bricks pays per job whose Stripe payout
 * account isn't ready. Vendors who went live before the onboarding checklist
 * had a payouts step (grandfathered) and the owner never see that step, so this
 * is how they get asked. In-app only; no email.
 */
export default function PayoutsBanner() {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const { pathname } = useLocation();

  useEffect(() => {
    api.get<Status>('/api/vendor-os/stripe-connect/status').then(setStatus).catch(() => setStatus(null));
  }, [pathname]);

  if (!status || !status.payout_required || status.connected) return null;
  if (pathname.startsWith('/onboarding') || pathname.startsWith('/settings')) return null;

  const pending = status.connect_status === 'pending' || status.connect_status === 'restricted';
  const go = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await startPayoutSetup(`${window.location.origin}/settings?stripe=return`);
      window.location.href = res.url;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open Stripe');
      setBusy(false);
    }
  };

  return (
    <div className="offline-bar" role="status" data-testid="payouts-banner"
      style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
      <span>
        {pending
          ? 'Stripe still needs a few details before we can pay you. Finish your payout setup.'
          : 'Connect payouts so we can pay you after each job.'}
        {error && <span style={{ marginLeft: 8 }}>{error}</span>}
      </span>
      <button type="button" className="btn-primary btn-brick" disabled={busy} onClick={go} data-testid="payouts-banner-connect">
        {busy ? 'Opening Stripe…' : pending ? 'Finish setup' : 'Connect payouts'}
      </button>
    </div>
  );
}
