import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  addCaptureRoom,
  ensureCaptureSession,
  openCaptureStation,
  uploadCaptureFrameBlob,
} from '../lib/capture-api';
import type { CaptureKind, CaptureSession } from '../lib/capture-api';

const DOME_TOTAL = 24;
const CORNER_TOTAL = 4;

function CoverageDome({ captured, total, headingTick }: { captured: Set<number>; total: number; headingTick?: number }) {
  const R = 92, cx = 110, cy = 110;
  const ticks = Array.from({ length: total }, (_, i) => {
    const ang = (i / total) * Math.PI * 2 - Math.PI / 2;
    const on = captured.has(i);
    const aim = headingTick === i;
    const x1 = cx + Math.cos(ang) * (R - 12), y1 = cy + Math.sin(ang) * (R - 12);
    const x2 = cx + Math.cos(ang) * R, y2 = cy + Math.sin(ang) * R;
    return (
      <line
        key={i}
        x1={x1} y1={y1} x2={x2} y2={y2}
        stroke={aim ? '#F4F1EB' : on ? '#D2542B' : '#F4F1EB30'}
        strokeWidth={aim || on ? 3 : 2}
        strokeLinecap="round"
      />
    );
  });
  return (
    <svg width={220} height={220} viewBox="0 0 220 220">
      <circle cx={cx} cy={cy} r={R - 22} fill="none" stroke="#F4F1EB1A" strokeWidth={1} />
      <circle cx={cx} cy={cy} r={R + 6} fill="none" stroke="#F4F1EB12" strokeWidth={1} />
      {ticks}
      <path d={`M${cx - 10} ${cy}H${cx + 10}M${cx} ${cy - 10}V${cy + 10}`} stroke="#F4F1EB55" strokeWidth={1} />
    </svg>
  );
}

async function blobFromVideo(video: HTMLVideoElement): Promise<Blob | null> {
  const canvas = document.createElement('canvas');
  const w = video.videoWidth || 1280;
  const h = video.videoHeight || 720;
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.drawImage(video, 0, 0, w, h);
  return new Promise((resolve) => {
    canvas.toBlob((b) => resolve(b), 'image/jpeg', 0.85);
  });
}

