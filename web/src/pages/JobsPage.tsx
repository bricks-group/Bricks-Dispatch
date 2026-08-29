import { FormEvent, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, AuthMember, Job, ListingHit, PaginatedJobsResponse, PaginatedTeamResponse, TeamMember, me } from '../lib/api';

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

function isPickerMember(m: TeamMember) {
  if (!m.is_active) return false;
  if ((m.id || '').toLowerCase().startsWith('0c3fa7c2')) return true;
  const t = `${m.title || ''} ${m.role || ''}`.toLowerCase();
  if (t.includes('owner')) return true;
  if (m.assignable === false) return false;
  return true;
}

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
      api.get<PaginatedTeamResponse>('/api/vendor-os/team', { page: 1, page_size: 200 }),
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
          <p>{admin ? 'All jobs including unassigned. Attach a shoot to an Office listing here.' : 'Assigned jobs. Attach a shoot to an Office listing here.'}</p>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      <section className="card full">
        <div className="row" style={{ paddingBottom: 16 }}>
          <input
            value={search}
            onChange={(e) => { setPage(1); setSearch(e.target.value); }}
            placeholder="Search address or city"
          />
          <select value={status} onChange={(e) => { setPage(1); setStatus(e.target.value); }}>
            <option value="">All statuses</option>
            <option value="pending_assignment">Unassigned</option>
            <option value="assigned">Assigned</option>
            <option value="in_progress">In Progress</option>
            <option value="completed">Completed</option>
          </select>
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
        <table className="table">
          <thead>
            <tr>
              <th>Address</th>
              <th>Listing</th>
              <th>Date</th>
              <th>Time window</th>
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
                <td style={{ fontWeight: 500 }}>{job.address}</td>
                <td className="muted">{job.listing_address || '—'}</td>
                <td className="muted">{job.scheduled_date?.slice(0, 10) ?? '—'}</td>
                <td className="muted">{job.time_window_start} – {job.time_window_end}</td>
                <td>{(job.assignee_id && (assigneeNames[job.assignee_id] ?? job.assignee_name)) || 'Unassigned'}</td>
                <td style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 13 }}>
                  {job.payout_display_cad != null ? `$${job.payout_display_cad.toLocaleString()}` : '—'}
                  {job.payout_model === 'owner_full' ? <span className="muted"> · $0 deduction</span> : null}
                </td>
                <td>
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
  const [uploads, setUploads] = useState<{ id: string; filename: string }[]>([]);
  const [uploading, setUploading] = useState(false);
  const [pick, setPick] = useState(job.assignee_id || '');

  useEffect(() => {
    setPick(job.assignee_id || '');
  }, [job.id, job.assignee_id]);

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
      await api.post(`/api/vendor-os/jobs/${job.id}/check-in`);
      onPatched({ ...job, status: 'in_progress' });
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onUpload = async (event: FormEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    setUploading(true);
    setErr('');
    try {
      const buf = await file.arrayBuffer();
      const bytes = new Uint8Array(buf);
      let binary = '';
      bytes.forEach((b) => { binary += String.fromCharCode(b); });
      const fd = new FormData();
      fd.append('job_id', job.id);
      fd.append('filename', file.name);
      fd.append('file_size', String(file.size));
      fd.append('mime_type', file.type || 'application/octet-stream');
      fd.append('file_data', btoa(binary));
      const rec = await api.postForm<{ id: string; filename: string }>('/api/uploads', fd);
      setUploads((list) => [...list, rec]);
      onPatched({ ...job, uploaded_count: (job.uploaded_count || 0) + 1 });
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setUploading(false);
      event.currentTarget.value = '';
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
      <h3>Job detail</h3>
      <div className="stack" style={{ padding: 20, maxWidth: 720 }}>
        <div>
          <p className="muted" style={{ padding: 0, margin: 0 }}>ADDRESS</p>
          <p style={{ margin: '4px 0 0', fontWeight: 500 }}>{job.address}</p>
        </div>
        <div>
          <p className="muted" style={{ padding: 0, margin: 0 }}>LISTING</p>
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
          <p className="muted" style={{ padding: 0, margin: 0 }}>ASSIGNED TO</p>
          <p style={{ margin: '4px 0 8px', fontWeight: 500 }}>{assignedLabel}</p>
          {isAdmin ? (
            <form className="row" onSubmit={assign}>
              <select value={pick} onChange={(e) => setPick(e.target.value)} style={{ flex: 1, minWidth: 180 }}>
                <option value="">Unassigned</option>
                {photographers.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
              <button type="submit" disabled={busy}>{job.assignee_id ? 'Reassign' : 'Assign'}</button>
            </form>
          ) : null}
        </div>
        <div>
          <p className="muted" style={{ padding: 0, margin: 0 }}>PAYOUT</p>
          <p style={{ margin: '4px 0 0', fontWeight: 500 }}>
            {job.payout_display_cad != null ? `$${job.payout_display_cad.toLocaleString()}` : '—'}
            {job.payout_model === 'owner_full' ? <span className="muted"> · $0 deduction</span> : <span className="muted"> · rate card</span>}
          </p>
        </div>
        <div className="row">
          {job.status === 'assigned' ? (
            <button type="button" disabled={busy} onClick={checkIn}>Check in</button>
          ) : null}
          {job.status === 'in_progress' ? (
            <button
              type="button"
              disabled={busy}
              onClick={async () => {
                setBusy(true); setErr('');
                try {
                  await api.put(`/api/vendor-os/jobs/${job.id}/status`, { status: 'completed' });
                  onPatched({ ...job, status: 'completed' });
                } catch (e) { setErr((e as Error).message); }
                finally { setBusy(false); }
              }}
            >Mark complete</button>
          ) : null}
        </div>
        <div>
          <p className="muted" style={{ padding: 0, margin: 0 }}>UPLOADS</p>
          <ul style={{ margin: '8px 0', paddingLeft: 18 }}>
            {uploads.length === 0 ? <li className="muted">None yet</li> : uploads.map((u) => <li key={u.id}>{u.filename}</li>)}
          </ul>
          <input type="file" onChange={onUpload} disabled={uploading || job.status === 'assigned'} />
          {uploading && <span className="muted"> Uploading…</span>}
        </div>
        <form className="row" onSubmit={search}>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search office listings or paste id"
            style={{ flex: 1, minWidth: 220 }}
          />
          <button type="submit" disabled={busy}>{busy ? '…' : 'Search'}</button>
          {job.transaction_id ? (
            <button type="button" disabled={busy} onClick={() => attach(null)}>Detach</button>
          ) : null}
          <button type="button" onClick={onClose}>Close</button>
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
