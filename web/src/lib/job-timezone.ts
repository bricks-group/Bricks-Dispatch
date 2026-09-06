/** Canada-wide property timezone. Unknown → America/Toronto (silent). */

export const DEFAULT_IANA = 'America/Toronto';

const PROVINCE_TO_IANA: Record<string, string> = {
  bc: 'America/Vancouver',
  'british columbia': 'America/Vancouver',
  ab: 'America/Edmonton',
  alberta: 'America/Edmonton',
  sk: 'America/Regina',
  saskatchewan: 'America/Regina',
  mb: 'America/Winnipeg',
  manitoba: 'America/Winnipeg',
  on: 'America/Toronto',
  ont: 'America/Toronto',
  ontario: 'America/Toronto',
  qc: 'America/Toronto',
  que: 'America/Toronto',
  quebec: 'America/Toronto',
  'québec': 'America/Toronto',
  nb: 'America/Moncton',
  'new brunswick': 'America/Moncton',
  ns: 'America/Halifax',
  'nova scotia': 'America/Halifax',
  pe: 'America/Halifax',
  pei: 'America/Halifax',
  'prince edward island': 'America/Halifax',
  nl: 'America/St_Johns',
  nf: 'America/St_Johns',
  newfoundland: 'America/St_Johns',
  'newfoundland and labrador': 'America/St_Johns',
  yt: 'America/Whitehorse',
  yukon: 'America/Whitehorse',
  nt: 'America/Yellowknife',
  'northwest territories': 'America/Yellowknife',
  nu: 'America/Iqaluit',
  nunavut: 'America/Iqaluit',
};

const POSTAL_TO_IANA: Record<string, string> = {
  A: 'America/St_Johns',
  B: 'America/Halifax',
  C: 'America/Halifax',
  E: 'America/Moncton',
  G: 'America/Toronto',
  H: 'America/Toronto',
  J: 'America/Toronto',
  K: 'America/Toronto',
  L: 'America/Toronto',
  M: 'America/Toronto',
  N: 'America/Toronto',
  P: 'America/Toronto',
  R: 'America/Winnipeg',
  S: 'America/Regina',
  T: 'America/Edmonton',
  V: 'America/Vancouver',
  X: 'America/Yellowknife',
  Y: 'America/Whitehorse',
};

const ALIAS: Record<string, string> = {
  et: 'America/Toronto',
  est: 'America/Toronto',
  edt: 'America/Toronto',
  eastern: 'America/Toronto',
  ct: 'America/Winnipeg',
  mt: 'America/Edmonton',
  pt: 'America/Vancouver',
  pst: 'America/Vancouver',
  pdt: 'America/Vancouver',
  pacific: 'America/Vancouver',
  at: 'America/Halifax',
  nt: 'America/St_Johns',
};

const STD_TO_LETTER: Record<string, string> = {
  EST: 'ET', EDT: 'ET',
  CST: 'CT', CDT: 'CT',
  MST: 'MT', MDT: 'MT',
  PST: 'PT', PDT: 'PT',
  AST: 'AT', ADT: 'AT',
  NST: 'NT', NDT: 'NT',
};

export type JobTzFields = {
  timezone?: string | null;
  time_zone?: string | null;
  tz?: string | null;
  province?: string | null;
  state?: string | null;
  zip_code?: string | null;
  postal_code?: string | null;
  address?: string | null;
  scheduled_date?: string | null;
};

function norm(raw?: string | null): string {
  return (raw || '').trim().toLowerCase().replace(/_/g, ' ').replace(/\./g, '');
}

function isIana(name: string): boolean {
  try {
    Intl.DateTimeFormat('en-CA', { timeZone: name });
    return true;
  } catch {
    return false;
  }
}

export function validIana(name?: string | null): string | null {
  const s = (name || '').trim();
  if (!s) return null;
  const alias = ALIAS[norm(s)];
  if (alias) return alias;
  return isIana(s) ? s : null;
}

function ianaFromAddress(address?: string | null): string | null {
  const compact = ` ${(address || '').replace(/,/g, ' ')} `;
  const keys = Object.keys(PROVINCE_TO_IANA).sort((a, b) => b.length - a.length);
  const low = compact.toLowerCase();
  for (const key of keys) {
    if (low.includes(` ${key} `) || low.trimEnd().endsWith(` ${key}`)) {
      return PROVINCE_TO_IANA[key];
    }
  }
  return null;
}

export function resolveJobIana(job?: JobTzFields | null): string {
  const j = job || {};
  const explicit = validIana(j.timezone || j.time_zone || j.tz);
  if (explicit) return explicit;
  const prov = PROVINCE_TO_IANA[norm(j.province || j.state)];
  if (prov) return prov;
  const postal = String(j.zip_code || j.postal_code || '').trim().toUpperCase().replace(/\s/g, '');
  if (postal && POSTAL_TO_IANA[postal[0]]) return POSTAL_TO_IANA[postal[0]];
  return ianaFromAddress(j.address) || DEFAULT_IANA;
}

export function tzAbbrev(job?: JobTzFields | null, iana?: string | null): string {
  const zone = validIana(iana) || resolveJobIana(job);
  const ymd = (job?.scheduled_date || '').slice(0, 10);
  const d = ymd.length === 10 ? new Date(`${ymd}T12:00:00`) : new Date();
  let short = '';
  try {
    short = new Intl.DateTimeFormat('en-CA', {
      timeZone: zone,
      timeZoneName: 'short',
    }).formatToParts(d).find((p) => p.type === 'timeZoneName')?.value || '';
  } catch {
    short = '';
  }
  const letter = STD_TO_LETTER[short.toUpperCase().replace(/\s/g, '')];
  if (letter) return letter;
  if (zone === 'America/Vancouver') return 'PT';
  if (zone === 'America/Edmonton' || zone === 'America/Whitehorse' || zone === 'America/Yellowknife') return 'MT';
  if (zone === 'America/Winnipeg' || zone === 'America/Regina') return 'CT';
  if (zone === 'America/Halifax' || zone === 'America/Moncton') return 'AT';
  if (zone === 'America/St_Johns') return 'NT';
  return 'ET';
}
