/**
 * Live guidance from motion sensors: level, pan speed, walk speed, pauses.
 *
 * Pure and platform-free (see guides.ts header: this file is mirrored into frontend/lib).
 * Feed it samples from DeviceMotionEvent (web) or expo-sensors DeviceMotion (native):
 *   rotationRate  °/s, alpha = about z, beta = about x, gamma = about y (W3C + Expo naming)
 *   acceleration  m/s², gravity removed
 *   gravity       m/s², accelerationIncludingGravity (sign conventions differ between
 *                 iOS and Android; everything here uses magnitudes or |dot| so it doesn't matter)
 *
 * Everything is advisory. Nothing here blocks recording.
 */
import type { MotionLimits } from './guides';

export interface Vec3 { x: number; y: number; z: number }
export interface MotionSample {
  t: number; // ms
  rotationRate?: { alpha?: number | null; beta?: number | null; gamma?: number | null } | null;
  acceleration?: Partial<Vec3> | null;
  gravity?: Partial<Vec3> | null;
}

export type CueKind = 'pan' | 'fast' | 'level' | 'aim' | 'still';
export interface Cue { kind: CueKind; title: string; body: string }

export const CUES: Record<CueKind, Cue> = {
  pan: { kind: 'pan', title: 'Turn slower', body: 'Rotate gently while you walk.' },
  fast: { kind: 'fast', title: 'Slow down', body: 'Guided-tour pace. Slower than feels natural.' },
  level: { kind: 'level', title: 'Hold level', body: 'Keep the phone upright, not tipped sideways.' },
  aim: { kind: 'aim', title: 'Look straight ahead', body: 'Chest height, facing into the room.' },
  still: { kind: 'still', title: 'Keep walking', body: 'Pause 2 seconds per room, then carry on.' },
};

const PRIORITY: CueKind[] = ['pan', 'fast', 'level', 'aim', 'still'];
const MIN_CUE_MS = 1200;

const n = (v: number | null | undefined) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
const deg = (r: number) => (r * 180) / Math.PI;

/** Roll (sideways tip) and pitch (forward/back) from gravity, for a phone held in portrait. */
export function levelFromGravity(g: Partial<Vec3> | null | undefined): { rollDeg: number; pitchDeg: number } | null {
  if (!g) return null;
  const x = n(g.x), y = n(g.y), z = n(g.z);
  const mag = Math.sqrt(x * x + y * y + z * z);
  if (mag < 3) return null; // no usable gravity reading
  const ay = Math.abs(y);
  return { rollDeg: deg(Math.atan2(x, Math.max(ay, 1e-6))), pitchDeg: deg(Math.atan2(z, Math.max(ay, 1e-6))) };
}

/** Rotation rate around the gravity axis (°/s): how fast the person is turning / panning. */
export function panRate(rr: MotionSample['rotationRate'], g: Partial<Vec3> | null | undefined): number {
  if (!rr) return 0;
  const wx = n(rr.beta), wy = n(rr.gamma), wz = n(rr.alpha);
  if (!g) return Math.sqrt(wx * wx + wy * wy + wz * wz);
  const gx = n(g.x), gy = n(g.y), gz = n(g.z);
  const mag = Math.sqrt(gx * gx + gy * gy + gz * gz);
  if (mag < 3) return Math.sqrt(wx * wx + wy * wy + wz * wz);
  return Math.abs((wx * gx + wy * gy + wz * gz) / mag);
}

/**
 * Walk speed from step cadence. Steps are peaks in vertical (gravity-axis) linear acceleration.
 * Speed = cadence × stride, stride growing with cadence (slow walk ≈ 0.55 m, brisk ≈ 0.7 m).
 * Rough by design: it's for "slow down", not navigation.
 */
export class WalkEstimator {
  private smooth = 0;
  private prev = 0;
  private rising = false;
  private steps: number[] = [];
  private lastStep = -Infinity;
  constructor(private threshold = 0.45, private minGapMs = 300, private windowMs = 4000) {}

