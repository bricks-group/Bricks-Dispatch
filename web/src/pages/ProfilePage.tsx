import { FormEvent, useEffect, useRef, useState } from 'react';
import { api, VendorProfile } from '../lib/api';
import BricksLoader from '../components/BricksLoader';

const SKILLS: { id: string; label: string }[] = [
  { id: 'photo', label: 'Photo' },
  { id: 'drone', label: 'Drone' },
  { id: 'reel', label: 'Reel' },
  { id: 'tour', label: 'Tour' },
  { id: 'twilight', label: 'Twilight' },
];

export function ProfilePage() {
  const [data, setData] = useState<VendorProfile | null>(null);
  const [areasRaw, setAreasRaw] = useState('');
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const photoRef = useRef<HTMLInputElement>(null);

  const load = () =>
    api.get<VendorProfile>('/api/vendor-os/profile').then((res) => {
      setData(res);
      setAreasRaw((res.service_areas || []).join(', '));
    }).catch((e) => setError((e as Error).message));

  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!data) return;
    setError('');
    try {
      const res = await api.put<VendorProfile>('/api/vendor-os/profile', {
        name: data.name,
        phone: data.phone,
        availability: data.availability,
        skills: data.skills,
        service_areas: areasRaw.split(',').map((v) => v.trim()).filter(Boolean),
        home_city: data.home_city,
      });
      setData(res);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const onRpas = async (event: FormEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    setUploading(true);
    setError('');
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('level', 'advanced');
      const res = await api.postForm<VendorProfile>('/api/vendor-os/profile/rpas', fd);
      setData(res);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const onPhoto = async (event: FormEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    setUploading(true);
    setError('');
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await api.postForm<VendorProfile>('/api/vendor-os/profile/photo', fd);
      setData(res);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading(false);
      if (photoRef.current) photoRef.current.value = '';
    }
  };

  const toggleSkill = (id: string) => {
    if (!data) return;
    const has = data.skills.includes(id);
    setData({
      ...data,
      skills: has ? data.skills.filter((s) => s !== id) : [...data.skills, id],
    });
  };

  return (
    <div className="page-content">
      <div className="page-header">
        <div>
          <h1>Profile</h1>
          <p>Identity, skills, RPAS, service area — the same fields the Office assigner reads</p>
        </div>
      </div>

      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="muted"><BricksLoader variant="assemble" size={40} style={{ display: 'block', marginBottom: 12 }} />Loading…</p>}

      {data && (
        <>
          <div className="page-grid">
            <section className="card">
              <h3>Title</h3>
              <p className="metric" style={{ fontSize: 22 }}>{data.title}</p>
              <p className="muted">{data.name}</p>
            </section>
            <section className="card">
              <h3>Tier</h3>
              <p className="metric">{data.tier_label}</p>
              <p className="muted">{data.title === 'Owner' ? 'Highest · Owner' : `Tier ${data.tier_label}`}</p>
            </section>
            <section className="card">
              <h3>Payouts</h3>
              <p className="metric" style={{ fontSize: 18 }}>{data.payout_label}</p>
              <p className="muted">
                {data.payout_model === 'owner_full' ? '$0 deduction' : 'Fixed rate card — not % of rack'}
              </p>
            </section>
            <section className="card">
              <h3>Assignable</h3>
              <p className="metric" style={{ fontSize: 18 }}>{data.assignable ? 'Yes' : 'No'}</p>
              <p className="muted">{data.assignable_reason || 'Approved and active'}</p>
            </section>
          </div>

          <section className="card full">
            <h3>Identity</h3>
            <div className="stack">
              <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
                Name
                <input value={data.name} onChange={(e) => setData({ ...data, name: e.target.value })} style={{ width: 320 }} />
              </label>
              <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
                Phone
                <input value={data.phone} onChange={(e) => setData({ ...data, phone: e.target.value })} style={{ width: 320 }} />
              </label>
              <p className="muted" style={{ padding: 0 }}>{data.email}</p>
              <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
                Photo
                {data.avatar_url ? (
                  <img src={data.avatar_url} alt="" style={{ width: 72, height: 72, objectFit: 'cover', borderRadius: 8 }} />
                ) : (
                  <span className="muted">No photo yet</span>
                )}
                <input ref={photoRef} type="file" accept="image/*" onChange={onPhoto} />
              </label>
            </div>
          </section>

          <section className="card full">
            <h3>Skills</h3>
            <div className="stack">
              <div className="chip-row">
                {SKILLS.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    className={`chip ${data.skills.includes(s.id) ? 'chip-on' : ''}`}
                    onClick={() => toggleSkill(s.id)}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
          </section>

          <section className="card full">
            <h3>Advanced RPAS</h3>
            <div className="stack">
              <p className="muted" style={{ padding: 0 }}>
                Transport Canada Advanced certificate. Stored as a file URL on your member record.
                {data.rpas_certificate_level ? ` Current level: ${data.rpas_certificate_level}.` : ''}
              </p>
              {data.rpas_certificate_url ? (
                <p style={{ margin: 0 }}>
                  On file · <span className="muted">{data.rpas_certificate_url}</span>
                </p>
              ) : (
                <p className="muted" style={{ padding: 0 }}>No certificate uploaded yet</p>
              )}
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <input ref={fileRef} type="file" accept=".pdf,image/*" onChange={onRpas} />
                {uploading && <span className="muted">Uploading…</span>}
              </div>
            </div>
          </section>

          <section className="card full">
            <h3>Service area &amp; availability</h3>
            <div className="stack">
              <label>
                <input
                  type="checkbox"
                  checked={data.availability}
                  onChange={(e) => setData({ ...data, availability: e.target.checked })}
                />
                Available for assignments
              </label>
              <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
                Home city
                <input value={data.home_city || ''} onChange={(e) => setData({ ...data, home_city: e.target.value })} style={{ width: 320 }} />
              </label>
              <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
                Service areas
                <input value={areasRaw} onChange={(e) => setAreasRaw(e.target.value)} placeholder="Toronto, Mississauga, …" style={{ width: 480 }} />
                <span className="muted">Comma-separated. Office, never brokerage.</span>
              </label>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <button type="submit" onClick={save}>Save profile</button>
                {saved && <span className="muted">Saved</span>}
              </div>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
