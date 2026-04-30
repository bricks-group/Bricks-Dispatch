import { useEffect, useState } from 'react';
import { api } from '../lib/api';

type Settings = {
  company_name: string;
  service_areas: string[];
  support_email: string;
};

export function SettingsPage() {
  const [data, setData] = useState<Settings | null>(null);
  const [serviceAreasRaw, setServiceAreasRaw] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.get<Settings>('/api/vendor-os/settings').then((res) => {
      setData(res);
      setServiceAreasRaw(res.service_areas.join(', '));
    });
  }, []);

  const save = async () => {
    if (!data) return;
    const payload = {
      ...data,
      service_areas: serviceAreasRaw.split(',').map((v) => v.trim()).filter(Boolean),
    };
    await api.put('/api/vendor-os/settings', payload);
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  return (
    <div className="page-content">
      <div className="page-header">
        <div>
          <h1>Settings</h1>
          <p>Company name, service areas, and contact</p>
        </div>
      </div>

      {!data && <p className="muted">Loading…</p>}

      {data && (
        <section className="card full">
          <h3>Vendor profile</h3>
          <div className="stack">
            <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
              Company name
              <input value={data.company_name} onChange={(e) => setData({ ...data, company_name: e.target.value })} style={{ width: 320 }} />
            </label>
            <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
              Service areas
              <input value={serviceAreasRaw} onChange={(e) => setServiceAreasRaw(e.target.value)} placeholder="City, City, …" style={{ width: 480 }} />
              <span className="muted">Comma-separated</span>
            </label>
            <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
              Support email
              <input value={data.support_email} onChange={(e) => setData({ ...data, support_email: e.target.value })} type="email" style={{ width: 320 }} />
            </label>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <button type="submit" onClick={save}>Save</button>
              {saved && <span className="muted">Saved</span>}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
