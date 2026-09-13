import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, Job, PaginatedJobsResponse } from '../lib/api';
import { captureNeitherCopy, jobCaptureModes } from '../lib/capture-modes';
import { formatShootWhen, formatShootWindow } from '../lib/shoot-window';

type Overview = {
  jobs_assigned_today: number;
  jobs_assigned_week: number;
  jobs_completed_week: number;
  capacity_utilization_pct: number;
  today_jobs?: Job[];
  upcoming_jobs: Job[];
  filter?: { table?: string; assignee_ids?: string[]; vendor_ids?: string[]; actor_id?: string };
};

function calendarToday() {
  return new Date().toLocaleDateString('en-CA');
}

function dateKey(raw?: string) {
  return (raw || '').slice(0, 10);
}

export function OverviewPage() {
  const navigate = useNavigate();
  const [data, setData] = useState<Overview | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([
      api.get<Overview>('/api/vendor-os/overview'),
      api.get<PaginatedJobsResponse>('/api/vendor-os/jobs', { page: 1, page_size: 50 }),
    ])
      .then(([ov, jobData]) => {
        setData(ov);
        setJobs(jobData.items || []);
      })
      .catch((e) => setError(e.message));
  }, []);

  const today = calendarToday();
  const todayJobs = (data?.today_jobs && data.today_jobs.length > 0)
    ? data.today_jobs
    : jobs.filter((j) => dateKey(j.scheduled_date) === today);

  return (
    <div className="page-content">
      <div className="page-header">
        <div>
          <h1>Today</h1>
          <p>Your jobs and capacity for today</p>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      {data && (
        <>
          <div className="page-grid">
            <section className="card">
              <h3>Assigned today</h3>
              <p className="metric">{data.jobs_assigned_today || todayJobs.length}</p>
              <p className="muted">jobs</p>
            </section>
            <section className="card">
              <h3>Assigned · week</h3>
              <p className="metric">{data.jobs_assigned_week}</p>
              <p className="muted">jobs</p>
            </section>
            <section className="card">
              <h3>Completed · week</h3>
              <p className="metric">{data.jobs_completed_week}</p>
              <p className="muted">jobs</p>
            </section>
            <section className="card">
              <h3>Capacity</h3>
              <p className="metric">{data.capacity_utilization_pct}%</p>
              <div className="bar-track"><div className="bar-fill" style={{ width: `${Math.min(data.capacity_utilization_pct, 100)}%` }} /></div>
              <p className="muted">utilization</p>
            </section>
          </div>

          <section className="card full">
            <h3>Today</h3>
            {todayJobs.length === 0
              ? <p className="muted" style={{ padding: '20px' }}>No jobs today</p>
              : (
                <ul className="list">
                  {todayJobs.map((job) => (
                    <li key={job.id} className="list-row">
                      <button
                        type="button"
                        className="row-click today-job-main"
                        onClick={() => navigate(`/jobs?id=${encodeURIComponent(job.id)}`)}
                        style={{ all: 'unset', cursor: 'pointer', display: 'flex', width: '100%', justifyContent: 'space-between', gap: 12 }}
                      >
                        <span>{job.address}</span>
                        <span className="muted">{formatShootWindow(job)} · {job.status?.replace('_', ' ')}</span>
                      </button>
                      {['assigned', 'in_progress'].includes(job.status || '') ? (
                        <TodayCaptureEntry job={job} />
                      ) : null}
                      <ShootChecklist job={job} />
                    </li>
                  ))}
                </ul>
              )}
          </section>

          <section className="card full">
            <h3>Upcoming Schedule</h3>
            {data.upcoming_jobs.length === 0
              ? <p className="muted" style={{ padding: '20px' }}>No upcoming jobs</p>
              : (
                <ul className="list">
                  {data.upcoming_jobs.map((job) => (
                    <li
                      key={job.id}
                      className="list-row row-click"
                      onClick={() => navigate(`/jobs?id=${encodeURIComponent(job.id)}`)}
                    >
                      <span>{job.address}</span>
                      <span className="muted">{formatShootWhen(job)}</span>
                    </li>
                  ))}
                </ul>
              )}
          </section>
        </>
      )}

      {!data && !error && <p className="muted">Loading…</p>}
    </div>
  );
}


function checklistItems(job: Job): Array<{ id?: string; label: string; completed?: boolean }> {
  const raw = job.checklist;
  if (!Array.isArray(raw)) return [];
  return raw.map((item: any, i: number) =>
    typeof item === 'string'
      ? { id: String(i), label: item, completed: false }
      : { id: item?.id || String(i), label: String(item?.label || ''), completed: !!item?.completed }
  ).filter((x) => x.label);
}

function ShootChecklist({ job }: { job: Job }) {
  const items = checklistItems(job);
  if (!items.length) return null;
  return (
    <div className="today-checklist" style={{ width: '100%', marginTop: 8 }}>
      <p className="muted" style={{ margin: 0, padding: 0, fontSize: 11, letterSpacing: '0.06em' }}>
        SHOOT CHECKLIST · ONE VISIT
      </p>
      <ol style={{ margin: '6px 0 0', paddingLeft: 18 }}>
        {items.map((item) => (
          <li key={item.id} className={item.completed ? 'muted' : undefined} style={{ marginBottom: 2 }}>
            {item.label.replace(/^\d+\.\s*/, '')}
          </li>
        ))}
      </ol>
    </div>
  );
}


function TodayCaptureEntry({ job }: { job: Job }) {
  const flags = jobCaptureModes(job);
  const hasMode = flags.tour || flags.floorplan || flags.visit;
  if (!hasMode) {
    return (
      <div className="today-capture capture-none">
        <p className="muted" style={{ padding: 0, margin: 0 }}>{captureNeitherCopy(flags)}</p>
      </div>
    );
  }
  const links = [];
  if (flags.tour) {
    links.push(
      <Link key="tour" className="btn-capture" to={`/capture?jobId=${encodeURIComponent(job.id)}&kind=splat`}>Start Capture · 3D tour</Link>
    );
  }
  if (flags.floorplan) {
    links.push(
      <Link key="fp" className="btn-capture" to={`/capture?jobId=${encodeURIComponent(job.id)}&kind=floorplan`}>Start Capture · Floor plan</Link>
    );
  }
  if (flags.visit) {
    links.push(
      <Link key="visit" className="btn-capture" to={`/capture/visit?jobId=${encodeURIComponent(job.id)}`}>Deliver Visit</Link>
    );
  }
  return (
    <div className="today-capture row" style={{ flexWrap: 'wrap', gap: 8 }}>
      {links}
    </div>
  );
}

