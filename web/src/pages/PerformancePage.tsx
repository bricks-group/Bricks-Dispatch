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
        <div className="page-grid">
          <section className="card">
            <h3>Completion rate</h3>
            <p className="metric">{data.completion_rate}%</p>
            <div className="bar-track"><div className="bar-fill" style={{ width: `${data.completion_rate}%` }} /></div>
            <p className="muted">of assigned jobs</p>
          </section>
          <section className="card">
            <h3>On-time rate</h3>
            <p className="metric">{data.on_time_rate}%</p>
            <div className="bar-track"><div className="bar-fill" style={{ width: `${data.on_time_rate}%` }} /></div>
            <p className="muted">on-time arrivals</p>
          </section>
          <section className="card">
            <h3>Job volume · week</h3>
            <p className="metric">{data.job_volume_week}</p>
            <p className="muted">jobs completed</p>
          </section>
        </div>
      )}
    </div>
  );
}
