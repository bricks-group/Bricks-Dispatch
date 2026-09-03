import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { getCaptureSession, submitCaptureSession } from '../lib/capture-api';
import type { CaptureKind, CaptureSession } from '../lib/capture-api';

export function CaptureReviewPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const jobId = params.get('jobId') || '';
  const kind: CaptureKind = params.get('kind') === 'floorplan' ? 'floorplan' : 'splat';

  const [session, setSession] = useState<CaptureSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!jobId) { setLoading(false); return; }
      const s = await getCaptureSession(jobId, kind);
      if (!cancelled) {
        setSession(s);
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
    if (!result.ok) {
      setSubmitError(result.error ?? 'Submit failed');
      return;
    }
    if (result.session) setSession(result.session);
    navigate(`/jobs?id=${encodeURIComponent(jobId)}`);
  };

  const rooms = session?.rooms ?? [];
  const pipeline = session?.pipeline ?? [];
  const stations = session?.stations ?? [];
  const stationCount = session?.stationCount ?? stations.length;
  const totalFrames = session?.frameCount ?? 0;
  const address = session?.address || 'Property';
  const meta = session?.meta || '';
  const coveredCount = rooms.filter((r) => r.n > 0).length;
  const thinStations = stations.filter((s) => !s.hasEquirect && (s.panCount ?? 0) < 8);
  const canSubmit = pipeline.length > 0 && stationCount > 0 && thinStations.length === 0;

  if (!jobId) {
    return (
      <div className="page-content">
        <p className="error">Missing jobId</p>
        <Link to="/jobs">Back to jobs</Link>
      </div>
    );
  }

  return (
    <div className="page-content capture-review">
      <div className="page-header">
        <div>
          <h1>{address}</h1>
          <p>
            {[meta, kind === 'floorplan' ? 'FLOOR PLAN' : '3D TOUR', `${stationCount} stations`, `${totalFrames} pans`].filter(Boolean).join(' · ')}
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
                ? 'Submit needs at least one 360 station (coverage dome), not snapshot stills.'
                : `Station ${thinStations[0].station_index} sweep is thin — fill the dome (8+ overlapping pans) or capture an equirect.`}
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

          <div className="row" style={{ marginTop: 16 }}>
            <button type="button" disabled={!canSubmit || submitting} onClick={handleSubmit}>
              {submitting ? 'Submitting…' : 'Submit capture'}
            </button>
            <Link to={`/jobs?id=${encodeURIComponent(jobId)}`}>Back to job</Link>
          </div>
        </div>
      </section>
    </div>
  );
}
