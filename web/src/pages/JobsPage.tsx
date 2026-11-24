import { FormEvent, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, AuthMember, Job, ListingHit, PaginatedJobsResponse, PaginatedTeamResponse, TeamMember, me } from '../lib/api';
import { getCaptureSession, rebuildFloorPlan, rebuildTourPlayer } from '../lib/capture-api';
import type { CaptureKind } from '../lib/capture-api';
import { captureNeitherCopy, captureStatusCopy, jobCaptureModes } from '../lib/capture-modes';
import { formatShootWhen, formatShootWindow } from '../lib/shoot-window';
import './job-uploads.css';
import {
  STILLS_ACCEPT,
  STILLS_TYPES,
  StillsUploadError,
  requestRawPreview,
  uploadStillFile,
  type RawPreview,
  type StillsType,
} from '../lib/stills-upload';
import {
  BRACKET_HINT,
  fileTooLargeMessage,
  isCameraRawName,
  pickStillsUploads,
  requiresBracketSet,
  validateHdrBracketSelection,
  validateHdrUploadsForComplete,
  exposureBiasFromName,
} from '../lib/bracket-require';
import { autoNameAebFiles } from '../lib/aeb-exif';
import BricksLoader from '../components/BricksLoader';
import { DayCell } from '../components/DayCell';
import { Button, ButtonLink } from '../components/Button';
import { captureLinks } from '../components/CaptureLinks';
import { JobGuide } from '../guide/JobGuide';
import { NoShowPanel } from '../components/NoShowPanel';
import { TradeChecklist } from '../components/TradeChecklist';
import { getCheckInFix, LOCATION_REQUIRED_COPY } from '../lib/noshow';

const JOB_STATUS_TABS: [string, string][] = [
  ['', 'All'],
  ['pending_assignment', 'Open'],
  ['assigned', 'Assigned'],
  ['in_progress', 'Active'],
  ['completed', 'Done'],
];

const STATUS_BADGE: Record<string, string> = {
  pending_assignment: 'badge-gray',
  unassigned: 'badge-gray',
  assigned:    'badge-yellow',
  in_progress: 'badge-blue',
  completed:   'badge-green',
};

function isOwnerAdmin(m: { role?: string; title?: string; id?: string } | null) {
  if (!m) return false;
  const blob = `${m.role || ''} ${m.title || ''}`.toLowerCase();
  if (blob.includes('owner') || blob.includes('admin') || blob.includes('manager')) return true;
  return (m.id || '').toLowerCase().startsWith('0c3fa7c2');
}

// GET /team does not return is_active; `assignable` already requires an
// active, approved, unpaused member.
function isPickerMember(m: TeamMember) {
  if (m.is_active === false) return false;
  if ((m.id || '').toLowerCase().startsWith('0c3fa7c2')) return true;
  const t = `${m.title || ''} ${m.role || ''}`.toLowerCase();
  if (t.includes('owner')) return true;
  return m.assignable === true;
}

// Mirrors backend vendor_termination.CANCEL_REASONS (all but "other" are reviewed by Bricks).
const CANCEL_REASONS: [string, string][] = [
  ['medical', 'Medical emergency'],
  ['bereavement', 'Bereavement'],
  ['accident', 'Accident or breakdown on the way'],
  ['weather', 'Severe weather or unsafe drone conditions'],
  ['client_site', 'Unsafe site or no access caused by the client'],
  ['app_outage', 'Bricks app or GPS outage'],
  ['other', 'Something else'],
];

