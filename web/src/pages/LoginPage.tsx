import { FormEvent, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { isAuthRestricted, isPhoneUser, isVendorMemberMissing, login, loginWithGoogle, loginWithPassword, me, needsEmailFirst, verifyOtp } from '../lib/api';
import { supabase } from '../lib/supabase';
import {
  OTP_LENGTH,
  RESEND_COOLDOWN_SECONDS,
  cleanOtp,
  fetchPhoneProviderEnabled,
  formatPhoneDisplay,
  phoneAuthErrorMessage,
  retryAfterSeconds as otpRetryAfter,
  sendPhoneOtp,
  verifyPhoneOtp,
} from '../lib/phone-auth';

function BrickMark() {
  return (
    <svg width="24" height="14" viewBox="0 0 27 16" fill="none" style={{ display: 'block' }}>
      <rect x="1" y="1"  width="12" height="6" rx="1.2" fill="#D2542B" />
      <rect x="1" y="9"  width="12" height="6" rx="1.2" fill="#D2542B" />
      <rect x="15" y="9" width="11" height="6" rx="1.2" fill="#D2542B" />
    </svg>
  );
}

function GoogleMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
    </svg>
  );
}

function PhoneMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="6.5" y="2.5" width="11" height="19" rx="2.2" stroke="currentColor" strokeWidth="1.6" />
      <path d="M10.5 18h3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

/**
 * "Continue with phone": signInWithOtp({ phone }) then verifyOtp({ phone, token, type: 'sms' }).
 * Phone-only accounts then confirm an email (AddEmailPage) before GO opens or they apply.
 */
