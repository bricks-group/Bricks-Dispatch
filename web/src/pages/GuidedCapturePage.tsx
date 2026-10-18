/**
 * Guided capture (PWA). First guide: Virtual visit walk-through on an approved phone.
 *
 * Flow: intro (device check + steps) → camera (meter + lock) → countdown → record → review →
 * upload into the Virtual Visit intake → send for processing.
 * Locks are feature-detected on the live track and shown honestly: "Locked" only when the browser
 * confirmed it. On iOS Safari (no exposure/focus lock) the vendor can use the Camera app route.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Button } from '../components/Button';
import {
  IconAlert, IconBack, IconCamera, IconCheck, IconLens, IconList, IconLock, IconPhone, IconUnlock, IconUpload, IconX, StepGlyph,
} from '../components/GuidedIcons';
import { getGuide, LOCK_LABEL, estimateBytes, type CaptureGuide, type LockKind } from '../lib/guided-capture/guides';
import { checkDevice, POLICY_LINE, type PolicyResult } from '../lib/guided-capture/device-policy';
import { coachVerdict, type CoachReading, type CoachSummary } from '../lib/guided-capture/motion';
import {
  applyLocks, isAndroidWeb, isIOSWeb, openCamera, rememberLens, releaseLocks, stopStream, webDeviceModel,
  WebRecorder, WEB_MAX_BYTES, WEB_WARN_BYTES, type CameraOpen, type LockState,
} from '../lib/guided-capture/web-camera';
import { requestMotionAccess, useWebMotion, type MotionAccess } from '../lib/guided-capture/useWebMotion';
import { uploadVisitVideo, visitVideoApi, type CaptureMeta, type UploadJob } from '../lib/guided-capture/visit-video-upload';

type Phase = 'intro' | 'camera-app' | 'camera' | 'countdown' | 'recording' | 'review' | 'upload' | 'done';
type Locks = Record<LockKind, LockState>;
const NO_LOCKS: Locks = { exposure: 'auto', focus: 'auto', whiteBalance: 'auto' };
const FLOORS = ['Main floor', 'Upper floor', 'Lower level', 'Basement'];

const clock = (sec: number) => {
  const s = Math.max(0, Math.floor(sec));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
};
const bytes = (b: number) => (b >= 1e9 ? `${(b / 1e9).toFixed(2)} GB` : b >= 1e6 ? `${Math.round(b / 1e6)} MB` : `${Math.max(0, Math.round(b / 1e3))} KB`);

/* ───────────── dev-only demo (screenshots without a camera or a session) ───────────── */

type Demo = { os: 'ios' | 'android'; motion: string } | null;
function demoStream(): MediaStream {
  const c = document.createElement('canvas');
  c.width = 720; c.height = 1280;
  const g = c.getContext('2d')!;
  const draw = () => {
    const t = performance.now() / 1000;
    const sway = Math.sin(t * 0.6) * 18;
    g.fillStyle = '#d9d2c4'; g.fillRect(0, 0, 720, 1280);
    g.fillStyle = '#c9c0ae'; g.beginPath(); g.moveTo(0, 0); g.lineTo(200 + sway, 380); g.lineTo(200 + sway, 900); g.lineTo(0, 1280); g.fill();
    g.fillStyle = '#bfb5a2'; g.beginPath(); g.moveTo(720, 0); g.lineTo(520 + sway, 380); g.lineTo(520 + sway, 900); g.lineTo(720, 1280); g.fill();
    g.fillStyle = '#8a6f52'; g.beginPath(); g.moveTo(0, 1280); g.lineTo(200 + sway, 900); g.lineTo(520 + sway, 900); g.lineTo(720, 1280); g.fill();
    const grd = g.createLinearGradient(260, 420, 460, 700); grd.addColorStop(0, '#fffdf6'); grd.addColorStop(1, '#e8eef2');
    g.fillStyle = grd; g.fillRect(270 + sway, 440, 180, 260);
    g.strokeStyle = '#6d6455'; g.lineWidth = 6; g.strokeRect(270 + sway, 440, 180, 260);
    g.fillStyle = '#3b3631'; g.fillRect(90 + sway * 1.3, 820, 170, 90);
    requestAnimationFrame(draw);
  };
  draw();
  return (c as HTMLCanvasElement & { captureStream: (fps?: number) => MediaStream }).captureStream(30);
}

/* ───────────── small pieces ───────────── */

