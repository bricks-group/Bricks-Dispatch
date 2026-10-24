import React, { useEffect, useState } from 'react';
import { AgreeTermsPage } from './pages/AgreeTermsPage';
import { needsTermsStep } from './lib/legal-docs';
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { logout, me, isRateLimited, retryAfterSeconds, isVendorMemberMissing, needsEmailFirst, isPhoneUser } from './lib/api';
import { supabase } from './lib/supabase';
import { AvailabilityPage } from './pages/AvailabilityPage';
import { EarningsPage } from './pages/EarningsPage';
import { JobsPage } from './pages/JobsPage';
import { LoginPage } from './pages/LoginPage';
import { OverviewPage } from './pages/OverviewPage';
import { NotificationsPage } from './pages/NotificationsPage';
import { PerformancePage } from './pages/PerformancePage';
import { ProfilePage } from './pages/ProfilePage';
import { SettingsPage } from './pages/SettingsPage';
import { TeamPage } from './pages/TeamPage';
import { CapturePage } from './pages/CapturePage';
import { CaptureReviewPage } from './pages/CaptureReviewPage';
import { VisitCapturePage } from './pages/VisitCapturePage';
import { GuidedCapturePage } from './pages/GuidedCapturePage';
import { GoogleCalendarCallbackPage } from './pages/GoogleCalendarCallbackPage';
import { AuthCallbackPage } from './pages/AuthCallbackPage';
import { UnderReviewPage } from './pages/UnderReviewPage';
import { AddEmailPage, NotOnTeamPage } from './pages/AddEmailPage';
import { hasAppAccess, AuthMember } from './lib/api';
import BricksLoader from './components/BricksLoader';
import { GuidePage } from './guide/GuidePage';

/** Native GO keeps working from cache offline; on web we at least say so plainly. */
function useOnline(): boolean {
  const [online, setOnline] = useState(typeof navigator === 'undefined' ? true : navigator.onLine);
  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener('online', up);
    window.addEventListener('offline', down);
    return () => { window.removeEventListener('online', up); window.removeEventListener('offline', down); };
  }, []);
  return online;
}

function OfflineBar() {
  const online = useOnline();
  if (online) return null;
  return (
    <div className="offline-bar" role="status" data-testid="offline-bar">
      Offline. You can read what is already on screen; check-ins, uploads and status changes need a connection.
    </div>
  );
}

function TopBar({ name, onLogout }: { name: string; onLogout: () => void }) {
  const now = new Date();
  const dateStr = now.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' });
  return (
    <div className="topbar">
      <span className="topbar-date">{dateStr}</span>
      <div className="topbar-right">
        {name && <span className="topbar-user">{name}</span>}
        <button className="btn-logout" onClick={onLogout}>Sign out</button>
      </div>
    </div>
  );
}

function RateLimitedNotice({ retryAfter, onRetry }: { retryAfter?: number; onRetry: () => void }) {
  return (
    <div style={{ padding: 28, maxWidth: 460 }}>
      <h1 style={{ fontSize: 22, margin: '0 0 8px' }}>Too many requests</h1>
      <p style={{ color: 'var(--c-ink-soft)', marginTop: 0 }}>
        You are still signed in. Dispatch is limiting requests from this browser
        {retryAfter ? ` for about ${retryAfter} seconds` : ''}.
      </p>
      <button type="button" onClick={onRetry}>Retry</button>
    </div>
  );
}

