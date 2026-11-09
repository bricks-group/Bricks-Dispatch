// Contractor agreement gate and hand-back reasons for the vendor app.
// Same rules as Dispatch web (web/src/lib/legal-docs.ts, JobsPage.tsx):
// each document opens in a sheet and only counts as read once it is scrolled
// to the end. Vendors who already accepted v1.9 see amendment A1 only.

export const LEGAL_BASE = 'https://www.brxs.ca';
export const DISPATCH_BASE = 'https://dispatch-api.brxs.ca';

export type LegalKey = 'ic' | 'privacy' | 'ic_a1';

export const LEGAL_DOCS: Record<LegalKey, { key: LegalKey; title: string; url: string }> = {
  ic: { key: 'ic', title: 'Independent contractor agreement', url: `${LEGAL_BASE}/legal/ic-agreement.txt` },
  privacy: { key: 'privacy', title: 'Privacy policy', url: `${LEGAL_BASE}/privacy` },
  ic_a1: { key: 'ic_a1', title: 'Amendment A1: accepted jobs', url: `${DISPATCH_BASE}/api/onboarding/ic-amendment.txt` },
};

export const REQUIRED_DOCS: LegalKey[] = ['ic', 'privacy', 'ic_a1'];

/** One-line summary of amendment A1, shown in the gate and the banner. */
export const A1_GUIDE_LINE =
  "You can hand back a job up to 48 hours before. After that, or if you don't check in within 30 minutes of the window start, your Bricks account ends. Emergencies are excepted, and completed work is always paid.";

/** The notice banner shows until A1 binds everyone (16 Nov 2026, 00:00 ET). */
export const A1_BANNER_UNTIL = Date.parse('2026-11-16T05:00:00Z');
export const A1_BANNER_KEY = 'bricks.go.a1-banner-dismissed';

/** Member shape from /api/vendor-os/auth/me (only the fields the gate reads). */
export type TermsMember = {
  needs_terms?: boolean | null;
  needs_ic_terms?: boolean | null;
  needs_amendment?: boolean | null;
} | null | undefined;

/** Which documents this member still has to accept. Older API without the split flags: all of them. */
export function requiredDocs(m: TermsMember): LegalKey[] {
  if (!m || (m.needs_ic_terms == null && m.needs_amendment == null)) return REQUIRED_DOCS;
  const out: LegalKey[] = [];
  if (m.needs_ic_terms) out.push('ic', 'privacy');
  if (m.needs_amendment) out.push('ic_a1');
  return out.length ? out : REQUIRED_DOCS;
}

/** Show the agreement step before the app opens. Missing flag (older API) never blocks. */
export function needsTermsStep(m: TermsMember, appAccess: boolean): boolean {
  return appAccess && !!m && m.needs_terms === true;
}

/** "IC-A1 v1.0" from the first line of the amendment text. */
export function amendmentVersionFromText(text: string): string {
  const first = (text || '').split('\n', 1)[0] || '';
  const m = first.match(/\bIC-A\d+ v\d+(?:\.\d+)*/i);
  return m ? m[0] : '';
}

/** The IC agreement version from the first line of the published text ("... v1.9"). */
export function icVersionFromText(text: string): string {
  const first = (text || '').split('\n', 1)[0] || '';
  const m = first.match(/\bv(\d+(?:\.\d+)*)\b/i);
  return m ? m[1] : '';
}

/** Within this many px of the end counts as read. */
export const READ_END_SLACK_PX = 24;

/** ScrollView metrics: content height, scroll offset and visible height. */
export type ScrollBox = { contentHeight: number; offsetY: number; viewHeight: number };

/** True once a scroll box is at (or within slack of) its end. A box that does not overflow is already at its end. */
export function isScrolledToEnd(box: ScrollBox, slack = READ_END_SLACK_PX): boolean {
  if (!(box.viewHeight > 0) || !(box.contentHeight > 0)) return false;
  return box.contentHeight - box.offsetY - box.viewHeight <= slack;
}

export function allRead(required: LegalKey[], read: Partial<Record<LegalKey, boolean>>): boolean {
  return required.length > 0 && required.every((k) => read[k] === true);
}

/** Reason the agree button is still locked, or '' when it is not. */
export function lockedReason(required: LegalKey[], read: Partial<Record<LegalKey, boolean>>): string {
  const left = required.filter((k) => !read[k]).map((k) => LEGAL_DOCS[k].title.charAt(0).toLowerCase() + LEGAL_DOCS[k].title.slice(1));
  if (!left.length) return '';
  if (left.length === 1) return `Open the ${left[0]} and scroll to the end to continue.`;
  return `Open the ${left.slice(0, -1).join(', ')} and the ${left[left.length - 1]}, and scroll each to the end to continue.`;
}

export type LoadedDoc = { text: string; updated: string; version: string };

