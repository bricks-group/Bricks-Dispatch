import { ChangeEvent, useCallback, useEffect, useRef, useState } from 'react';
import { api, Job } from '../lib/api';
import { compressPhoto, formatCountdown, getCheckInFix, LOCATION_REQUIRED_COPY, noShowApi, NoShowStatus } from '../lib/noshow';
import { Button } from './Button';

/**
 * Client no-show evidence for a checked-in job: wait countdown, Call client,
 * property photo, and Report no-show (enabled only when the server says all four are met).
 */
export function NoShowPanel({ job, onPatched }: { job: Job; onPatched: (job: Job) => void }) {
  const [status, setStatus] = useState<NoShowStatus | null>(null);
  const [skewMs, setSkewMs] = useState(0);
  const [nowMs, setNowMs] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [notes, setNotes] = useState('');
  const [open, setOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const apply = useCallback((next: NoShowStatus) => {
    setStatus((prev) => ({ ...next, client_phone: next.client_phone ?? prev?.client_phone ?? null }));
    const serverNow = Date.parse(next.now);
    if (Number.isFinite(serverNow)) setSkewMs(serverNow - Date.now());
  }, []);

  const refresh = useCallback(async () => {
    try { apply(await noShowApi.status(job.id)); } catch { /* keep last */ }
  }, [apply, job.id]);

  useEffect(() => { setStatus(null); setOpen(false); setNotes(''); setErr(''); void refresh(); }, [job.id, job.status, refresh]);
  useEffect(() => {
    const t = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const readyMs = status?.ready_at ? Date.parse(status.ready_at) : NaN;
  const leftMs = Number.isFinite(readyMs) ? readyMs - (nowMs + skewMs) : NaN;
  const waitDone = !!status?.waited || (Number.isFinite(leftMs) && leftMs <= 0);
  // Re-read once the countdown hits zero so can_report flips without a reload.
  const flippedRef = useRef(false);
  useEffect(() => {
    if (status && !status.waited && waitDone && !flippedRef.current) {
      flippedRef.current = true;
      void refresh();
    }
    if (!waitDone) flippedRef.current = false;
  }, [status, waitDone, refresh]);

  if (!status) return null;

  // In progress from before check-in existed (or started elsewhere): check in here first.
  if (!status.checked_in) {
    const checkInNow = async () => {
      setBusy(true); setErr('');
      try {
        const fix = await getCheckInFix();
        const out = await api.post<{ noshow?: NoShowStatus }>(`/api/vendor-os/jobs/${job.id}/check-in`, fix);
        if (out?.noshow) apply(out.noshow);
        void refresh();
      } catch (e) { setErr((e as Error).message); }
      finally { setBusy(false); }
    };
    return (
      <div className="stack" style={{ gap: 8 }}>
        <p className="muted" style={{ margin: 0, padding: 0 }}>Check in at the property before you report a no-show. {LOCATION_REQUIRED_COPY}</p>
        <div><Button variant="secondary" disabled={busy} onClick={checkInNow}>Check in</Button></div>
        {err ? <p className="error" style={{ margin: 0 }}>{err}</p> : null}
      </div>
    );
  }

  const callClient = async () => {
    setBusy(true); setErr('');
    const phone = status.client_phone;
    try {
      apply(await noShowApi.callAttempt(job.id, phone ? 'dialer' : 'no_number'));
      if (phone) window.location.href = `tel:${phone.replace(/[^\d+]/g, '')}`;
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  };

  const onPhoto = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.currentTarget.files?.[0];
    e.currentTarget.value = '';
    if (!file) return;
    setBusy(true); setErr('');
    try {
      const takenAt = new Date(file.lastModified || Date.now()).toISOString();
      const blob = await compressPhoto(file);
      apply(await noShowApi.photo(job.id, blob, takenAt));
    } catch (e2) { setErr((e2 as Error).message); }
    finally { setBusy(false); }
  };

  const report = async () => {
    setBusy(true); setErr('');
    try {
      const body: { notes?: string } = {};
      if (notes.trim()) body.notes = notes.trim();
      await api.post(`/api/vendor-os/jobs/${job.id}/agent-no-show`, body);
      onPatched({ ...job, status: 'agent_no_show' });
    } catch (e) { setErr((e as Error).message); void refresh(); }
    finally { setBusy(false); }
  };

  const rows: { done: boolean; label: string }[] = [
    {
      done: true,
      label: status.check_in_distance_m != null
        ? `Checked in, about ${Math.round(status.check_in_distance_m)} m from the property`
        : 'Checked in',
    },
    {
      done: waitDone,
      label: waitDone
        ? `Waited ${status.wait_minutes} min`
        : Number.isFinite(leftMs)
          ? `Wait ${formatCountdown(leftMs)} more`
          : `Wait ${status.wait_minutes} min`,
    },
    { done: status.has_photo, label: status.has_photo ? 'Photo taken' : 'Take a photo of the property' },
    {
      done: status.has_call,
      label: status.has_call
        ? (status.call_method === 'no_number' ? 'Call attempt logged (no number on file)' : 'Call attempt logged')
        : 'Call the client',
    },
  ];
  const missing = status.missing.filter((m) => !(waitDone && m.startsWith('Wait ')));

  return (
    <div className="stack" style={{ gap: 8 }}>
      <div>
        <Button variant="tertiary" onClick={() => setOpen((v) => !v)}>
          {open ? 'Hide no-show steps' : 'Client isn’t here?'}
        </Button>
      </div>
      {open ? (
        <div className="stack" style={{ border: '1px solid var(--c-hair, #333)', padding: 12, borderRadius: 8, gap: 10 }}>
          <p className="muted" style={{ margin: 0, padding: 0 }}>
            If the client doesn’t show, you’re paid half the job’s payout. The {status.wait_minutes}-minute wait starts at check-in or the booked start, whichever is later. Take a photo and try to call the client while you wait.
          </p>
          <ul style={{ margin: 0, paddingLeft: 0, listStyle: 'none' }}>
            {rows.map((r) => (
              <li key={r.label} style={{ display: 'flex', gap: 8, alignItems: 'baseline', padding: '2px 0' }}>
                <span aria-hidden style={{ width: 16, color: r.done ? 'var(--c-text, inherit)' : 'var(--c-muted, #888)' }}>
                  {r.done ? '✓' : '○'}
                </span>
                <span className={r.done ? undefined : 'muted'}>{r.label}</span>
              </li>
            ))}
          </ul>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Button disabled={busy} onClick={callClient}>
              {status.client_phone ? 'Call client' : 'Call client (no number)'}
            </Button>
            <Button disabled={busy} onClick={() => fileRef.current?.click()}>
              {status.has_photo ? 'Retake photo' : 'Take photo'}
            </Button>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              capture="environment"
              style={{ display: 'none' }}
              onChange={onPhoto}
            />
          </div>
          {!status.client_phone ? (
            <p className="muted" style={{ margin: 0, padding: 0, fontSize: 12 }}>
              We don’t have a number for this client. Tapping Call client logs that you tried.
            </p>
          ) : null}
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="What happened? (optional)"
            rows={3}
            maxLength={500}
            style={{ width: '100%' }}
          />
          {!status.can_report && missing.length ? (
            <div className="muted" style={{ fontSize: 13 }}>
              <p style={{ margin: 0, padding: 0 }}>Before you can report a no-show:</p>
              <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                {missing.map((m) => <li key={m}>{m}</li>)}
              </ul>
            </div>
          ) : null}
          <div>
            <Button variant="danger" disabled={busy || !status.can_report} onClick={report}>Report no-show</Button>
          </div>
          <p className="muted" style={{ margin: 0, padding: 0, fontSize: 12 }}>
            Bricks reviews the report within 2 business days. You can appeal within 7 days by emailing help@brxs.ca.
          </p>
          {err ? <p className="error" style={{ margin: 0, padding: 0 }}>{err}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
