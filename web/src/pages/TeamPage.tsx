import { useEffect, useState } from 'react';
import { api, PaginatedTeamResponse, TeamMember } from '../lib/api';

const STATUS_OPTIONS = ['available', 'busy', 'offline'] as const;
type MemberStatus = typeof STATUS_OPTIONS[number];

const STATUS_BADGE: Record<MemberStatus, string> = {
  available: 'badge-green',
  busy:      'badge-yellow',
  offline:   'badge-gray',
};

export function TeamPage() {
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('Photographer');
  const [filterStatus, setFilterStatus] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const pageSize = 10;

  const load = () =>
    api.get<PaginatedTeamResponse>('/api/vendor-os/team', { search, status: filterStatus, page, page_size: pageSize })
      .then((res) => { setMembers(res.items); setTotal(res.total); });

  useEffect(() => { load(); }, [page, search, filterStatus]);

  const setMemberStatus = async (memberId: string, status: MemberStatus) => {
    await api.put(`/api/vendor-os/team/${memberId}`, { status });
    setMembers((prev) => prev.map((m) => m.id === memberId ? { ...m, status } : m));
  };

  const addMember = async () => {
    if (!name.trim() || !email.trim()) return;
    await api.post('/api/vendor-os/team', { name, email, role, phone: '' });
    setName(''); setEmail(''); setRole('Photographer');
    load();
  };

  return (
    <div className="page-content">
      <div className="page-header">
        <div>
          <h1>Team</h1>
          <p>Manage photographers and status</p>
        </div>
      </div>

      <section className="card full">
        <div className="row" style={{ paddingBottom: 16 }}>
          <input
            value={search}
            onChange={(e) => { setPage(1); setSearch(e.target.value); }}
            placeholder="Search name, email, or role"
          />
          <select value={filterStatus} onChange={(e) => { setPage(1); setFilterStatus(e.target.value); }}>
            <option value="">All statuses</option>
            {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Role</th>
              <th>Active jobs</th>
              <th>Availability</th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id}>
                <td style={{ fontWeight: 500 }}>{m.name}</td>
                <td className="muted">{m.role === 'Owner' ? 'Owner' : m.role || 'Photographer'}</td>
                <td style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 13 }}>{m.assigned_jobs}</td>
                <td>
                  <div className="status-toggle">
                    {STATUS_OPTIONS.map((s) => (
                      <button
                        key={s}
                        className={`status-btn ${m.status === s ? STATUS_BADGE[s] + ' active' : ''}`}
                        onClick={() => setMemberStatus(m.id, s)}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="row" style={{ borderTop: '1px solid var(--c-hair)', padding: '14px 20px', marginTop: 0 }}>
          <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</button>
          <span className="muted">Page {page} of {Math.max(Math.ceil(total / pageSize), 1)}</span>
          <button disabled={page >= Math.ceil(total / pageSize)} onClick={() => setPage((p) => p + 1)}>Next</button>
        </div>
      </section>

      <section className="card full">
        <h3>Add team member</h3>
        <div className="row" style={{ padding: '18px 20px' }}>
          <input value={name}  onChange={(e) => setName(e.target.value)}  placeholder="Full name" />
          <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email" type="email" />
          <input value={role}  onChange={(e) => setRole(e.target.value)}  placeholder="Role" />
          <button type="submit" onClick={addMember}>Add</button>
        </div>
      </section>
    </div>
  );
}
