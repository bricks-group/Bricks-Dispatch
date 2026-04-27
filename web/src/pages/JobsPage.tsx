import { useEffect, useState } from 'react';
import { api, Job, PaginatedJobsResponse, PaginatedTeamResponse } from '../lib/api';

const STATUS_BADGE: Record<string, string> = {
  assigned: 'badge-yellow',
  in_progress: 'badge-blue',
  completed: 'badge-green',
};

export function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [assigneeNames, setAssigneeNames] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const pageSize = 10;

  useEffect(() => {
    Promise.all([
      api.get<PaginatedJobsResponse>('/api/vendor-os/jobs', { status, search, page, page_size: pageSize }),
      api.get<PaginatedTeamResponse>('/api/vendor-os/team', { page: 1, page_size: 200 }),
    ])
      .then(([jobData, teamData]) => {
        setJobs(jobData.items);
        setTotal(jobData.total);
        const map: Record<string, string> = {};
        teamData.items.forEach((m) => { map[m.id] = m.name; });
        setAssigneeNames(map);
      })
      .catch((e) => setError((e as Error).message));
  }, [status, search, page]);

  if (error) return <p className="error">{error}</p>;

  return (
    <section className="card full">
      <h3>Dispatch Feed</h3>
      <p className="muted" style={{ marginBottom: '0.75rem' }}>Jobs are auto-allocated by Patchy. Review status and monitor your team's workload here.</p>
      <div className="row">
        <input value={search} onChange={(e) => { setPage(1); setSearch(e.target.value); }} placeholder="Search address/city" />
        <select value={status} onChange={(e) => { setPage(1); setStatus(e.target.value); }}>
          <option value="">All statuses</option>
          <option value="assigned">Assigned</option>
          <option value="in_progress">In Progress</option>
          <option value="completed">Completed</option>
        </select>
      </div>
      <table className="table">
        <thead>
          <tr><th>Address</th><th>Date</th><th>Time Window</th><th>Assigned To</th><th>Status</th></tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td>{job.address}</td>
              <td className="muted">{job.scheduled_date?.slice(0, 10) ?? '—'}</td>
              <td className="muted">{job.time_window_start} – {job.time_window_end}</td>
              <td>{assigneeNames[job.assignee_id] ?? job.assignee_name ?? '—'}</td>
              <td><span className={`badge ${STATUS_BADGE[job.status] ?? ''}`}>{job.status.replace('_', ' ')}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row">
        <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</button>
        <span className="muted">Page {page} of {Math.max(Math.ceil(total / pageSize), 1)}</span>
        <button disabled={page >= Math.ceil(total / pageSize)} onClick={() => setPage((p) => p + 1)}>Next</button>
      </div>
    </section>
  );
}
