import React, { useRef, useEffect } from 'react';
import { View, Text, StyleSheet, Pressable, Animated, Easing } from 'react-native';
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
  b1: Animated.Value; b2: Animated.Value; b3: Animated.Value;
}) {
  const h     = Math.round(7 * size);
  const wTop  = Math.round(11 * size);
  const wBotL = Math.round(6 * size);
  const wBotR = Math.round(10 * size);
  const rGap  = Math.round(2 * size);
  const cGap  = Math.max(1, Math.round(size));
  const r     = 1.2 * size;

  return (
    <View style={{ width: wBotL + cGap + wBotR, height: h + rGap + h, position: 'relative' }}>
      <Animated.View style={[
        { position: 'absolute', left: 0, top: 0, width: wTop, height: h, borderRadius: r, backgroundColor: color },
        { transform: [{ translateY: b1 }] },
      ]} />
      <Animated.View style={[
        { position: 'absolute', left: 0, top: h + rGap, width: wBotL, height: h, borderRadius: r, backgroundColor: color },
        { transform: [{ translateY: b2 }] },
      ]} />
      <Animated.View style={[
        { position: 'absolute', left: wBotL + cGap, top: h + rGap, width: wBotR, height: h, borderRadius: r, backgroundColor: color },
        { transform: [{ translateY: b3 }] },
      ]} />
    </View>
  );
}

// Brick mark spinning on Y axis — used as loading indicator
export function BrickLoader({ size = 2.5, color = TOKENS.brick }: { size?: number; color?: string }) {
  const rot = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    const anim = Animated.loop(
      Animated.timing(rot, { toValue: 1, duration: 1600, easing: Easing.linear, useNativeDriver: true })
    );
    anim.start();
    return () => anim.stop();
  }, []);

  const rotateY = rot.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });
  const h     = Math.round(7 * size);
  const wTop  = Math.round(11 * size);
  const wBotL = Math.round(6 * size);
  const wBotR = Math.round(10 * size);
  const rGap  = Math.round(2 * size);
  const cGap  = Math.max(1, Math.round(size));
  const r     = 1.2 * size;

  return (
    <Animated.View style={{ transform: [{ perspective: 400 }, { rotateY }] }}>
      <View style={{ width: wBotL + cGap + wBotR, height: h + rGap + h, position: 'relative' }}>
        <View style={{ position: 'absolute', left: 0, top: 0, width: wTop, height: h, borderRadius: r, backgroundColor: color }} />
        <View style={{ position: 'absolute', left: 0, top: h + rGap, width: wBotL, height: h, borderRadius: r, backgroundColor: color }} />
        <View style={{ position: 'absolute', left: wBotL + cGap, top: h + rGap, width: wBotR, height: h, borderRadius: r, backgroundColor: color }} />
      </View>
    </Animated.View>
  );
}

export default function BricksWordmark({ size = 1, color = TOKENS.brick, showWordmark = true, pressable = false }: Props) {
  const fontSize = Math.round(17 * size);

  const b1 = useRef(new Animated.Value(0)).current;
  const b2 = useRef(new Animated.Value(0)).current;
  const b3 = useRef(new Animated.Value(0)).current;

  const pop = (val: Animated.Value) => Animated.sequence([
    Animated.timing(val, { toValue: -3 * size, duration: 200, easing: Easing.out(Easing.ease), useNativeDriver: true }),
    Animated.timing(val, { toValue: 0, duration: 300, easing: Easing.out(Easing.ease), useNativeDriver: true }),
  ]);

  const triggerBounce = () => {
    Animated.stagger(70, [pop(b1), pop(b2), pop(b3)]).start();
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
