// GO's contractor agreement step for members who never applied on www.
// Same reading rules as www and app.brxs.ca: each document opens as a pop-up
// and only counts as read once it is scrolled to the end.

export const LEGAL_BASE = 'https://www.brxs.ca';

export type LegalKey = 'ic' | 'privacy';

export const LEGAL_DOCS: Record<LegalKey, { key: LegalKey; title: string; url: string }> = {
  ic: { key: 'ic', title: 'Independent contractor agreement', url: `${LEGAL_BASE}/legal/ic-agreement.txt` },
  privacy: { key: 'privacy', title: 'Privacy policy', url: `${LEGAL_BASE}/privacy` },
};

export const REQUIRED_DOCS: LegalKey[] = ['ic', 'privacy'];

/** Within this many px of the end counts as read. */
export const READ_END_SLACK_PX = 24;

type Box = { scrollHeight: number; scrollTop: number; clientHeight: number };

/** True once a scroll box is at (or within slack of) its end. A box that does not overflow is already at its end. */
export function isScrolledToEnd(box: Box, slack = READ_END_SLACK_PX): boolean {
  return box.scrollHeight - box.scrollTop - box.clientHeight <= slack;
}

export function allRead(required: LegalKey[], read: Partial<Record<LegalKey, boolean>>): boolean {
  return required.length > 0 && required.every((k) => read[k] === true);
}

/** Reason the agree button is still locked, or '' when it is not. */
export function lockedReason(required: LegalKey[], read: Partial<Record<LegalKey, boolean>>): string {
  const left = required.filter((k) => !read[k]).map((k) => LEGAL_DOCS[k].title.toLowerCase());
  if (!left.length) return '';
  if (left.length === 1) return `Open the ${left[0]} and scroll to the end to continue.`;
  return `Open the ${left.slice(0, -1).join(', ')} and the ${left[left.length - 1]}, and scroll each to the end to continue.`;
}

/** Member shape GO gets from /auth/me (only the fields the gate reads). */
export type TermsMember = { needs_terms?: boolean | null } | null | undefined;

/** Show the agreement step before the app opens. Missing flag (older API) never blocks. */
export function needsTermsStep(m: TermsMember, appAccess: boolean): boolean {
  return appAccess && !!m && m.needs_terms === true;
}

/** The IC agreement version, read from the first line of the published text ("... v1.9"). */
export function icVersionFromText(text: string): string {
  const first = (text || '').split('\n', 1)[0] || '';
  const m = first.match(/\bv(\d+(?:\.\d+)*)\b/i);
  return m ? m[1] : '';
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export type LoadedDoc = { html: string; updated: string; version: string };

export function textDoc(body: string): LoadedDoc {
  const t = (body || '').trim();
  if (!t) throw new Error('empty document');
  return { html: `<div class="ldoc-text">${escapeHtml(t)}</div>`, updated: '', version: icVersionFromText(t) };
}

/** Keep just the .legal-body sections of a www legal page, stripped of scripts, handlers and styles. */
export function htmlDoc(body: string, pageUrl: string, parser: DOMParser = new DOMParser()): LoadedDoc {
  const doc = parser.parseFromString(body, 'text/html');
  const parts = Array.from(doc.querySelectorAll('.legal-body'));
  if (!parts.length) throw new Error('no document body');
  const updated = (doc.querySelector('.hero .updated')?.textContent || '').replace(/\s+/g, ' ').trim();
  const out = doc.createElement('div');
  for (const part of parts) {
    part.querySelectorAll('script,style,iframe,object,embed,form,button,input').forEach((n) => n.remove());
    part.querySelectorAll('*').forEach((el) => {
      for (const attr of Array.from(el.attributes)) {
        if (/^on/i.test(attr.name) || attr.name === 'style' || attr.name === 'class') el.removeAttribute(attr.name);
      }
    });
    part.querySelectorAll('a[href]').forEach((a) => {
      const href = a.getAttribute('href') || '';
      if (/^\s*javascript:/i.test(href)) { a.removeAttribute('href'); return; }
      if (href.startsWith('mailto:') || href.startsWith('tel:')) return;
      a.setAttribute('href', new URL(href, pageUrl).href);
      a.setAttribute('target', '_blank');
      a.setAttribute('rel', 'noopener noreferrer');
    });
    const wrap = doc.createElement('section');
    wrap.innerHTML = part.innerHTML;
    out.appendChild(wrap);
  }
  return { html: out.innerHTML, updated, version: '' };
}

const cache = new Map<string, Promise<LoadedDoc>>();

/** Browser only: fetch a legal document (www sends CORS for these paths). */
export function loadLegalDoc(key: LegalKey): Promise<LoadedDoc> {
  const meta = LEGAL_DOCS[key];
  const hit = cache.get(meta.url);
  if (hit) return hit;
  const p = fetch(meta.url, { credentials: 'omit', cache: 'no-cache' })
    .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then((body) => (key === 'ic' ? textDoc(body) : htmlDoc(body, meta.url)));
  p.catch(() => cache.delete(meta.url));
  cache.set(meta.url, p);
  return p;
}
