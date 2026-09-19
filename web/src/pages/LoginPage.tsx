import { FormEvent, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { login, loginWithGoogle, loginWithPassword, verifyOtp } from '../lib/api';

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
  const navigate = useNavigate();

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
      // Browser navigates to Google; if we return, OAuth failed to start.
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
      } else if (
        low.includes('provider is not enabled')
        || low.includes('unsupported provider')
        || low.includes('validation_failed')
      ) {
        setError('Google sign-in is not connected yet. Use email and password, or ask an owner to enable Google in Supabase Auth.');
      } else {
        setError(msg);
      }
      setGoogleLoading(false);
    }
  };

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
          disabled={submitting || googleLoading || otpLoading || !email.trim()}
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
          disabled={submitting || googleLoading || otpLoading}
        >
          <GoogleMark />
          {googleLoading ? 'Redirecting…' : 'Continue with Google'}
        </button>
        {/* Apple stays OFF until Apple Developer / Supabase Apple is connected — same honesty as OS. */}
      </form>
    </div>
  );
}
