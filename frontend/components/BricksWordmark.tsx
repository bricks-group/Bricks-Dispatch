import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { TOKENS } from '../constants/Theme';

interface Props {
  size?: number;
  color?: string;
  showWordmark?: boolean;
}

function LogoMark({ size, color }: { size: number, color: string }) {
  const h     = Math.round(7 * size);
  const wTop  = Math.round(11 * size);
  const wBotL = Math.round(6 * size);
  const wBotR = Math.round(10 * size);
  const rGap  = Math.round(2 * size);
  const cGap  = Math.max(1, Math.round(size));
  const r     = 1.2 * size;

  return (
    <View style={{ width: wBotL + cGap + wBotR, height: h + rGap + h, position: 'relative' }}>
      <View style={{ position: 'absolute', left: 0, top: 0, width: wTop, height: h, borderRadius: r, backgroundColor: color }} />
      <View style={{ position: 'absolute', left: 0, top: h + rGap, width: wBotL, height: h, borderRadius: r, backgroundColor: color }} />
      <View style={{ position: 'absolute', left: wBotL + cGap, top: h + rGap, width: wBotR, height: h, borderRadius: r, backgroundColor: color }} />
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
