import React from 'react';
import { StyleSheet, View, Text } from 'react-native';
import { T } from '../../../constants/Theme';

// Bricks UI Components for React Native (Dispatch)
// Dark mode by default

// Bricks UI Components for React Native (Dispatch)
// Dark mode by default

const F = {
  display: 'System', 
  mono: 'System',
};

export const I = ({ name, size = 18, color = '#F4F1EB', sw = 1.5, style = {} }) => {
  // Simple icon placeholder using text for now, should be replaced with react-native-svg
  return <Text style={[{ fontSize: size, color, ...style }]}>{name}</Text>;
};

export const Over = ({ children, color = '#8F8678', style = {} }) => (
  <Text style={[styles.over, { color, ...style }]}>{children.toString().toUpperCase()}</Text>
);

export const Pill = ({ children, tone = 'neutral', style = {} }) => {
  const map = {
    brick: { bg: '#D2542B33', fg: '#F4D9CC' },
    ok: { bg: '#4A6B3C33', fg: '#B8CC9E' },
    warn: { bg: '#B8741F33', fg: '#E6B477' },
    neutral: { bg: '#23201C', fg: '#D6CFC2' },
  };
  const c = map[tone] || map.neutral;
  return (
    <View style={[styles.pill, { backgroundColor: c.bg, ...style }]}>
      <Text style={[styles.pillText, { color: c.fg }]}>{children.toString().toUpperCase()}</Text>
    </View>
  );
};

const styles = StyleSheet.create({
  over: {
    fontFamily: F.mono,
    fontSize: 10,
    letterSpacing: 1.8,
  },
  pill: {
    paddingHorizontal: 9,
    paddingVertical: 5,
    borderRadius: 2,
  },
  pillText: {
    fontFamily: F.mono,
    fontSize: 10,
    letterSpacing: 1.4,
  },
});
