/**
 * Product law: HDR stills must be uploaded as bracket sets (±EV / under-mid-over).
 * Mirrors backend photo_enhance_service.bracket_stem / exposure_bias_from_name
 * so client grouping matches post-prod group_assets_by_bracket.
 */

export const HDR_BRACKET_MIN = 3;

export const BRACKET_HINT =
  'Upload bracketed exposures for each angle (− / 0 / +).';

/** Name patterns the backend clusters on (stem + ±EV / under / over). */
const EV_SUFFIX =
  /^(?<stem>.+?)(?:[_-](?:ev)?(?<ev>[+-]?\d+(?:\.\d+)?)|_m(?<m>\d+)|_p(?<p>\d+)|_und(?:er)?|_over|_ov)$/i;

export type BracketFileLike = { name: string };

export type BracketIssue = {
  stem: string;
  count: number;
  filenames: string[];
  message: string;
};

export function basename(pathOrName: string): string {
  const base = (pathOrName || '').split(/[/\\]/).pop() || pathOrName || '';
  return base;
}

/** Collapse +/-EV / under / over suffixes so siblings share one stem. */
export function bracketStem(filename: string): string {
  const base = basename(filename);
  const name = base.includes('.') ? base.slice(0, base.lastIndexOf('.')) : base;
  const m = name.match(EV_SUFFIX);
  if (m?.groups?.stem) return m.groups.stem.replace(/[_-]+$/, '');
  return name.replace(/[_-]0$/i, '');
}

export function exposureBiasFromName(filename: string): number | null {
  const base = basename(filename);
  const name = base.includes('.') ? base.slice(0, base.lastIndexOf('.')) : base;
  const m = name.match(EV_SUFFIX);
  if (!m?.groups) return null;
  if (m.groups.ev != null && m.groups.ev !== '') {
    const n = Number(m.groups.ev);
    return Number.isFinite(n) ? n : null;
  }
  if (m.groups.m != null && m.groups.m !== '') return -Number(m.groups.m);
  if (m.groups.p != null && m.groups.p !== '') return Number(m.groups.p);
  const low = name.toLowerCase();
  if (low.includes('und')) return -2;
  if (low.includes('over') || low.endsWith('_ov')) return 2;
  return null;
}

export function groupByBracketStem(filenames: string[]): Map<string, string[]> {
  const buckets = new Map<string, string[]>();
  for (const raw of filenames) {
    const name = basename(raw);
    if (!name) continue;
    const key = bracketStem(name) || name;
    const list = buckets.get(key) || [];
    list.push(name);
    buckets.set(key, list);
  }
  return buckets;
}

function isImageName(name: string): boolean {
  return /\.(jpe?g|png|tif{1,2}|webp|heic|dng|cr2|nef|arw|raf|orf)$/i.test(name);
}

/**
 * Validate a proposed HDR upload batch against already-registered filenames.
 * Returns null when OK; otherwise an issue describing incomplete brackets.
 * Soft bypass: pass allowIncomplete=true (admin override only).
 */
export function validateHdrBracketSelection(
  selectedNames: string[],
  existingNames: string[] = [],
  opts?: { allowIncomplete?: boolean; min?: number },
): BracketIssue | null {
  if (opts?.allowIncomplete) return null;
  const min = opts?.min ?? HDR_BRACKET_MIN;
  const images = selectedNames.map(basename).filter(isImageName);
  if (!images.length) {
    // Videos / non-images under hdr type — treat as not a stills bracket slot.
    return null;
  }

  const existing = existingNames.map(basename).filter(Boolean);
  const selectedStems = groupByBracketStem(images);
  const existingByStem = groupByBracketStem(existing);

  for (const [stem, files] of selectedStems) {
    const prior = existingByStem.get(stem) || [];
    // Dedupe by basename so re-selecting already-uploaded names doesn't inflate.
    const merged = Array.from(new Set([...prior, ...files]));
    if (merged.length < min) {
      return {
        stem,
        count: merged.length,
        filenames: merged,
        message:
          `HDR stills need a bracket of at least ${min} exposures (− / 0 / +) per angle. ` +
          `"${stem}" has ${merged.length} frame${merged.length === 1 ? '' : 's'}` +
          (merged.length === 1 ? ' — single frames are not accepted.' : ` (need ${min - merged.length} more).`) +
          ` Name files with ±EV (e.g. living_-2.jpg, living_0.jpg, living_+2.jpg).`,
      };
    }
  }
  return null;
}

/**
 * Block Mark complete / submit when any HDR stem on the job is incomplete.
 */
export function validateHdrUploadsForComplete(
  uploads: { filename?: string; media_type?: string | null }[],
  opts?: { allowIncomplete?: boolean; min?: number },
): BracketIssue | null {
  if (opts?.allowIncomplete) return null;
  const min = opts?.min ?? HDR_BRACKET_MIN;
  const hdrNames = uploads
    .filter((u) => {
      const mt = String(u.media_type || 'hdr').toLowerCase();
      return mt === 'hdr' || mt === 'photo' || mt === 'stills' || mt === '';
    })
    .map((u) => basename(u.filename || ''))
    .filter((n) => n && isImageName(n));

  if (!hdrNames.length) return null;

  const groups = groupByBracketStem(hdrNames);
  for (const [stem, files] of groups) {
    if (files.length < min) {
      return {
        stem,
        count: files.length,
        filenames: files,
        message:
          `Cannot complete: HDR angle "${stem}" has only ${files.length} exposure` +
          `${files.length === 1 ? '' : 's'} (need ≥${min}). ` +
          BRACKET_HINT,
      };
    }
  }
  return null;
}

export function requiresBracketSet(mediaType: string | null | undefined): boolean {
  const mt = String(mediaType || '').toLowerCase();
  return mt === 'hdr' || mt === 'photo' || mt === 'stills';
}
