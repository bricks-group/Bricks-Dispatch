/**
 * Bricks GO — single source of truth for visual tokens.
 *
 * Source: Bricks DESIGN_SYSTEM.md + bricks-ds-study tokens (colors.css, typography.css,
 * spacing.css, motion.css) and the Dispatch screens in the locked design bundle.
 * GO is DARK ALWAYS (field legibility at 6am, matte not glossy).
 *
 * Rules: no ad-hoc numbers in screens. Colours come from `color`, sizes from `type`,
 * gaps/padding from `space`, corners from `radius`, timing from `motion`.
 */
import { Easing } from 'react-native-reanimated';

/* ── Raw palette (never use directly in screens; use `color`) ── */
export const palette = {
  night: '#141210',
  nightSoft: '#1C1A17',
  nightDeep: '#23201C',
  moon: '#F4F1EB',
  moonSoft: '#D6CFC2',
  moonMute: '#8F8678',
  moonFaint: '#5E574C',
  ink: '#14110E',
  brick: '#D2542B',
  brickDeep: '#A8401F',
  brickSoft: '#F4D9CC',
  white: '#FFFFFF',
} as const;

/* ── Semantic colours · dark theme (data-theme="dark" in the web tokens) ── */
export const color = {
  bg: palette.night,               // page
  surface: palette.nightSoft,      // raised card / hero
  surfaceDeep: palette.nightDeep,  // nested fill, neutral pill, input well
  hair: '#F4F1EB14',               // 1px hairline, 8% moon
  hairStrong: '#F4F1EB26',         // selected / focused / hero border
  graphite: '#4A453E',             // 1px outline on secondary buttons
  graphitePressed: '#6B645A',      // secondary outline while pressed

  text: palette.moon,              // headings, values
  textBody: palette.moonSoft,      // paragraph copy
  textMuted: '#9E9484',            // labels, icons (lifted for legibility on night)
  textFaint: '#837A6A',            // meta, timestamps (lifted)
  textOnAccent: palette.white,
  textInverse: palette.night,      // text on a moon fill

  accent: palette.brick,
  accentPressed: palette.brickDeep,
  accentText: palette.brick,       // DS dark: accent-text = brick
  accentTint: 'rgba(210, 84, 43, 0.15)',
  accentSoft: 'rgba(210, 84, 43, 0.30)',

  ok: '#6FAB5E',
  okTint: 'rgba(111, 171, 94, 0.15)',
  warn: '#D4903A',
  warnTint: 'rgba(212, 144, 58, 0.15)',
  bad: '#C45547',
  badTint: 'rgba(196, 85, 71, 0.15)',

  scrim: 'rgba(0, 0, 0, 0.6)',
  inverse: palette.moon,           // moon fill (selected day cell)
} as const;

/* ── Font families: one family name per weight so Android and iOS match the web ── */
export const font = {
  regular: 'GeneralSans-Regular',
  medium: 'GeneralSans-Medium',
  semibold: 'GeneralSans-Semibold',
  mono: 'IoskeleyMono-Regular',
  monoMedium: 'IoskeleyMono-Medium',
} as const;

/** expo-font map — load these in the root layout before first paint. */
export const fontAssets = {
  [font.regular]: require('../assets/fonts/GeneralSans-Regular.otf'),
  [font.medium]: require('../assets/fonts/GeneralSans-Medium.otf'),
  [font.semibold]: require('../assets/fonts/GeneralSans-Semibold.otf'),
  [font.mono]: require('../assets/fonts/IoskeleyMono-Regular.ttf'),
  [font.monoMedium]: require('../assets/fonts/IoskeleyMono-Medium.ttf'),
};

/* ── Type scale (DS: 28 display · 22 h3 · 18 h4 · 15 body · 13 small · 12 caption;
      mono 36 KPI · 13 data · 12 data-sm · 10.5 overline). Headings tracked negative. ── */
