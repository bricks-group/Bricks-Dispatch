import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { TOKENS } from '../constants/Theme';

interface Props {
  size?: number;
  color?: string;
  showWordmark?: boolean;
}

/**
 * The mark is three rectangular bricks arranged to read as a lowercase b.
 * [════════════]
 * [════════════] [═══════════]
 */
function LogoMark({ size, color }: { size: number, color: string }) {
  const h = Math.round(6 * size);
  const w1 = Math.round(12 * size);
  const w2 = Math.round(11 * size);
  const gap = Math.round(2 * size);
  const r = 1.2 * size;

  return (
    <View style={{ width: Math.round(27 * size), height: Math.round(16 * size), position: 'relative' }}>
      {/* Top Left - Stem start */}
      <View style={{ position: 'absolute', left: 0, top: 0, width: w1, height: h, borderRadius: r, backgroundColor: color }} />
      {/* Bottom Left - Stem end */}
      <View style={{ position: 'absolute', left: 0, top: h + gap, width: w1, height: h, borderRadius: r, backgroundColor: color }} />
      {/* Bottom Right - Bowl */}
      <View style={{ position: 'absolute', left: w1 + gap, top: h + gap, width: w2, height: h, borderRadius: r, backgroundColor: color }} />
    </View>
  );
}

export default function BricksWordmark({ size = 1, color = TOKENS.brick, showWordmark = true }: Props) {
  const fontSize = Math.round(17 * size);
  return (
    <View style={styles.row}>
      <LogoMark size={size} color={color} />
      {showWordmark && (
        <Text style={[styles.text, { 
          fontSize, 
          color: color === TOKENS.brick ? TOKENS.moon : color, 
          marginLeft: Math.round(fontSize * 0.4),
          letterSpacing: fontSize * -0.035
        }]}>
          bricks
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center' },
  text: { 
    fontFamily: 'GeneralSans-Medium',
    textTransform: 'lowercase'
  },
});
