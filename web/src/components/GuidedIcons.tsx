/** Thin line icons for guided capture (1.5 stroke, round caps; lucide geometry). */
import type { ReactNode } from 'react';
import type { StepIcon } from '../lib/guided-capture/guides';

type P = { size?: number; className?: string };
function Svg({ size = 20, className, children }: P & { children: ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}
      strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">{children}</svg>
  );
}

export const IconX = (p: P) => <Svg {...p}><path d="M18 6 6 18M6 6l12 12" /></Svg>;
export const IconBack = (p: P) => <Svg {...p}><path d="m15 18-6-6 6-6" /></Svg>;
export const IconCheck = (p: P) => <Svg {...p}><path d="M20 6 9 17l-5-5" /></Svg>;
export const IconLock = (p: P) => <Svg {...p}><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></Svg>;
export const IconUnlock = (p: P) => <Svg {...p}><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 7.8-1.2" /></Svg>;
export const IconPhone = (p: P) => <Svg {...p}><rect x="6" y="2" width="12" height="20" rx="2" /><path d="M11 18h2" /></Svg>;
export const IconDoor = (p: P) => <Svg {...p}><path d="M13 4h3a2 2 0 0 1 2 2v14M2 20h3M13 20h9M10 12v.01" /><path d="M13 4.6v16.1a1 1 0 0 1-1.2 1L5 20V5.6a2 2 0 0 1 1.5-1.9l4-1A2 2 0 0 1 13 4.6Z" /></Svg>;
export const IconWalk = (p: P) => <Svg {...p}><circle cx="13" cy="4" r="1.6" /><path d="m9 21 2.5-6 2.5 2v4M8 12l2-4 3 1 2 3 3 1M11.5 15 12 9" /></Svg>;
export const IconPause = (p: P) => <Svg {...p}><rect x="6" y="4" width="4" height="16" rx="1" /><rect x="14" y="4" width="4" height="16" rx="1" /></Svg>;
export const IconTurn = (p: P) => <Svg {...p}><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" /><path d="M3 3v5h5" /></Svg>;
export const IconSun = (p: P) => <Svg {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></Svg>;
export const IconLevel = (p: P) => <Svg {...p}><path d="M2 12h6M16 12h6" /><circle cx="12" cy="12" r="3" /></Svg>;
export const IconCamera = (p: P) => <Svg {...p}><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3Z" /><circle cx="12" cy="13" r="3" /></Svg>;
export const IconUpload = (p: P) => <Svg {...p}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" /></Svg>;
export const IconAlert = (p: P) => <Svg {...p}><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></Svg>;
export const IconList = (p: P) => <Svg {...p}><path d="M9 6h12M9 12h12M9 18h12M4 6h.01M4 12h.01M4 18h.01" /></Svg>;
export const IconLens = (p: P) => <Svg {...p}><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="4" /></Svg>;

export function StepGlyph({ icon, size = 20 }: { icon: StepIcon; size?: number }) {
  switch (icon) {
    case 'door': return <IconDoor size={size} />;
    case 'phone': return <IconPhone size={size} />;
    case 'walk': return <IconWalk size={size} />;
    case 'pause': return <IconPause size={size} />;
    case 'turn': return <IconTurn size={size} />;
    case 'sun': return <IconSun size={size} />;
    case 'level': return <IconLevel size={size} />;
    case 'lock': return <IconLock size={size} />;
    default: return <IconCheck size={size} />;
  }
}
