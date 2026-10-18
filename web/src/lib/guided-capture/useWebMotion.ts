/** Browser motion → MotionCoach. iOS needs DeviceMotionEvent.requestPermission() from a tap. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { MotionCoach, type CoachReading, type MotionSample } from './motion';
import type { MotionLimits } from './guides';

export type MotionAccess = 'idle' | 'granted' | 'denied' | 'unavailable';

type DMEWithPerm = typeof DeviceMotionEvent & { requestPermission?: () => Promise<'granted' | 'denied'> };

/** Call from a click handler. Resolves the permission state without throwing. */
export async function requestMotionAccess(): Promise<MotionAccess> {
  if (typeof window === 'undefined' || typeof DeviceMotionEvent === 'undefined') return 'unavailable';
  const D = DeviceMotionEvent as DMEWithPerm;
  if (typeof D.requestPermission === 'function') {
    try { return (await D.requestPermission()) === 'granted' ? 'granted' : 'denied'; } catch { return 'denied'; }
  }
  return 'granted';
}

/** Synthetic walk for dev screenshots only (?demo=...). */
function demoSample(t: number, variant: string): MotionSample {
  const s = t / 1000;
  const fast = variant === 'fast';
  const stepHz = fast ? 2.1 : 1.35;
  const bounce = Math.sin(2 * Math.PI * stepHz * s) * (fast ? 2.4 : 1.1);
  const roll = variant === 'tilt' ? 0.33 : Math.sin(s * 0.7) * 0.05;
  const g = { x: Math.sin(roll) * 9.8, y: -Math.cos(roll) * 9.8, z: -0.8 };
  return {
    t,
    rotationRate: { alpha: Math.sin(s * 0.5) * (fast ? 60 : 8), beta: 1, gamma: 0.5 },
    acceleration: { x: 0, y: -bounce * 0.99, z: 0.1 },
    gravity: g,
  };
}

export function useWebMotion(limits: MotionLimits, active: boolean, demo?: string | null) {
  const coach = useRef(new MotionCoach(limits));
  const [reading, setReading] = useState<CoachReading>(coach.current.reading());
  const lastPaint = useRef(0);

  useEffect(() => {
    if (!active) return;
    const paint = (r: CoachReading, t: number) => {
      if (t - lastPaint.current < 100) return;
      lastPaint.current = t;
      setReading(r);
    };
    if (demo) {
      const id = window.setInterval(() => {
        const t = performance.now();
        paint(coach.current.push(demoSample(t, demo)), t);
      }, 50);
      return () => window.clearInterval(id);
    }
    const onMotion = (e: DeviceMotionEvent) => {
      const t = performance.now();
      const r = coach.current.push({
        t,
        rotationRate: e.rotationRate ? { alpha: e.rotationRate.alpha, beta: e.rotationRate.beta, gamma: e.rotationRate.gamma } : null,
        acceleration: e.acceleration ? { x: e.acceleration.x ?? 0, y: e.acceleration.y ?? 0, z: e.acceleration.z ?? 0 } : null,
        gravity: e.accelerationIncludingGravity
          ? { x: e.accelerationIncludingGravity.x ?? 0, y: e.accelerationIncludingGravity.y ?? 0, z: e.accelerationIncludingGravity.z ?? 0 }
          : null,
      });
      paint(r, t);
    };
    window.addEventListener('devicemotion', onMotion);
    return () => window.removeEventListener('devicemotion', onMotion);
  }, [active, demo]);

  const reset = useCallback(() => coach.current.reset(), []);
  const summary = useCallback(() => coach.current.summary(), []);
  return { reading, reset, summary };
}