export function textDoc(body: string, key: LegalKey = 'ic'): LoadedDoc {
  const t = (body || '').trim();
  if (!t) throw new Error('empty document');
  const version = key === 'ic_a1' ? amendmentVersionFromText(t) : icVersionFromText(t);
  return { text: t, updated: '', version };
}

const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', middot: '·', mdash: '—', ndash: '–', rsquo: '’', lsquo: '‘', rdquo: '”', ldquo: '“', hellip: '…' };

function decodeEntities(s: string): string {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (all, e: string) => {
    if (e[0] === '#') {
      const n = e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return Number.isFinite(n) ? String.fromCodePoint(n) : all;
    }
    return ENTITIES[e.toLowerCase()] ?? all;
  });
}

/** Inner HTML of every <div class="legal-body"> block (nesting-aware). */
function legalBodies(html: string): string[] {
  const out: string[] = [];
  const open = /<div\b[^>]*class="[^"]*\blegal-body\b[^"]*"[^>]*>/gi;
  let m: RegExpExecArray | null;
  while ((m = open.exec(html))) {
    const start = m.index + m[0].length;
    const tag = /<\/?div\b[^>]*>/gi;
    tag.lastIndex = start;
    let depth = 1;
    let t: RegExpExecArray | null;
    let end = -1;
    while ((t = tag.exec(html))) {
      depth += t[0][1] === '/' ? -1 : 1;
      if (depth === 0) { end = t.index; break; }
    }
    if (end < 0) break;
    out.push(html.slice(start, end));
    open.lastIndex = end;
  }
  return out;
}

/** Plain text of a www legal page: just its .legal-body sections, without scripts or styles. */
export function htmlDoc(body: string): LoadedDoc {
  const parts = legalBodies(body || '');
  if (!parts.length) throw new Error('no document body');
  const upd = (body.match(/class="updated"[^>]*>([^<]*)</i) || [])[1] || '';
  const text = parts.map((p) => p
    .replace(/<(script|style|iframe|object|embed|form|button)\b[\s\S]*?<\/\1>/gi, '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<li\b[^>]*>/gi, '\n• ')
    .replace(/<\/(p|h[1-6]|li|ul|ol|section|div|tr|table)>/gi, '\n\n')
    .replace(/<td\b[^>]*>/gi, '  ')
    .replace(/<[^>]+>/g, '')
  ).map(decodeEntities).join('\n\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  if (!text) throw new Error('empty document');
  return { text, updated: decodeEntities(upd).replace(/\s+/g, ' ').trim(), version: '' };
}

const cache = new Map<string, Promise<LoadedDoc>>();

/** Fetch a legal document (www and Dispatch send CORS for these paths, so this also works on web). */
export function loadLegalDoc(key: LegalKey, fetcher: typeof fetch = fetch): Promise<LoadedDoc> {
  const meta = LEGAL_DOCS[key];
  const hit = cache.get(meta.url);
  if (hit) return hit;
  const p = fetcher(meta.url, { cache: 'no-cache' } as RequestInit)
    .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then((body) => (key === 'privacy' ? htmlDoc(body) : textDoc(body, key)));
  p.catch(() => cache.delete(meta.url));
  cache.set(meta.url, p);
  return p;
}

/** Body for POST /api/vendor-os/profile/accept-terms (same as web acceptTerms). */
export function acceptTermsBody(required: LegalKey[], read: Partial<Record<LegalKey, boolean>>, icVersion: string, a1Version: string) {
  const amendmentOnly = !required.includes('ic');
  return {
    ic_agreement_version: (amendmentOnly ? '' : icVersion) || null,
    ic_amendment_version: a1Version || null,
    read: required.filter((k) => read[k]),
  };
}

/** Hand-back reasons. Mirrors backend vendor_termination.CANCEL_REASONS (all but "other" are reviewed by Bricks). */
export const CANCEL_REASONS: [string, string][] = [
  ['medical', 'Medical emergency'],
  ['bereavement', 'Bereavement'],
  ['accident', 'Accident or breakdown on the way'],
  ['weather', 'Severe weather or unsafe drone conditions'],
  ['client_site', 'Unsafe site or no access caused by the client'],
  ['app_outage', 'Bricks app or GPS outage'],
  ['other', 'Something else'],
];

export const HANDBACK_RULE_COPY =
  "Free until 48 hours before the start. Inside 48 hours, cancelling closes your account unless it's one of the reasons below, which go to Bricks for review. You can't send someone else.";

export const IMMEDIATE_FAMILY_COPY =
  'Immediate family means your spouse or partner, children, parents and siblings.';

/** Body for PUT /api/vendor-os/jobs/{id}/status when handing a job back (same as web). */
export function handbackBody(reason: string, note: string) {
  const n = (note || '').trim();
  return { status: 'declined', cancel_reason: reason, cancel_note: n ? n.slice(0, 1000) : undefined };
}
