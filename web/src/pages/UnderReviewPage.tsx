import { logout } from '../lib/api';

export function UnderReviewPage({ onLogout }: { onLogout?: () => void }) {
  const handleLogout = async () => {
    if (onLogout) onLogout();
    else {
      await logout();
      window.location.href = '/login';
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        background: 'var(--c-night, #141210)',
        color: 'var(--c-moon, #F4F1EB)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 36,
        fontFamily: 'inherit',
      }}
      data-testid="under-review-page"
    >
      <div style={{ maxWidth: 420 }}>
        <div
          style={{
            fontSize: 11,
            letterSpacing: 1.8,
            color: 'var(--c-ink-mute, #8F8678)',
            marginBottom: 16,
            fontFamily: 'ui-monospace, monospace',
          }}
        >
          Application pending
        </div>
        <h1 style={{ fontSize: 34, fontWeight: 500, letterSpacing: -1.1, margin: '0 0 14px', textTransform: 'lowercase' }}>
          Under review
        </h1>
        <p style={{ fontSize: 14, lineHeight: 1.55, color: 'var(--c-ink-soft, #D6CFC2)', margin: '0 0 28px' }}>
          Your application is under review. You will get access to Today, Jobs, and Capture after Kam approves you in Hiring.
        </p>
        <button className="btn-logout" type="button" onClick={handleLogout} data-testid="under-review-sign-out">
          Sign out
        </button>
      </div>
    </div>
  );
}
