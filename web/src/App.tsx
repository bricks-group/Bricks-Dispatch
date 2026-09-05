import React, { useEffect, useState } from 'react';
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { logout, me } from './lib/api';
import { supabase } from './lib/supabase';
import { AvailabilityPage } from './pages/AvailabilityPage';
import { EarningsPage } from './pages/EarningsPage';
import { JobsPage } from './pages/JobsPage';
import { LoginPage } from './pages/LoginPage';
import { OverviewPage } from './pages/OverviewPage';
import { PerformancePage } from './pages/PerformancePage';
import { ProfilePage } from './pages/ProfilePage';
import { SettingsPage } from './pages/SettingsPage';
import { TeamPage } from './pages/TeamPage';
import { CapturePage } from './pages/CapturePage';
import { CaptureReviewPage } from './pages/CaptureReviewPage';
import { VisitCapturePage } from './pages/VisitCapturePage';

function TopBar({ name, onLogout }: { name: string; onLogout: () => void }) {
  const now = new Date();
  const dateStr = now.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' }).toUpperCase();
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

function ProtectedLayout() {
  const [loading, setLoading] = useState(true);
  const [isAuthed, setIsAuthed] = useState(false);
  const [name, setName] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) { setLoading(false); setIsAuthed(false); return; }
      me()
        .then((member) => { setIsAuthed(true); setName(member.name); })
        .catch(() => setIsAuthed(false))
        .finally(() => setLoading(false));
    });
  }, []);

  const onLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  if (loading) return <p style={{ padding: 28, color: 'var(--c-ink-mute)' }}>Checking session…</p>;
  if (!isAuthed) return <Navigate to="/login" replace />;

  return (
    <div className="app-shell">
      <Sidebar />
      <main className="main">
        <TopBar name={name} onLogout={onLogout} />
        <Routes>
          <Route path="/"             element={<OverviewPage />} />
          <Route path="/jobs"         element={<JobsPage />} />
          <Route path="/profile"      element={<ProfilePage />} />
          <Route path="/team"         element={<TeamPage />} />
          <Route path="/earnings"     element={<EarningsPage />} />
          <Route path="/availability" element={<AvailabilityPage />} />
          <Route path="/performance"  element={<PerformancePage />} />
          <Route path="/settings"     element={<SettingsPage />} />
          <Route path="*"             element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function CaptureGate({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [ok, setOk] = useState(false);
  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setOk(!!session);
      setLoading(false);
    });
  }, []);
  if (loading) return <p style={{ padding: 28, color: 'var(--c-ink-mute)' }}>Checking session…</p>;
  if (!ok) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/capture" element={<CaptureGate><CapturePage /></CaptureGate>} />
      <Route path="/capture/review" element={<CaptureGate><CaptureReviewPage /></CaptureGate>} />
      <Route path="/capture/visit" element={<CaptureGate><VisitCapturePage /></CaptureGate>} />
      <Route path="/*"     element={<ProtectedLayout />} />
    </Routes>
  );
}