function LockChip({ kind, state }: { kind: LockKind; state: LockState }) {
  const locked = state === 'locked';
  const text = locked ? 'Locked' : state === 'unsupported' ? 'Auto' : state === 'failed' ? 'Didn’t lock' : 'Auto';
  return (
    <span className={`gc-chip ${locked ? 'is-on' : ''}`} aria-label={`${LOCK_LABEL[kind]}: ${text}`}>
      {locked ? <IconLock size={14} /> : <IconUnlock size={14} />}
      <span>{LOCK_LABEL[kind]}</span>
      <span className="gc-chip-state">{text}</span>
    </span>
  );
}

function LevelMeter({ roll, warn }: { roll: number; warn: boolean }) {
  const r = Math.max(-30, Math.min(30, roll));
  return (
    <div className={`gc-level ${warn ? 'is-warn' : Math.abs(roll) < 3 ? 'is-ok' : ''}`} role="img" aria-label={`Level ${Math.round(roll)} degrees`}>
      <span className="gc-level-tick gc-level-tick-l" />
      <span className="gc-level-line" style={{ transform: `rotate(${-r}deg)` }} />
      <span className="gc-level-tick gc-level-tick-r" />
    </div>
  );
}

function SpeedMeter({ mps, limit, pan, panLimit }: { mps: number; limit: number; pan: number; panLimit: number }) {
  const max = limit * 1.6;
  const pct = Math.min(100, (mps / max) * 100);
  const limitPct = (limit / max) * 100;
  const fast = mps > limit;
  const turnFast = pan > panLimit;
  return (
    <div className="gc-meters">
      <div className="gc-meter">
        <div className="gc-meter-head"><span>Walk</span><span className={`gc-mono ${fast ? 'is-warn' : ''}`}>{mps ? `${mps.toFixed(1)} m/s` : 'Still'}</span></div>
        <div className="gc-bar"><span className={`gc-bar-fill ${fast ? 'is-warn' : ''}`} style={{ width: `${pct}%` }} /><span className="gc-bar-limit" style={{ left: `${limitPct}%` }} /></div>
      </div>
      <div className="gc-meter">
        <div className="gc-meter-head"><span>Turn</span><span className={`gc-mono ${turnFast ? 'is-warn' : ''}`}>{pan}°/s</span></div>
        <div className="gc-bar"><span className={`gc-bar-fill ${turnFast ? 'is-warn' : ''}`} style={{ width: `${Math.min(100, (pan / (panLimit * 1.6)) * 100)}%` }} /><span className="gc-bar-limit" style={{ left: `${100 / 1.6}%` }} /></div>
      </div>
    </div>
  );
}

