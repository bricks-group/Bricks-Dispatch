import { useEffect, useRef, useState } from 'react';
import {
  addTourTag,
  deleteTourTag,
  getSceneLookUrl,
} from '../lib/capture-api';
import type { CaptureSession, CaptureStation, TourTag } from '../lib/capture-api';

type Props = {
  jobId: string;
  session: CaptureSession;
  onSession: (s: CaptureSession) => void;
};

function tagsFrom(session: CaptureSession): TourTag[] {
  if (Array.isArray(session.tags) && session.tags.length) return session.tags;
  const out: TourTag[] = [];
  for (const st of session.stations || []) {
    for (const t of st.tags || []) out.push({ ...t, station_index: t.station_index ?? st.station_index });
  }
  return out;
}

export function TourTagEditor({ jobId, session, onSession }: Props) {
  const stations: CaptureStation[] = session.stations || [];
  const sessionId = session.sessionId || '';
  const [stationIndex, setStationIndex] = useState<number>(
    stations[0]?.station_index ?? 0,
  );
  const [label, setLabel] = useState('');
  const [note, setNote] = useState('');
  const [yaw, setYaw] = useState(0);
  const [pitch, setPitch] = useState(0);
  const [sceneUrl, setSceneUrl] = useState<string | null>(null);
  const [sceneErr, setSceneErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const drag = useRef<{ x: number; y: number; off: number; yaw: number; pitch: number } | null>(null);
  const strip = useRef<HTMLDivElement | null>(null);
  const img = useRef<HTMLImageElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!sessionId) return;
      setSceneUrl(null);
      setSceneErr(null);
      const res = await getSceneLookUrl(sessionId, stationIndex);
      if (cancelled) return;
      if (!res.ok || !res.url) {
        setSceneErr(res.error || 'No stored 360 for this station — type yaw/pitch instead.');
        return;
      }
      setSceneUrl(res.url);
    })();
    return () => { cancelled = true; };
  }, [sessionId, stationIndex]);

  const applyLookFromDrag = (dx: number, dy: number) => {
    const el = strip.current;
    const im = img.current;
    const w = el?.clientWidth || 1;
    const h = el?.clientHeight || 1;
    const fw = im && im.naturalHeight ? im.naturalWidth * (h / im.naturalHeight) : w * 2;
    const yawPerPx = fw ? 360 / fw : 0.4;
    const pitchPerPx = 80 / Math.max(h, 1);
    setYaw((y) => {
      let n = y - dx * yawPerPx;
      while (n > 180) n -= 360;
      while (n < -180) n += 360;
      return Math.round(n * 10) / 10;
    });
    setPitch((p) => Math.max(-80, Math.min(80, Math.round((p + dy * pitchPerPx) * 10) / 10)));
  };

  const handleAdd = async () => {
    const lab = label.trim();
    if (!lab) {
      setMsg('Tag needs a label — e.g. Walk-in closet. Empty amenities are not invented.');
      return;
    }
    if (!sessionId) {
      setMsg('No capture session');
      return;
    }
    setBusy(true);
    setMsg(null);
    const result = await addTourTag(jobId, sessionId, {
      station_index: stationIndex,
      label: lab,
      note: note.trim() || undefined,
      yaw,
      pitch,
    });
    setBusy(false);
    if (result.session) onSession(result.session);
    if (!result.ok) {
      setMsg(result.error ?? 'Could not add tag');
      return;
    }
    setLabel('');
    setNote('');
    setMsg(result.rebuilt === false
      ? 'Tag saved. Rebuild the tour player so the listing iframe picks it up.'
      : 'Tag on the tour. Hard-refresh the listing iframe to see it.');
  };

  const handleDelete = async (tagId: string) => {
    if (!sessionId) return;
    setBusy(true);
    setMsg(null);
    const result = await deleteTourTag(jobId, sessionId, tagId);
    setBusy(false);
    if (result.session) onSession(result.session);
    if (!result.ok) setMsg(result.error ?? 'Could not remove tag');
  };

  const tags = tagsFrom(session);
  const stationLabel = (idx: number) => {
    const st = stations.find((s) => s.station_index === idx);
    return st?.room ? `${st.room} · ${idx}` : `Station ${idx}`;
  };

  if (!sessionId) return null;

  return (
    <section className="card full" style={{ marginTop: 16 }}>
      <h3>Feature tags</h3>
      <div className="stack" style={{ padding: 20, gap: 12 }}>
        <p className="muted" style={{ padding: 0, margin: 0 }}>
          Drag the 360 to the amenity, then add a short label. Tags sit inside the tour — listing embeds pick them up after save (no recapture).
        </p>
        <label>
          Station
          <select
            value={stationIndex}
            onChange={(e) => setStationIndex(Number(e.target.value))}
          >
            {(stations.length ? stations : [{ station_index: 0, room: 'Station 0' }]).map((s) => (
              <option key={s.station_index} value={s.station_index}>
                {stationLabel(s.station_index)}
              </option>
            ))}
          </select>
        </label>
        <div
          ref={strip}
          className="tag-look"
          style={{
            position: 'relative',
            height: 180,
            overflow: 'hidden',
            background: '#0c0a09',
            borderRadius: 2,
            cursor: 'grab',
            touchAction: 'none',
          }}
          onPointerDown={(e) => {
            (e.currentTarget as HTMLDivElement).setPointerCapture(e.pointerId);
            drag.current = { x: e.clientX, y: e.clientY, off: 0, yaw, pitch };
          }}
          onPointerUp={() => { drag.current = null; }}
          onPointerCancel={() => { drag.current = null; }}
          onPointerMove={(e) => {
            if (!drag.current) return;
            const dx = e.clientX - drag.current.x;
            const dy = e.clientY - drag.current.y;
            drag.current.x = e.clientX;
            drag.current.y = e.clientY;
            applyLookFromDrag(dx, dy);
          }}
        >
          {sceneUrl ? (
            <img
              ref={img}
              src={sceneUrl}
              alt="360 look"
              draggable={false}
              style={{
                height: '100%',
                width: 'auto',
                maxWidth: 'none',
                display: 'block',
                transform: `translateX(${-((yaw + 180) / 360) * ((img.current?.naturalWidth || 2) * (180 / Math.max(img.current?.naturalHeight || 1, 1)) - (strip.current?.clientWidth || 0))}px)`,
                userSelect: 'none',
              }}
            />
          ) : (
            <div style={{ padding: 16, color: '#a8a29e', fontSize: 13 }}>
              {sceneErr || 'Loading 360…'}
            </div>
          )}
          <div style={{
            position: 'absolute', left: '50%', top: '50%', width: 12, height: 12,
            marginLeft: -6, marginTop: -6, borderRadius: '50%',
            background: '#8B3A2A', border: '2px solid #fafaf9', pointerEvents: 'none',
          }} />
        </div>
        <p className="muted" style={{ padding: 0, margin: 0, fontFamily: 'ui-monospace, monospace', fontSize: 12 }}>
          Look · yaw {yaw.toFixed(1)}° · pitch {pitch.toFixed(1)}°
        </p>
        <label>
          Label
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Walk-in closet"
            maxLength={80}
          />
        </label>
        <label>
          Note (optional)
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Cedar lining"
            maxLength={240}
          />
        </label>
        <div className="row" style={{ gap: 8 }}>
          <button type="button" disabled={busy || !label.trim()} onClick={handleAdd}>
            {busy ? 'Saving…' : 'Add tag'}
          </button>
        </div>
        {msg ? <p className={msg.startsWith('Tag on') || msg.startsWith('Tag saved') ? undefined : 'error'} style={{ margin: 0, padding: 0 }}>{msg}</p> : null}
        <div>
          <p className="muted" style={{ padding: 0, margin: '8px 0' }}>
            {tags.length ? `${tags.length} tag${tags.length === 1 ? '' : 's'} on this tour` : 'No feature tags yet — none are invented.'}
          </p>
          {tags.length > 0 ? (
            <ul>
              {tags.map((t) => (
                <li key={t.id} style={{ display: 'flex', gap: 8, alignItems: 'baseline', justifyContent: 'space-between' }}>
                  <span>
                    {t.label}
                    {t.note ? ` — ${t.note}` : ''}
                    <span className="muted"> · {stationLabel(t.station_index ?? 0)} · {Number(t.yaw).toFixed(0)}°</span>
                  </span>
                  <button type="button" disabled={busy} onClick={() => handleDelete(t.id)}>Remove</button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </section>
  );
}
