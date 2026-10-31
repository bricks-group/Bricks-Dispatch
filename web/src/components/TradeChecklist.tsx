import { ChangeEvent, useCallback, useEffect, useState } from 'react';
import { Job } from '../lib/api';
import {
  CHECKLIST_MAX_BYTES,
  checklistApi,
  InventoryRow,
  TradeChecklist as Checklist,
  TradeChecklistItem,
} from '../lib/job-checklist';
import { compressPhoto } from '../lib/noshow';
import { Button } from './Button';
import '../pages/job-uploads.css';

/**
 * Trade job checklist: tick each item and attach its proof. Saved on the job.
 * The job can't be marked complete until every item is ticked with its proof.
 */
export function TradeChecklist({ job, onChange }: { job: Job; onChange?: (c: Checklist) => void }) {
  const [data, setData] = useState<Checklist | null>(null);
  const [err, setErr] = useState('');
  const [busyItem, setBusyItem] = useState<string | null>(null);
  const [progress, setProgress] = useState('');

  const apply = useCallback((next: Checklist) => { setData(next); onChange?.(next); }, [onChange]);

  useEffect(() => {
    let cancelled = false;
    setData(null); setErr('');
    checklistApi.get(job.id)
      .then((c) => { if (!cancelled) apply(c); })
      .catch((e) => { if (!cancelled) setErr((e as Error).message); });
    return () => { cancelled = true; };
  }, [job.id, job.status]);

  const run = async (itemId: string, fn: () => Promise<Checklist>) => {
    setBusyItem(itemId); setErr('');
    try { apply(await fn()); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusyItem(null); setProgress(''); }
  };

  if (!data) {
    return err ? (
      <div>
        <p className="label">Job checklist</p>
        <p className="error" style={{ padding: 0, margin: '4px 0 0' }}>{err}</p>
      </div>
    ) : null;
  }

  const editable = data.editable && ['assigned', 'in_progress'].includes(job.status);

  const addPhotos = (item: TradeChecklistItem) => async (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    if (!files.length) return;
    await run(item.id, async () => {
      let last = data;
      for (let i = 0; i < files.length; i++) {
        setProgress(files.length > 1 ? `Adding photo ${i + 1} of ${files.length}` : 'Adding photo');
        const file = files[i];
        const blob = await compressPhoto(file, 2000, 0.82);
        if (blob.size > CHECKLIST_MAX_BYTES) throw new Error('That photo is over 4 MB. Use a smaller photo.');
        last = await checklistApi.addPhoto(job.id, item.id, blob, new Date(file.lastModified || Date.now()).toISOString());
        apply(last);
      }
      return last;
    });
  };

  const uploadReport = (item: TradeChecklistItem) => async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (file.size > CHECKLIST_MAX_BYTES) {
      setErr('That file is over 4 MB. Export a smaller PDF, or upload a photo of the report.');
      return;
    }
    setProgress('Uploading report');
    await run(item.id, () => checklistApi.uploadReport(job.id, item.id, file));
  };

  return (
    <div>
      <p className="label">{data.title || 'Job checklist'}</p>
      <p className="muted" style={{ padding: 0, margin: '4px 0 8px', fontSize: 12 }}>
        {editable
          ? 'Tick each item as you finish it. Items that need photos, an inventory or a report can only be ticked once those are added.'
          : data.complete ? 'All done.' : 'This job is closed.'}
      </p>
      <ol className="trade-checklist" style={{ margin: 0, paddingLeft: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 12 }}>
        {data.items.map((item) => (
          <ChecklistRow
            key={item.id}
            item={item}
            editable={editable}
            busy={busyItem === item.id}
            anyBusy={busyItem !== null}
            onTick={(completed) => run(item.id, () => checklistApi.patch(job.id, item.id, { completed }))}
            onPatch={(body) => run(item.id, () => checklistApi.patch(job.id, item.id, body))}
            onRemovePhoto={(photoId) => run(item.id, () => checklistApi.removePhoto(job.id, item.id, photoId))}
            onAddPhotos={addPhotos(item)}
            onReport={uploadReport(item)}
          />
        ))}
      </ol>
      {progress ? <p className="muted upload-progress" role="status">{progress}</p> : null}
      {err ? <p className="error" style={{ padding: 0, margin: '8px 0 0' }}>{err}</p> : null}
    </div>
  );
}

