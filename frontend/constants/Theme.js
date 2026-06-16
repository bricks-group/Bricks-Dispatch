// Bricks Design System Tokens
// Source: @DESIGN_SYSTEM.md
import { Platform } from 'react-native';

export const TOKENS = {
  // Dark Mode (Night)
  night: '#141210',
  nightSoft: '#1C1A17',
  nightDeep: '#23201C',
  nightHair: '#F4F1EB14',   // hairline border on dark
  nightHairS: '#F4F1EB26',  // strong border / focus on dark

  // Light Mode (Paper)
  paper: '#F4F1EB',
  paperSoft: '#EDE8DF',
  paperDeep: '#E4DED2',
  hair: '#1411100F',        // hairline border on light
  hairStrong: '#14111020',  // strong border / focus on light

  // Text — light scale
  ink: '#14110E',
  inkSoft: '#3B332A',
  inkMute: '#6B6054',
  inkFaint: '#A39684',

  // Text — dark scale
  moon: '#F4F1EB',
  moonSoft: '#D6CFC2',
  moonMute: '#8F8678',
  moonFaint: '#5E574C',

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
  heading: 'GeneralSans-Medium',
  headingLight: 'GeneralSans-Regular',
  body: 'GeneralSans-Regular',
  bodyMedium: 'GeneralSans-Medium',
  mono: 'IoskeleyMono-Regular',
  monoMedium: 'IoskeleyMono-Medium',
};
