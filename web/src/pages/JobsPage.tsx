import { useEffect, useState } from 'react';
import { api, Job, PaginatedJobsResponse, PaginatedTeamResponse, TeamMember } from '../lib/api';

export function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [assigneeId, setAssigneeId] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const pageSize = 10;

  const load = async () => {
    try {
      const [jobData, teamData] = await Promise.all([
        api.get<PaginatedJobsResponse>('/api/vendor-os/jobs', { status, search, assignee_id: assigneeId, page, page_size: pageSize }),
        api.get<PaginatedTeamResponse>('/api/vendor-os/team', { page: 1, page_size: 200 }),
      ]);
      setJobs(jobData.items);
      setTotal(jobData.total);
      setMembers(teamData.items);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    load();
  }, [status, search, assigneeId, page]);

  const assign = async (jobId: string, memberId: string) => {
    await api.put(`/api/vendor-os/jobs/${jobId}/assign`, { member_id: memberId });
    load();
  };

  if (error) return <p className="error">{error}</p>;

  return (
    <section className="card full">
      <h3>Assigned Jobs</h3>
      <div className="row">
        <input value={search} onChange={(e) => { setPage(1); setSearch(e.target.value); }} placeholder="Search address/city" />
        <select value={status} onChange={(e) => { setPage(1); setStatus(e.target.value); }}>
          <option value="">All statuses</option>
          <option value="assigned">Assigned</option>
          <option value="in_progress">In Progress</option>
          <option value="completed">Completed</option>
        </select>
        <select value={assigneeId} onChange={(e) => { setPage(1); setAssigneeId(e.target.value); }}>
          <option value="">All assignees</option>
          {members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
      </div>
      <table className="table">
        <thead>
          <tr><th>Address</th><th>Time</th><th>Status</th><th>Assignee</th></tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td>{job.address}</td>
              <td>{job.time_window_start} - {job.time_window_end}</td>
              <td>{job.status}</td>
              <td>
                <select value={job.assignee_id} onChange={(e) => assign(job.id, e.target.value)}>
                  {members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
              </td>
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