function ChecklistRow({
  item, editable, busy, anyBusy, onTick, onPatch, onRemovePhoto, onAddPhotos, onReport,
}: {
  item: TradeChecklistItem;
  editable: boolean;
  busy: boolean;
  anyBusy: boolean;
  onTick: (completed: boolean) => void;
  onPatch: (body: { note?: string; none_found?: boolean; inventory?: InventoryRow[] }) => void;
  onRemovePhoto: (photoId: string) => void;
  onAddPhotos: (e: ChangeEvent<HTMLInputElement>) => void;
  onReport: (e: ChangeEvent<HTMLInputElement>) => void;
}) {
  const [note, setNote] = useState(item.note || '');
  const [rows, setRows] = useState<InventoryRow[]>(item.inventory?.length ? item.inventory : [{ item: '', room: '' }]);
  useEffect(() => { setNote(item.note || ''); }, [item.note]);
  useEffect(() => { setRows(item.inventory?.length ? item.inventory : [{ item: '', room: '' }]); }, [JSON.stringify(item.inventory || [])]);

  const takesPhotos = item.proof === 'photo' || item.proof === 'findings';
  const needsProof = item.proof !== 'tick';
  const canTick = !item.missing || item.missing === 'tick it';
  const disabled = !editable || anyBusy;
  const savedRows = JSON.stringify(item.inventory || []);
  const cleanRows = rows.filter((r) => r.item.trim() || r.room.trim() || (r.note || '').trim());

  return (
    <li style={{ borderTop: '1px solid var(--c-graphite, rgba(244,241,235,0.12))', paddingTop: 10 }}>
      <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', minHeight: 44, cursor: disabled ? 'default' : 'pointer' }}>
        <input
          type="checkbox"
          checked={item.completed}
          disabled={disabled || (!item.completed && !canTick)}
          onChange={(e) => onTick(e.target.checked)}
          style={{ width: 20, height: 20, marginTop: 2 }}
          aria-label={item.label}
        />
        <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span className={item.completed ? 'muted' : undefined}>{item.label}</span>
          {item.missing && needsProof && item.missing !== 'tick it' ? (
            <span className="muted" style={{ fontSize: 12 }}>To tick this, {item.missing}.</span>
          ) : null}
        </span>
      </label>

      {takesPhotos ? (
        <div style={{ marginLeft: 30 }}>
          {item.photos.length ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, margin: '6px 0' }}>
              {item.photos.map((p) => (
                <span key={p.id} style={{ position: 'relative' }}>
                  {p.url ? <a href={p.url} target="_blank" rel="noreferrer"><img className="upload-thumb" src={p.url} alt="" /></a> : <span className="upload-thumb upload-thumb-empty">Photo</span>}
                  {editable ? (
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => onRemovePhoto(p.id)}
                      aria-label="Remove photo"
                      style={{ position: 'absolute', top: -6, right: -6, width: 22, height: 22, borderRadius: 11, border: 0, cursor: 'pointer', fontSize: 12, lineHeight: '22px', padding: 0 }}
                    >×</button>
                  ) : null}
                </span>
              ))}
            </div>
          ) : null}
          {editable ? (
            <label className={`btn btn-secondary upload-pick${disabled ? ' is-disabled' : ''}`} style={{ display: 'inline-flex', flex: '0 0 auto', marginTop: 4 }}>
              <span className="btn-label">{busy ? 'Adding' : item.photos.length ? 'Add more photos' : 'Add photos'}</span>
              <input type="file" accept="image/*" multiple onChange={onAddPhotos} disabled={disabled} />
            </label>
          ) : null}
        </div>
      ) : null}

      {item.proof === 'findings' ? (
        <div className="stack" style={{ marginLeft: 30, marginTop: 8, gap: 6 }}>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            disabled={disabled || !!item.none_found}
            placeholder="What you found, and where"
            rows={2}
            style={{ width: '100%' }}
          />
          {editable ? (
            <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <Button variant="secondary" disabled={disabled || note.trim() === (item.note || '').trim()} onClick={() => onPatch({ note })}>Save note</Button>
              <label className="muted" style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13 }}>
                <input type="checkbox" checked={!!item.none_found} disabled={disabled} onChange={(e) => onPatch({ none_found: e.target.checked })} />
                Nothing to report
              </label>
            </div>
          ) : null}
        </div>
      ) : null}

      {item.proof === 'inventory' ? (
        <div className="stack" style={{ marginLeft: 30, marginTop: 8, gap: 6 }}>
          {rows.map((r, i) => (
            <div key={i} className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              <input value={r.item} disabled={disabled} placeholder="Item" aria-label="Item"
                onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, item: e.target.value } : x))} style={{ flex: '2 1 140px' }} />
              <input value={r.room} disabled={disabled} placeholder="Room" aria-label="Room"
                onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, room: e.target.value } : x))} style={{ flex: '1 1 100px' }} />
              <input value={r.note || ''} disabled={disabled} placeholder="Damage or note (optional)" aria-label="Note"
                onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, note: e.target.value } : x))} style={{ flex: '2 1 140px' }} />
              {editable && rows.length > 1 ? (
                <Button variant="tertiary" disabled={disabled} onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</Button>
              ) : null}
            </div>
          ))}
          {editable ? (
            <div className="row" style={{ gap: 8 }}>
              <Button variant="tertiary" disabled={disabled} onClick={() => setRows([...rows, { item: '', room: '' }])}>Add a line</Button>
              <Button variant="secondary" disabled={disabled || JSON.stringify(cleanRows) === savedRows} onClick={() => onPatch({ inventory: cleanRows })}>Save inventory</Button>
            </div>
          ) : null}
        </div>
      ) : null}

      {item.proof === 'report' ? (
        <div style={{ marginLeft: 30, marginTop: 6 }}>
          {item.report ? (
            <p style={{ padding: 0, margin: '0 0 6px', fontSize: 13 }}>
              {item.report.url ? <a href={item.report.url} target="_blank" rel="noreferrer">{item.report.filename || 'Report'}</a> : (item.report.filename || 'Report')}
            </p>
          ) : null}
          {editable ? (
            <label className={`btn btn-secondary upload-pick${disabled ? ' is-disabled' : ''}`} style={{ display: 'inline-flex', flex: '0 0 auto' }}>
              <span className="btn-label">{busy ? 'Uploading' : item.report ? 'Replace report' : 'Upload report'}</span>
              <input type="file" accept="application/pdf,image/*" onChange={onReport} disabled={disabled} />
            </label>
          ) : null}
          <p className="muted" style={{ padding: 0, margin: '4px 0 0', fontSize: 12 }}>PDF or photo, up to 4 MB.</p>
        </div>
      ) : null}
    </li>
  );
}
