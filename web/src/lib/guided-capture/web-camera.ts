/**
 * Browser camera for guided capture (PWA only; not mirrored to native).
 *
 * What a browser can actually lock is decided by feature detection on the live track, never
 * by user agent: getCapabilities() lists the modes, applyConstraints() tries them, and
 * getSettings() confirms. A lock is only reported as "locked" when getSettings() agrees.
 *
 * Android Chrome: exposureMode / focusMode / whiteBalanceMode 'manual' (+ current
 *   exposureTime / focusDistance / colorTemperature) on most phones.
 * iOS Safari: no exposureMode / focusMode in WebKit; whiteBalanceMode and zoom exist on
 *   recent iOS. Lens choice works by deviceId ("Back Ultra Wide Camera").
 * Stabilisation, HDR and codec aren't controllable from any browser.
 */
import type { CaptureGuide, LockKind } from './guides';

export type LockState = 'locked' | 'auto' | 'unsupported' | 'failed';
export type LensPick = 'ultra-wide' | 'wide' | 'unknown';

export interface CameraOpen {
  stream: MediaStream;
  track: MediaStreamTrack;
  deviceId: string | null;
  lens: LensPick;
  lensLabel: string;
  lensHow: 'label' | 'zoom' | 'manual' | 'default';
  backCameras: { deviceId: string; label: string }[];
  width: number;
  height: number;
  fps: number;
  lockable: Record<LockKind, boolean>;
}

type Caps = MediaTrackCapabilities & {
  exposureMode?: string[]; focusMode?: string[]; whiteBalanceMode?: string[];
  zoom?: { min: number; max: number; step?: number };
};
type Settings = MediaTrackSettings & {
  exposureMode?: string; focusMode?: string; whiteBalanceMode?: string;
  exposureTime?: number; focusDistance?: number; colorTemperature?: number; zoom?: number;
};

const LENS_KEY = 'bricks.guided.lens.v1';
const UW_LABEL = /ultra\s*-?\s*wide|0[.,]5|\buw\b/i;
const BACK_LABEL = /back|rear|environment|facing back/i;

export function caps(track: MediaStreamTrack): Caps {
  try { return (track.getCapabilities?.() || {}) as Caps; } catch { return {} as Caps; }
}
function settings(track: MediaStreamTrack): Settings {
  try { return (track.getSettings?.() || {}) as Settings; } catch { return {} as Settings; }
}

const MODE_KEY: Record<LockKind, 'exposureMode' | 'focusMode' | 'whiteBalanceMode'> = {
  exposure: 'exposureMode', focus: 'focusMode', whiteBalance: 'whiteBalanceMode',
};

/** Which locks this track can attempt (manual, or single-shot for focus). */
export function lockableFrom(c: Caps): Record<LockKind, boolean> {
  const has = (k: keyof Caps, v: string) => Array.isArray(c[k]) && (c[k] as string[]).includes(v);
  return {
    exposure: has('exposureMode', 'manual'),
    focus: has('focusMode', 'manual') || has('focusMode', 'single-shot'),
    whiteBalance: has('whiteBalanceMode', 'manual'),
  };
}

function videoConstraints(guide: CaptureGuide, deviceId: string | null, size?: { longEdge: number; shortEdge: number }): MediaTrackConstraints {
  const s = size ?? { longEdge: guide.camera.longEdge, shortEdge: guide.camera.shortEdge };
  return {
    ...(deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: 'environment' } }),
    width: { ideal: s.longEdge },
    height: { ideal: s.shortEdge },
    frameRate: { ideal: guide.camera.fps, max: guide.camera.fps },
  };
}

async function listBack(): Promise<{ deviceId: string; label: string }[]> {
  const all = await navigator.mediaDevices.enumerateDevices();
  const vids = all.filter((d) => d.kind === 'videoinput');
  const back = vids.filter((d) => BACK_LABEL.test(d.label) || UW_LABEL.test(d.label));
  return (back.length ? back : vids).map((d) => ({ deviceId: d.deviceId, label: d.label || 'Camera' }));
}

export function stopStream(s: MediaStream | null | undefined) {
  s?.getTracks().forEach((t) => { try { t.stop(); } catch { /* noop */ } });
}

/**
 * Open the back camera, preferring the ultra-wide. `preferId` = the vendor's manual pick.
 * Requests 4K30 and falls back through guide.camera.fallbacks.
 */
