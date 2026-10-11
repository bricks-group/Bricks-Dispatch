/**
 * Live pace / steadiness coaching for Visit video capture (gyro + accelerometer
 * via expo-sensors DeviceMotion). Thresholds follow the capture rules the splat
 * pipeline needs: slow turns, slow walk, phone upright at chest height, keep moving.
 *
 *   turn   rotation rate (EMA) > 45°/s          → "Turn slower"
 *   shake  user acceleration (EMA) > 2.2 m/s²   → "Slow down"
 *   tilt   > 30° away from upright              → "Hold the phone upright"
 *   still  nearly no motion for 6 s             → "Keep walking"
 *
 * Priority turn > shake > tilt > still; a cue stays up at least 1.2 s so it can be read.
 * Everything is advisory: nothing blocks recording.
 */
import { useEffect, useRef, useState } from 'react';
import { DeviceMotion, type DeviceMotionMeasurement } from 'expo-sensors';

export type CueKind = 'turn' | 'shake' | 'tilt' | 'still';
export interface Cue { kind: CueKind; title: string; body: string }

export const CUES: Record<CueKind, Cue> = {
  turn: { kind: 'turn', title: 'Turn slower', body: 'Slow down, rotate gently, keep walking.' },
  shake: { kind: 'shake', title: 'Slow down', body: 'Guided-tour pace. Slower than feels natural.' },
  tilt: { kind: 'tilt', title: 'Hold the phone upright', body: 'Chest height, looking ahead. Not at the floor.' },
  still: { kind: 'still', title: 'Keep walking', body: 'Don’t stop. Walk the walls, then corner to corner.' },
};

export const LIMITS = { turnDps: 45, shakeMs2: 2.2, tiltDeg: 30, stillSec: 6, minCueMs: 1200 } as const;

export interface CoachSummary {
  turn_fast_pct: number;
  shake_pct: number;
  tilt_pct: number;
  still_pct: number;
  max_turn_dps: number;
}

export interface CoachState {
  available: boolean | null;
  cue: Cue | null;
  turnDps: number;
  tiltDeg: number;
  steady: number; // 0–100, shown as a mono readout
}

const INTERVAL_MS = 100;
const ALPHA = 0.25; // EMA weight per 100 ms sample (~0.4 s time constant)

