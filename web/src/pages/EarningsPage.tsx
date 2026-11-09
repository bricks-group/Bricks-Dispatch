import { useEffect, useState } from 'react';
import { fmtMoneyCompact, fmtMoneyExact } from '../lib/money';
import { api } from '../lib/api';
import BricksLoader from '../components/BricksLoader';
import { DayCell } from '../components/DayCell';

type EarningJob = {
  id: string;
  address: string;
  scheduled_date: string;
  status: string;
  payout_amount: number;
  payout_status: 'paid' | 'pending' | 'scheduled';
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
  paid:      'badge-green',
  pending:   'badge-yellow',
  scheduled: 'badge-yellow',
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
                <p className="meta-line">Full package, no deduction</p>
                <p className="muted" style={{ margin: 0 }}>You receive the full package. No platform cut is applied.</p>
              </div>
            </section>
          )}
          {data.payout_model === 'ic_rate_card' && (
            <p className="stats-foot" style={{ margin: 0 }}>You're paid a fixed rate card by package, home size and tier. Virtual visits pay extra.</p>
          )}
          <div className="stats">
            <div className="stat">
              <p className="stat-label">This week</p>
              <p className="stat-value" title={fmtMoneyExact(data.week_total)}>{fmtMoneyCompact(data.week_total)}</p>
              <p className="stat-note">{data.week_job_count} job{data.week_job_count !== 1 ? 's' : ''}</p>
            </div>
            <div className="stat">
              <p className="stat-label">Paid</p>
              <p className="stat-value" title={fmtMoneyExact(data.paid_amount)}>{fmtMoneyCompact(data.paid_amount)}</p>
              <p className="stat-note">Settled</p>
            </div>
            <div className="stat">
              <p className="stat-label">Pending</p>
              <p className="stat-value" title={fmtMoneyExact(data.pending_amount)}>{fmtMoneyCompact(data.pending_amount)}</p>
              <p className="stat-note">Pays {data.payout_day}</p>
            </div>
          </div>

          <section className="card full">
            <h3>Recent jobs</h3>
            {data.recent_jobs.length === 0 ? (
              <p className="muted" style={{ padding: '20px' }}>No recent jobs</p>
            ) : (
              <table className="table t1l">
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
                      <td data-label="Address" className="c-t" style={{ fontWeight: 500 }}>{job.address}</td>
                      <td data-label="Date" className="muted"><DayCell d={job.scheduled_date} /></td>
                      <td data-label="Status" className="c-s">
                        <span className={`badge ${PAYOUT_BADGE[job.payout_status] ?? 'badge-gray'}`}>
                          {job.payout_status === 'scheduled' ? 'Pays Monday' : job.payout_status}
                        </span>
                      </td>
                      <td data-label="Payout" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 13 }}>
                        {fmtMoneyExact(job.payout_amount)}
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