export const type = {
  display:  { fontFamily: font.medium,  fontSize: 28, lineHeight: 34, letterSpacing: -0.6 },
  title:    { fontFamily: font.medium,  fontSize: 22, lineHeight: 28, letterSpacing: -0.45 },
  headline: { fontFamily: font.medium,  fontSize: 18, lineHeight: 24, letterSpacing: -0.3 },
  bodyLg:   { fontFamily: font.regular, fontSize: 17, lineHeight: 24 },
  body:     { fontFamily: font.regular, fontSize: 15, lineHeight: 22 },
  bodyMedium: { fontFamily: font.medium, fontSize: 15, lineHeight: 22 },
  callout:  { fontFamily: font.medium,  fontSize: 14, lineHeight: 20 },   // buttons, row titles
  bodySm:   { fontFamily: font.regular, fontSize: 13, lineHeight: 19 },
  caption:  { fontFamily: font.regular, fontSize: 12, lineHeight: 16 },
  // Section labels / eyebrows: quiet sentence-case grotesque (was mono caps). Mono stays for pills, times, figures.
  overline: { fontFamily: font.medium, fontSize: 13, lineHeight: 18, letterSpacing: 0 },
  tabLabel: { fontFamily: font.medium, fontSize: 11, lineHeight: 13, letterSpacing: 0 },
  pill:     { fontFamily: font.monoMedium, fontSize: 10, lineHeight: 12, letterSpacing: 1.4, textTransform: 'uppercase' as const },
  kpi:      { fontFamily: font.mono, fontSize: 36, lineHeight: 40, letterSpacing: -1 },
  kpiSm:    { fontFamily: font.mono, fontSize: 24, lineHeight: 28, letterSpacing: -0.5 },
  dataLg:   { fontFamily: font.mono, fontSize: 17, lineHeight: 22 },
  dataMd:   { fontFamily: font.mono, fontSize: 15, lineHeight: 20 },
  data:     { fontFamily: font.mono, fontSize: 13, lineHeight: 18 },
  dataSm:   { fontFamily: font.mono, fontSize: 12, lineHeight: 16 },
} as const;

/* ── Spacing scale — DS: 4 · 6 · 8 · 10 · 12 · 14 · 16 · 18 · 20 · 24 · 28 · 32 · 36 · 48 ── */
export const space = {
  4: 4, 6: 6, 8: 8, 10: 10, 12: 12, 14: 14, 16: 16, 18: 18,
  20: 20, 24: 24, 28: 28, 32: 32, 36: 36, 48: 48,
} as const;

/* ── Shape ── */
export const radius = { sm: 2, pill: 999 } as const;   // 2px everywhere. pill = tiny non-interactive status dots only
export const hairline = 1;

/* ── Layout ── */
export const layout = {
  gutter: space[20],      // screen side padding
  section: space[28],     // gap between sections
  tap: 44,                // minimum tap target (pt)
  control: 48,            // button / input height
  rowMin: 52,             // list row minimum height
  icon: 18,               // default icon size
  iconStroke: 1.5,        // hairline icon stroke; 1.75 for active nav
} as const;

/* ── Motion — response, not arrival. Never ease-in; UI under 300ms. ── */
export const motion = {
  press: 100,             // press-in scale
  release: 140,           // press-out
  fast: 120,
  base: 180,
  slow: 200,
  pressScale: 0.97,
  easeOut: Easing.bezier(0.23, 1, 0.32, 1),
  easeInOut: Easing.bezier(0.77, 0, 0.175, 1),
} as const;

export type Tone = 'neutral' | 'accent' | 'ok' | 'warn' | 'bad';
export const toneColors: Record<Tone, { bg: string; fg: string }> = {
  neutral: { bg: color.surfaceDeep, fg: color.textBody },
  accent: { bg: color.accentTint, fg: color.accentText },
  ok: { bg: color.okTint, fg: color.ok },
  warn: { bg: color.warnTint, fg: color.warn },
  bad: { bg: color.badTint, fg: color.bad },
};