function ProtectedLayout() {
  const [loading, setLoading] = useState(true);
  const [isAuthed, setIsAuthed] = useState(false);
  const [member, setMember] = useState<AuthMember | null>(null);
  const [name, setName] = useState('');
  const [rateLimited, setRateLimited] = useState(false);
  const [retryAfter, setRetryAfter] = useState<number | undefined>(undefined);
  // Phone-OTP: confirm an email first; then a phone user with no member sees NotOnTeam.
  const [needsEmail, setNeedsEmail] = useState(false);
  const [notOnTeam, setNotOnTeam] = useState(false);
  const navigate = useNavigate();

  const refreshSession = () => {
    setLoading(true);
    setNeedsEmail(false);
    setNotOnTeam(false);
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) {
        setLoading(false);
        setIsAuthed(false);
        setRateLimited(false);
        return;
      }
      me()
        .then((m) => {
          setIsAuthed(true);
          setMember(m);
          setName(m.name);
          setRateLimited(false);
        })
        .catch(async (err) => {
          if (isVendorMemberMissing(err)) {
            if (await needsEmailFirst()) {
              setIsAuthed(true);
              setNeedsEmail(true);
              return;
            }
            if (await isPhoneUser()) {
              setIsAuthed(true);
              setNotOnTeam(true);
              return;
            }
          }
          // 429 is backpressure, not an expired session. Do not send the user to /login.
          if (isRateLimited(err)) {
            setIsAuthed(true);
            setRateLimited(true);
            setRetryAfter(retryAfterSeconds(err));
            return;
          }
          setRateLimited(false);
          setIsAuthed(false);
        })
        .finally(() => setLoading(false));
    });
  };

  useEffect(() => {
    refreshSession();
  }, []);

  const onLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  if (loading) return (
    <div style={{ minHeight: '100dvh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 16 }}>
      <BricksLoader variant="assemble" size={64} />
      <p style={{ margin: 0, color: 'var(--c-ink-mute)' }}>Checking session…</p>
    </div>
  );
  if (rateLimited) return <RateLimitedNotice retryAfter={retryAfter} onRetry={refreshSession} />;
  if (!isAuthed) return <Navigate to="/login" replace />;
  if (needsEmail) return <AddEmailPage onConfirmed={refreshSession} onLogout={onLogout} />;
  if (notOnTeam) return <NotOnTeamPage onLogout={onLogout} />;

  // Pending / inactive / Applicant — block Today/Jobs/Capture until Hiring approval.
  if (!hasAppAccess(member)) {
    return <UnderReviewPage onLogout={onLogout} />;
  }

  // Added from the team screen without an application: agreement and privacy policy first.
  if (needsTermsStep(member, true)) {
    return <AgreeTermsPage name={name} onAccepted={refreshSession} onLogout={onLogout} />;
  }

  return (
    <div className="app-shell">
      <Sidebar />
      <main className="main">
        <TopBar name={name} onLogout={onLogout} />
        <OfflineBar />
        <Routes>
          <Route path="/"                element={<OverviewPage />} />
          <Route path="/jobs"           element={<JobsPage />} />
          <Route path="/notifications"  element={<NotificationsPage />} />
          <Route path="/profile"        element={<ProfilePage />} />
          <Route path="/team"         element={<TeamPage />} />
          <Route path="/earnings"     element={<EarningsPage />} />
          <Route path="/availability" element={<AvailabilityPage />} />
          <Route path="/performance"  element={<PerformancePage />} />
          <Route path="/settings"     element={<SettingsPage />} />
          <Route path="/guide"        element={<GuidePage />} />
          <Route path="*"             element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function CaptureGate({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [ok, setOk] = useState(false);
  const [allowed, setAllowed] = useState(false);
  const [needsTerms, setNeedsTerms] = useState(false);
  const [memberName, setMemberName] = useState('');
  const [needsEmail, setNeedsEmail] = useState(false);
  const [rateLimited, setRateLimited] = useState(false);
  const [retryAfter, setRetryAfter] = useState<number | undefined>(undefined);

  const refreshSession = () => {
    setLoading(true);
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (!session) {
        setOk(false);
        setRateLimited(false);
        setLoading(false);
        return;
      }
      setOk(true);
      try {
        const m = await me();
        setAllowed(hasAppAccess(m));
        setNeedsTerms(needsTermsStep(m, hasAppAccess(m)));
        setMemberName(m.name || '');
        setRateLimited(false);
      } catch (err) {
        if (isRateLimited(err)) {
          setRateLimited(true);
          setRetryAfter(retryAfterSeconds(err));
          return;
        }
        setRateLimited(false);
        setAllowed(false);
        setNeedsEmail(isVendorMemberMissing(err) && await needsEmailFirst());
      } finally {
        setLoading(false);
      }
    });
  };

  useEffect(() => {
    refreshSession();
  }, []);
  if (loading) return (
    <div style={{ minHeight: '100dvh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 16 }}>
      <BricksLoader variant="assemble" size={64} />
      <p style={{ margin: 0, color: 'var(--c-ink-mute)' }}>Checking session…</p>
    </div>
  );
  if (!ok) return <Navigate to="/login" replace />;
  if (rateLimited) return <RateLimitedNotice retryAfter={retryAfter} onRetry={refreshSession} />;
  if (needsEmail) return <AddEmailPage onConfirmed={refreshSession} />;
  if (!allowed) return <UnderReviewPage />;
  if (needsTerms) return <AgreeTermsPage name={memberName} onAccepted={refreshSession} onLogout={async () => { await logout(); window.location.href = '/login'; }} />;
  return <>{children}</>;
}

/** Guided capture is gated like every capture page. `?demo=` skips the gate in dev builds only (screenshots). */
function GuidedCaptureRoute() {
  if (import.meta.env.DEV && new URLSearchParams(window.location.search).get('demo')) return <GuidedCapturePage />;
  return <CaptureGate><GuidedCapturePage /></CaptureGate>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/auth/callback" element={<AuthCallbackPage />} />
      <Route path="/auth/google-calendar" element={<GoogleCalendarCallbackPage />} />
      <Route path="/capture" element={<CaptureGate><CapturePage /></CaptureGate>} />
      <Route path="/capture/review" element={<CaptureGate><CaptureReviewPage /></CaptureGate>} />
      <Route path="/capture/visit" element={<CaptureGate><VisitCapturePage /></CaptureGate>} />
      <Route path="/capture/guided" element={<GuidedCaptureRoute />} />
      <Route path="/*"     element={<ProtectedLayout />} />
    </Routes>
  );
}
