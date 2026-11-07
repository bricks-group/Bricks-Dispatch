import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { getCaptureSession, rebuildFloorPlan, rebuildTourPlayer, submitCaptureSession } from '../lib/capture-api';
import type { CaptureKind, CaptureSession } from '../lib/capture-api';
import { captureStatusCopy } from '../lib/capture-modes';
import { TourTagEditor } from '../components/TourTagEditor';
import BricksLoader from '../components/BricksLoader';

export function CaptureReviewPage() {
  const [params] = useSearchParams();
  const jobId = params.get('jobId') || '';
  const kind: CaptureKind = params.get('kind') === 'floorplan' ? 'floorplan' : 'splat';

  const [session, setSession] = useState<CaptureSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildMsg, setRebuildMsg] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!jobId) { setLoading(false); return; }
      const s = await getCaptureSession(jobId, kind);
      if (!cancelled) {
        setSession(s);
        const st = (s.status || '').toLowerCase();
        if (['submitted', 'splatting', 'tracing', 'delivered', 'failed'].includes(st)) {
          setSubmitted(true);
        }
        setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [jobId, kind]);

  const handleSubmit = async () => {
    if (!jobId) return;
    setSubmitting(true);
    setSubmitError(null);
    const result = await submitCaptureSession(jobId, kind);
    setSubmitting(false);
    if (result.session) setSession(result.session);
    if (!result.ok) {
      setSubmitError(result.error ?? 'Submit failed');
      setSubmitted(true);
      return;
    }
    setSubmitted(true);
  };

  const handleRebuild = async () => {
    if (!jobId || (kind !== 'splat' && kind !== 'floorplan')) return;
    setRebuilding(true);
    setRebuildMsg(null);
    setSubmitError(null);
    const result = kind === 'floorplan'
      ? await rebuildFloorPlan(jobId, session?.sessionId)
      : await rebuildTourPlayer(jobId, kind, session?.sessionId);
    setRebuilding(false);
    if (result.session) setSession(result.session);
    if (!result.ok) {
      setRebuildMsg(result.error ?? 'Rebuild failed');
      return;
    }
    setRebuildMsg(
      kind === 'floorplan'
        ? 'Floor plan PNG rebuilt from stored stations. Refresh the listing to see north arrow, scale, and labels.'
        : 'Tour player rebuilt from existing 360 stations. Listing embed URL is unchanged — hard-refresh the listing iframe to see in-scene hotspots.',
    );
  };

  const rooms = session?.rooms ?? [];
  const pipeline = session?.pipeline ?? [];
  const stations = session?.stations ?? [];
  const stationCount = session?.stationCount ?? stations.length;
  const totalFrames = session?.frameCount ?? 0;
  const address = session?.address || 'Property';
  const meta = session?.meta || '';
  const coveredCount = rooms.filter((r) => r.n > 0).length;
  const minPans = kind === 'floorplan' ? 4 : 8;
  const thinStations = stations.filter((s) => !s.hasEquirect && (s.panCount ?? 0) < minPans);
  const canSubmit = pipeline.length > 0 && stationCount > 0 && thinStations.length === 0;
  const status = (session?.status || '').toLowerCase();
  const statusLine = captureStatusCopy(status, kind);
  const kindLabel = kind === 'floorplan' ? 'FLOOR PLAN' : '3D TOUR';
  const jobHref = `/jobs?id=${encodeURIComponent(jobId)}`;

  if (!jobId) {
    return (
      <div className="page-content">
        <p className="error">Missing jobId</p>
        <Link to="/jobs">Back to jobs</Link>
      </div>
    );
  }

  if (submitted) {
    const failed = status === 'failed' || !!submitError;
    const compiling = ['submitted', 'splatting', 'tracing'].includes(status);
    const ready = status === 'delivered';
    return (
      <div className="page-content capture-review">
        <div className="page-header">
          <div>
            <h1>{address}</h1>
            <p>{[meta, kindLabel].filter(Boolean).join(' · ')}</p>
          </div>
        </div>
        <section className={`card full capture-submit-result ${failed ? 'is-failed' : ready ? 'is-ready' : 'is-compiling'}`}>
          <div className="stack" style={{ padding: 20 }}>
            <h3 style={{ margin: 0, padding: 0 }}>
              {failed ? 'Compile failed' : ready ? 'Submitted · ready' : 'Submitted · compiling'}
            </h3>
            <p style={{ margin: 0 }}>
              {failed
                ? (submitError || statusLine || 'Compile failed — try again from review or recapture stations.')
                : (statusLine || (ready
                  ? `${kindLabel === 'FLOOR PLAN' ? 'Floor plan' : '3D tour'} is ready on the job.${kind === 'splat' ? ' Rebuild tour player refreshes hosted HTML from existing 360s — no recapture.' : ' Rebuild floor plan regenerates the PNG from stored stations — no recapture.'}`
                  : `${kindLabel === 'FLOOR PLAN' ? 'Floor plan' : '3D tour'} is compiling. This usually takes a few minutes.`))}
            </p>
            {rebuildMsg ? <p className={(rebuildMsg.startsWith('Tour player rebuilt') || rebuildMsg.startsWith('Floor plan PNG rebuilt')) ? undefined : 'error'} style={{ margin: 0, padding: 0 }}>{rebuildMsg}</p> : null}
            <div className="row" style={{ marginTop: 8, flexWrap: 'wrap', gap: 8 }}>
              <Link className="btn btn-secondary" to={jobHref}>Back to job</Link>
              {failed ? (
                <button
                  type="button"
                  disabled={submitting || !canSubmit}
                  onClick={handleSubmit}
                >
                  {submitting && <BricksLoader variant="cycle" size={16} color="currentColor" style={{ marginRight: 8 }} />}{submitting ? 'Retrying…' : 'Retry compile'}
                </button>
              ) : null}
              {ready && (kind === 'splat' || kind === 'floorplan') ? (
                <button
                  type="button"
                  disabled={rebuilding}
                  onClick={handleRebuild}
                >
                  {rebuilding && <BricksLoader variant="cycle" size={16} color="currentColor" style={{ marginRight: 8 }} />}{rebuilding ? 'Rebuilding…' : (kind === 'floorplan' ? 'Rebuild floor plan' : 'Rebuild tour player')}
                </button>
              ) : null}
              <Link to={`/capture?jobId=${encodeURIComponent(jobId)}&kind=${kind}`}>
                {failed ? 'Recapture' : 'Continue capture'}
              </Link>
            </div>
          </div>
        </section>
        {kind === 'splat' && session ? (
          <TourTagEditor jobId={jobId} session={session} onSession={setSession} />
        ) : null}
      </div>
    );
  }

  return (
    <div className="page-content capture-review">
      <div className="page-header">
        <div>
          <h1>{address}</h1>
          <p>
            {[meta, kindLabel, `${stationCount} stations`, `${totalFrames} pans`].filter(Boolean).join(' · ')}
          </p>
        </div>
        <Link to={`/capture?jobId=${encodeURIComponent(jobId)}&kind=${kind}`}>Continue capture</Link>
      </div>

      {loading && <p className="muted">Loading capture…</p>}

      <section className="card full">
        <h3>Coverage by room ({coveredCount}/{rooms.length})</h3>
        <div className="stack" style={{ padding: 20 }}>
          {rooms.length === 0 && !loading ? (
            <p className="muted">No rooms recorded yet.</p>
          ) : (
            <ul>
              {rooms.map((r) => (
                <li key={r.id ?? r.name}>{r.name} — {r.n} frames ({r.tone})</li>
              ))}
            </ul>
          )}

          {!loading && (stationCount === 0 || thinStations.length > 0) && (
            <p className="error" style={{ padding: 0 }}>
              {stationCount === 0
                ? (kind === 'floorplan'
                  ? 'Submit needs at least one station with room corners, not snapshot stills.'
                  : 'Submit needs at least one 360 station (coverage dome), not snapshot stills.')
                : (kind === 'floorplan'
                  ? `Station ${thinStations[0].station_index} needs more corners — capture 4 corners of the room.`
                  : `Station ${thinStations[0].station_index} sweep is thin — fill the dome (8+ overlapping pans) or capture an equirect.`)}
            </p>
          )}

          <h3 style={{ marginTop: 16 }}>Pipeline</h3>
          {pipeline.length === 0 && !loading ? (
            <p className="muted">No pipeline yet — submit is unavailable until a session exists.</p>
          ) : (
            <ol>
              {pipeline.map((p) => (
                <li key={p.title}>
                  <strong>{p.title}</strong> [{p.status}] — {p.detail}
                </li>
              ))}
            </ol>
          )}

          {submitError && <p className="error" style={{ padding: 0 }}>{submitError}</p>}

          <div className="row" style={{ marginTop: 16, flexWrap: 'wrap', gap: 8 }}>
            <button type="button" disabled={!canSubmit || submitting} onClick={handleSubmit}>
              {submitting && <BricksLoader variant="cycle" size={16} color="currentColor" style={{ marginRight: 8 }} />}{submitting ? 'Submitting…' : 'Submit capture'}
            </button>
            <Link to={jobHref}>Back to job</Link>
          </div>
        </div>
      </section>
    </div>
  );
}
