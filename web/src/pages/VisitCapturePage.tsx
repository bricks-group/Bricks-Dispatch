import React, { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  deliverVisitFile,
  deliverVisitUrl,
  formatVisitFileLabel,
  VISIT_EXTS,
  type VisitUploadPhase,
} from '../lib/visit-upload';

function phaseLabel(phase: VisitUploadPhase | null, busy: boolean): string {
  if (!busy) return 'Deliver to listing';
  if (phase === 'signing') return 'Preparing upload…';
  if (phase === 'uploading') return 'Uploading file…';
  if (phase === 'delivering') return 'Delivering…';
  return 'Delivering…';
}

export function VisitCapturePage() {
  const [params] = useSearchParams();
  const jobId = params.get('jobId') || '';
  const [url, setUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<VisitUploadPhase | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const canSubmit = useMemo(() => {
    if (!jobId || busy) return false;
    if (file) return true;
    return url.trim().length > 8;
  }, [jobId, busy, file, url]);

  const deliver = async () => {
    if (!jobId) return;
    setBusy(true);
    setPhase(null);
    setError(null);
    setDone(null);
    try {
      if (file) {
        await deliverVisitFile(jobId, file, { onPhase: setPhase });
        setDone('Virtual Visit file is on the listing.');
        setFile(null);
      } else {
        setPhase('delivering');
        await deliverVisitUrl(jobId, url);
        setDone('Virtual Visit link is on the listing.');
        setUrl('');
      }
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
      setPhase(null);
    }
  };

  if (!jobId) {
    return (
      <div className="capture-shell">
        <p className="error">Missing jobId. Open Deliver Visit from a job.</p>
      </div>
    );
  }

  const accept = [...VISIT_EXTS, 'application/octet-stream'].join(',');

  return (
    <div className="capture-shell" style={{ padding: 24, maxWidth: 520, margin: '0 auto' }}>
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 18 }}>
        <Link to="/" className="btn-logout" style={{ textDecoration: 'none' }}>← Back</Link>
        <span className="kicker" style={{ letterSpacing: 2, fontSize: 11, color: 'var(--c-brick, #C45C26)' }}>VIRTUAL VISIT</span>
      </div>
      <h1 style={{ fontSize: 22, margin: '0 0 8px' }}>Deliver Virtual Visit</h1>
      <p className="muted" style={{ marginBottom: 18, lineHeight: 1.45 }}>
        Free-roam splat of the space — not the path-confined 3D tour. Paste a Polycam share URL, or upload a .ply / .splat / .spz.
        Both paths land on the listing the same way (property_visits + media_delivery). Owned GPU cook from GO frames comes later.
      </p>

      <label className="muted" style={{ display: 'block', marginBottom: 6 }}>Polycam / share URL</label>
      <input
        value={url}
        onChange={(e) => { setUrl(e.target.value); setFile(null); }}
        placeholder="https://poly.cam/…"
        disabled={busy || !!file}
        style={{ width: '100%', padding: '10px 12px', borderRadius: 2, border: '1px solid #F4F1EB33', background: '#141110', color: '#F4F1EB', marginBottom: 14 }}
      />

      <p className="muted" style={{ margin: '8px 0' }}>or</p>

      <label className="muted" style={{ display: 'block', marginBottom: 6 }}>.ply / .splat / .spz file</label>
      <input
        type="file"
        accept={accept}
        disabled={busy || !!url.trim()}
        onChange={(e) => {
          const f = e.target.files?.[0] || null;
          setFile(f);
          if (f) setUrl('');
          setError(null);
          setDone(null);
        }}
        style={{ marginBottom: 8, color: '#F4F1EB' }}
      />
      {file ? (
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: 18, gap: 12 }}>
          <span className="muted" style={{ fontSize: 13, wordBreak: 'break-all' }}>{formatVisitFileLabel(file)}</span>
          <button
            type="button"
            className="btn-logout"
            disabled={busy}
            onClick={() => setFile(null)}
            style={{ flexShrink: 0 }}
          >
            Clear
          </button>
        </div>
      ) : (
        <p className="muted" style={{ marginBottom: 18, fontSize: 12 }}>
          Large .ply / .splat / .spz upload direct to storage (not through the 4.5MB API body). Same deliver finalize as Polycam URL.
        </p>
      )}

      {error && <p className="error" style={{ marginBottom: 12 }}>{error}</p>}
      {done && <p style={{ color: '#7dba7d', marginBottom: 12 }}>{done}</p>}

      <button className="btn-capture" type="button" disabled={!canSubmit} onClick={deliver}>
        {phaseLabel(phase, busy)}
      </button>
    </div>
  );
}
