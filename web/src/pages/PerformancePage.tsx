import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import BricksLoader from '../components/BricksLoader';

type Performance = {
  completion_rate: number;
  on_time_rate: number;
  job_volume_week: number;
};

export function PerformancePage() {
  const [data, setData] = useState<Performance | null>(null);

  useEffect(() => {
    api.get<Performance>('/api/vendor-os/performance').then(setData);
  }, []);

  return (
    <div className="page-content">
      <div className="page-header">
        <div>
          <h1>Performance</h1>
          <p>Completion and on-time delivery rates</p>
        </div>
      </div>

      {!data && <p className="muted"><BricksLoader variant="assemble" size={40} style={{ display: 'block', marginBottom: 12 }} />Loading…</p>}

      {data && (
        <div className="stats">
          <div className="stat">
            <p className="stat-label">Completion</p>
            <p className="stat-value">{data.completion_rate}%</p>
            <p className="stat-note">of assigned jobs</p>
          </div>
          <div className="stat">
            <p className="stat-label">On time</p>
            <p className="stat-value">{data.on_time_rate}%</p>
            <p className="stat-note">arrivals</p>
          </div>
          <div className="stat">
            <p className="stat-label">This week</p>
            <p className="stat-value">{data.job_volume_week}</p>
            <p className="stat-note">jobs completed</p>
          </div>
        </div>
      )}
    </div>
  );
}