export function CapturePage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const jobId = params.get('jobId') || '';
  const kind: CaptureKind = params.get('kind') === 'floorplan' ? 'floorplan' : 'splat';
  const videoRef = useRef<HTMLVideoElement>(null);
  const shutterBusy = useRef(false);
  const headingRef = useRef(0);
  const streamRef = useRef<MediaStream | null>(null);

  const [session, setSession] = useState<CaptureSession | null>(null);
  const [frameCount, setFrameCount] = useState(0);
  const [stationIndex, setStationIndex] = useState(0);
  const [ticks, setTicks] = useState<Set<number>>(() => new Set());
  const [aimTick, setAimTick] = useState(0);
  const [heading0, setHeading0] = useState<number | null>(null);
  const [perm, setPerm] = useState<'pending' | 'granted' | 'denied'>('pending');
  const [camNote, setCamNote] = useState('');
  const [captureError, setCaptureError] = useState<string | null>(null);
  const [shutterPressed, setShutterPressed] = useState(false);
  const [newRoomName, setNewRoomName] = useState('');
  const [selectedRoomId, setSelectedRoomId] = useState<string | null>(null);
  const [headingNote, setHeadingNote] = useState('');

  const startCamera = useCallback(async () => {
    setCamNote('');
    try {
      if (!window.isSecureContext) {
        setPerm('denied');
        setCamNote('Camera needs HTTPS (or localhost). Open Bricks on your phone.');
        return;
      }
      if (!navigator.mediaDevices?.getUserMedia) {
        setPerm('denied');
        setCamNote('This browser has no camera API. Use the Bricks app to capture.');
        return;
      }
      // iPhone Safari often rejects tight constraints — fall back to simpler facingMode, then any camera.
      const attempts: MediaStreamConstraints[] = [
        { video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false },
        { video: { facingMode: { ideal: 'environment' } }, audio: false },
        { video: true, audio: false },
      ];
      let stream: MediaStream | null = null;
      let lastErr: any = null;
      for (const constraints of attempts) {
        try {
          stream = await navigator.mediaDevices.getUserMedia(constraints);
          break;
        } catch (e) {
          lastErr = e;
        }
      }
      if (!stream) {
        setPerm('denied');
        const name = String(lastErr?.name || '');
        const msg = String(lastErr?.message || lastErr || 'Camera permission denied');
        if (name === 'NotAllowedError' || /permission|denied/i.test(msg)) {
          setCamNote('Camera blocked. Tap Allow camera, or enable camera for this site in Safari Settings.');
        } else if (name === 'NotFoundError') {
          setCamNote('No camera found on this device.');
        } else {
          setCamNote(msg);
        }
        return;
      }
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play().catch(() => {});
      }
      setPerm('granted');
    } catch (e: any) {
      setPerm('denied');
      setCamNote(String(e?.message || e) || 'Camera permission denied');
    }
  }, []);

  useEffect(() => {
    startCamera();
    return () => {
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };
  }, [startCamera]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!jobId) return;
      const result = await ensureCaptureSession(jobId, kind);
      if (cancelled) return;
      if (!result.ok || !result.session) {
        setCaptureError(result.error ?? 'Could not start capture session');
        return;
      }
      setCaptureError(null);
      const s = result.session;
      setSession(s);
      setFrameCount(s.frameCount ?? 0);
      setStationIndex(s.currentStationIndex ?? 0);
      setSelectedRoomId(s.currentRoomId ?? s.rooms?.[0]?.id ?? null);
    })();
    return () => { cancelled = true; };
  }, [jobId, kind]);

  useEffect(() => {
    const total = kind === 'floorplan' ? CORNER_TOTAL : DOME_TOTAL;
    const onOrient = (ev: DeviceOrientationEvent) => {
      // alpha is compass-like when available; fallback 0 keeps pan workable
      const yaw = typeof ev.alpha === 'number' ? (360 - ev.alpha) % 360 : headingRef.current;
      headingRef.current = yaw;
      const origin = heading0 ?? yaw;
      const rel = ((yaw - origin) + 360) % 360;
      setAimTick(Math.round(rel / (360 / total)) % total);
    };
    window.addEventListener('deviceorientation', onOrient);
    return () => window.removeEventListener('deviceorientation', onOrient);
  }, [heading0, kind]);

  const requestMotion = async () => {
    setHeadingNote('');
    const DOE = DeviceOrientationEvent as unknown as { requestPermission?: () => Promise<string> };
    if (typeof DOE.requestPermission === 'function') {
      try {
        const res = await DOE.requestPermission();
        if (res !== 'granted') {
          setHeadingNote('Heading locked — pan still works; fill the dome by eye.');
        }
      } catch {
        setHeadingNote('Heading unavailable — pan still works; fill the dome by eye.');
      }
      return;
    }
    setHeadingNote('Compass not required here — pan slowly and fill the dome.');
  };

  if (!jobId) {
    return (
      <div className="capture-shell">
        <p className="error">Missing jobId. Open Start Capture from a job.</p>
        <Link to="/jobs">Back to jobs</Link>
      </div>
    );
  }

  const address = session?.address || 'Property';
  const roomLabel = (session?.guidance?.room || session?.rooms?.find((r) => r.id === selectedRoomId)?.name || 'Room 1').toUpperCase();
  const pct = Math.round(Math.min(ticks.size, DOME_TOTAL) / DOME_TOTAL * 100);
  const roomFrames = session?.rooms?.find((r) => r.id === selectedRoomId)?.n ?? 0;

  const onShutter = async () => {
    if (shutterBusy.current || !jobId || !videoRef.current) return;
    shutterBusy.current = true;
    setShutterPressed(true);
    setCaptureError(null);
    try {
      const yaw = headingRef.current;
      if (heading0 == null) setHeading0(yaw);
      const origin = heading0 ?? yaw;
      const rel = ((yaw - origin) + 360) % 360;
      const tick = Math.round(rel / (360 / DOME_TOTAL)) % DOME_TOTAL;
      const blob = await blobFromVideo(videoRef.current);
      if (!blob) {
        setCaptureError('Camera did not return a photo');
        return;
      }
      const result = await uploadCaptureFrameBlob(jobId, blob, selectedRoomId ?? session?.currentRoomId, kind, {
        stationIndex,
        yaw,
        heading: yaw,
        role: 'pan',
        contentType: 'image/jpeg',
      });
      if (!result.ok) {
        setCaptureError(result.error ?? 'Frame upload failed');
        return;
      }
      if (result.session) {
        setSession(result.session);
        if (result.session.currentStationIndex != null) setStationIndex(result.session.currentStationIndex);
      }
      setFrameCount(result.frameCount ?? ((c) => c + 1));
      setTicks((prev) => new Set(prev).add(kind === 'floorplan' ? (prev.size % CORNER_TOTAL) : tick));
    } catch (e: any) {
      setCaptureError(String(e?.message ?? e));
    } finally {
      shutterBusy.current = false;
      setShutterPressed(false);
    }
  };

  const onNewStation = async () => {
    const result = await openCaptureStation(jobId, kind, {
      roomId: selectedRoomId ?? session?.currentRoomId,
      heading: headingRef.current,
      increment: true,
    });
    if (!result.ok) {
      setCaptureError(result.error ?? 'Could not open next station');
      return;
    }
    if (result.session) setSession(result.session);
    setStationIndex(result.session?.currentStationIndex ?? stationIndex);
    setTicks(new Set());
    setHeading0(null);
  };

  const onAddRoom = async () => {
    const name = newRoomName.trim();
    if (!name) return;
    const result = await addCaptureRoom(jobId, name, kind);
    if (!result.ok) {
      setCaptureError(result.error ?? 'Could not add room');
      return;
    }
    if (result.session) {
      setSession(result.session);
      const last = result.session.rooms[result.session.rooms.length - 1];
      setSelectedRoomId(last?.id ?? null);
    }
    setNewRoomName('');
  };

  return (
    <div className="capture-shell">
      <video ref={videoRef} className="capture-video" playsInline muted autoPlay />
      <div className="capture-scrim" />

      <div className="capture-ui">
        <div className="capture-top">
          <button type="button" className="capture-chip" onClick={() => navigate(`/jobs?id=${encodeURIComponent(jobId)}`)}>✕</button>
          <div style={{ textAlign: 'center' }}>
            <div className="capture-addr">{address}</div>
            <div className="capture-room">{roomLabel} · {kind === 'floorplan' ? 'FLOOR PLAN' : '3D TOUR'}</div>
          </div>
          <button type="button" className="capture-chip" onClick={requestMotion} title="Enable compass">◎</button>
        </div>

        {perm !== 'granted' ? (
          <div className="capture-perm">
            <p>{camNote || 'Bricks needs camera access to photograph listings.'}</p>
            <button type="button" onClick={startCamera}>Allow camera</button>
            <p className="muted" style={{ marginTop: 12 }}>
              Works best on a phone. The Bricks app tracks heading best.
            </p>
          </div>
        ) : (
          <>
            <div className="capture-pill">
              Station {stationIndex} · {ticks.size}/{kind === 'floorplan' ? CORNER_TOTAL : DOME_TOTAL} · {frameCount} frames
            </div>

            <div className="capture-dome-wrap">
              <CoverageDome captured={ticks} total={kind === 'floorplan' ? CORNER_TOTAL : DOME_TOTAL} headingTick={aimTick} />
              <div className="capture-dome-center">
                <div className="capture-dome-pct">{kind === 'floorplan' ? `${Math.min(roomFrames, CORNER_TOTAL)}/${CORNER_TOTAL}` : `${pct}%`}</div>
                <div className="capture-dome-label">{kind === 'floorplan' ? `Room corners · station ${stationIndex}` : `Station ${stationIndex} sweep`}</div>
              </div>
            </div>

            <div className="capture-guidance">
              <strong>
                {kind === 'floorplan'
                  ? (session?.guidance ? `Floor plan · ${session.guidance.room}` : 'Four corners · same spot on the floor')
                  : (session?.guidance ? `360 station · ${session.guidance.room}` : 'Stand still · pan slowly through 360')}
              </strong>
              <div>
                {kind === 'floorplan'
                  ? (session?.guidance?.message ?? 'Shoot each corner of this room (4). Add a room, walk, repeat. Server compiles the plan — not a 360 dome.')
                  : (session?.guidance?.message ?? 'Fill the coverage dome for this station, then walk to the next. Server stitches the pan into a 360.')}
              </div>
            </div>

            {kind === 'floorplan' && (
              <div className="capture-rooms">
                <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}>
                  {(session?.rooms || []).map((r) => (
                    <button
                      key={r.id || r.name}
                      type="button"
                      className={selectedRoomId === r.id ? 'btn-primary' : ''}
                      onClick={() => setSelectedRoomId(r.id || null)}
                    >
                      {r.name} ({r.n})
                    </button>
                  ))}
                </div>
                <div className="row" style={{ marginTop: 8 }}>
                  <input value={newRoomName} onChange={(e) => setNewRoomName(e.target.value)} placeholder="New room name" />
                  <button type="button" onClick={onAddRoom}>Add room</button>
                </div>
              </div>
            )}

            {headingNote ? <div className="capture-pill" style={{ marginBottom: 8 }}>{headingNote}</div> : null}
            {captureError ? <div className="capture-error">{captureError}</div> : null}

            <div className="capture-shutter-row">
              <button type="button" className="capture-chip" onClick={onNewStation}>+St</button>
              <button
                type="button"
                className={`capture-shutter ${shutterPressed ? 'pressed' : ''}`}
                onClick={onShutter}
                aria-label="Shutter"
              >
                <span />
              </button>
              <Link className="capture-chip" to={`/capture/review?jobId=${encodeURIComponent(jobId)}&kind=${kind}`}>✓</Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
