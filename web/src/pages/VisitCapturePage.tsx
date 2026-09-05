import React, { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { API_BASE } from '../lib/api';
import { supabase } from '../lib/supabase';

async function authHeaders(extra: Record<string, string> = {}) {
  const { data: { session } } = await supabase.auth.getSession();
  const headers: Record<string, string> = { ...extra };
  if (session?.access_token) headers.Authorization = `Bearer ${session.access_token}`;
  return headers;
}

export function VisitCapturePage() {
  const [params] = useSearchParams();
  const jobId = params.get('jobId') || '';
  const [url, setUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
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
    setError(null);
    setDone(null);
    try {
      if (file) {
        const signRes = await fetch(`${API_BASE}/api/capture/visit/sign`, {
          method: 'POST',
          headers: await authHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ job_id: jobId, filename: file.name }),
        });
        const sign = await signRes.json().catch(() => ({}));
        if (!signRes.ok) {
          throw new Error(sign?.detail || sign?.message || 'Could not start visit upload');
        }
        const put = await fetch(sign.signed_url, {
          method: 'PUT',
          headers: { 'Content-Type': sign.content_type || 'application/octet-stream' },
          body: file,
        });
        if (!put.ok) throw new Error('Visit file upload failed');
        const delRes = await fetch(`${API_BASE}/api/capture/visit/deliver`, {
          method: 'POST',
          headers: await authHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ job_id: jobId, storage_path: sign.storage_path, title: 'Virtual Visit' }),
        });
        const del = await delRes.json().catch(() => ({}));
        if (!delRes.ok) throw new Error(del?.detail || del?.message || 'Could not deliver visit');
        setDone('Virtual Visit file is on the listing.');
        setFile(null);
      } else {
        const delRes = await fetch(`${API_BASE}/api/capture/visit/deliver`, {
          method: 'POST',
          headers: await authHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ job_id: jobId, polycam_url: url.trim(), title: 'Virtual Visit' }),
        });
        const del = await delRes.json().catch(() => ({}));
        if (!delRes.ok) throw new Error(del?.detail || del?.message || 'Could not deliver visit');
        setDone('Virtual Visit link is on the listing.');
        setUrl('');
      }
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  if (!jobId) {
    return (
      <div className="capture-shell">
        <p className="error">Missing jobId. Open Deliver Visit from a job.</p>
      </div>
    );
  }

  return (
    <div className="capture-shell" style={{ padding: 24, maxWidth: 520, margin: '0 auto' }}>
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 18 }}>
        <Link to="/" className="btn-logout" style={{ textDecoration: 'none' }}>← Back</Link>
        <span className="kicker" style={{ letterSpacing: 2, fontSize: 11, color: 'var(--c-brick, #C45C26)' }}>VIRTUAL VISIT</span>
      </div>
      <h1 style={{ fontSize: 22, margin: '0 0 8px' }}>Deliver Virtual Visit</h1>
      <p className="muted" style={{ marginBottom: 18, lineHeight: 1.45 }}>
        Free-roam splat of the space — not the path-confined 3D tour. Paste a Polycam share URL, or upload a .ply / .splat / .spz.
        Owned GPU cook from GO frames comes later.
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
        accept=".ply,.splat,.spz,application/octet-stream"
        disabled={busy || !!url.trim()}
        onChange={(e) => {
          const f = e.target.files?.[0] || null;
          setFile(f);
          if (f) setUrl('');
        }}
        style={{ marginBottom: 18, color: '#F4F1EB' }}
      />

      {error && <p className="error" style={{ marginBottom: 12 }}>{error}</p>}
      {done && <p style={{ color: '#7dba7d', marginBottom: 12 }}>{done}</p>}

      <button className="btn-capture" type="button" disabled={!canSubmit} onClick={deliver}>
        {busy ? 'Delivering…' : 'Deliver to listing'}
      </button>
    </div>
  );
}
