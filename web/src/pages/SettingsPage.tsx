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
    alert('Settings saved');
  };

  if (!data) return <p>Loading settings...</p>;

  return (
    <section className="card full">
      <h3>Settings</h3>
      <div className="stack">
        <label>Company Name <input value={data.company_name} onChange={(e) => setData({ ...data, company_name: e.target.value })} /></label>
        <label>Service Areas (comma separated) <input value={serviceAreasRaw} onChange={(e) => setServiceAreasRaw(e.target.value)} /></label>
        <label>Support Email <input value={data.support_email} onChange={(e) => setData({ ...data, support_email: e.target.value })} /></label>
        <button onClick={save}>Save</button>
      </div>
    </section>
  );
}