export function useMotionCoach(active: boolean) {
  const [state, setState] = useState<CoachState>({ available: null, cue: null, turnDps: 0, tiltDeg: 0, steady: 100 });
  const ema = useRef({ turn: 0, shake: 0 });
  const stillSince = useRef<number | null>(null);
  const cueShownAt = useRef(0);
  const cueRef = useRef<Cue | null>(null);
  const counts = useRef({ n: 0, turn: 0, shake: 0, tilt: 0, still: 0, maxTurn: 0 });

  useEffect(() => {
    let sub: { remove: () => void } | null = null;
    let cancelled = false;
    (async () => {
      const ok = await DeviceMotion.isAvailableAsync().catch(() => false);
      if (cancelled) return;
      setState((s) => ({ ...s, available: ok }));
      if (!ok || !active) return;
      DeviceMotion.setUpdateInterval(INTERVAL_MS);
      sub = DeviceMotion.addListener((m: DeviceMotionMeasurement) => onSample(m));
    })();
    return () => { cancelled = true; sub?.remove(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  function onSample(m: DeviceMotionMeasurement) {
    const now = Date.now();
    const rr = m.rotationRate;
    const turn = rr ? Math.sqrt((rr.alpha || 0) ** 2 + (rr.beta || 0) ** 2 + (rr.gamma || 0) ** 2) : 0;
    const a = m.acceleration;
    const shake = a ? Math.sqrt((a.x || 0) ** 2 + (a.y || 0) ** 2 + (a.z || 0) ** 2) : 0;
    const g = m.accelerationIncludingGravity;
    const gMag = g ? Math.sqrt((g.x || 0) ** 2 + (g.y || 0) ** 2 + (g.z || 0) ** 2) : 0;
    const tilt = g && gMag > 1 ? (Math.acos(Math.min(1, Math.abs(g.y || 0) / gMag)) * 180) / Math.PI : 0;

    ema.current.turn = ema.current.turn * (1 - ALPHA) + turn * ALPHA;
    ema.current.shake = ema.current.shake * (1 - ALPHA) + shake * ALPHA;
    const t = ema.current.turn;
    const sh = ema.current.shake;

    const isStill = t < 5 && sh < 0.25;
    if (isStill) stillSince.current = stillSince.current ?? now;
    else stillSince.current = null;
    const stillLong = stillSince.current != null && now - stillSince.current > LIMITS.stillSec * 1000;

    let next: Cue | null = null;
    if (t > LIMITS.turnDps) next = CUES.turn;
    else if (sh > LIMITS.shakeMs2) next = CUES.shake;
    else if (tilt > LIMITS.tiltDeg) next = CUES.tilt;
    else if (stillLong) next = CUES.still;

    const c = counts.current;
    c.n += 1;
    if (t > LIMITS.turnDps) c.turn += 1;
    if (sh > LIMITS.shakeMs2) c.shake += 1;
    if (tilt > LIMITS.tiltDeg) c.tilt += 1;
    if (stillLong) c.still += 1;
    c.maxTurn = Math.max(c.maxTurn, t);

    // Hold a cue long enough to read; swap immediately to a higher-priority one.
    const cur = cueRef.current;
    const order: CueKind[] = ['turn', 'shake', 'tilt', 'still'];
    const higher = next && cur && order.indexOf(next.kind) < order.indexOf(cur.kind);
    if (next?.kind !== cur?.kind && (higher || !cur || now - cueShownAt.current > LIMITS.minCueMs)) {
      cueRef.current = next;
      cueShownAt.current = now;
    }
    const steady = Math.max(0, Math.round(100 - Math.max(0, t - 15) * 1.2 - Math.max(0, sh - 0.8) * 18 - Math.max(0, tilt - 15)));
    setState((s) => ({ ...s, cue: cueRef.current, turnDps: Math.round(t), tiltDeg: Math.round(tilt), steady }));
  }

  const summary = (): CoachSummary => {
    const c = counts.current;
    const pct = (x: number) => (c.n ? Math.round((x / c.n) * 1000) / 10 : 0);
    return { turn_fast_pct: pct(c.turn), shake_pct: pct(c.shake), tilt_pct: pct(c.tilt), still_pct: pct(c.still), max_turn_dps: Math.round(c.maxTurn) };
  };
  const reset = () => {
    counts.current = { n: 0, turn: 0, shake: 0, tilt: 0, still: 0, maxTurn: 0 };
    stillSince.current = null;
    cueRef.current = null;
  };
  return { ...state, summary, reset };
}

/** Review verdict from the coach summary. Advisory copy only. */
export function coachVerdict(s: CoachSummary | null | undefined): { tone: 'ok' | 'warn'; title: string; body: string } {
  if (!s) return { tone: 'ok', title: 'No motion data', body: 'This phone did not report motion sensors. Check the video before sending.' };
  const issues: string[] = [];
  if (s.turn_fast_pct > 10) issues.push(`fast turns ${s.turn_fast_pct}% of the time`);
  if (s.shake_pct > 15) issues.push(`walking fast ${s.shake_pct}%`);
  if (s.tilt_pct > 20) issues.push(`phone tilted ${s.tilt_pct}%`);
  if (s.still_pct > 20) issues.push(`standing still ${s.still_pct}%`);
  if (!issues.length) return { tone: 'ok', title: 'Steady recording', body: 'Pace and turns look right for the 3D build.' };
  return { tone: 'warn', title: 'Consider re-recording', body: `${issues.join(', ')}. Re-recording now is cheaper than a re-shoot.` };
}