  push(t: number, verticalAcc: number): void {
    this.smooth = this.smooth * 0.6 + verticalAcc * 0.4;
    const s = this.smooth;
    if (s > this.prev) this.rising = true;
    else if (this.rising && s < this.prev) {
      // local max at prev
      if (this.prev > this.threshold && t - this.lastStep >= this.minGapMs) {
        this.steps.push(t);
        this.lastStep = t;
      }
      this.rising = false;
    }
    this.prev = s;
    while (this.steps.length && t - this.steps[0] > this.windowMs) this.steps.shift();
  }

  cadenceHz(now: number): number {
    if (now - this.lastStep > 1800 || this.steps.length < 3) return 0;
    const span = (this.steps[this.steps.length - 1] - this.steps[0]) / 1000;
    return span > 0 ? (this.steps.length - 1) / span : 0;
  }

  speedMps(now: number): number {
    const c = this.cadenceHz(now);
    if (!c) return 0;
    return c * (0.35 + 0.18 * Math.min(2.5, c));
  }

  reset() { this.smooth = 0; this.prev = 0; this.rising = false; this.steps = []; this.lastStep = -Infinity; }
}

export interface CoachReading {
  cue: Cue | null;
  rollDeg: number;
  pitchDeg: number;
  panDps: number;
  walkMps: number;
  cadenceSpm: number;
  /** Seconds the phone has been still (0 while moving). */
  stillFor: number;
  /** True once a pause reaches limits.pauseSec. */
  paused: boolean;
  pauses: number;
  /** 0–100 composite, for a mono readout. */
  steady: number;
  hasSensors: boolean;
}

export interface CoachSummary {
  turn_fast_pct: number;
  shake_pct: number;
  tilt_pct: number;
  still_pct: number;
  max_turn_dps: number;
  pauses: number;
  avg_walk_mps: number;
}

/** Combines the estimators into one reading + cue. Pure: call push() per sample. */
export class MotionCoach {
  private emaPan = 0;
  private emaShake = 0;
  private walk = new WalkEstimator();
  private stillSince: number | null = null;
  private pauseCounted = false;
  private cue: Cue | null = null;
  private cueAt = 0;
  private c = { n: 0, pan: 0, fast: 0, tilt: 0, still: 0, maxPan: 0, walkSum: 0, walkN: 0, pauses: 0 };
  private last: CoachReading = {
    cue: null, rollDeg: 0, pitchDeg: 0, panDps: 0, walkMps: 0, cadenceSpm: 0, stillFor: 0, paused: false, pauses: 0, steady: 100, hasSensors: false,
  };

  constructor(public limits: MotionLimits, private alpha = 0.25) {}

