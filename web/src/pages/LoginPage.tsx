import { FormEvent, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { login } from '../lib/api';

export function LoginPage() {
  const [email, setEmail] = useState('alex.rivera@bricksdispatch.com');
  const [name, setName] = useState('Alex Rivera');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await login(email, name);
      navigate('/', { replace: true });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={onSubmit}>
        <h2>Vendor OS Login</h2>
        <p className="muted">Sign in to manage team workload and operations.</p>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          Display name
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        {error ? <p className="error">{error}</p> : null}
        <button disabled={submitting} type="submit">{submitting ? 'Signing in...' : 'Sign In'}</button>
      </form>
    </div>
  );
}
