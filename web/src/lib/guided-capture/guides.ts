/**
 * Guided capture: data-driven guides (steps, locks, camera targets, limits per capture type).
 *
 * SOURCE OF TRUTH: web/src/lib/guided-capture/. frontend/lib/guided-capture/ is a byte-for-byte
 * copy (guided-capture.test.mjs fails if they drift). Pure data + pure functions only: no React,
 * no DOM, no native modules, so the same file runs in the PWA, the Expo app and node tests.
 *
 * A guide is plain JSON-shaped data. Film clip and stills guides (bricks-standard/guide) plug in
 * by adding an entry to GUIDES (or loading JSON and passing it through validateGuide).
 */

export type LockKind = 'exposure' | 'focus' | 'whiteBalance';
export type CaptureType = 'virtual-visit' | 'film-clip' | 'still';
export type DeviceClass = 'approved-phone' | 'dedicated-camera';
export type LensTarget = 'ultra-wide' | 'wide';
export type StepIcon = 'door' | 'phone' | 'walk' | 'pause' | 'turn' | 'sun' | 'level' | 'lock' | 'check';

export interface GuideStep {
  id: string;
  title: string;
  body: string;
  icon: StepIcon;
}

export interface CameraTarget {
  lens: LensTarget;
  /** Portrait-first sizes are normalised; width/height here are the long/short edge. */
  longEdge: number;
  shortEdge: number;
  /** Tried in order when the target isn't available. */
  fallbacks: { longEdge: number; shortEdge: number }[];
  fps: number;
  stabilization: 'standard' | 'off';
  hdr: boolean;
  codec: 'hevc' | 'h264';
  bitrateMbps: number;
  audio: boolean;
}

export interface MotionLimits {
  /** Turning faster than this (°/s around gravity) → "Turn slower". */
  panDps: number;
  /** Estimated walking faster than this (m/s) → "Slow down". */
  walkMps: number;
  /** Phone rolled/pitched further than this from upright (°) → "Hold level". */
  levelDeg: number;
  /** Shake (linear accel EMA, m/s²) above this also counts as moving too fast. */
  shakeMs2: number;
  /** A pause of at least this long counts as "paused in the room". */
  pauseSec: number;
  /** Standing still longer than this → "Keep walking". 0 disables. */
  stillSec: number;
}

export interface CaptureGuide {
  id: CaptureType;
  version: number;
  title: string;
  summary: string;
  device: DeviceClass;
  camera: CameraTarget;
  locks: LockKind[];
  metering: { title: string; body: string; action: string };
  steps: GuideStep[];
  countdownSec: number;
  maxSeconds: number;
  warnSeconds: number;
  minSeconds: number;
  limits: MotionLimits;
}

export const VIRTUAL_VISIT_GUIDE: CaptureGuide = {
  id: 'virtual-visit',
  version: 1,
  title: 'Virtual visit',
  summary: 'One slow walk-through per floor. We build the 3D visit from it.',
  device: 'approved-phone',
  camera: {
    lens: 'ultra-wide',
    longEdge: 3840,
    shortEdge: 2160,
    fallbacks: [{ longEdge: 1920, shortEdge: 1080 }],
    fps: 30,
    stabilization: 'standard',
    hdr: false,
    codec: 'hevc',
    bitrateMbps: 30,
    audio: false,
  },
  locks: ['exposure', 'focus', 'whiteBalance'],
  metering: {
    title: 'Point at the brightest room',
    body: 'Hold still for a moment, then tap Lock. Brightness, focus and colour stay fixed for the whole walk.',
    action: 'Lock',
  },
  steps: [
    { id: 'door', title: 'Start at the front door', body: 'Facing in, door behind you.', icon: 'door' },
    { id: 'chest', title: 'Phone at chest height', body: 'Upright, looking ahead. Not at the floor.', icon: 'phone' },
    { id: 'slow', title: 'Walk slowly', body: 'Slower than feels natural.', icon: 'walk' },
    { id: 'pause', title: 'Pause 2 seconds in each room', body: 'Stand still in the middle, then carry on.', icon: 'pause' },
    { id: 'turn', title: 'Turn slowly', body: 'Rotate gently. Never whip round.', icon: 'turn' },
  ],
  countdownSec: 3,
  maxSeconds: 15 * 60,
  warnSeconds: 13 * 60,
  minSeconds: 20,
  limits: { panDps: 45, walkMps: 0.9, levelDeg: 12, shakeMs2: 2.2, pauseSec: 2, stillSec: 8 },
};

