/**
 * Bundled guide data: the steps JSON and the 14 line diagrams ship inside the
 * app bundle (no fetch), so the guide keeps working on site with no signal
 * once the app has loaded.
 */
import raw from './data/guide.json';
import { darkDiagram, type GuideData } from './guide-model';

export const guide = raw as unknown as GuideData;

const svgs = import.meta.glob('./data/svg/*.svg', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

const dark: Record<string, string> = {};
for (const [path, svg] of Object.entries(svgs)) {
  const name = path.split('/').pop()!.replace(/\.svg$/, '');
  dark[`svg/${name}.svg`] = darkDiagram(svg, `gd-${name}`);
}

/** Dark-surface SVG markup for a step's diagram path (e.g. "svg/push-in.svg"). */
export function diagramMarkup(path: string | null): string | null {
  return path ? dark[path] ?? null : null;
}

/**
 * The guided capture camera is a separate feature. When its page is part of
 * this build, Virtual Visit steps in a job link to it; until then they read
 * as text only.
 */
const guided = import.meta.glob('../pages/GuidedCapturePage.tsx');
export const hasGuidedCapture = Object.keys(guided).length > 0;
export function guidedCaptureHref(jobId: string): string {
  return `/capture/guided?jobId=${encodeURIComponent(jobId)}&type=virtual-visit`;
}
