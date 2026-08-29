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

  return (
    <div className="page-content">
      <div className="page-header">
        <div>
          <h1>Overview</h1>
          <p>Today's workload at a glance</p>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      {data && (
        <>
          <div className="page-grid">
            <section className="card">
              <h3>Assigned today</h3>
              <p className="metric">{data.jobs_assigned_today}</p>
              <p className="muted">jobs</p>
            </section>
            <section className="card">
              <h3>Assigned · week</h3>
              <p className="metric">{data.jobs_assigned_week}</p>
              <p className="muted">jobs</p>
            </section>
            <section className="card">
              <h3>Completed · week</h3>
              <p className="metric">{data.jobs_completed_week}</p>
              <p className="muted">jobs</p>
            </section>
            <section className="card">
              <h3>Capacity</h3>
              <p className="metric">{data.capacity_utilization_pct}%</p>
              <div className="bar-track"><div className="bar-fill" style={{ width: `${Math.min(data.capacity_utilization_pct, 100)}%` }} /></div>
              <p className="muted">utilization</p>
            </section>
          </div>

          <section className="card full">
            <h3>Today</h3>
            {data.upcoming_jobs.filter((j) => (j.scheduled_date || '').slice(0, 10) === new Date().toLocaleDateString('en-CA')).length === 0
              ? <p className="muted" style={{ padding: '20px' }}>No jobs today</p>
              : (
                <ul className="list">
                  {data.upcoming_jobs.filter((j) => (j.scheduled_date || '').slice(0, 10) === new Date().toLocaleDateString('en-CA')).map((job) => (
                    <li key={job.id} className="list-row">
                      <span>{job.address}</span>
                      <span className="muted">{job.time_window_start} – {job.time_window_end} · {job.status?.replace('_', ' ')}</span>
                    </li>
                  ))}
                </ul>
              )}
          </section>

          <section className="card full">
            <h3>Upcoming Schedule</h3>
            {data.upcoming_jobs.length === 0
              ? <p className="muted" style={{ padding: '20px' }}>No upcoming jobs</p>
              : (
                <ul className="list">
                  {data.upcoming_jobs.map((job) => (
                    <li key={job.id} className="list-row">
                      <span>{job.address}</span>
                      <span className="muted">{job.time_window_start} – {job.time_window_end}</span>
                    </li>
                  ))}
                </ul>
              )}
          </section>
        </>
      )}

      {!data && !error && <p className="muted">Loading…</p>}
    </div>
  );
}
