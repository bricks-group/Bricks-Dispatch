import { FormEvent, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { login, verifyOtp } from '../lib/api';

function BrickMark() {
  return (
    <svg width="24" height="14" viewBox="0 0 27 16" fill="none" style={{ display: 'block' }}>
      <rect x="1" y="1"  width="12" height="6" rx="1.2" fill="#D2542B" />
      <rect x="1" y="9"  width="12" height="6" rx="1.2" fill="#D2542B" />
      <rect x="15" y="9" width="11" height="6" rx="1.2" fill="#D2542B" />
    </svg>
  );
}

export function LoginPage() {
  const [email, setEmail] = useState('');
  const [otp, setOtp] = useState('');
  const [step, setStep] = useState<'login' | 'verify'>('login');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  const onLogin = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await login(email);
      setStep('verify');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
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
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  if (step === 'verify') {
    return (
      <div className="login-wrap">
        <form className="login-card" onSubmit={onVerify}>
          <BrickMark />
          <h2>Check your email</h2>
          <p className="muted" style={{ margin: 0 }}>Sent a verification code to {email}</p>
          <label className="login-card label">
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
      <form className="login-card" onSubmit={onLogin}>
        <BrickMark />
        <h2>Vendor OS</h2>
        <p className="muted" style={{ margin: 0 }}>Sign in to manage team workload and operations.</p>
        <label className="login-card label">
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required placeholder="you@example.com" />
        </label>
        {error && <p className="error" style={{ padding: 0 }}>{error}</p>}
        <button type="submit" disabled={submitting}>{submitting ? 'Sending code…' : 'Send magic link'}</button>
      </form>
    </div>
  );
}
