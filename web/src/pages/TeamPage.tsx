import { useEffect, useState } from 'react';
import { api, PaginatedTeamResponse, TeamMember } from '../lib/api';

export function TeamPage() {
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('Photographer');
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const pageSize = 10;

  const load = () => api.get<PaginatedTeamResponse>('/api/vendor-os/team', { search, status, page, page_size: pageSize }).then((res) => {
    setMembers(res.items);
    setTotal(res.total);
  });

  useEffect(() => {
    load();
  }, [page, search, status]);

  const addMember = async () => {
    if (!name.trim() || !email.trim()) return;
    await api.post('/api/vendor-os/team', { name, email, role, phone: '' });
    setName('');
    setEmail('');
    setRole('Photographer');
    load();
  };

  return (
    <div className="page-grid">
      <section className="card full">
        <h3>People</h3>
        <div className="row">
          <input value={search} onChange={(e) => { setPage(1); setSearch(e.target.value); }} placeholder="Search name/email/role" />
          <select value={status} onChange={(e) => { setPage(1); setStatus(e.target.value); }}>
            <option value="">All statuses</option>
            <option value="available">Available</option>
            <option value="busy">Busy</option>
            <option value="offline">Offline</option>
          </select>
        </div>
        <table className="table">
          <thead>
            <tr><th>Name</th><th>Status</th><th>Role</th><th>Assigned Jobs</th></tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id}>
                <td>{m.name}</td>
                <td>{m.status}</td>
                <td>{m.role}</td>
                <td>{m.assigned_jobs}</td>
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
      <section className="card full">
        <h3>Add Team Member</h3>
        <div className="row">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" />
          <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email" />
          <input value={role} onChange={(e) => setRole(e.target.value)} placeholder="Role" />
          <button onClick={addMember}>Add</button>
        </div>
      </section>
    </div>
  );
}
