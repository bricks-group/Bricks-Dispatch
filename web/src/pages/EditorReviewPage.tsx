import React, { useEffect, useState } from 'react';
import { api } from '../lib/api';

type Verdict = { required: string[]; present: string[]; missing: string[]; reasons: string[]; editor_approved: boolean };
type Media = { id: string; filename: string; url: string; kind: string; provider: string; qc_status: string; processing_status: string };
type Item = { id: string; address: string; client: string; scheduled_date: string; created_at: string; waiting_since: string; status: string; verdict: Verdict; media: Media[]; capture_media: { kind: string; url: string }[] };
type Approval = { status: string; job_status: string; fulfillment_gate?: Verdict };
const date = (s: string) => !s ? 'Not recorded' : /^\d{4}-\d{2}-\d{2}$/.test(s) ? s + ' (date only)' : new Date(s).toLocaleString('en-CA', { timeZone: 'America/Toronto' }) + ' ET';
const waited = (s: string) => { const h = Math.max(0, Math.floor((Date.now() - Date.parse(s)) / 3600000)); return Number.isFinite(h) ? h < 24 ? `${h}h` : `${Math.floor(h / 24)}d ${h % 24}h` : 'Unknown'; };

export function EditorReviewPage() {
  const [items, setItems] = useState<Item[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [results, setResults] = useState<Record<string, Approval>>({});
  const [enabled, setEnabled] = useState(true);
  const load = async () => {
    setLoading(true); setError('');
    try {
      const data = await api.get<{ items: Item[]; gate_enabled: boolean }>('/api/admin/editor-review');
      setItems(data.items); setEnabled(data.gate_enabled);
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not load queue'); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);
  useEffect(() => {
    if (!loading && window.location.hash) document.getElementById(decodeURIComponent(window.location.hash.slice(1)))?.scrollIntoView({ behavior: 'smooth' });
  }, [loading]);
  const approve = async (item: Item) => {
    if (!window.confirm(`Approve the complete media package for ${item.address || item.id}? This can release delivery and vendor payout.`)) return;
    setBusy(item.id); setError('');
    try {
      const result = await api.post<Approval>(`/api/admin/jobs/${encodeURIComponent(item.id)}/editor-approve`);
      setResults(r => ({ ...r, [item.id]: result }));
    } catch (e) { setError(e instanceof Error ? e.message : 'Approval failed. Refresh before retrying.'); }
    finally { setBusy(''); }
  };
  return <section className="page" style={{ padding: 28, maxWidth: 1200 }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'center' }}><div>
      <p className="overline">Dispatch admin</p><h1>Editor review</h1>
      <p>Watch the films and check the stills. Delivery and payout stay held until the package is complete and approved.</p>
    </div><button onClick={load} disabled={loading || !!busy}>Refresh</button></div>
    {!enabled && <p role="alert">Warning: the fulfillment gate emergency override is OFF. Holds are not enforced.</p>}
    {error && <p role="alert" style={{ color: '#cf5555' }}>{error}</p>}
    {loading ? <p role="status">Loading review queue…</p> : !error && <p>{items.length} open jobs · {items.filter(j => j.waiting_since).length} recorded editor holds · Dates shown in Toronto time (ET).</p>}
    {!loading && !error && !items.length && <p>No jobs waiting for your sign-off.</p>}
    {items.map(item => <article key={item.id} id={item.id} style={{ border: '1px solid var(--c-line, #555)', borderRadius: 14, padding: 22, margin: '20px 0', scrollMarginTop: 24 }}>
      <h2>{item.address || 'Address not recorded'}</h2><p>{item.client} · {item.status}</p>
      <p>Booked: {date(item.created_at)} · Shoot: {date(item.scheduled_date)}<br />{item.waiting_since ? <>Waiting {waited(item.waiting_since)} · Since {date(item.waiting_since)}</> : <>Editor hold not started / time not recorded</>}</p>
      <p><strong>Required:</strong> {item.verdict.required.join(', ')}<br /><strong>Present:</strong> {item.verdict.present.join(', ') || 'none'}<br /><strong>Missing:</strong> {item.verdict.missing.join(', ') || 'none'}</p>
      <p>{item.verdict.reasons.join(' · ')}</p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 280px), 1fr))', gap: 16 }}>
        {[...item.media].sort((a, b) => Number(b.provider === 'editor_final') - Number(a.provider === 'editor_final')).map(m => <figure key={m.id} style={{ margin: 0 }}>
          <figcaption style={{ marginBottom: 8 }}>{m.provider === 'editor_final' ? 'Final film' : m.kind === 'video' ? 'Source clip' : 'Still / asset'} · {m.filename}<br /><small>QC {m.qc_status || 'pending'} · {m.processing_status}</small></figcaption>
          {m.kind === 'video' ? <video controls preload="metadata" playsInline src={m.url} style={{ width: '100%', maxHeight: 420, background: '#111' }} /> : m.kind === 'image' ? <a href={m.url} target="_blank" rel="noreferrer"><img loading="lazy" src={m.url} alt={m.filename || 'Property still'} style={{ width: '100%', maxHeight: 420, objectFit: 'contain' }} /></a> : <a href={m.url} target="_blank" rel="noreferrer">Open asset</a>}
        </figure>)}
      </div>
      {item.capture_media.map((m, i) => <p key={i}><a href={m.url} target="_blank" rel="noreferrer">Open {m.kind || 'capture'}</a></p>)}
      {!item.media.length && !item.capture_media.length && <p>No reviewable media uploaded yet. Approval is disabled.</p>}
      {results[item.id] ? <div role="status"><strong>Editor approved.</strong> Delivery status: {results[item.id].job_status || 'pending'}. {results[item.id].job_status !== 'delivered' && 'Still held / processing — check missing components and refresh.'}</div> : <button style={{ marginTop: 18 }} disabled={!!busy || !!item.verdict.missing.length || !item.media.length && !item.capture_media.length} onClick={() => approve(item)}>{busy === item.id ? 'Approving…' : 'Approve package & release delivery'}</button>}
      <p><small>Job {item.id}</small></p>
    </article>)}
  </section>;
}
