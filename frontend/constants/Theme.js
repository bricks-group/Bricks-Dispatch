// Bricks Design System Tokens
// Source: @DESIGN_SYSTEM.md
import { Platform } from 'react-native';

export const TOKENS = {
  // Dark Mode (Night)
  night: '#141210',
  nightSoft: '#1C1A17',
  nightDeep: '#23201C',
  nightHair: '#F4F1EB14',   // 1px Hairline (6% ink)
  nightHairS: '#F4F1EB26',  // Strong borders
  
  // Light Mode (Paper)
  paper: '#F4F1EB',
  paperSoft: '#EDE8DF',
  paperDeep: '#E4DED2',
  
  // Text
  moon: '#F4F1EB',          // Primary Text (Dark)
  moonSoft: '#D6CFC2',      // Secondary Text
  moonMute: '#8F8678',      // Labels, Icons
  moonFaint: '#5E574C',     // Sub-labels
  
  // Accent — Terracotta
  brick: '#D2542B',
  brickDeep: '#A8401F',
  brickSoft: '#F4D9CC',
  brickTint: '#FBEFE8',
  
  // Status
  ok: '#4A6B3C',
  okTint: '#E6ECDD',
  warn: '#B8741F',
  warnTint: '#F6E8D1',
  bad: '#8C2F24',
  badTint: '#F1DAD4',
  
  // Shapes
  radius: 2,
};

export const TYPOGRAPHY = {
  heading: Platform.OS === 'ios' ? 'Helvetica Neue' : 'sans-serif-medium',
  body: Platform.OS === 'ios' ? 'Helvetica' : 'sans-serif',
  // Must match the font names registered in app/_layout.tsx via @expo-google-fonts/jetbrains-mono
  mono: 'JetBrainsMono_400Regular',
  monoMedium: 'JetBrainsMono_500Medium',
};