export const GUIDES: Partial<Record<CaptureType, CaptureGuide>> = {
  'virtual-visit': VIRTUAL_VISIT_GUIDE,
  // 'film-clip' and 'still' land here from bricks-standard/guide (dedicated cameras only).
};

export function getGuide(id: string | null | undefined): CaptureGuide | null {
  if (!id) return null;
  return (GUIDES as Record<string, CaptureGuide | undefined>)[id] ?? null;
}

export const LOCK_LABEL: Record<LockKind, string> = {
  exposure: 'Exposure',
  focus: 'Focus',
  whiteBalance: 'White balance',
};

/** Validate a guide loaded from JSON. Returns a list of problems (empty = valid). */
export function validateGuide(g: unknown): string[] {
  const errs: string[] = [];
  const o = g as Partial<CaptureGuide> | null;
  if (!o || typeof o !== 'object') return ['guide must be an object'];
  if (!o.id || !['virtual-visit', 'film-clip', 'still'].includes(o.id)) errs.push('id must be virtual-visit, film-clip or still');
  if (typeof o.version !== 'number' || o.version < 1) errs.push('version must be a positive number');
  if (!o.title || typeof o.title !== 'string') errs.push('title is required');
  if (o.device !== 'approved-phone' && o.device !== 'dedicated-camera') errs.push('device must be approved-phone or dedicated-camera');
  if (!Array.isArray(o.steps) || o.steps.length === 0) errs.push('steps must be a non-empty list');
  else o.steps.forEach((s, i) => {
    if (!s || !s.id || !s.title) errs.push(`step ${i + 1} needs id and title`);
    if (s?.title && /[A-Z]{2,}/.test(s.title.replace(/\bAE\b|\bAF\b|\bAWB\b|\bHDR\b|\b4K\b/g, ''))) errs.push(`step ${i + 1} title should be sentence case`);
  });
  if (!Array.isArray(o.locks)) errs.push('locks must be a list');
  else o.locks.forEach((l) => { if (!['exposure', 'focus', 'whiteBalance'].includes(l)) errs.push(`unknown lock ${l}`); });
  const c = o.camera;
  if (!c) errs.push('camera is required');
  else {
    if (!(c.fps > 0)) errs.push('camera.fps must be > 0');
    if (!(c.longEdge >= c.shortEdge && c.shortEdge > 0)) errs.push('camera.longEdge/shortEdge invalid');
    if (!(c.bitrateMbps > 0)) errs.push('camera.bitrateMbps must be > 0');
  }
  if (!(typeof o.maxSeconds === 'number' && o.maxSeconds > 0)) errs.push('maxSeconds must be > 0');
  if (typeof o.warnSeconds === 'number' && typeof o.maxSeconds === 'number' && o.warnSeconds >= o.maxSeconds) errs.push('warnSeconds must be below maxSeconds');
  const l = o.limits;
  if (!l) errs.push('limits are required');
  else for (const k of ['panDps', 'walkMps', 'levelDeg', 'shakeMs2', 'pauseSec'] as const) {
    if (!(typeof l[k] === 'number' && l[k] > 0)) errs.push(`limits.${k} must be > 0`);
  }
  return errs;
}

/** Rough size estimate for a recording at the guide's bitrate. */
export function estimateBytes(guide: CaptureGuide, seconds: number): number {
  return Math.max(0, seconds) * (guide.camera.bitrateMbps * 1_000_000) / 8;
}
