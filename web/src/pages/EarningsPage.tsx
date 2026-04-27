import { useEffect, useState } from 'react';
import { api } from '../lib/api';

type EarningJob = {
  id: string;
  address: string;
  scheduled_date: string;
  status: string;
  payout_amount: number;
  payout_status: 'paid' | 'pending';
};

type Earnings = {
  week_total: number;
  paid_amount: number;
  pending_amount: number;
  payout_day: string;
  week_job_count: number;
  recent_jobs: EarningJob[];
};

const STATUS_BADGE: Record<string, string> = {
  paid: 'badge-green',
  pending: 'badge-yellow',
};

export function EarningsPage() {
  const [data, setData] = useState<Earnings | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api.get<Earnings>('/api/vendor-os/earnings')
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p>Loading earnings...</p>;

  return (
    <div className="page-grid">
      <section className="card">
        <h3>This Week</h3>
        <p className="metric">${data.week_total.toLocaleString()}</p>
        <p className="muted">{data.week_job_count} job{data.week_job_count !== 1 ? 's' : ''}</p>
      </section>
      <section className="card">
        <h3>Paid</h3>
        <p className="metric">${data.paid_amount.toLocaleString()}</p>
      </section>
      <section className="card">
        <h3>Pending</h3>
        <p className="metric">${data.pending_amount.toLocaleString()}</p>
        <p className="muted">Pays out {data.payout_day}</p>
      </section>
      <section className="card full">
        <h3>Recent Jobs</h3>
        {data.recent_jobs.length === 0 ? (
          <p className="muted">No recent jobs</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Address</th>
                <th>Date</th>
                <th>Status</th>
                <th>Payout</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_jobs.map((job) => (
                <tr key={job.id}>
                  <td>{job.address}</td>
                  <td>{job.scheduled_date?.slice(0, 10) ?? '—'}</td>
                  <td>
                    <span className={`badge ${STATUS_BADGE[job.payout_status] ?? ''}`}>
                      {job.payout_status}
                    </span>
                  </td>
                  <td>${job.payout_amount.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
