import { useEffect, useState } from 'react';
import { api, Job, TeamMember } from '../lib/api';

export function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [error, setError] = useState('');

  const load = async () => {
    try {
      const [jobData, teamData] = await Promise.all([
        api.get<Job[]>('/api/jobs'),
        api.get<TeamMember[]>('/api/vendor-os/team'),
      ]);
      setJobs(jobData);
      setMembers(teamData);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const assign = async (jobId: string, memberId: string) => {
    await api.put(`/api/vendor-os/jobs/${jobId}/assign`, { member_id: memberId });
    load();
  };

  if (error) return <p className="error">{error}</p>;

  return (
    <section className="card full">
      <h3>Assigned Jobs</h3>
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
    </section>
  );
}