function PhoneLogin({ onBack }: { onBack: () => void }) {
  const navigate = useNavigate();
  const [phoneInput, setPhoneInput] = useState('');
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [busy, setBusy] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const t = setTimeout(() => setCooldown((c) => Math.max(0, c - 1)), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  const send = async (event?: FormEvent) => {
    event?.preventDefault();
    if (busy || cooldown > 0) return;
    setError(''); setInfo('');
    setBusy(true);
    try {
      const { phone: e164, error: err } = await sendPhoneOtp(supabase, phone || phoneInput, { createUser: true });
      if (err) {
        setError(phoneAuthErrorMessage(err, 'send'));
        const wait = otpRetryAfter(err as { message?: string });
        if (wait) setCooldown(wait);
        return;
      }
      setPhone(e164);
      setCode('');
      setCooldown(RESEND_COOLDOWN_SECONDS);
      setInfo(`We texted a ${OTP_LENGTH}-digit code to ${formatPhoneDisplay(e164)}.`);
    } finally {
      setBusy(false);
    }
  };

  const verify = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setError('');
    setBusy(true);
    try {
      const { error: err } = await verifyPhoneOtp(supabase, phone, code);
      if (err) {
        setError(phoneAuthErrorMessage(err, 'verify'));
        return;
      }
      try {
        await me();
        navigate('/', { replace: true });
      } catch (e) {
        // No member yet: phone-only → confirm an email first (ProtectedLayout shows AddEmailPage).
        if (isVendorMemberMissing(e) && ((await needsEmailFirst()) || (await isPhoneUser()))) {
          navigate('/', { replace: true });
          return;
        }
        setError((e as Error).message || 'Could not load your account.');
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="login-card" onSubmit={phone ? verify : send} data-testid="phone-auth-panel">
      <BrickMark />
      <h2>{phone ? 'Check your texts' : 'Continue with phone'}</h2>
      {!phone ? (
        <label className="login-field">
          Mobile number
          <input type="tel" inputMode="tel" autoComplete="tel" value={phoneInput}
            onChange={(e) => setPhoneInput(e.target.value)} required placeholder="(416) 555-0123" data-testid="phone-input" />
        </label>
      ) : (
        <label className="login-field">
          Code
          <input type="text" inputMode="numeric" autoComplete="one-time-code" maxLength={OTP_LENGTH} value={code}
            onChange={(e) => setCode(cleanOtp(e.target.value))} required placeholder="123456" autoFocus data-testid="phone-otp-input" />
        </label>
      )}
      {!phone && (
        <p className="muted" style={{ margin: 0, fontSize: 12 }}>
          Canada and US numbers work as typed. Add + and a country code for others. Message and data rates may apply.
        </p>
      )}
      {error && <p className="error" style={{ padding: 0 }} role="alert" data-testid="phone-auth-error">{error}</p>}
      {info && !error && <p className="muted" style={{ margin: 0 }}>{info}</p>}
      <button type="submit" disabled={busy || (!phone && cooldown > 0)} data-testid={phone ? 'phone-verify-btn' : 'phone-send-btn'}>
        {busy ? '…' : phone ? 'Verify and sign in' : 'Text me a code'}
      </button>
      {phone && (
        <button type="button" className="btn-link" onClick={() => send()} disabled={busy || cooldown > 0} data-testid="phone-resend-btn">
          {cooldown > 0 ? `Resend code in ${cooldown}s` : 'Resend code'}
        </button>
      )}
      <button type="button" className="btn-link"
        onClick={phone ? () => { setPhone(''); setCode(''); setError(''); setInfo(''); } : onBack}>
        {phone ? 'Use a different number' : 'Use email instead'}
      </button>
    </form>
  );
}

function authErrorMessage(err: unknown): string {
  const msg = (err as Error)?.message || String(err || 'Login failed');
  const low = msg.toLowerCase();
  if (
    low.includes('402')
    || low.includes('exceed_storage')
    || low.includes('fair-use')
    || low.includes('fair use')
    || low.includes('restricted')
  ) {
    return 'Sign-in is temporarily limited. Use email and password — magic links and Google may be unavailable.';
  }
  return msg;
}

export function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [otp, setOtp] = useState('');
  const [step, setStep] = useState<'login' | 'verify'>('login');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [otpLoading, setOtpLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [authRestricted, setAuthRestricted] = useState(false);
  // null = unknown (show; provider-disabled error is handled), false = hidden.
  const [phoneEnabled, setPhoneEnabled] = useState<boolean | null>(null);
  const [phoneMode, setPhoneMode] = useState(false);
  const navigate = useNavigate();

  // Hide "Continue with phone" when Supabase reports external.phone === false.
  useEffect(() => {
    let alive = true;
    fetchPhoneProviderEnabled(import.meta.env.VITE_SUPABASE_URL || '', import.meta.env.VITE_SUPABASE_ANON_KEY || '')
      .then((enabled) => { if (alive) setPhoneEnabled(enabled); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const err = params.get('error');
    if (err) {
      setError(err);
      params.delete('error');
      const qs = params.toString();
      window.history.replaceState({}, '', qs ? `/login?${qs}` : '/login');
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    isAuthRestricted()
      .then((restricted) => {
        if (!cancelled) setAuthRestricted(restricted);
      })
      .catch(() => {
        if (!cancelled) setAuthRestricted(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const onPasswordLogin = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await loginWithPassword(email, password);
      navigate('/', { replace: true });
    } catch (e) {
      setError(authErrorMessage(e));
    } finally {
      setSubmitting(false);
    }
  };

  const onEmailCode = async () => {
    setOtpLoading(true);
    setError('');
    try {
      await login(email);
      setStep('verify');
    } catch (e) {
      setError(authErrorMessage(e));
    } finally {
      setOtpLoading(false);
    }
  };

  const onVerify = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await verifyOtp(email, otp);
      navigate('/', { replace: true });
    } catch (e) {
      setError(authErrorMessage(e));
    } finally {
      setSubmitting(false);
    }
  };

  const onGoogle = async () => {
    setError('');
    setGoogleLoading(true);
    try {
      await loginWithGoogle();
      // Browser navigates to Google; if we return without redirect, clear loading below.
    } catch (e) {
      const msg = (e as Error).message || 'Google sign-in is not available.';
      const low = msg.toLowerCase();
      if (
        low.includes('402')
        || low.includes('exceed_storage')
        || low.includes('fair-use')
        || low.includes('fair use')
        || low.includes('restricted')
      ) {
        setError('Google sign-in is unavailable while Auth is restricted. Use email and password.');
        setAuthRestricted(true);
      } else if (
        low.includes('provider is not enabled')
        || low.includes('unsupported provider')
        || low.includes('validation_failed')
      ) {
        setError('Google sign-in is not connected yet. Use email and password, or ask an owner to enable Google in Supabase Auth.');
      } else {
        setError(msg);
      }
    } finally {
      setGoogleLoading(false);
    }
  };

  if (phoneMode) {
    return (
      <div className="login-wrap">
        <PhoneLogin onBack={() => setPhoneMode(false)} />
      </div>
    );
  }

  if (step === 'verify') {
    return (
      <div className="login-wrap">
        <form className="login-card" onSubmit={onVerify}>
          <BrickMark />
          <h2>Check your email</h2>
          <p className="muted" style={{ margin: 0 }}>Sent a verification code to {email}</p>
          <label className="login-field">
            Code
            <input type="text" value={otp} onChange={(e) => setOtp(e.target.value)} required placeholder="123456" />
          </label>
          {error && <p className="error" style={{ padding: 0 }}>{error}</p>}
          <button type="submit" disabled={submitting}>{submitting ? 'Verifying…' : 'Verify'}</button>
          <button type="button" className="btn-link" onClick={() => setStep('login')}>Back to login</button>
        </form>
      </div>
    );
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={onPasswordLogin}>
        <BrickMark />
        <h2>bricks</h2>
        <p className="muted" style={{ margin: 0 }}>Sign in to GO.</p>
        {authRestricted && (
          <p className="muted" style={{ margin: '8px 0 0' }} data-testid="auth-fair-use-banner">
            Google and magic link are unavailable under Fair Use — use email and password.
          </p>
        )}
        <label className="login-field">
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            placeholder="you@example.com"
            autoComplete="username"
          />
        </label>
        <label className="login-field">
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            placeholder="••••••••"
            autoComplete="current-password"
          />
        </label>
        {error && <p className="error" style={{ padding: 0 }} data-testid="auth-error">{error}</p>}
        <button type="submit" disabled={submitting || googleLoading || otpLoading}>
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>

        <button
          type="button"
          className="btn-link"
          onClick={onEmailCode}
          disabled={submitting || googleLoading || otpLoading || !email.trim() || authRestricted}
          style={{ marginTop: 8 }}
        >
          {otpLoading ? 'Sending code…' : 'Email me a code'}
        </button>

        <div className="login-divider" aria-hidden>
          <span>or</span>
        </div>

        <button
          type="button"
          className="login-oauth"
          data-testid="login-google-btn"
          onClick={onGoogle}
          disabled={submitting || googleLoading || otpLoading || authRestricted}
        >
          <GoogleMark />
          {googleLoading ? 'Redirecting…' : authRestricted ? 'Google unavailable' : 'Continue with Google'}
        </button>
        {phoneEnabled !== false && !authRestricted && (
          <button
            type="button"
            className="login-oauth"
            data-testid="login-phone-btn"
            onClick={() => { setError(''); setPhoneMode(true); }}
            disabled={submitting || googleLoading || otpLoading}
          >
            <PhoneMark />
            Continue with phone
          </button>
        )}
        {/* Apple stays OFF until Apple Developer / Supabase Apple is connected — same honesty as OS. */}
      </form>
    </div>
  );
}
