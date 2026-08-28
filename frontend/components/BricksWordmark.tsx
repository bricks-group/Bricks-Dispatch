import React, { useEffect } from 'react';
import { View, Text, StyleSheet, Pressable } from 'react-native';
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withTiming,
  withSequence,
  withDelay,
  withRepeat,
  cancelAnimation,
  Easing,
  SharedValue,
} from 'react-native-reanimated';
import { TOKENS } from '../constants/Theme';

interface Props {
  size?: number;
  color?: string;
  showWordmark?: boolean;
  pressable?: boolean;
}

// Proportions match the dispatch layout (viewBox-equivalent 27×16 with 2px gaps)
function AnimatedLogoMark({ size, color, b1, b2, b3 }: {
  size: number; color: string;
  b1: SharedValue<number>;
  b2: SharedValue<number>;
  b3: SharedValue<number>;
}) {
  const h     = Math.round(7 * size);
  const wTop  = Math.round(11 * size);
  const wBotL = Math.round(6 * size);
  const wBotR = Math.round(10 * size);
  const rGap  = Math.round(2 * size);
  const cGap  = Math.max(1, Math.round(size));
  const r     = 1.2 * size;

  const style1 = useAnimatedStyle(() => ({ transform: [{ translateY: b1.value }] }));
  const style2 = useAnimatedStyle(() => ({ transform: [{ translateY: b2.value }] }));
  const style3 = useAnimatedStyle(() => ({ transform: [{ translateY: b3.value }] }));

  return (
    <View style={{ width: wBotL + cGap + wBotR, height: h + rGap + h, position: 'relative' }}>
      <Animated.View style={[
        { position: 'absolute', left: 0, top: 0, width: wTop, height: h, borderRadius: r, backgroundColor: color },
        style1,
      ]} />
      <Animated.View style={[
        { position: 'absolute', left: 0, top: h + rGap, width: wBotL, height: h, borderRadius: r, backgroundColor: color },
        style2,
      ]} />
      <Animated.View style={[
        { position: 'absolute', left: wBotL + cGap, top: h + rGap, width: wBotR, height: h, borderRadius: r, backgroundColor: color },
        style3,
      ]} />
    </View>
  );
}

// Brick mark bounce — same hop as the wordmark, looping, for loading
export function BrickLoader({ size = 2.5, color = TOKENS.brick }: { size?: number; color?: string }) {
  const b1 = useSharedValue(0);
  const b2 = useSharedValue(0);
  const b3 = useSharedValue(0);

  useEffect(() => {
    const hop = (sv: SharedValue<number>, delay: number) => {
      sv.value = withDelay(
        delay,
        withRepeat(
          withSequence(
            withTiming(-6 * (size / 2.5), { duration: 200, easing: Easing.out(Easing.ease) }),
            withTiming(0, { duration: 300, easing: Easing.out(Easing.ease) }),
            withTiming(0, { duration: 220 }),
          ),
          -1,
        ),
      );
    };
    hop(b1, 0);
    hop(b2, 70);
    hop(b3, 140);
    return () => {
      cancelAnimation(b1);
      cancelAnimation(b2);
      cancelAnimation(b3);
    };
  }, [size]);

  const h     = Math.round(7 * size);
  const wTop  = Math.round(11 * size);
  const wBotL = Math.round(6 * size);
  const wBotR = Math.round(10 * size);
  const rGap  = Math.round(2 * size);
  const cGap  = Math.max(1, Math.round(size));
  const r     = 1.2 * size;

  const s1 = useAnimatedStyle(() => ({ transform: [{ translateY: b1.value }] }));
  const s2 = useAnimatedStyle(() => ({ transform: [{ translateY: b2.value }] }));
  const s3 = useAnimatedStyle(() => ({ transform: [{ translateY: b3.value }] }));

  return (
    <View style={{ width: wBotL + cGap + wBotR, height: h + rGap + h + 8, position: 'relative' }}>
      <Animated.View style={[{ position: 'absolute', left: 0, top: 8, width: wTop, height: h, borderRadius: r, backgroundColor: color }, s1]} />
      <Animated.View style={[{ position: 'absolute', left: 0, top: 8 + h + rGap, width: wBotL, height: h, borderRadius: r, backgroundColor: color }, s2]} />
      <Animated.View style={[{ position: 'absolute', left: wBotL + cGap, top: 8 + h + rGap, width: wBotR, height: h, borderRadius: r, backgroundColor: color }, s3]} />
    </View>
  );
}

export default function BricksWordmark({ size = 1, color = TOKENS.brick, showWordmark = true, pressable = false }: Props) {
  const fontSize = Math.round(17 * size);

  const b1 = useSharedValue(0);
  const b2 = useSharedValue(0);
  const b3 = useSharedValue(0);

  const brickPop = (sv: SharedValue<number>, delay: number) => {
    sv.value = withDelay(delay, withSequence(
      withTiming(-3 * size, { duration: 200, easing: Easing.out(Easing.ease) }),
      withTiming(0, { duration: 300, easing: Easing.out(Easing.ease) }),
    ));
  };

  const triggerBounce = () => {
    brickPop(b1, 0);
    brickPop(b2, 70);
    brickPop(b3, 140);
  };

  const inner = (
    <View style={styles.row}>
      <AnimatedLogoMark size={size} color={color} b1={b1} b2={b2} b3={b3} />
      {showWordmark && (
        <Text style={[styles.text, {
          fontSize,
          color: color === TOKENS.brick ? TOKENS.moon : color,
          marginLeft: Math.round(fontSize * 0.4),
          letterSpacing: fontSize * -0.035,
        }]}>
          bricks
        </Text>
      )}
    </View>
  );

  if (pressable) {
    return <Pressable onPress={triggerBounce} hitSlop={8}>{inner}</Pressable>;
  }
  return inner;
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center' },
  text: {
    fontFamily: 'GeneralSans-Medium',
    textTransform: 'lowercase',
  },
});
