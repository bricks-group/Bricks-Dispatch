import { useEffect, useState } from 'react';
import { api, Job } from '../lib/api';

type Overview = {
  jobs_assigned_today: number;
  jobs_assigned_week: number;
  jobs_completed_week: number;
  capacity_utilization_pct: number;
  upcoming_jobs: Job[];
};

export function OverviewPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api.get<Overview>('/api/vendor-os/overview')
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p>Loading overview...</p>;

  return (
    <div className="page-grid">
      <section className="card">
        <h3>Assigned</h3>
        <p className="metric">{data.jobs_assigned_today}</p>
        <p className="muted">today</p>
      </section>
      <section className="card">
        <h3>Assigned (week)</h3>
        <p className="metric">{data.jobs_assigned_week}</p>
      </section>
      <section className="card">
        <h3>Completed (week)</h3>
        <p className="metric">{data.jobs_completed_week}</p>
      </section>
      <section className="card">
        <h3>Capacity Utilization</h3>
        <div className="bar-track"><div className="bar-fill" style={{ width: `${Math.min(data.capacity_utilization_pct, 100)}%` }} /></div>
        <p className="muted">{data.capacity_utilization_pct}%</p>
      </section>
      <section className="card full">
        <h3>Upcoming Schedule</h3>
        {data.upcoming_jobs.length === 0 ? <p className="muted">No upcoming jobs</p> : (
          <ul className="list">
            {data.upcoming_jobs.map((job) => (
              <li key={job.id} className="list-row">
                <span>{job.address}</span>
                <span>{job.time_window_start} - {job.time_window_end}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