export async function openCamera(guide: CaptureGuide, preferId?: string | null): Promise<CameraOpen> {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('This browser can’t open the camera.');
  // 1. Permission first, so enumerateDevices returns labels.
  let stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
  const backCameras = await listBack().catch(() => []);
  let pickId: string | null = preferId ?? null;
  let how: CameraOpen['lensHow'] = preferId ? 'manual' : 'default';
  if (!pickId) {
    const saved = typeof localStorage !== 'undefined' ? localStorage.getItem(LENS_KEY) : null;
    if (saved && backCameras.some((c) => c.deviceId === saved)) { pickId = saved; how = 'manual'; }
  }
  if (!pickId) {
    const uw = backCameras.find((c) => UW_LABEL.test(c.label));
    if (uw) { pickId = uw.deviceId; how = 'label'; }
  }
  // 2. Re-open on the chosen lens at the target size.
  const sizes = [{ longEdge: guide.camera.longEdge, shortEdge: guide.camera.shortEdge }, ...guide.camera.fallbacks];
  let opened = false;
  for (const size of sizes) {
    try {
      const next = await navigator.mediaDevices.getUserMedia({ video: videoConstraints(guide, pickId, size), audio: false });
      stopStream(stream);
      stream = next;
      opened = true;
      break;
    } catch { /* try the next size */ }
  }
  if (!opened && pickId) {
    // Lens refused every size: keep the permission stream (default lens).
    pickId = null;
    how = 'default';
  }
  const track = stream.getVideoTracks()[0];
  const c = caps(track);
  // 3. No ultra-wide device by label: some Android builds expose 0.5× as zoom < 1 on the main camera.
  if (how === 'default' && c.zoom && c.zoom.min < 1) {
    try {
      await track.applyConstraints({ advanced: [{ zoom: c.zoom.min } as MediaTrackConstraintSet] });
      how = 'zoom';
    } catch { /* keep 1× */ }
  }
  const st = settings(track);
  const label = track.label || backCameras.find((b) => b.deviceId === st.deviceId)?.label || 'Back camera';
  const lens: LensPick = how === 'label' || how === 'zoom' || UW_LABEL.test(label) ? 'ultra-wide' : how === 'manual' ? 'unknown' : 'wide';
  return {
    stream, track, deviceId: st.deviceId ?? pickId, lens, lensLabel: label, lensHow: how, backCameras,
    width: Math.max(st.width || 0, st.height || 0), height: Math.min(st.width || 0, st.height || 0),
    fps: Math.round(st.frameRate || 0), lockable: lockableFrom(c),
  };
}

export function rememberLens(deviceId: string | null) {
  try { if (deviceId) localStorage.setItem(LENS_KEY, deviceId); else localStorage.removeItem(LENS_KEY); } catch { /* private mode */ }
}

/**
 * Lock the requested settings at their current values. Returns the verified state per lock.
 * Call while the camera is pointed at the metering scene.
 */
export async function applyLocks(track: MediaStreamTrack, kinds: LockKind[]): Promise<Record<LockKind, LockState>> {
  const out = { exposure: 'unsupported', focus: 'unsupported', whiteBalance: 'unsupported' } as Record<LockKind, LockState>;
  const c = caps(track);
  const can = lockableFrom(c);
  for (const k of kinds) {
    if (!can[k]) continue;
    const s = settings(track);
    const set: Record<string, unknown> = {};
    if (k === 'exposure') {
      set.exposureMode = 'manual';
      if (typeof s.exposureTime === 'number') set.exposureTime = s.exposureTime;
    } else if (k === 'focus') {
      const manual = Array.isArray(c.focusMode) && c.focusMode.includes('manual');
      set.focusMode = manual ? 'manual' : 'single-shot';
      if (manual && typeof s.focusDistance === 'number') set.focusDistance = s.focusDistance;
    } else {
      set.whiteBalanceMode = 'manual';
      if (typeof s.colorTemperature === 'number') set.colorTemperature = s.colorTemperature;
    }
    try {
      await track.applyConstraints({ advanced: [set as MediaTrackConstraintSet] });
      const after = settings(track)[MODE_KEY[k]];
      out[k] = after === set[MODE_KEY[k]] ? 'locked' : after ? 'auto' : 'locked';
      // WebKit can report a stale mode right after applying (bug 280970): trust the apply when no mode is reported.
    } catch {
      out[k] = 'failed';
    }
  }
  return out;
}

/** Back to continuous auto for everything we touched (best effort). */
export async function releaseLocks(track: MediaStreamTrack) {
  const c = caps(track);
  const set: Record<string, string> = {};
  for (const k of ['exposureMode', 'focusMode', 'whiteBalanceMode'] as const) {
    if (Array.isArray(c[k]) && (c[k] as string[]).includes('continuous')) set[k] = 'continuous';
  }
  if (Object.keys(set).length) await track.applyConstraints({ advanced: [set as MediaTrackConstraintSet] }).catch(() => {});
}

