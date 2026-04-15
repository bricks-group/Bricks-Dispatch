import { useEffect, useState } from 'react';
import { api } from '../lib/api';

type Availability = {
  work_start: string;
  work_end: string;
  jobs_per_day: number;
  available: boolean;
  blocked_windows: Array<{ start: string; end: string }>;
};

export function AvailabilityPage() {
  const [data, setData] = useState<Availability | null>(null);

  useEffect(() => {
    api.get<Availability>('/api/vendor-os/availability').then(setData);
  }, []);

  const save = async () => {
    if (!data) return;
    await api.put('/api/vendor-os/availability', data);
    alert('Availability updated');
  };

  if (!data) return <p>Loading availability...</p>;

  return (
    <section className="card full">
      <h3>Availability & Capacity</h3>
      <div className="row">
        <label>Available <input type="checkbox" checked={data.available} onChange={(e) => setData({ ...data, available: e.target.checked })} /></label>
        <label>Start <input type="time" value={data.work_start} onChange={(e) => setData({ ...data, work_start: e.target.value })} /></label>
        <label>End <input type="time" value={data.work_end} onChange={(e) => setData({ ...data, work_end: e.target.value })} /></label>
        <label>Jobs/day <input type="number" min={1} value={data.jobs_per_day} onChange={(e) => setData({ ...data, jobs_per_day: Number(e.target.value) })} /></label>
        <button onClick={save}>Save</button>
      </div>
    </section>
  );
}
