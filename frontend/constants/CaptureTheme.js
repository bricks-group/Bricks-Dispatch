// Capture flow tokens — port from Bricks-Capture/mobile/src/constants/theme.ts,
// mapped onto Dispatch's existing TOKENS so there's one source of truth.
import { TOKENS } from './Theme';

export const COLORS = {
  brick: TOKENS.brick,
  brickDeep: TOKENS.brickDeep,
  ink: TOKENS.ink,
  ink2: TOKENS.inkSoft,
  ink3: TOKENS.inkMute,
  paper: TOKENS.paper,
  paperSoft: TOKENS.paperSoft,
  paperDeep: TOKENS.paperDeep,
  hair: 'rgba(244, 241, 235, 0.08)',
  bad: TOKENS.bad,
  ok: TOKENS.ok,
  warn: TOKENS.warn,
};

export const FONTS = {
  body: 'GeneralSans-Medium',
  // Dispatch doesn't ship Semibold — map to Medium (visual diff is minor for these labels)
  bodySemibold: 'GeneralSans-Medium',
  mono: 'IoskeleyMono-Regular',
  monoMedium: 'IoskeleyMono-Medium',
};
