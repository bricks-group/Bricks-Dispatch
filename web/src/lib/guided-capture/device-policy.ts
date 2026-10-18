/**
 * Approved phones for Virtual Visit capture (policy approved by Kam):
 *   iPhone 13 Pro or newer · Pixel 8 or newer · Galaxy S23 or newer.
 * Film clips and stills use dedicated cameras, never phones.
 *
 * "Newer" means a later generation that still has an ultra-wide (0.5×) lens, because the
 * guide records on the ultra-wide. Same-generation non-Pro iPhones (13, 13 mini) and phones
 * without an ultra-wide (iPhone SE, 16e, Air) are not approved.
 *
 * The check is a soft block: the vendor sees why and can still continue; the recording is
 * tagged so QC knows. Pure (mirrored into frontend/lib, see guides.ts).
 */

export type PolicyStatus = 'approved' | 'not-approved' | 'unknown';
export interface DeviceInfo {
  os: 'ios' | 'android' | 'other';
  /** Hardware id: iOS "iPhone16,1", Android Build.MODEL "SM-S921B" / "Pixel 8". */
  modelId?: string | null;
  /** Marketing name when known: "iPhone 15 Pro", "Pixel 8", "Galaxy S24". */
  modelName?: string | null;
  manufacturer?: string | null;
}
export interface PolicyResult {
  status: PolicyStatus;
  /** Short readable model label, e.g. "iPhone 15 Pro". */
  label: string;
  reason: string;
}

export const POLICY_LINE = 'iPhone 13 Pro or newer, Pixel 8 or newer, or Galaxy S23 or newer.';

/** iPhone hardware ids from the 13 Pro generation on (iPhone14,x = 2021). */
const IPHONE_IDS: Record<string, { name: string; ok: boolean; why?: string }> = {
  'iPhone14,2': { name: 'iPhone 13 Pro', ok: true },
  'iPhone14,3': { name: 'iPhone 13 Pro Max', ok: true },
  'iPhone14,4': { name: 'iPhone 13 mini', ok: false, why: 'Virtual visits need an iPhone 13 Pro or newer.' },
  'iPhone14,5': { name: 'iPhone 13', ok: false, why: 'Virtual visits need an iPhone 13 Pro or newer.' },
  'iPhone14,6': { name: 'iPhone SE', ok: false, why: 'This iPhone has no ultra-wide lens.' },
  'iPhone14,7': { name: 'iPhone 14', ok: true },
  'iPhone14,8': { name: 'iPhone 14 Plus', ok: true },
  'iPhone15,2': { name: 'iPhone 14 Pro', ok: true },
  'iPhone15,3': { name: 'iPhone 14 Pro Max', ok: true },
  'iPhone15,4': { name: 'iPhone 15', ok: true },
  'iPhone15,5': { name: 'iPhone 15 Plus', ok: true },
  'iPhone16,1': { name: 'iPhone 15 Pro', ok: true },
  'iPhone16,2': { name: 'iPhone 15 Pro Max', ok: true },
  'iPhone17,1': { name: 'iPhone 16 Pro', ok: true },
  'iPhone17,2': { name: 'iPhone 16 Pro Max', ok: true },
  'iPhone17,3': { name: 'iPhone 16', ok: true },
  'iPhone17,4': { name: 'iPhone 16 Plus', ok: true },
  'iPhone17,5': { name: 'iPhone 16e', ok: false, why: 'This iPhone has no ultra-wide lens.' },
  'iPhone18,1': { name: 'iPhone 17 Pro', ok: true },
  'iPhone18,2': { name: 'iPhone 17 Pro Max', ok: true },
  'iPhone18,3': { name: 'iPhone 17', ok: true },
  'iPhone18,4': { name: 'iPhone Air', ok: false, why: 'This iPhone has no ultra-wide lens.' },
};

const NOT_APPROVED = `Virtual visits need an approved phone: ${POLICY_LINE}`;