  push(s: MotionSample): CoachReading {
    const g = s.gravity ?? null;
    const lvl = levelFromGravity(g);
    const pan = panRate(s.rotationRate, g);
    const a = s.acceleration ?? null;
    const ax = n(a?.x), ay = n(a?.y), az = n(a?.z);
    const shake = Math.sqrt(ax * ax + ay * ay + az * az);
    let vertical = 0;
    if (g) {
      const gx = n(g.x), gy = n(g.y), gz = n(g.z);
      const mag = Math.sqrt(gx * gx + gy * gy + gz * gz);
      // Upward-positive regardless of platform sign: project on gravity, sign by convention below.
      if (mag > 3) vertical = -(ax * gx + ay * gy + az * gz) / mag;
    }
    this.walk.push(s.t, Math.abs(vertical) > 0 ? vertical : 0);

    this.emaPan = this.emaPan * (1 - this.alpha) + pan * this.alpha;
    this.emaShake = this.emaShake * (1 - this.alpha) + shake * this.alpha;
    const walkMps = this.walk.speedMps(s.t);
    const L = this.limits;

    const isStill = this.emaPan < 6 && this.emaShake < 0.3 && walkMps === 0;
    if (isStill) this.stillSince = this.stillSince ?? s.t;
    else { this.stillSince = null; this.pauseCounted = false; }
    const stillFor = this.stillSince == null ? 0 : (s.t - this.stillSince) / 1000;
    const paused = stillFor >= L.pauseSec;
    if (paused && !this.pauseCounted) { this.pauseCounted = true; this.c.pauses += 1; }

    const roll = lvl?.rollDeg ?? 0;
    const pitch = lvl?.pitchDeg ?? 0;
    const offLevel = !!lvl && Math.abs(roll) > L.levelDeg;
    const offAim = !!lvl && Math.abs(pitch) > L.levelDeg * 2.5;
    const tooFast = walkMps > L.walkMps || this.emaShake > L.shakeMs2;
    const turnFast = this.emaPan > L.panDps;
    const stillLong = L.stillSec > 0 && stillFor > L.stillSec;

    let next: Cue | null = null;
    if (turnFast) next = CUES.pan;
    else if (tooFast) next = CUES.fast;
    else if (offLevel) next = CUES.level;
    else if (offAim) next = CUES.aim;
    else if (stillLong) next = CUES.still;

    const cur = this.cue;
    const higher = !!(next && cur && PRIORITY.indexOf(next.kind) < PRIORITY.indexOf(cur.kind));
    if (next?.kind !== cur?.kind && (higher || !cur || s.t - this.cueAt > MIN_CUE_MS)) {
      this.cue = next;
      this.cueAt = s.t;
    }

    const c = this.c;
    c.n += 1;
    if (turnFast) c.pan += 1;
    if (tooFast) c.fast += 1;
    if (offLevel || offAim) c.tilt += 1;
    if (stillLong) c.still += 1;
    c.maxPan = Math.max(c.maxPan, this.emaPan);
    if (walkMps > 0) { c.walkSum += walkMps; c.walkN += 1; }

    const steady = Math.max(0, Math.min(100, Math.round(
      100 - Math.max(0, this.emaPan - 15) * 1.2 - Math.max(0, this.emaShake - 0.8) * 18
      - Math.max(0, Math.abs(roll) - 5) * 1.5 - Math.max(0, walkMps - L.walkMps * 0.8) * 60,
    )));

    this.last = {
      cue: this.cue,
      rollDeg: Math.round(roll * 10) / 10,
      pitchDeg: Math.round(pitch * 10) / 10,
      panDps: Math.round(this.emaPan),
      walkMps: Math.round(walkMps * 100) / 100,
      cadenceSpm: Math.round(this.walk.cadenceHz(s.t) * 60),
      stillFor: Math.round(stillFor * 10) / 10,
      paused,
      pauses: c.pauses,
      steady,
      hasSensors: !!(g || s.rotationRate || a),
    };
    return this.last;
  }

  reading(): CoachReading { return this.last; }

  summary(): CoachSummary {
    const c = this.c;
    const pct = (x: number) => (c.n ? Math.round((x / c.n) * 1000) / 10 : 0);
    return {
      turn_fast_pct: pct(c.pan),
      shake_pct: pct(c.fast),
      tilt_pct: pct(c.tilt),
      still_pct: pct(c.still),
      max_turn_dps: Math.round(c.maxPan),
      pauses: c.pauses,
      avg_walk_mps: c.walkN ? Math.round((c.walkSum / c.walkN) * 100) / 100 : 0,
    };
  }

  reset() {
    this.emaPan = 0; this.emaShake = 0; this.walk.reset(); this.stillSince = null; this.pauseCounted = false;
    this.cue = null; this.cueAt = 0;
    this.c = { n: 0, pan: 0, fast: 0, tilt: 0, still: 0, maxPan: 0, walkSum: 0, walkN: 0, pauses: 0 };
  }
}

/** Review verdict. Advisory copy only. */
export function coachVerdict(s: CoachSummary | null | undefined): { tone: 'ok' | 'warn'; title: string; body: string } {
  if (!s) return { tone: 'ok', title: 'No motion data', body: 'This phone didn’t share motion sensors. Check the video before sending.' };
  const issues: string[] = [];
  if (s.turn_fast_pct > 10) issues.push(`fast turns ${s.turn_fast_pct}% of the time`);
  if (s.shake_pct > 15) issues.push(`moving fast ${s.shake_pct}%`);
  if (s.tilt_pct > 20) issues.push(`off level ${s.tilt_pct}%`);
  if (s.still_pct > 20) issues.push(`standing still ${s.still_pct}%`);
  if (!issues.length) return { tone: 'ok', title: 'Steady recording', body: 'Pace, turns and level look right for the 3D build.' };
  const text = issues.join(', ');
  return { tone: 'warn', title: 'Consider recording again', body: `${text.charAt(0).toUpperCase()}${text.slice(1)}. Re-recording now is cheaper than a re-shoot.` };
}
