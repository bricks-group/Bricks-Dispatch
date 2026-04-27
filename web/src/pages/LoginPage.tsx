import { FormEvent, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { login, verifyOtp } from '../lib/api';

export function LoginPage() {
  const [email, setEmail] = useState('alex.rivera@bricksdispatch.com');
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
        <form className="card login-card" onSubmit={onVerify}>
          <h2>Enter Verification Code</h2>
          <p className="muted">We've sent a code to {email}</p>
          <label>
            Code
            <input type="text" value={otp} onChange={(e) => setOtp(e.target.value)} required placeholder="123456" />
          </label>
          {error ? <p className="error">{error}</p> : null}
          <button disabled={submitting} type="submit">{submitting ? 'Verifying...' : 'Verify'}</button>
          <button type="button" className="btn-link" onClick={() => setStep('login')}>Back to login</button>
        </form>
      </div>
    );
  }

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={onLogin}>
        <h2>Vendor OS Login</h2>
        <p className="muted">Sign in to manage team workload and operations.</p>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        {error ? <p className="error">{error}</p> : null}
        <button disabled={submitting} type="submit">{submitting ? 'Sending Code...' : 'Send Magic Link'}</button>
      </form>
    </div>
  );
}