function StepsList({ guide }: { guide: CaptureGuide }) {
  return (
    <ol className="gc-steps">
      {guide.steps.map((s, i) => (
        <li key={s.id} className="gc-step">
          <span className="gc-step-icon"><StepGlyph icon={s.icon} /></span>
          <span className="gc-step-text">
            <span className="gc-step-title"><span className="gc-mono gc-step-n">{i + 1}</span>{s.title}</span>
            <span className="gc-step-body">{s.body}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

/* ───────────── page ───────────── */

export function GuidedCapturePage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const jobId = params.get('jobId') || '';
  const guide = getGuide(params.get('type') || 'virtual-visit');
  const demo: Demo = useMemo(() => {
    if (!import.meta.env.DEV) return null;
    const d = params.get('demo');
    return d ? { os: d.startsWith('ios') ? 'ios' : 'android', motion: params.get('motion') || 'calm' } : null;
  }, [params]);

  const ios = demo ? demo.os === 'ios' : isIOSWeb();
  const android = demo ? demo.os === 'android' : isAndroidWeb();

  const [phase, setPhase] = useState<Phase>((params.get('phase') as Phase) && demo ? (params.get('phase') as Phase) : 'intro');
  const [policy, setPolicy] = useState<PolicyResult | null>(null);
  const [override, setOverride] = useState(false);
  const [floor, setFloor] = useState('Main floor');
  const [cam, setCam] = useState<CameraOpen | null>(null);
  const [locks, setLocks] = useState<Locks>(NO_LOCKS);
  const [lockedAt, setLockedAt] = useState<number | null>(null);
  const [motion, setMotion] = useState<MotionAccess>('idle');
  const [count, setCount] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [recBytes, setRecBytes] = useState(0);
  const [result, setResult] = useState<{ blob: Blob; durationS: number; url: string; source: 'guided' | 'camera-app' } | null>(null);
  const [coachSummary, setCoachSummary] = useState<CoachSummary | null>(null);
  const [showSteps, setShowSteps] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<{ sent: number; total: number } | null>(null);
  const [online, setOnline] = useState(typeof navigator === 'undefined' ? true : navigator.onLine);
  const [submitted, setSubmitted] = useState(false);

  const videoRef = useRef<HTMLVideoElement>(null);
  const recRef = useRef<WebRecorder | null>(null);
  const startedAt = useRef(0);
  const uploadJob = useRef<UploadJob | null>(null);
  const stopUpload = useRef(false);

  const motionActive = phase === 'camera' || phase === 'countdown' || phase === 'recording';
  const coach = useWebMotion(guide?.limits ?? { panDps: 45, walkMps: 1, levelDeg: 12, shakeMs2: 2.2, pauseSec: 2, stillSec: 8 }, motionActive && (motion === 'granted' || !!demo), demo?.motion);

  // Device policy.
  useEffect(() => {
    if (demo) {
      setPolicy(demo.os === 'ios'
        ? checkDevice({ os: 'ios', modelId: null, modelName: null })
        : checkDevice({ os: 'android', modelId: 'Pixel 8', modelName: 'Pixel 8' }));
      return;
    }
    webDeviceModel().then((d) => setPolicy(checkDevice({ os: d.os, modelName: d.modelName, modelId: d.modelName }))).catch(() => {});
  }, [demo]);

  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener('online', on);
    window.addEventListener('offline', off);
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off); };
  }, []);

  // Attach the stream to the preview.
  useEffect(() => {
    if (videoRef.current && cam) {
      videoRef.current.srcObject = cam.stream;
      videoRef.current.play().catch(() => {});
    }
  }, [cam, phase]);

  // Dev demo: jump straight into a camera phase with a fake stream.
  useEffect(() => {
    if (!demo || !guide || cam || !['camera', 'countdown', 'recording'].includes(phase)) return;
    const stream = demoStream();
    const track = stream.getVideoTracks()[0];
    const lockable = demo.os === 'android'
      ? { exposure: true, focus: true, whiteBalance: true }
      : { exposure: false, focus: false, whiteBalance: true };
    setCam({ stream, track, deviceId: 'demo', lens: 'ultra-wide', lensLabel: demo.os === 'ios' ? 'Back Ultra Wide Camera' : 'camera2 2, facing back', lensHow: demo.os === 'ios' ? 'label' : 'zoom', backCameras: [], width: 3840, height: 2160, fps: 30, lockable });
    if (params.get('locked')) {
      setLocks(demo.os === 'android'
        ? { exposure: 'locked', focus: 'locked', whiteBalance: 'locked' }
        : { exposure: 'unsupported', focus: 'unsupported', whiteBalance: 'locked' });
      setLockedAt(Date.now());
    }
    if (phase === 'recording') { startedAt.current = Date.now() - 134_000; setElapsed(134); }
    if (phase === 'countdown') setCount(2);
  }, [demo, guide, cam, phase, params]);

  // Release the camera when leaving.
  useEffect(() => () => { stopStream(cam?.stream); }, [cam]);

  // Recording clock.
  useEffect(() => {
    if (phase !== 'recording') return;
    const id = window.setInterval(() => {
      const s = (Date.now() - startedAt.current) / 1000;
      setElapsed(s);
      if (guide && s >= guide.maxSeconds && !demo) void stopRecording();
    }, 250);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

  // Countdown → record.
  useEffect(() => {
    if (phase !== 'countdown' || (demo && params.get('phase') === 'countdown')) return;
    if (count <= 0) { beginRecording(); return; }
    const id = window.setTimeout(() => setCount((c) => c - 1), 1000);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, count, demo, params]);

  const openCam = useCallback(async (preferId?: string | null) => {
    if (!guide) return;
    setError(null);
    if (demo) { setPhase('camera'); return; }
    setBusy(true);
    try {
      if (motion === 'idle') setMotion(await requestMotionAccess());
      stopStream(cam?.stream);
      const opened = await openCamera(guide, preferId);
      setCam(opened);
      setLocks(NO_LOCKS);
      setLockedAt(null);
      setPhase('camera');
    } catch (e) {
      const msg = (e as Error)?.name === 'NotAllowedError'
        ? 'Camera access is off. Allow the camera for this site in your browser settings, then try again.'
        : (e as Error)?.message || 'Couldn’t open the camera.';
      setError(msg);
    } finally {
      setBusy(false);
    }
  }, [guide, cam, motion]);

  const lockNow = async () => {
    if (!cam || !guide) return;
    setBusy(true);
    try {
      if (demo) {
        setLocks(demo.os === 'android'
          ? { exposure: 'locked', focus: 'locked', whiteBalance: 'locked' }
          : { exposure: 'unsupported', focus: 'unsupported', whiteBalance: 'locked' });
      } else {
        setLocks(await applyLocks(cam.track, guide.locks));
      }
      setLockedAt(Date.now());
    } finally {
      setBusy(false);
    }
  };

  const unlock = async () => {
    if (cam && !demo) await releaseLocks(cam.track);
    setLocks(NO_LOCKS);
    setLockedAt(null);
  };

  const switchLens = () => {
    if (!cam || cam.backCameras.length < 2) return;
    const i = cam.backCameras.findIndex((c) => c.deviceId === cam.deviceId);
    const next = cam.backCameras[(i + 1) % cam.backCameras.length];
    rememberLens(next.deviceId);
    void openCam(next.deviceId);
  };

  function beginRecording() {
    if (!cam) return;
    try {
      const r = new WebRecorder();
      r.onBytes = setRecBytes;
      r.onLimit = () => { void stopRecording(); };
      r.start(cam.stream);
      recRef.current = r;
      coach.reset();
      startedAt.current = Date.now();
      setElapsed(0);
      setRecBytes(0);
      setPhase('recording');
      navigator.vibrate?.(30);
    } catch (e) {
      setError((e as Error).message);
      setPhase('camera');
    }
  }

  async function stopRecording() {
    const r = recRef.current;
    if (!r) return;
    recRef.current = null;
    try {
      const out = await r.stop();
      setCoachSummary(motion === 'granted' ? coach.summary() : null);
      setResult({ blob: out.blob, durationS: out.durationS, url: URL.createObjectURL(out.blob), source: 'guided' });
      stopStream(cam?.stream);
      setCam(null);
      setPhase('review');
    } catch (e) {
      setError((e as Error).message || 'Recording stopped unexpectedly.');
      setPhase('camera');
    }
  }

  const pickFile = (f: File | null) => {
    if (!f) return;
    const url = URL.createObjectURL(f);
    const v = document.createElement('video');
    v.preload = 'metadata';
    v.onloadedmetadata = () => {
      setCoachSummary(null);
      setLocks({ exposure: 'locked', focus: 'locked', whiteBalance: 'auto' }); // Camera app AE/AF lock, by the vendor
      setResult({ blob: f, durationS: Number.isFinite(v.duration) ? v.duration : 0, url, source: 'camera-app' });
      setPhase('review');
    };
    v.onerror = () => setError('Couldn’t read that video. Pick the walk-through from your library.');
    v.src = url;
  };

  const meta = (): CaptureMeta => ({
    guide: guide!.id,
    guide_version: guide!.version,
    platform: ios ? 'web-ios' : android ? 'web-android' : 'web-other',
    device: policy?.label || 'unknown',
    device_status: override && policy?.status !== 'approved' ? `${policy?.status}-override` : policy?.status || 'unknown',
    lens: result?.source === 'camera-app' ? 'camera-app' : cam?.lens || 'unknown',
    locks: result?.source === 'camera-app' ? { exposure: 'locked', focus: 'locked', whiteBalance: 'auto' } : locks,
    width: cam?.width || 0,
    height: cam?.height || 0,
    fps: cam?.fps || 0,
    stabilization: 'browser-default',
    hdr: 'browser-default',
    mime: result?.blob.type || '',
  });

  const startUpload = async () => {
    if (!result || !guide) return;
    setError(null);
    setPhase('upload');
    stopUpload.current = false;
    if (!uploadJob.current) {
      uploadJob.current = {
        jobId, name: floor, floorIndex: Math.max(0, FLOORS.indexOf(floor)), blob: result.blob,
        durationS: result.durationS, coach: coachSummary, capture: meta(),
      };
    }
    try {
      await uploadVisitVideo(uploadJob.current, (p) => setProgress({ sent: p.sent, total: p.total }), () => stopUpload.current);
      if (!stopUpload.current) setPhase('done');
    } catch (e) {
      setError((e as Error).message || 'Upload stopped.');
    }
  };

  // Resume when the connection comes back.
  useEffect(() => {
    if (phase === 'upload' && online && error && uploadJob.current) void startUpload();
    if (!online) stopUpload.current = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [online]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await visitVideoApi.submit(jobId);
      setSubmitted(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const restart = () => {
    if (result) URL.revokeObjectURL(result.url);
    setResult(null);
    uploadJob.current = null;
    setProgress(null);
    setLocks(NO_LOCKS);
    setLockedAt(null);
    setPhase('intro');
  };

  const close = () => {
    stopStream(cam?.stream);
    setCam(null);
    recRef.current?.cancel();
    recRef.current = null;
    if (phase === 'intro' || phase === 'done') navigate(jobId ? '/jobs' : '/');
    else setPhase('intro');
  };

  if (!guide) {
    return <div className="gc-page"><p className="gc-note">This capture guide isn’t available yet.</p></div>;
  }
  if (!jobId && !demo) {
    return (
      <div className="gc-page">
        <p className="gc-note">Open the guided camera from a job.</p>
        <Link to="/jobs" className="btn btn-secondary">Jobs</Link>
      </div>
    );
  }

  /* ───────────── camera phases ───────────── */
  if (phase === 'camera' || phase === 'countdown' || phase === 'recording') {
    const r: CoachReading = coach.reading;
    const anyLockable = !!cam && (cam.lockable.exposure || cam.lockable.focus || cam.lockable.whiteBalance);
    const isLocked = lockedAt != null;
    const recording = phase === 'recording';
    const limitWarn = recording && (elapsed >= guide.warnSeconds || recBytes >= WEB_WARN_BYTES);
    const sizeNow = recording ? (recBytes || estimateBytes(guide, elapsed) * 0.5) : 0;
    const levelWarn = Math.abs(r.rollDeg) > guide.limits.levelDeg;

    let card: { kicker: string; title: string; body: string; tone: 'calm' | 'warn' | 'ok' };
    if (error) card = { kicker: 'Camera', title: 'Something went wrong', body: error, tone: 'warn' };
    else if (limitWarn) card = { kicker: recBytes >= WEB_WARN_BYTES ? 'Memory' : `${clock(guide.maxSeconds - elapsed)} left`, title: 'Wrap up this floor', body: recBytes >= WEB_WARN_BYTES ? 'The browser holds the video in memory. Finish this floor now.' : 'Recording stops at 15:00. Record the rest as another floor.', tone: 'warn' };
    else if (recording && r.cue) card = { kicker: 'Guide', title: r.cue.title, body: r.cue.body, tone: 'warn' };
    else if (recording && r.paused) card = { kicker: 'Guide', title: 'Good pause', body: 'Carry on to the next room.', tone: 'ok' };
    else if (recording) card = { kicker: 'Recording', title: 'Walk slowly, room by room', body: 'Pause 2 seconds in each room. Turn gently.', tone: 'calm' };
    else if (!isLocked && anyLockable) card = { kicker: 'Step 1 of 2', title: guide.metering.title, body: guide.metering.body, tone: 'calm' };
    else if (!isLocked) card = { kicker: 'Step 1 of 2', title: 'This browser can’t lock the camera', body: ios ? 'Safari adjusts brightness and focus as you walk. For locked settings, use the Camera app route.' : 'Brightness and focus will adjust as you walk. Hold steady when entering bright rooms.', tone: 'calm' };
    else card = { kicker: 'Step 2 of 2', title: 'Go to the front door', body: 'Phone at chest height, facing in. Tap record when you’re ready.', tone: 'calm' };

    const lensText = cam?.lens === 'ultra-wide' ? '0.5×' : cam?.lens === 'wide' ? '1×' : 'Lens';
    return (
      <div className="gc-cam">
        <video ref={videoRef} className="gc-video" playsInline muted autoPlay />
        <div className="gc-scrim gc-scrim-top" />
        <div className="gc-scrim gc-scrim-bottom" />

        <div className="gc-hud">
          <header className="gc-top">
            <button type="button" className="gc-icon-btn" onClick={() => (recording ? void stopRecording() : close())} aria-label={recording ? 'Stop recording' : 'Close camera'}>
              <IconX />
            </button>
            <div className="gc-top-title">
              <span className="gc-top-name">{floor}</span>
              <span className="gc-top-sub">{guide.title}</span>
            </div>
            {recording ? (
              <span className="gc-rec" aria-label="Recording"><span className="gc-rec-dot" />{clock(elapsed)}</span>
            ) : (
              cam && cam.backCameras.length > 1 ? (
                <button type="button" className={`gc-icon-btn gc-lens ${cam.lens === 'ultra-wide' ? '' : 'is-warn'}`} onClick={switchLens} aria-label={`Lens ${lensText}. Switch lens`}>
                  <span className="gc-mono">{lensText}</span>
                </button>
              ) : (
                <span className={`gc-icon-btn gc-lens ${cam?.lens === 'ultra-wide' ? '' : 'is-warn'}`} aria-label={`Lens ${lensText}`}>
                  <span className="gc-mono">{lensText}</span>
                </span>
              )
            )}
          </header>

          <div className="gc-chips">
            {guide.locks.map((k) => <LockChip key={k} kind={k} state={isLocked ? locks[k] : cam && !cam.lockable[k] ? 'unsupported' : 'auto'} />)}
            {cam ? <span className="gc-chip gc-chip-plain"><span className="gc-mono">{cam.height >= 2000 ? '4K' : cam.height >= 1000 ? '1080p' : `${cam.height}p`} · {cam.fps || guide.camera.fps} fps</span></span> : null}
          </div>
          {recording ? <div className="gc-sub-readout gc-mono">{bytes(sizeNow)} · {clock(guide.maxSeconds)} max</div> : null}

          <div className="gc-middle">
            {phase === 'countdown' ? <div className="gc-count gc-mono" aria-live="assertive">{count || ''}</div> : null}
            {cam?.lens !== 'ultra-wide' && !recording && cam ? (
              <div className="gc-inline-note"><IconLens size={16} /> {cam.backCameras.length > 1 ? 'Tap the lens button until the view is widest.' : 'Ultra-wide isn’t available in this browser.'}</div>
            ) : null}
          </div>

          <div className="gc-guides">
            <LevelMeter roll={r.rollDeg} warn={levelWarn} />
            {motion === 'granted' || demo ? (
              <SpeedMeter mps={r.walkMps} limit={guide.limits.walkMps} pan={r.panDps} panLimit={guide.limits.panDps} />
            ) : (
              <p className="gc-inline-note">Motion sensors are off, so there’s no speed guide. Walk slowly.</p>
            )}
          </div>

          <div className={`gc-card is-${card.tone}`} role="status" aria-live="polite">
            <span className="gc-card-kicker">{card.kicker}</span>
            <span className="gc-card-title">{card.title}</span>
            <span className="gc-card-body">{card.body}</span>
            {error && ios ? (
              <div className="gc-card-actions"><Button variant="primary" onClick={() => { stopStream(cam?.stream); setCam(null); setError(null); setPhase('camera-app'); }}>Use the Camera app instead</Button></div>
            ) : null}
            {phase === 'camera' && !isLocked && !error ? (
              <div className="gc-card-actions">
                {anyLockable ? (
                  <Button variant="primary" icon={<IconLock size={18} />} onClick={lockNow} disabled={busy || !cam}>{guide.metering.action}</Button>
                ) : (
                  <Button variant="primary" onClick={() => setLockedAt(Date.now())} disabled={!cam}>Continue</Button>
                )}
              </div>
            ) : null}
            {phase === 'camera' && isLocked && anyLockable && !error ? (
              <div className="gc-card-actions"><Button variant="tertiary" onClick={unlock}>Meter again</Button></div>
            ) : null}
          </div>

          {showSteps && !recording ? (
            <div className="gc-sheet" role="dialog" aria-label="Steps">
              <StepsList guide={guide} />
              <Button variant="secondary" onClick={() => setShowSteps(false)}>Close</Button>
            </div>
          ) : null}

          <footer className="gc-controls">
            <div className="gc-ctl-side">
              {!recording ? (
                <button type="button" className="gc-icon-btn gc-labeled" onClick={() => setShowSteps((v) => !v)} aria-label="Steps">
                  <IconList /><span>Steps</span>
                </button>
              ) : null}
            </div>
            <button
              type="button"
              className={`gc-shutter ${recording ? 'is-rec' : ''}`}
              disabled={phase === 'countdown' || (!recording && !isLocked) || !cam}
              onClick={() => (recording ? void stopRecording() : (setCount(guide.countdownSec), setPhase('countdown')))}
              aria-label={recording ? 'Stop recording' : 'Start recording'}
            >
              <span className="gc-shutter-core" />
            </button>
            <div className="gc-ctl-side gc-ctl-right">
              {recording && (motion === 'granted' || demo) ? <span className="gc-steady"><span>Steady</span> <span className="gc-mono">{r.steady}</span></span> : null}
            </div>
          </footer>
        </div>
      </div>
    );
  }

  /* ───────────── page phases ───────────── */
  const pill = policy?.status === 'approved' ? { t: 'Approved', c: 'is-ok' } : policy?.status === 'unknown' ? { t: 'Check model', c: 'is-warn' } : { t: 'Not approved', c: 'is-bad' };
  const blocked = policy?.status === 'not-approved' && !override;

  return (
    <div className="gc-page">
      <header className="gc-nav">
        <button type="button" className="gc-icon-btn gc-back" onClick={phase === 'intro' ? close : restart} aria-label="Back"><IconBack /></button>
        <span className="gc-nav-title">{phase === 'camera-app' ? 'Camera app' : phase === 'review' ? 'Check the video' : phase === 'upload' || phase === 'done' ? 'Upload' : guide.title}</span>
        <span className="gc-nav-spacer" />
      </header>

      {phase === 'intro' ? (
        <main className="gc-body">
          <div className="gc-head">
            <h1 className="gc-h1">{guide.title}</h1>
            <p className="gc-lede">{guide.summary}</p>
          </div>

          <section className="gc-group" aria-label="This phone">
            <div className="gc-row">
              <span className="gc-row-icon"><IconPhone /></span>
              <span className="gc-row-text">
                <span className="gc-row-title">{policy?.label || 'This phone'}</span>
                <span className="gc-row-sub">{policy?.reason || 'Checking…'}</span>
              </span>
              {policy ? <span className={`gc-pill ${pill.c}`}>{pill.t}</span> : null}
            </div>
            <div className="gc-row">
              <span className="gc-row-icon"><IconLock /></span>
              <span className="gc-row-text">
                <span className="gc-row-title">Camera settings</span>
                <span className="gc-row-sub">
                  {ios
                    ? 'Safari can’t lock exposure or focus. We lock what it allows, and say so on screen.'
                    : android
                      ? 'Most Android phones lock exposure, focus and white balance here. We check when the camera opens.'
                      : 'We lock what this browser allows and show it on screen.'}
                </span>
              </span>
            </div>
          </section>

          {policy?.status === 'not-approved' && !override ? (
            <div className="gc-notice is-warn" role="alert">
              <IconAlert size={18} />
              <div>
                <p className="gc-notice-title">This phone isn’t approved for virtual visits</p>
                <p className="gc-notice-body">The 3D build needs a recent phone with a 0.5× lens: {POLICY_LINE} Recordings from other phones may be sent back.</p>
                <Button variant="tertiary" onClick={() => setOverride(true)}>Continue anyway</Button>
              </div>
            </div>
          ) : null}

          <section aria-label="Floor">
            <p className="gc-label">This recording</p>
            <div className="gc-chiprow">
              {FLOORS.map((f) => (
                <button type="button" key={f} className={`gc-select ${floor === f ? 'is-on' : ''}`} onClick={() => setFloor(f)} aria-pressed={floor === f}>{f}</button>
              ))}
            </div>
          </section>

          <section aria-label="Steps">
            <p className="gc-label">How to walk it</p>
            <StepsList guide={guide} />
          </section>

          {error ? <div className="gc-notice is-bad" role="alert"><IconAlert size={18} /><p className="gc-notice-body">{error}</p></div> : null}

          <footer className="gc-footer">
            <Button variant="primary" icon={<IconCamera size={18} />} onClick={() => openCam()} disabled={busy || blocked}>Open camera</Button>
            {ios ? <Button variant="secondary" onClick={() => setPhase('camera-app')} disabled={blocked}>Use the Camera app instead</Button> : null}
          </footer>
        </main>
      ) : null}

      {phase === 'camera-app' ? (
        <main className="gc-body">
          <div className="gc-head">
            <h1 className="gc-h1">Record in the Camera app</h1>
            <p className="gc-lede">The Camera app can lock exposure and focus. White balance stays automatic.</p>
          </div>
          <ol className="gc-steps">
            {[
              ['Set 4K at 30 fps', 'Settings › Camera › Record Video. Turn HDR Video off and Lock Camera on.'],
              ['Open Camera, choose Video, tap 0.5×', 'The ultra-wide lens sees the most of each room.'],
              ['Point at the brightest room', 'Touch and hold the screen until AE/AF Lock shows.'],
              ['Walk the floor', 'Front door, chest height, slowly. Pause 2 seconds in each room. Turn slowly.'],
              ['Come back and choose the video', 'One video per floor.'],
            ].map(([t, b], i) => (
              <li key={t} className="gc-step">
                <span className="gc-step-icon gc-mono">{i + 1}</span>
                <span className="gc-step-text"><span className="gc-step-title">{t}</span><span className="gc-step-body">{b}</span></span>
              </li>
            ))}
          </ol>
          {error ? <div className="gc-notice is-bad" role="alert"><IconAlert size={18} /><p className="gc-notice-body">{error}</p></div> : null}
          <footer className="gc-footer">
            <label className="btn btn-primary gc-file">
              <IconUpload size={18} /><span className="btn-label">Choose video</span>
              <input type="file" accept="video/*" onChange={(e) => pickFile(e.target.files?.[0] || null)} />
            </label>
            <Button variant="secondary" onClick={() => setPhase('intro')}>Use the guided camera</Button>
          </footer>
        </main>
      ) : null}

      {phase === 'review' && result ? (
        <main className="gc-body">
          <video className="gc-review-video" src={result.url} controls playsInline />
          <section className="gc-group">
            <div className="gc-kv"><span>Length</span><span className="gc-mono">{clock(result.durationS)}</span></div>
            <div className="gc-kv"><span>Size</span><span className="gc-mono">{bytes(result.blob.size)}</span></div>
            <div className="gc-kv"><span>Locked</span><span>{(Object.keys(locks) as LockKind[]).filter((k) => (result.source === 'camera-app' ? k !== 'whiteBalance' : locks[k] === 'locked')).map((k) => LOCK_LABEL[k]).join(', ') || 'Nothing (browser limit)'}</span></div>
          </section>
          {result.source === 'guided' ? (() => {
            const v = coachVerdict(coachSummary);
            return <div className={`gc-notice ${v.tone === 'ok' ? 'is-ok' : 'is-warn'}`}>{v.tone === 'ok' ? <IconCheck size={18} /> : <IconAlert size={18} />}<div><p className="gc-notice-title">{v.title}</p><p className="gc-notice-body">{v.body}</p></div></div>;
          })() : null}
          {result.durationS > 0 && result.durationS < guide.minSeconds ? (
            <div className="gc-notice is-warn"><IconAlert size={18} /><p className="gc-notice-body">That’s under {guide.minSeconds} seconds. Walk the whole floor in one take.</p></div>
          ) : null}
          {result.blob.size > WEB_MAX_BYTES * 2.2 ? <div className="gc-notice is-bad"><IconAlert size={18} /><p className="gc-notice-body">Over 4 GB. Record at 4K 30, one floor per video.</p></div> : null}
          <footer className="gc-footer">
            <Button variant="primary" icon={<IconUpload size={18} />} onClick={startUpload} disabled={result.blob.size > 4_000_000_000 || (!jobId && !demo)}>Upload</Button>
            <Button variant="secondary" onClick={restart}>Record again</Button>
          </footer>
        </main>
      ) : null}

      {(phase === 'upload' || phase === 'done') ? (
        <main className="gc-body">
          <div className="gc-head">
            <h1 className="gc-h1">{phase === 'done' ? (submitted ? 'Sent for processing' : 'Uploaded') : 'Uploading'}</h1>
            <p className="gc-lede">
              {phase === 'done'
                ? (submitted ? 'The 3D build runs on our side. Nothing else is needed from you.' : 'Record another floor, or send the visit for processing.')
                : online ? 'Keep this page open. It picks up where it left off if the connection drops.' : 'Offline. Upload resumes when you’re back online.'}
            </p>
          </div>
          {progress ? (
            <div className="gc-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((progress.sent / Math.max(1, progress.total)) * 100)}>
              <span className="gc-progress-fill" style={{ width: `${(progress.sent / Math.max(1, progress.total)) * 100}%` }} />
            </div>
          ) : null}
          {progress ? <p className="gc-mono gc-note">{bytes(progress.sent)} of {bytes(progress.total)}</p> : null}
          {error ? <div className="gc-notice is-bad" role="alert"><IconAlert size={18} /><p className="gc-notice-body">{error}</p></div> : null}
          <footer className="gc-footer">
            {phase === 'upload' && error ? <Button variant="primary" onClick={startUpload}>Try again</Button> : null}
            {phase === 'done' && !submitted ? <Button variant="primary" onClick={submit} disabled={busy}>Send for processing</Button> : null}
            {phase === 'done' && !submitted ? <Button variant="secondary" onClick={restart}>Record another floor</Button> : null}
            {phase === 'done' && submitted ? <Link to="/jobs" className="btn btn-primary"><span className="btn-label">Back to jobs</span></Link> : null}
          </footer>
        </main>
      ) : null}
    </div>
  );
}
