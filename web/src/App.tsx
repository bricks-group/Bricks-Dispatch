import { Navigate, Route, Routes } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { AvailabilityPage } from './pages/AvailabilityPage';
import { JobsPage } from './pages/JobsPage';
import { OverviewPage } from './pages/OverviewPage';
import { PerformancePage } from './pages/PerformancePage';
import { SettingsPage } from './pages/SettingsPage';
import { TeamPage } from './pages/TeamPage';

export default function App() {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="main">
        <header className="page-header">
          <h1>Bricks Vendor OS</h1>
          <p>Operational control layer for vendor teams and solo providers</p>
        </header>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/team" element={<TeamPage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/availability" element={<AvailabilityPage />} />
          <Route path="/performance" element={<PerformancePage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
