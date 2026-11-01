import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import BricksLoader from '../components/BricksLoader';
import {
  acknowledgeGuide,
  confirmAvailability,
  getOnboarding,
  startPayoutSetup,
  type OnboardingState,
  type OnboardingStep,
} from '../lib/api';

/**
 * Setup checklist for approved vendors. Every step done = live, and jobs start
 * coming automatically. One orange button: the next step. Vendor copy says
 * "Bricks" only.
 */
export function OnboardingPage({ onLive }: { onLive?: () => void }) {
  const navigate = useNavigate();
  const [state, setState] = useState<OnboardingState | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const load = () =>
    getOnboarding()
      .then((s) => {
        setState(s);
        setError('');
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Could not load your setup steps'));

  useEffect(() => {
    load();
  }, []);

  const run = async (key: string, fn: () => Promise<OnboardingState | void>) => {
    setBusy(key);
    setError('');
    try {
      const next = await fn();
      if (next) setState(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong. Try again.');
    } finally {
      setBusy(null);
    }
  };

  const payouts = () =>
    run('payouts', async () => {
      const res = await startPayoutSetup(`${window.location.origin}/onboarding?stripe=return`);
      window.location.href = res.url;
    });

  if (!state && !error) {
    return (
      <div className="page-content" style={{ padding: 32 }}>
        <BricksLoader variant="assemble" size={48} />
      </div>
    );
  }

  const steps = state?.steps || [];
  const next = steps.find((s) => s.required && !s.done);
  const live = !!state?.complete;

  const action = (s: OnboardingStep, primary: boolean) => {
    const cls = primary ? 'btn-primary btn-brick' : '';
    const isBusy = busy === s.key;
    switch (s.key) {
      case 'payouts':
        return (
          <button type="button" className={cls} disabled={!!busy} onClick={payouts} data-testid="onb-payouts">
            {isBusy ? 'Opening Stripe…' : 'Connect payouts'}
          </button>
        );
      case 'availability':
        return (
          <span style={{ display: 'inline-flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" className="btn-tertiary" onClick={() => navigate('/availability')}>
              Edit availability
            </button>
            <button type="button" className={cls} disabled={!!busy} onClick={() => run(s.key, confirmAvailability)} data-testid="onb-availability">
              {isBusy ? 'Saving…' : 'Confirm availability'}
            </button>
          </span>
        );
      case 'guide':
        return (
          <span style={{ display: 'inline-flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" className="btn-tertiary" onClick={() => navigate('/guide')}>
              Open the guide
            </button>
            <button type="button" className={cls} disabled={!!busy} onClick={() => run(s.key, acknowledgeGuide)} data-testid="onb-guide">
              {isBusy ? 'Saving…' : "I've read the guide"}
            </button>
          </span>
        );
      default:
        return null;
    }
  };

  return (
    <div className="page-content" data-testid="onboarding-page" style={{ maxWidth: 640 }}>
      <div className="page-header">
        <h1 style={{ margin: 0 }}>{live ? "You're live" : 'Finish setting up'}</h1>
        <p className="muted" style={{ margin: '6px 0 0' }}>
          {live
            ? 'Jobs that fit your schedule and area now come to you automatically.'
            : `${state?.done ?? 0} of ${state?.total ?? 5} done. When every step is done, jobs start coming to you automatically.`}
        </p>
      </div>

      {error && (
        <p role="alert" style={{ color: 'var(--c-brick)', margin: '12px 0' }}>
          {error}
        </p>
      )}

      {live ? (
        <button type="button" className="btn-primary btn-brick" onClick={() => (onLive ? onLive() : navigate('/'))} style={{ marginTop: 16 }}>
          Go to today
        </button>
      ) : (
        <ol style={{ listStyle: 'none', padding: 0, margin: '20px 0 0', display: 'grid', gap: 0 }}>
          {steps.map((s, i) => (
            <li
              key={s.key}
              data-testid={`onb-step-${s.key}`}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                justifyContent: 'space-between',
                gap: 16,
                flexWrap: 'wrap',
                padding: '16px 0',
                borderTop: i === 0 ? '1px solid var(--c-hair)' : undefined,
                borderBottom: '1px solid var(--c-hair)',
              }}
            >
              <div style={{ display: 'flex', gap: 12, minWidth: 0, flex: '1 1 260px' }}>
                <span
                  aria-hidden
                  style={{
                    width: 20,
                    height: 20,
                    flex: '0 0 20px',
                    marginTop: 1,
                    borderRadius: 2,
                    border: `1px solid ${s.done ? 'var(--c-ink-soft)' : 'var(--c-graphite)'}`,
                    background: s.done ? 'var(--c-ink-soft)' : 'transparent',
                    color: 'var(--c-bg)',
                    fontSize: 13,
                    lineHeight: '18px',
                    textAlign: 'center',
                  }}
                >
                  {s.done ? '✓' : ''}
                </span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 500, color: s.done ? 'var(--c-ink-mute)' : 'var(--c-ink)' }}>
                    {s.title}
                    <span className="sr-only">{s.done ? ' (done)' : ' (not done yet)'}</span>
                  </div>
                  <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>
                    {s.done && !s.required ? 'Not needed for your account.' : s.detail}
                  </div>
                </div>
              </div>
              {!s.done && action(s, next?.key === s.key)}
            </li>
          ))}
        </ol>
      )}

      <p className="muted" style={{ fontSize: 13, marginTop: 24 }}>
        Questions? Write <a href="mailto:help@brxs.ca" style={{ color: 'var(--c-ink-soft)' }}>help@brxs.ca</a>.
      </p>
    </div>
  );
}
