/**
 * Bricks GO motion primitives (Emil / Linear taste).
 * transform + opacity only; ease-out enters; honor reduce-motion.
 */
import React, { useCallback } from 'react';
import {
  Pressable,
  type GestureResponderEvent,
  type PressableProps,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import Animated, {
  FadeIn,
  FadeInDown,
  SlideInDown,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withTiming,
} from 'react-native-reanimated';

import { motion } from '../theme';

export const EASE_OUT = motion.easeOut;

const AnimatedPressable = Animated.createAnimatedComponent(Pressable);

/** Screen / form enter: opacity + slight translateY. */
export function enterFadeDown(duration = 240, distance = 8) {
  return FadeInDown.duration(duration).easing(EASE_OUT).withInitialValues({
    opacity: 0,
    transform: [{ translateY: distance }],
  });
}

export function enterFade(duration: number = motion.slow) {
  return FadeIn.duration(duration).easing(EASE_OUT);
}

export function enterSheet(duration = 300) {
  return SlideInDown.duration(duration).easing(EASE_OUT);
}

type EnterFadeDownProps = {
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  duration?: number;
  distance?: number;
};

/** Mount enter: opacity + translateY (opacity-only when reduce-motion). */
export function EnterFadeDown({
  children,
  style,
  duration = 240,
  distance = 8,
}: EnterFadeDownProps) {
  const reduced = useReducedMotion();
  const entering = reduced
    ? enterFade(Math.min(duration, 160))
    : enterFadeDown(duration, distance);
  return (
    <Animated.View entering={entering} style={style}>
      {children}
    </Animated.View>
  );
}

type SheetEnterProps = {
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
};

/** Bottom-sheet panel enter (opacity+translate); reduce-motion → fade only. */
export function SheetEnter({ children, style }: SheetEnterProps) {
  const reduced = useReducedMotion();
  const entering = reduced ? enterFade(160) : enterSheet(300);
  return (
    <Animated.View entering={entering} style={style}>
      {children}
    </Animated.View>
  );
}

type PressScaleProps = Omit<PressableProps, 'style'> & {
  style?: StyleProp<ViewStyle>;
  scaleTo?: number;
  children: React.ReactNode;
};

/**
 * Primary-action press feedback: scale ~0.97 on press-in (100ms ease-out).
 * Animates the pressable itself so flex/row button layouts stay intact.
 */
export function PressScale({
  children,
  style,
  scaleTo = motion.pressScale,
  disabled,
  onPressIn,
  onPressOut,
  ...rest
}: PressScaleProps) {
  const scale = useSharedValue(1);
  const reduced = useReducedMotion();
  const animStyle = useAnimatedStyle(() => ({
    transform: [{ scale: scale.value }],
  }));

  const handleIn = useCallback(
    (e: GestureResponderEvent) => {
      if (!disabled) {
        scale.value = withTiming(reduced ? 0.99 : scaleTo, {
          duration: reduced ? 80 : motion.press,
          easing: EASE_OUT,
        });
      }
      onPressIn?.(e);
    },
    [disabled, reduced, scale, scaleTo, onPressIn],
  );

  const handleOut = useCallback(
    (e: GestureResponderEvent) => {
      scale.value = withTiming(1, { duration: motion.release, easing: EASE_OUT });
      onPressOut?.(e);
    },
    [scale, onPressOut],
  );

  return (
    <AnimatedPressable
      disabled={disabled}
      onPressIn={handleIn}
      onPressOut={handleOut}
      android_ripple={null}
      style={[style, animStyle, reduced ? undefined : null]}
      {...rest}
    >
      {children}
    </AnimatedPressable>
  );
}
