import { useEffect, useState } from 'react';
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { clearToken, getToken, logout, me } from './lib/api';
import { AvailabilityPage } from './pages/AvailabilityPage';
import { EarningsPage } from './pages/EarningsPage';
import { JobsPage } from './pages/JobsPage';
import { LoginPage } from './pages/LoginPage';
import { OverviewPage } from './pages/OverviewPage';
import { PerformancePage } from './pages/PerformancePage';
import { SettingsPage } from './pages/SettingsPage';
import { TeamPage } from './pages/TeamPage';

function ProtectedLayout() {
  const [loading, setLoading] = useState(true);
  const [isAuthed, setIsAuthed] = useState(false);
  const [name, setName] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setLoading(false);
      setIsAuthed(false);
      return;
    }
    me()
      .then((member) => {
        setIsAuthed(true);
        setName(member.name);
      })
      .catch(() => {
        clearToken();
        setIsAuthed(false);
      })
      .finally(() => setLoading(false));
  }, []);

  const onLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  if (loading) return <p className="main">Checking session...</p>;
  if (!isAuthed) return <Navigate to="/login" replace />;

  return (
    <div className="app-shell">
      <Sidebar />
      <main className="main">
        <header className="page-header">
          <h1>Bricks Vendor OS</h1>
          <p>Operational control layer for vendor teams and solo providers</p>
          <div className="row page-meta">
            <span className="muted">Signed in as {name}</span>
            <button onClick={onLogout}>Logout</button>
          </div>
        </header>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/team" element={<TeamPage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/earnings" element={<EarningsPage />} />
          <Route path="/availability" element={<AvailabilityPage />} />
          <Route path="/performance" element={<PerformancePage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/*" element={<ProtectedLayout />} />
    </Routes>
  );
}
