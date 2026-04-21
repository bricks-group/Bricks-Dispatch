import { useEffect, useState } from 'react';
import { api } from '../lib/api';

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

  if (!data) return <p>Loading performance...</p>;

  return (
    <div className="page-grid">
      <section className="card">
        <h3>Completion Rate</h3>
        <p className="metric">{data.completion_rate}%</p>
      </section>
      <section className="card">
        <h3>On-Time Rate</h3>
        <p className="metric">{data.on_time_rate}%</p>
      </section>
      <section className="card">
        <h3>Job Volume (Week)</h3>
        <p className="metric">{data.job_volume_week}</p>
      </section>
    </div>
  );
}
