// Capture flow tokens. GO is dark always, so the capture screens map onto the
// same semantic dark palette as the rest of the app (../theme). The old names are
// kept so the capture pipeline screens don't need logic changes.
import { color, font } from '../theme';

export const COLORS = {
  brick: color.accent,
  brickDeep: color.accentPressed,
  ink: color.text,            // primary text
  ink2: color.textBody,       // body text
  ink3: color.textMuted,      // labels / meta
  paper: color.bg,            // page
  paperSoft: color.surface,   // raised / input well
  paperDeep: color.surfaceDeep,
  hair: color.hair,
  bad: color.bad,
  ok: color.ok,
  warn: color.warn,
};

export const FONTS = {
  body: font.regular,
  bodySemibold: font.medium,
  mono: font.mono,
  monoMedium: font.monoMedium,
};
