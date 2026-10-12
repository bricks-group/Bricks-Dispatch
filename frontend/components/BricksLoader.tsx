// Bricks loader (React Native) — the filled brand mark (bricks-mark.svg geometry, 24-unit grid) built from
// plain Views so it needs no native SVG module (OTA-safe). Two motions, same timing as web:
//   variant="assemble": splash / full-screen / page loads. Bricks set in (80ms stagger), hold, lift away. 2.0s loop.
//   variant="cycle":    buttons, inline, small. Mark stays intact; each brick dims in turn (90ms stagger). 1.8s loop.
// One UI-thread clock drives every brick (Reanimated worklets). Rest frame = the exact static mark.
// Reduced motion: static mark with a gentle opacity pulse.
// `startAtHold` begins on the fully set mark — used for the native splash -> JS handoff.
// Shared across Bricks Expo apps — keep copies identical (source: ship/loader-rollout/src).
import { useEffect } from 'react';
import { View, type ViewStyle, type StyleProp } from 'react-native';
import Animated, {
  Easing, cancelAnimation, useAnimatedStyle, useReducedMotion, useSharedValue,
  withRepeat, withSequence, withTiming, type SharedValue,
} from 'react-native-reanimated';

const BRICK = '#D2542B';
const GEO = [
  { x: 3, y: 4, w: 11, h: 6.4, a: 0.82 },
  { x: 3, y: 11.6, w: 6, h: 8.4, a: 1 },
  { x: 10.2, y: 11.6, w: 10.8, h: 8.4, a: 1 },
] as const;

const OUT = Easing.bezierFn(0.16, 1, 0.3, 1);
const IN = Easing.bezierFn(0.55, 0, 0.75, 0.2);
const INOUT = Easing.bezierFn(0.33, 0, 0.2, 1);

const A_PERIOD = 2000, A_STAGGER = 80;
const C_PERIOD = 1800, C_STAGGER = 90;
const A_HOLD_START = 600; // all three bricks set by 580ms

function withAlpha(c: string, a: number) {
  if (a >= 1) return c;
  const m = /^#([0-9a-f]{6})$/i.exec(c);
  if (!m) return c;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

function Brick({ i, t, unit, color, variant, still }: {
  i: number; t: SharedValue<number>; unit: number; color: string; variant: 'assemble' | 'cycle'; still: boolean;
}) {
  const g = GEO[i];
  const style = useAnimatedStyle(() => {
    if (still) return { opacity: 1, transform: [{ translateY: 0 }] };
    if (variant === 'assemble') {
      const lt = (((t.value - i * A_STAGGER) % A_PERIOD) + A_PERIOD) % A_PERIOD;
      const p = lt / A_PERIOD;
      let op = 0, ty = -4;
      if (p < 0.21) { const e = OUT(p / 0.21); op = e; ty = -5 * (1 - e); }
      else if (p < 0.55) { op = 1; ty = 0; }
      else if (p < 0.72) { const e = IN((p - 0.55) / 0.17); op = 1 - e; ty = -4 * e; }
      return { opacity: op, transform: [{ translateY: ty * unit }] };
    }
    const lt = (((t.value - i * C_STAGGER) % C_PERIOD) + C_PERIOD) % C_PERIOD;
    const p = lt / C_PERIOD;
    let op = 1;
    if (p < 0.12) op = 1 - 0.72 * OUT(p / 0.12);
    else if (p < 0.36) op = 0.28 + 0.72 * INOUT((p - 0.12) / 0.24);
    return { opacity: op, transform: [{ translateY: 0 }] };
  });
  return (
    <Animated.View
      style={[{
        position: 'absolute', left: g.x * unit, top: g.y * unit, width: g.w * unit, height: g.h * unit,
        borderRadius: 0.8 * unit, backgroundColor: withAlpha(color, g.a),
      }, style]}
    />
  );
}

export type BricksLoaderProps = {
  variant?: 'assemble' | 'cycle';
  size?: number;
  color?: string;
  startAtHold?: boolean;
  style?: StyleProp<ViewStyle>;
  accessibilityLabel?: string;
  testID?: string;
};

export default function BricksLoader({
  variant = 'cycle', size = 18, color = BRICK, startAtHold = false, style, accessibilityLabel = 'Loading', testID,
}: BricksLoaderProps) {
  const reduced = useReducedMotion();
  const period = variant === 'assemble' ? A_PERIOD : C_PERIOD;
  const t = useSharedValue(variant === 'assemble' && startAtHold ? A_HOLD_START : 0);
  const pulse = useSharedValue(1);

  useEffect(() => {
    if (reduced) {
      pulse.value = withRepeat(withTiming(0.6, { duration: 1000, easing: Easing.inOut(Easing.ease) }), -1, true);
      return () => cancelAnimation(pulse);
    }
    const start = t.value;
    const loop = withRepeat(withSequence(withTiming(0, { duration: 0 }), withTiming(period, { duration: period, easing: Easing.linear })), -1);
    t.value = start > 0
      ? withSequence(withTiming(period, { duration: period - start, easing: Easing.linear }), loop)
      : loop;
    return () => cancelAnimation(t);
  }, [reduced, period]);

  const pulseStyle = useAnimatedStyle(() => ({ opacity: pulse.value }));
  const unit = size / 24;
  return (
    <Animated.View
      accessible accessibilityRole="progressbar" accessibilityLabel={accessibilityLabel} testID={testID}
      style={[{ width: size, height: size }, pulseStyle, style]}
    >
      <View style={{ width: size, height: size }} pointerEvents="none">
        {GEO.map((_, i) => (
          <Brick key={i} i={i} t={t} unit={unit} color={color} variant={variant} still={!!reduced} />
        ))}
      </View>
    </Animated.View>
  );
}