export function JobsPage() {
  const [params] = useSearchParams();
  const deepLinkId = params.get('id') || '';
  const [meMember, setMeMember] = useState<AuthMember | null | undefined>(undefined);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [assigneeNames, setAssigneeNames] = useState<Record<string, string>>({});
  const [photographers, setPhotographers] = useState<TeamMember[]>([]);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [assigneeFilter, setAssigneeFilter] = useState('all');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<Job | null>(null);
  const pageSize = 10;

  useEffect(() => {
    me().then(setMeMember).catch(() => setMeMember(null));
  }, []);

  const admin = isOwnerAdmin(meMember || null);

  useEffect(() => {
    if (meMember === undefined) return;
    const query: Record<string, string | number | undefined> = { status, search, page, page_size: pageSize };
    if (admin) {
      if (assigneeFilter === 'all') query.assignee_id = 'all';
      else if (assigneeFilter === 'unassigned') query.assignee_id = 'unassigned';
      else if (assigneeFilter !== 'me') query.assignee_id = assigneeFilter;
    }
    Promise.all([
      api.get<PaginatedJobsResponse>('/api/vendor-os/jobs', query),
      admin
        ? api.get<PaginatedTeamResponse>('/api/vendor-os/team', { page: 1, page_size: 200 })
        : Promise.resolve<PaginatedTeamResponse>({ items: [], total: 0, page: 1, page_size: 200 }),
    ])
      .then(([jobData, teamData]) => {
        setJobs(jobData.items);
        setTotal(jobData.total);
        const map: Record<string, string> = {};
        teamData.items.forEach((m) => { map[m.id] = m.name; });
        setAssigneeNames(map);
        setPhotographers(teamData.items.filter(isPickerMember));
        setSelected((prev) => {
          const fromList = jobData.items.find((j) => j.id === (deepLinkId || prev?.id));
          if (fromList) return fromList;
          if (deepLinkId && prev?.id === deepLinkId) return prev;
          return prev;
        });
        if (deepLinkId && !jobData.items.some((j) => j.id === deepLinkId)) {
          api.get<Job>(`/api/vendor-os/jobs/${deepLinkId}`).then((job) => {
            setSelected(job);
            setJobs((list) => (list.some((j) => j.id === job.id) ? list : [job, ...list]));
          }).catch(() => {});
        }
      })
      .catch((e) => setError((e as Error).message));
  }, [status, search, page, deepLinkId, assigneeFilter, admin, meMember]);

  const onJobPatched = (updated: Job) => {
    setJobs((list) => list.map((j) => (j.id === updated.id ? { ...j, ...updated } : j)));
    setSelected((prev) => (prev && prev.id === updated.id ? { ...prev, ...updated } : prev));
  };

  return (
    <div className="page-content">
      <div className="page-header">
        <div>
          <h1>Jobs</h1>
          <p>{admin ? 'All jobs including unassigned. Attach a job to an Office listing here.' : 'Assigned jobs. Attach a job to an Office listing here.'}</p>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      <section className="card full">
        <div className="tabs" role="tablist" aria-label="Status">
          {JOB_STATUS_TABS.map(([v, label]) => (
            <button
              key={v || 'all'}
              type="button"
              role="tab"
              aria-selected={status === v}
              className={`tab${status === v ? ' tab-on' : ''}`}
              onClick={() => { setPage(1); setStatus(v); }}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="row" style={{ paddingBottom: 16 }}>
          <input
            className="jobs-search"
            value={search}
            onChange={(e) => { setPage(1); setSearch(e.target.value); }}
            placeholder="Search address or city"
          />
          {admin ? (
            <select value={assigneeFilter} onChange={(e) => { setPage(1); setAssigneeFilter(e.target.value); }}>
              <option value="all">All</option>
              <option value="unassigned">Unassigned</option>
              <option value="me">Assigned to me</option>
              {photographers.map((p) => (
                <option key={p.id} value={p.id}>Assigned to {p.name}</option>
              ))}
            </select>
          ) : null}
        </div>
        <table className="table t1l">
          <thead>
            <tr>
              <th>Address</th>
              <th>Listing</th>
              <th>Date</th>
              <th>Time</th>
              <th>Assigned to</th>
              <th>Payout</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr
                key={job.id}
                className={selected?.id === job.id ? 'row-selected' : 'row-click'}
                onClick={() => setSelected(job)}
              >
                <td data-label="Address" className="c-t" style={{ fontWeight: 500 }}>{job.address}</td>
                <td data-label="Listing" className="muted c-mh">{job.listing_address || '—'}</td>
                <td data-label="Date" className="muted"><DayCell d={job.scheduled_date} /></td>
                <td data-label="Time" className="muted">{formatShootWindow(job)}</td>
                <td data-label="Assigned to" className="c-x">{(job.assignee_id && (assigneeNames[job.assignee_id] ?? job.assignee_name)) || 'Unassigned'}</td>
                <td data-label="Payout" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 13 }}>
                  {job.payout_display_cad != null ? `$${job.payout_display_cad.toLocaleString()}` : '—'}
                  {job.payout_model === 'owner_full' ? <span className="muted"> · $0 deduction</span> : null}
                </td>
                <td data-label="Status" className="c-s">
                  <span className={`badge ${STATUS_BADGE[job.status] ?? 'badge-gray'}`}>
                    {(job.status || '').replace('_', ' ')}
                  </span>
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

      {selected && (
        <JobListingPanel
          job={selected}
          isAdmin={admin}
          photographers={photographers}
          onClose={() => setSelected(null)}
          onPatched={onJobPatched}
        />
      )}
      {selected && <JobGuide job={selected} />}
    </div>
  );
}

function JobListingPanel({
  job,
  isAdmin,
  photographers,
  onClose,
  onPatched,
}: {
  job: Job;
  isAdmin: boolean;
  photographers: TeamMember[];
  onClose: () => void;
  onPatched: (job: Job) => void;
}) {
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<ListingHit[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [uploads, setUploads] = useState<
    { id: string; filename: string; media_type?: string; camera_raw?: boolean; preview?: RawPreview }[]
  >([]);
  const [notices, setNotices] = useState<string[]>([]);
  const [progress, setProgress] = useState<{ index: number; total: number; pct: number } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [handback, setHandback] = useState(false);
  const [cancelReason, setCancelReason] = useState('');
  const [cancelNote, setCancelNote] = useState('');
  const [stillsType, setStillsType] = useState<StillsType>('hdr');
  const [allowIncompleteBrackets, setAllowIncompleteBrackets] = useState(false);
  const [pick, setPick] = useState(job.assignee_id || '');
  const [arriveAt, setArriveAt] = useState((job.confirmed_arrive_at || job.time_window_start || '').slice(0, 5));

  useEffect(() => {
    setPick(job.assignee_id || '');
    setArriveAt((job.confirmed_arrive_at || job.time_window_start || '').slice(0, 5));
  }, [job.id, job.assignee_id, job.confirmed_arrive_at, job.time_window_start]);

  useEffect(() => {
    api.get<{ items?: { id: string; filename: string }[]; data?: { id: string; filename: string }[] } | { id: string; filename: string }[]>(
      '/api/uploads',
      { job_id: job.id },
    ).then((res) => {
      const rows = Array.isArray(res) ? res : (res.items || (res as { data?: { id: string; filename: string }[] }).data || []);
      setUploads(rows as { id: string; filename: string }[]);
    }).catch(() => setUploads([]));
  }, [job.id]);

  const checkIn = async () => {
    setBusy(true);
    setErr('');
    try {
      const fix = await getCheckInFix();
      await api.post(`/api/vendor-os/jobs/${job.id}/check-in`, fix);
      onPatched({ ...job, status: 'in_progress' });
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const confirmArrive = async (event?: FormEvent) => {
    event?.preventDefault();
    const clock = (arriveAt || '').trim().slice(0, 5);
    if (!/^\d{1,2}:\d{2}$/.test(clock)) {
      setErr('Pick an arrive time (HH:MM).');
      return;
    }
    setBusy(true);
    setErr('');
    try {
      const updated = await api.post<Job>(`/api/vendor-os/jobs/${job.id}/confirm-arrive`, {
        arrive_at: clock,
      });
      onPatched({ ...job, ...updated });
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onUpload = async (event: FormEvent<HTMLInputElement>) => {
    const input = event.currentTarget;
    let files = Array.from(input.files || []);
    if (!files.length) return;
    setUploading(true);
    setErr('');
    setNotices([]);
    const notes: string[] = [];
    try {
      // RAW first. A RAW+JPEG camera gives two files per frame: upload the RAW,
      // and the JPEG only when that RAW is over the size limit.
      const pick = pickStillsUploads(files);
      for (const f of pick.tooLarge) notes.push(`${f.name}: ${fileTooLargeMessage(f.name)}`);
      for (const f of pick.usedJpegFor) notes.push(`${f.name} is over 50 MB, so its JPEG is uploading. Keep the RAW.`);
      if (pick.droppedTwins.length) {
        notes.push(`Skipped ${pick.droppedTwins.length} JPEG ${pick.droppedTwins.length === 1 ? 'copy' : 'copies'}. The RAW is uploading.`);
      }
      setNotices([...notes]);
      files = pick.files;
      if (!files.length) return;
      if (requiresBracketSet(stillsType)) {
        // Camera AEB bursts (IMG_4031…4035) carry ±EV only in EXIF — name them
        // <firstFrame>_±EV so the gate + post-prod merge see one bracketed angle.
        // Works for camera RAW and JPEG. Unprovable frames keep the vendor's filename.
        ({ files } = await autoNameAebFiles(files, (n) => exposureBiasFromName(n) == null));
        const issue = validateHdrBracketSelection(
          files.map((f) => f.name),
          uploads.map((u) => u.filename),
          { allowIncomplete: isAdmin && allowIncompleteBrackets },
        );
        if (issue) {
          setErr(issue.message);
          return;
        }
      }
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        setProgress({ index: i + 1, total: files.length, pct: 0 });
        let rec;
        try {
          // Preserve original filename (±EV / under / over) for backend group_brackets.
          rec = await uploadStillFile(job.id, file, stillsType, (sent, total) =>
            setProgress({ index: i + 1, total: files.length, pct: total ? Math.round((sent / total) * 100) : 0 }),
          );
        } catch (e) {
          if (e instanceof StillsUploadError && e.code === 'file_too_large') {
            notes.push(`${file.name}: ${e.message}`);
            setNotices([...notes]);
            continue;
          }
          throw e;
        }
        setUploads((list) => [...list, rec]);
        onPatched({ ...job, uploaded_count: (job.uploaded_count || 0) + 1 });
        if (rec.camera_raw) {
          // Background: Bricks makes the working JPEG + preview from the RAW.
          void requestRawPreview(job.id, rec.id).then((preview) =>
            setUploads((list) => list.map((u) => (u.id === rec.id ? { ...u, preview } : u))),
          );
        }
      }
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setUploading(false);
      setProgress(null);
      input.value = '';
    }
  };

  const search = async (event?: FormEvent) => {
    event?.preventDefault();
    const query = q.trim();
    setErr('');
    if (!query) {
      setHits([]);
      return;
    }
    setBusy(true);
    try {
      const data = await api.get<{ items: ListingHit[] }>('/api/vendor-os/listings', { q: query, job_id: job.id });
      setHits(data.items || []);
    } catch (e) {
      setErr((e as Error).message);
      setHits([]);
    } finally {
      setBusy(false);
    }
  };

  const attach = async (transactionId: string | null) => {
    setBusy(true);
    setErr('');
    try {
      const updated = await api.patch<Job>(`/api/vendor-os/jobs/${job.id}/listing`, {
        transaction_id: transactionId,
      });
      onPatched(updated);
      setHits([]);
      setQ('');
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const assign = async (event?: FormEvent) => {
    event?.preventDefault();
    setBusy(true);
    setErr('');
    try {
      const updated = await api.put<Job>(`/api/vendor-os/jobs/${job.id}/assign`, {
        member_id: pick || 'unassign',
      });
      onPatched(updated);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const assignedLabel = job.assignee_name || (job.assignee_id ? job.assignee_id : 'Unassigned');

  return (
    <section className="card full">
      <h3>Job</h3>
      <div className="stack" style={{ padding: 20, maxWidth: 720 }}>
        <div>
          <p className="label">Address</p>
          <p style={{ margin: '4px 0 0', fontWeight: 500 }}>{job.address}</p>
        </div>
        <div>
          <p className="label">When</p>
          <p style={{ margin: '4px 0 0', fontWeight: 500 }}>{formatShootWhen(job)}</p>
          {['assigned', 'in_progress', 'pending_assignment'].includes(job.status) ? (
            <form className="joined" onSubmit={confirmArrive} style={{ marginTop: 12 }}>
              <input
                type="time"
                aria-label="Arrival time"
                value={arriveAt}
                onChange={(e) => setArriveAt(e.target.value)}
                required
              />
              <Button type="submit" disabled={busy}>
                {job.confirmed_arrive_at ? 'Update agent' : 'Tell agent'}
              </Button>
            </form>
          ) : null}
          {job.confirmed_arrive_at ? (
            <p className="muted" style={{ padding: 0, margin: '6px 0 0' }}>
              Agent emailed · arrives ~{formatShootWindow(job)}
            </p>
          ) : (
            <p className="muted" style={{ padding: 0, margin: '6px 0 0' }}>
              We email the agent your exact arrival time.
            </p>
          )}
        </div>
        <div>
          <p className="label">Listing</p>
          {job.listing_address ? (
            <p style={{ margin: '4px 0 0', fontWeight: 500 }}>
              {job.listing_address}
              {job.listing?.city ? <span className="muted"> · {job.listing.city}</span> : null}
            </p>
          ) : (
            <p className="muted" style={{ padding: 0, margin: '4px 0 0' }}>Not attached</p>
          )}
        </div>
        <div>
          <p className="label">Assigned to</p>
          <p style={{ margin: '4px 0 8px', fontWeight: 500 }}>{assignedLabel}</p>
          {isAdmin ? (
            <form className="row" onSubmit={assign}>
              <select value={pick} onChange={(e) => setPick(e.target.value)} style={{ flex: 1, minWidth: 180 }}>
                <option value="">Unassigned</option>
                {photographers.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
              <Button type="submit" disabled={busy}>{job.assignee_id ? 'Reassign' : 'Assign'}</Button>
            </form>
          ) : null}
        </div>
        <div>
          <p className="label">Payout</p>
          <p style={{ margin: '4px 0 0', fontWeight: 500 }}>
            {job.payout_display_cad != null ? `$${job.payout_display_cad.toLocaleString()}` : '—'}
            {job.payout_model === 'owner_full' ? <span className="muted"> · $0 deduction</span> : <span className="muted"> · rate card</span>}
          </p>
        </div>
        {handback && job.status === 'assigned' ? (
          <div className="stack" style={{ gap: 8 }}>
            <p className="label">Hand back this job</p>
            <p className="muted" style={{ margin: 0 }}>
              Free until 48 hours before the start. Inside 48 hours, cancelling closes your account unless
              it's one of the reasons below, which go to Bricks for review. You can't send someone else.
            </p>
            <select value={cancelReason} onChange={(e) => setCancelReason(e.target.value)} aria-label="Reason">
              <option value="">Pick a reason</option>
              {CANCEL_REASONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            <textarea rows={3} placeholder="Anything Bricks should know (optional)" value={cancelNote}
              onChange={(e) => setCancelNote(e.target.value)} maxLength={1000} />
            <Button
              disabled={busy || !cancelReason}
              onClick={async () => {
                setBusy(true); setErr('');
                try {
                  await api.put(`/api/vendor-os/jobs/${job.id}/status`, {
                    status: 'declined', cancel_reason: cancelReason, cancel_note: cancelNote || undefined,
                  });
                  setHandback(false);
                  onPatched({ ...job, status: 'declined' });
                } catch (e) { setErr((e as Error).message); }
                finally { setBusy(false); }
              }}
            >Hand back job</Button>
          </div>
        ) : null}
        {/* One primary per state (Accept / Check in / Mark complete); capture links are secondary. */}
        <div className="actions">
          {['assigned', 'in_progress'].includes(job.status) ? captureLinks(job) : null}
          {job.status === 'pending_assignment' ? (
            <>
              <Button
                variant="primary"
                disabled={busy}
                onClick={async () => {
                  setBusy(true); setErr('');
                  try {
                    await api.post(`/api/vendor-os/jobs/${job.id}/watch-action`, { action: 'accept' });
                    onPatched({ ...job, status: 'assigned' });
                  } catch (e) { setErr((e as Error).message); }
                  finally { setBusy(false); }
                }}
              >Accept</Button>
              <Button
                disabled={busy}
                onClick={async () => {
                  setBusy(true); setErr('');
                  try {
                    await api.put(`/api/vendor-os/jobs/${job.id}/status`, { status: 'declined' });
                    onPatched({ ...job, status: 'declined' });
                  } catch (e) { setErr((e as Error).message); }
                  finally { setBusy(false); }
                }}
              >Decline</Button>
            </>
          ) : null}
          {job.status === 'assigned' ? (
            <>
              <Button variant="primary" disabled={busy} onClick={checkIn}>Check in</Button>
              <Button disabled={busy} onClick={() => setHandback((v) => !v)}>Can't make it</Button>
            </>
          ) : null}
          {job.status === 'in_progress' ? (
            <Button
              variant="primary"
              disabled={busy}
              onClick={async () => {
                setBusy(true); setErr('');
                try {
                  const bracketBlock = validateHdrUploadsForComplete(uploads, {
                    allowIncomplete: isAdmin && allowIncompleteBrackets,
                  });
                  if (bracketBlock) {
                    setErr(bracketBlock.message);
                    return;
                  }
                  await api.put(`/api/vendor-os/jobs/${job.id}/status`, { status: 'completed' });
                  onPatched({ ...job, status: 'completed' });
                } catch (e) { setErr((e as Error).message); }
                finally { setBusy(false); }
              }}
            >Mark complete</Button>
          ) : null}
        </div>
        {job.status === 'assigned' ? (
          <p className="muted" style={{ margin: 0, padding: 0, fontSize: 12 }}>{LOCATION_REQUIRED_COPY}</p>
        ) : null}
        {job.status === 'in_progress' ? <NoShowPanel job={job} onPatched={onPatched} /> : null}
        {['assigned', 'in_progress'].includes(job.status) && !job.trade_job ? (
          <CaptureNone job={job} />
        ) : null}
        {job.trade_job ? <TradeChecklist job={job} /> : <JobShootChecklist job={job} />}
        {job.trade_job ? null : <CaptureStatusBlock job={job} />}
        {job.trade_job && uploads.length === 0 ? null : job.trade_job ? (
          <div>
            <p className="label">Files on this job</p>
            <ul className="upload-list">
              {uploads.map((u) => (
                <li key={u.id} className="upload-row">
                  <span className="upload-meta"><span className="upload-name">{u.filename}</span></span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
        <div>
          <p className="label">Uploads</p>
          <p className="muted" style={{ padding: 0, margin: '4px 0 8px', fontSize: 12 }}>
            Upload after the shoot. Pick a type first so delivery handles it right.
          </p>
          {requiresBracketSet(stillsType) ? (
            <>
              <p style={{ padding: 0, margin: '0 0 8px', fontSize: 13, color: 'var(--c-brick, #D2542B)' }}>
                {BRACKET_HINT} At least 3 per angle, plus the window frame.
              </p>
              <p className="muted" style={{ padding: 0, margin: '0 0 8px', fontSize: 12 }}>
                Upload the RAW files. JPEG only if a RAW is over 50 MB. Keep your RAWs until the job is approved.
              </p>
              {stillsType === 'twilight' ? (
                <p className="muted" style={{ padding: 0, margin: '0 0 8px', fontSize: 12 }}>
                  Real dusk only. We keep the dusk sky.
                </p>
              ) : null}
            </>
          ) : stillsType === 'staging' ? (
            <p className="muted" style={{ padding: 0, margin: '0 0 8px', fontSize: 12 }}>
              Delivered as shot. No enhance.
            </p>
          ) : stillsType === 'floorplan' ? (
            <p className="muted" style={{ padding: 0, margin: '0 0 8px', fontSize: 12 }}>
              PNG or PDF, delivered as is.
            </p>
          ) : null}
          <ul className="upload-list">
            {uploads.length === 0 ? <li className="muted">None yet</li> : uploads.map((u) => {
              const raw = u.camera_raw || isCameraRawName(u.filename);
              return (
                <li key={u.id} className="upload-row">
                  {u.preview?.preview_url ? (
                    <img className="upload-thumb" src={u.preview.preview_url} alt="" />
                  ) : raw ? (
                    <span className="upload-thumb upload-thumb-empty" aria-hidden="true">RAW</span>
                  ) : null}
                  <span className="upload-meta">
                    <span className="upload-name">{u.filename}</span>
                    <span className="muted">
                      {raw ? 'RAW' : null}
                      {raw && u.media_type ? ' · ' : null}
                      {u.media_type || null}
                      {raw && u.preview?.status === 'pending' ? ' · Making preview' : null}
                      {raw && u.preview?.status === 'ready' ? ' · Preview ready' : null}
                    </span>
                    {raw && u.preview?.status === 'needs_review' ? (
                      <span className="upload-warn">{u.preview.message || 'Bricks will check this RAW. Keep the file.'}</span>
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ul>
          <div className="row upload-controls">
            <select
              value={stillsType}
              onChange={(e) => setStillsType(e.target.value as StillsType)}
              disabled={uploading || job.status === 'assigned'}
              aria-label="Upload type"
            >
              {STILLS_TYPES.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
            </select>
            <label className={`btn btn-secondary upload-pick${uploading || job.status === 'assigned' ? ' is-disabled' : ''}`}>
              <span className="btn-label">{uploading ? 'Uploading' : 'Choose files'}</span>
              <input
                type="file"
                multiple
                accept={STILLS_ACCEPT}
                onChange={onUpload}
                disabled={uploading || job.status === 'assigned'}
              />
            </label>
          </div>
          {progress ? (
            <p className="muted upload-progress" role="status">
              Uploading {progress.index} of {progress.total} · {progress.pct}%
            </p>
          ) : null}
          {notices.length ? (
            <ul className="upload-notices" role="status">
              {notices.map((n) => <li key={n}>{n}</li>)}
            </ul>
          ) : null}
          {isAdmin ? (
            <label className="muted" style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8, fontSize: 12 }}>
              <input
                type="checkbox"
                checked={allowIncompleteBrackets}
                onChange={(e) => setAllowIncompleteBrackets(e.target.checked)}
              />
              Admin override: allow incomplete brackets
            </label>
          ) : null}
        </div>
        )}
        <form className="row" onSubmit={search}>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search office listings or paste id"
            style={{ flex: 1, minWidth: 220 }}
          />
          <Button type="submit" disabled={busy}>{busy ? '…' : 'Search'}</Button>
          {job.transaction_id ? (
            <Button variant="tertiary" disabled={busy} onClick={() => attach(null)}>Detach</Button>
          ) : null}
          <Button variant="tertiary" onClick={onClose}>Close</Button>
        </form>
        {err && <p className="error" style={{ padding: 0 }}>{err}</p>}
        {hits.length > 0 && (
          <ul className="listing-hits">
            {hits.map((hit) => (
              <li key={hit.id}>
                <button type="button" className="listing-hit" disabled={busy} onClick={() => attach(hit.id)}>
                  <span>{hit.address || 'Listing'}</span>
                  <span className="muted">{[hit.city, hit.deal_type].filter(Boolean).join(' · ') || 'Office listing'}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}


function JobShootChecklist({ job }: { job: Job }) {
  const raw = job.checklist;
  const items = Array.isArray(raw)
    ? raw.map((item: any, i: number) =>
        typeof item === 'string'
          ? { id: String(i), label: item, completed: false }
          : { id: item?.id || String(i), label: String(item?.label || ''), completed: !!item?.completed }
      ).filter((x: any) => x.label)
    : [];
  if (!items.length) return null;
  return (
    <div>
      <p className="label">{job.checklist_title || 'Shoot checklist'}</p>
      <ol style={{ margin: '8px 0 0', paddingLeft: 18 }}>
        {items.map((item: any) => (
          <li key={item.id} className={item.completed ? 'muted' : undefined}>{item.label}</li>
        ))}
      </ol>
    </div>
  );
}


function CaptureNone({ job }: { job: Job }) {
  const flags = jobCaptureModes(job);
  if (flags.tour || flags.floorplan || flags.visit) return null;
  return (
    <div className="capture-none">
      <p className="muted" style={{ padding: 0, margin: 0 }}>{captureNeitherCopy(flags)}</p>
    </div>
  );
}



function CaptureStatusBlock({ job }: { job: Job }) {
  const flags = jobCaptureModes(job);
  const kinds: CaptureKind[] = [];
  if (flags.tour) kinds.push('splat');
  if (flags.floorplan) kinds.push('floorplan');
  const [rows, setRows] = useState<{ kind: CaptureKind; status: string; line: string; sessionId?: string | null }[]>([]);
  const [rebuildState, setRebuildState] = useState<{ busy?: boolean; msg?: string; error?: string }>({});

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (kinds.length === 0) { setRows([]); return; }
      const next: { kind: CaptureKind; status: string; line: string; sessionId?: string | null }[] = [];
      for (const kind of kinds) {
        const s = await getCaptureSession(job.id, kind);
        const status = (s.status || '').toLowerCase();
        if (!status || status === 'in_progress' && (s.frameCount || 0) === 0) continue;
        const line = captureStatusCopy(status, kind);
        if (!line && !status) continue;
        next.push({ kind, status, line: line || status, sessionId: s.sessionId });
      }
      if (!cancelled) setRows(next);
    })();
    return () => { cancelled = true; };
  }, [job.id, flags.tour, flags.floorplan]);

  const handleRebuild = async (kind: CaptureKind, sessionId?: string | null) => {
    setRebuildState({ busy: true });
    const result = kind === 'floorplan'
      ? await rebuildFloorPlan(job.id, sessionId)
      : await rebuildTourPlayer(job.id, 'splat', sessionId);
    if (!result.ok) {
      setRebuildState({ error: result.error ?? 'Rebuild failed' });
      return;
    }
    setRebuildState({
      msg: kind === 'floorplan'
        ? 'Floor plan rebuilt. Refresh the listing.'
        : 'Tour rebuilt. Hard-refresh the listing.',
    });
  };

  if (rows.length === 0) return null;
  return (
    <div className="capture-status-block">
      <p className="label" style={{ marginBottom: 8 }}>Capture status</p>
      <div className="stack" style={{ gap: 8 }}>
        {rows.map((r) => {
          const failed = r.status === 'failed';
          const ready = r.status === 'delivered';
          const compiling = ['submitted', 'splatting', 'tracing'].includes(r.status);
          const cls = failed ? 'is-failed' : ready ? 'is-ready' : compiling ? 'is-compiling' : '';
          return (
            <div key={r.kind} className={`capture-status-row ${cls}`}>
              <span>{r.line}</span>
              <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
                {ready && (r.kind === 'splat' || r.kind === 'floorplan') ? (
                  <Button disabled={rebuildState.busy} onClick={() => handleRebuild(r.kind, r.sessionId)}>
                    {rebuildState.busy && <BricksLoader variant="cycle" size={16} color="currentColor" style={{ marginRight: 8 }} />}{rebuildState.busy ? 'Rebuilding…' : (r.kind === 'floorplan' ? 'Rebuild floor plan' : 'Rebuild tour')}
                  </Button>
                ) : null}
                <ButtonLink variant="tertiary" to={`/capture/review?jobId=${encodeURIComponent(job.id)}&kind=${r.kind}`}>
                  {failed ? 'Review and retry' : 'Review'}
                </ButtonLink>
              </div>
            </div>
          );
        })}
        {rebuildState.error ? <p className="error" style={{ padding: 0, margin: 0 }}>{rebuildState.error}</p> : null}
        {rebuildState.msg ? <p className="muted" style={{ padding: 0, margin: 0 }}>{rebuildState.msg}</p> : null}
      </div>
    </div>
  );
}