function iphoneFromName(name: string): PolicyResult | null {
  const m = /iphone\s*(\d{1,2})\s*(pro max|pro|plus|mini|e)?/i.exec(name);
  if (!m) {
    if (/iphone\s*(se|air)/i.test(name)) return { status: 'not-approved', label: name.trim(), reason: 'This iPhone has no ultra-wide lens.' };
    return null;
  }
  const gen = Number(m[1]);
  const variant = (m[2] || '').toLowerCase();
  const label = name.trim();
  if (variant === 'e') return { status: 'not-approved', label, reason: 'This iPhone has no ultra-wide lens.' };
  if (gen > 13) return { status: 'approved', label, reason: 'Approved for virtual visits.' };
  if (gen === 13 && variant.startsWith('pro')) return { status: 'approved', label, reason: 'Approved for virtual visits.' };
  return { status: 'not-approved', label, reason: NOT_APPROVED };
}

function pixel(name: string): PolicyResult | null {
  const m = /pixel\s*(\d{1,2})/i.exec(name);
  if (!m) return /pixel\s*fold/i.test(name) ? { status: 'not-approved', label: name.trim(), reason: NOT_APPROVED } : null;
  const gen = Number(m[1]);
  return gen >= 8
    ? { status: 'approved', label: name.trim(), reason: 'Approved for virtual visits.' }
    : { status: 'not-approved', label: name.trim(), reason: NOT_APPROVED };
}

/** Samsung Galaxy S: SM-S9{g}x (S23 = SM-S91x, S24 = SM-S92x …) and FE SM-S7{g}1. */
function galaxy(id: string, name: string): PolicyResult | null {
  const code = /SM-S(9|7)(\d)(\d)/i.exec(id) || /SM-S(9|7)(\d)(\d)/i.exec(name);
  if (code) {
    const line = code[1];
    const g = Number(code[2]);
    const label = name && !/^SM-/i.test(name) ? name.trim() : `Galaxy S${22 + g}${line === '7' ? ' FE' : ''}`;
    if (g >= 1) return { status: 'approved', label, reason: 'Approved for virtual visits.' };
    return { status: 'not-approved', label, reason: NOT_APPROVED };
  }
  const m = /galaxy\s*s\s?(\d{2})/i.exec(name) || /galaxy\s*s\s?(\d{2})/i.exec(id);
  if (m) {
    const gen = Number(m[1]);
    const label = name.trim() || `Galaxy S${gen}`;
    return gen >= 23
      ? { status: 'approved', label, reason: 'Approved for virtual visits.' }
      : { status: 'not-approved', label, reason: NOT_APPROVED };
  }
  return null;
}

export function checkDevice(d: DeviceInfo): PolicyResult {
  const id = (d.modelId || '').trim();
  const name = (d.modelName || '').trim();
  if (d.os === 'ios') {
    if (/^iPad/i.test(id) || /ipad/i.test(name)) return { status: 'not-approved', label: 'iPad', reason: NOT_APPROVED };
    const known = IPHONE_IDS[id];
    if (known) return { status: known.ok ? 'approved' : 'not-approved', label: known.name, reason: known.ok ? 'Approved for virtual visits.' : (known.why || NOT_APPROVED) };
    const byId = /^iPhone(\d+),\d+$/.exec(id);
    if (byId) {
      const major = Number(byId[1]);
      if (major < 14) return { status: 'not-approved', label: name || 'This iPhone', reason: NOT_APPROVED };
      // Newer than this table: trust the name if we have one, otherwise assume a current model.
      const fromName = name ? iphoneFromName(name) : null;
      return fromName ?? { status: 'approved', label: name || 'This iPhone', reason: 'Approved for virtual visits.' };
    }
    const fromName = name ? iphoneFromName(name) : null;
    return fromName ?? { status: 'unknown', label: name || 'This iPhone', reason: 'The browser doesn’t share the model. Approved iPhones: 13 Pro or newer, with a 0.5× lens.' };
  }
  if (d.os === 'android') {
    const both = `${id} ${name}`;
    const p = pixel(both);
    if (p) return p;
    const s = galaxy(id, name);
    if (s) return s;
    if (!id && !name) return { status: 'unknown', label: 'This phone', reason: `We can’t read the model here. Approved: ${POLICY_LINE}` };
    return { status: 'not-approved', label: name || id, reason: NOT_APPROVED };
  }
  return { status: 'not-approved', label: name || 'This device', reason: `Record virtual visits on a phone: ${POLICY_LINE}` };
}
