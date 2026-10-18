/**
 * Native motion feed for the guided camera: expo-sensors DeviceMotion → MotionCoach.
 * Samples at 50 Hz (walk cadence needs it), repaints at 10 Hz. Advisory only.
 */
import { useEffect, useRef, useState } from 'react';
import { DeviceMotion, type DeviceMotionMeasurement } from 'expo-sensors';
import { MotionCoach, type CoachReading } from './motion';
import type { MotionLimits } from './guides';

const SAMPLE_MS = 20;
const PAINT_MS = 100;

export function useNativeMotion(limits: MotionLimits, active: boolean) {
  const coach = useRef(new MotionCoach(limits));
  const [reading, setReading] = useState<CoachReading>(coach.current.reading());
  const [available, setAvailable] = useState<boolean | null>(null);

  useEffect(() => {
    if (!active) return;
    let sub: { remove: () => void } | null = null;
    let paint: ReturnType<typeof setInterval> | null = null;
    let cancelled = false;
    (async () => {
      const ok = await DeviceMotion.isAvailableAsync().catch(() => false);
      if (cancelled) return;
      setAvailable(ok);
      if (!ok) return;
      const perm = await DeviceMotion.requestPermissionsAsync().catch(() => null);
      if (cancelled || (perm && !perm.granted)) { setAvailable(false); return; }
      DeviceMotion.setUpdateInterval(SAMPLE_MS);
      sub = DeviceMotion.addListener((m: DeviceMotionMeasurement) => {
        coach.current.push({
          t: Date.now(),
          rotationRate: m.rotationRate, // expo-sensors: °/s on both platforms
          acceleration: m.acceleration,
          gravity: m.accelerationIncludingGravity,
        });
      });
      paint = setInterval(() => setReading({ ...coach.current.reading() }), PAINT_MS);
    })();
    return () => {
      cancelled = true;
      sub?.remove();
      if (paint) clearInterval(paint);
    };
  }, [active]);

  return {
    reading,
    available,
    reset: () => coach.current.reset(),
    summary: () => coach.current.summary(),
  };
}
