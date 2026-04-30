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
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.get<Availability>('/api/vendor-os/availability').then(setData);
  }, []);

  const save = async () => {
    if (!data) return;
    await api.put('/api/vendor-os/availability', data);
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  return (
    <div className="page-content">
      <div className="page-header">
        <div>
          <h1>Availability</h1>
          <p>Set your working hours and daily capacity</p>
        </div>
      </div>

      {!data && <p className="muted">Loading…</p>}

      {data && (
        <section className="card full">
          <h3>Working hours &amp; capacity</h3>
          <div className="stack">
            <label>
              <input
                type="checkbox"
                checked={data.available}
                onChange={(e) => setData({ ...data, available: e.target.checked })}
              />
              Available for assignments
            </label>
            <div className="row" style={{ padding: 0 }}>
              <label>
                Start
                <input type="time" value={data.work_start} onChange={(e) => setData({ ...data, work_start: e.target.value })} />
              </label>
              <label>
                End
                <input type="time" value={data.work_end} onChange={(e) => setData({ ...data, work_end: e.target.value })} />
              </label>
              <label>
                Max jobs / day
                <input type="number" min={1} value={data.jobs_per_day} onChange={(e) => setData({ ...data, jobs_per_day: Number(e.target.value) })} style={{ width: 80 }} />
              </label>
            </div>
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
