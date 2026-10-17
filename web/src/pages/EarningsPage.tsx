import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import BricksLoader from '../components/BricksLoader';

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
  title?: string;
  payout_model?: 'owner_full' | 'ic_rate_card';
  payout_label?: string;
  deduction?: number | null;
  show_platform_cut?: boolean;
};

const PAYOUT_BADGE: Record<string, string> = {
  paid:    'badge-green',
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

  return (
    <div className="page-content">
      <div className="page-header">
        <div>
          <h1>Earnings</h1>
          <p>Payouts for assigned jobs</p>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      {data && (
        <>
          {data.payout_model === 'owner_full' && (
            <section className="card full">
              <h3>Owner payout</h3>
              <div className="stack">
                <p className="metric" style={{ fontSize: 22, padding: '14px 20px 0' }}>Full package · $0 deduction</p>
                <p className="muted">You receive the full package. No platform cut is applied or shown.</p>
              </div>
            </section>
          )}
          {data.payout_model === 'ic_rate_card' && (
            <section className="card full">
              <h3>Rate card</h3>
              <p className="muted">Fixed photographer rate for www à-la-carte media (Photos only → Full suite) by size and tier — not a percent of rack. Virtual Visit is a paid add-on.</p>
            </section>
          )}
          <div className="page-grid">
            <section className="card">
              <h3>This week</h3>
              <p className="metric">${data.week_total.toLocaleString()}</p>
              <p className="muted">{data.week_job_count} job{data.week_job_count !== 1 ? 's' : ''}</p>
            </section>
            <section className="card">
              <h3>Paid</h3>
              <p className="metric">${data.paid_amount.toLocaleString()}</p>
              <p className="muted">settled</p>
            </section>
            <section className="card">
              <h3>Pending</h3>
              <p className="metric">${data.pending_amount.toLocaleString()}</p>
              <p className="muted">Pays out {data.payout_day}</p>
            </section>
          </div>

          <section className="card full">
            <h3>Recent jobs</h3>
            {data.recent_jobs.length === 0 ? (
              <p className="muted" style={{ padding: '20px' }}>No recent jobs</p>
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
                      <td data-label="Address" style={{ fontWeight: 500 }}>{job.address}</td>
                      <td data-label="Date" className="muted">{job.scheduled_date?.slice(0, 10) ?? '—'}</td>
                      <td data-label="Status">
                        <span className={`badge ${PAYOUT_BADGE[job.payout_status] ?? 'badge-gray'}`}>
                          {job.payout_status}
                        </span>
                      </td>
                      <td data-label="Payout" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 13 }}>
                        ${job.payout_amount.toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}

      {!data && !error && <p className="muted"><BricksLoader variant="assemble" size={40} style={{ display: 'block', marginBottom: 12 }} />Loading…</p>}
    </div>
  );
}