/* ───────────── recording ───────────── */

const MIME_CANDIDATES = [
  'video/mp4;codecs=hvc1',
  'video/mp4;codecs=avc1.640033',
  'video/mp4;codecs=avc1',
  'video/mp4',
  'video/webm;codecs=vp9',
  'video/webm;codecs=vp8',
  'video/webm',
];

export function pickMimeType(): string | null {
  if (typeof MediaRecorder === 'undefined') return null;
  for (const m of MIME_CANDIDATES) {
    try { if (MediaRecorder.isTypeSupported(m)) return m; } catch { /* noop */ }
  }
  return '';
}

/** In-memory recording cap for the browser (the whole file lives in RAM until upload). */
export const WEB_MAX_BYTES = 1_800_000_000;
export const WEB_WARN_BYTES = 1_500_000_000;
/** Browsers hold the file in memory, so the web flow asks for a lower bitrate than native. */
export const WEB_BITRATE = 16_000_000;

export interface WebRecording { blob: Blob; mimeType: string; durationS: number; sizeBytes: number }

export class WebRecorder {
  private rec: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private startedAt = 0;
  bytes = 0;
  mimeType = '';
  onBytes?: (bytes: number) => void;
  onLimit?: () => void;

  start(stream: MediaStream) {
    const mime = pickMimeType();
    if (mime === null) throw new Error('This browser can’t record video.');
    const opts: MediaRecorderOptions = { videoBitsPerSecond: WEB_BITRATE };
    if (mime) opts.mimeType = mime;
    this.rec = new MediaRecorder(stream, opts);
    this.mimeType = this.rec.mimeType || mime || 'video/mp4';
    this.chunks = [];
    this.bytes = 0;
    this.rec.ondataavailable = (e) => {
      if (!e.data || !e.data.size) return;
      this.chunks.push(e.data);
      this.bytes += e.data.size;
      this.onBytes?.(this.bytes);
      if (this.bytes >= WEB_MAX_BYTES) this.onLimit?.();
    };
    this.startedAt = Date.now();
    this.rec.start(1000);
  }

  stop(): Promise<WebRecording> {
    return new Promise((resolve, reject) => {
      const r = this.rec;
      if (!r) { reject(new Error('Not recording')); return; }
      r.onstop = () => {
        const type = (this.mimeType || 'video/mp4').split(';')[0];
        const blob = new Blob(this.chunks, { type });
        resolve({ blob, mimeType: type, durationS: (Date.now() - this.startedAt) / 1000, sizeBytes: blob.size });
        this.chunks = [];
        this.rec = null;
      };
      try { r.stop(); } catch (e) { reject(e); }
    });
  }

  cancel() {
    try { this.rec?.stop(); } catch { /* noop */ }
    this.rec = null;
    this.chunks = [];
  }
}

/* ───────────── platform hints ───────────── */

export function isIOSWeb(): boolean {
  if (typeof navigator === 'undefined') return false;
  const ua = navigator.userAgent || '';
  return /iPhone|iPod/.test(ua) || (/Macintosh/.test(ua) && (navigator.maxTouchPoints || 0) > 1 && /Mobile|Safari/.test(ua) && 'ontouchend' in document);
}
export function isAndroidWeb(): boolean {
  return typeof navigator !== 'undefined' && /Android/i.test(navigator.userAgent || '');
}

/** Model hint for the device policy. Android Chrome shares it via UA client hints; Safari never does. */
export async function webDeviceModel(): Promise<{ os: 'ios' | 'android' | 'other'; modelName: string | null }> {
  if (isIOSWeb()) return { os: 'ios', modelName: null };
  if (isAndroidWeb()) {
    const uad = (navigator as Navigator & { userAgentData?: { getHighEntropyValues?: (h: string[]) => Promise<{ model?: string }> } }).userAgentData;
    if (uad?.getHighEntropyValues) {
      try {
        const v = await uad.getHighEntropyValues(['model']);
        if (v.model) return { os: 'android', modelName: v.model };
      } catch { /* fall through */ }
    }
    const m = /Android [\d.]+; ([^;)]+)[;)]/.exec(navigator.userAgent);
    const model = m && m[1] && m[1] !== 'K' ? m[1].trim() : null;
    return { os: 'android', modelName: model };
  }
  return { os: 'other', modelName: null };
}
