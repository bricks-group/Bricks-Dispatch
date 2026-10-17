import { FormEvent, useEffect, useState } from 'react';
import { authCallbackUrl, logout } from '../lib/api';
import { supabase } from '../lib/supabase';
import { RESEND_COOLDOWN_SECONDS, addEmailErrorMessage, formatPhoneDisplay, isValidEmail } from '../lib/phone-auth';

/**
 * Phone-OTP accounts add and confirm an email before GO opens or they apply.
 * Vendor onboarding, IC agreement and payouts are keyed by email, so the email
 * must be Supabase-confirmed (updateUser → confirmation link). GO never links a
 * member by a typed, unconfirmed email.
 */
export function AddEmailPage({ onConfirmed, onLogout }: { onConfirmed: () => void; onLogout?: () => void }) {
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [sentTo, setSentTo] = useState('');
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [busy, setBusy] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => {
      const u = data?.user as { phone?: string; new_email?: string } | null;
      if (u?.phone) setPhone(`+${String(u.phone).replace(/^\+/, '')}`);
      if (u?.new_email) { setEmail(u.new_email); setSentTo(u.new_email); }
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const t = setTimeout(() => setCooldown((c) => Math.max(0, c - 1)), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  const sendLink = async (event?: FormEvent) => {
    event?.preventDefault();
    if (busy || cooldown > 0) return;
    const clean = email.trim().toLowerCase();
    setError(''); setInfo('');
    if (!isValidEmail(clean)) { setError('Enter a valid email address.'); return; }
    setBusy(true);
    try {
      const { error: err } = await supabase.auth.updateUser({ email: clean }, { emailRedirectTo: authCallbackUrl() });
      if (err) { setError(addEmailErrorMessage(err)); return; }
      setSentTo(clean);
      setCooldown(RESEND_COOLDOWN_SECONDS);
      setInfo(`We sent a confirmation link to ${clean}. Open it, then come back here.`);
    } finally {
      setBusy(false);
    }
  };

  const checkConfirmed = async () => {
    setError(''); setBusy(true);
    try {
      await supabase.auth.refreshSession();
      const { data } = await supabase.auth.getUser();
      if (!data?.user?.email) {
        setError("That email isn't confirmed yet. Open the link in the email we sent.");
        return;
      }
      onConfirmed();
    } catch {
      setError('Could not check your email yet. Try again in a moment.');
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    if (onLogout) onLogout();
    else {
      await logout();
      window.location.href = '/login';
    }
  };

  return (
    <div className="login-wrap" data-testid="add-email-page">
      <form className="login-card" onSubmit={sendLink}>
        <h2>Add your email</h2>
        <p className="muted" style={{ margin: 0 }}>
          {phone ? `You're signed in with ${formatPhoneDisplay(phone)}. ` : ''}
          Confirm an email to apply and get paid.
        </p>
        <label className="login-field">
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required
            placeholder="you@example.com" autoComplete="email" data-testid="add-email-input" />
        </label>
        {error && <p className="error" style={{ padding: 0 }} role="alert" data-testid="add-email-error">{error}</p>}
        {info && !error && <p className="muted" style={{ margin: 0 }}>{info}</p>}
        <button type="submit" className="btn-primary" disabled={busy || cooldown > 0} data-testid="add-email-send">
          {busy ? '…' : sentTo ? (cooldown > 0 ? `Resend link in ${cooldown}s` : 'Resend confirmation link') : 'Send confirmation link'}
        </button>
        {sentTo && (
          <button type="button" className="login-oauth" onClick={checkConfirmed} disabled={busy} data-testid="add-email-confirmed">
            I've confirmed my email
          </button>
        )}
        <button type="button" className="btn-link" onClick={signOut}>Sign out</button>
      </form>
    </div>
  );
}

export function NotOnTeamPage({ onLogout }: { onLogout: () => void }) {
  return (
    <div className="login-wrap" data-testid="not-on-team-page">
      <div className="login-card">
        <h2>Not on the team yet</h2>
        <p className="muted" style={{ margin: 0 }}>
          You're signed in, but this account isn't linked to a Bricks photographer. Apply with the same email at{' '}
          <a href="https://www.brxs.ca/photographers">brxs.ca/photographers</a>, or ask an owner to add you.
        </p>
        <button type="button" className="btn-link" onClick={onLogout}>Sign out</button>
      </div>
    </div>
  );
}
