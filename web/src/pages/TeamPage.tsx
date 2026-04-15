import { useEffect, useState } from 'react';
import { api, TeamMember } from '../lib/api';

export function TeamPage() {
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('Photographer');

  const load = () => api.get<TeamMember[]>('/api/vendor-os/team').then(setMembers);

  useEffect(() => {
    load();
  }, []);

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
