/**
 * Product law: bracketed stills (HDR + twilight) must be uploaded as
 * bracket sets (±EV / under-mid-over). Staging/drone/reel are excluded.
 * Mirrors backend photo_enhance_service.bracket_stem / exposure_bias_from_name
 * and backend bracket_require.validate_hdr_uploads_for_complete so client
 * grouping matches post-prod + Mark complete API gate.
 */

export const HDR_BRACKET_MIN = 3;

export const BRACKET_HINT =
  'Upload bracketed exposures for each angle (− / 0 / +).';

/** Near-zero band for "mid" plate (parity with backend _MID_ABS). */
const MID_ABS = 0.5;

/** Name patterns the backend clusters on (stem + ±EV / under / over). */
const EV_SUFFIX =
  /^(?<stem>.+?)(?:[_-](?<evp>ev)?(?<ev>[+-]?\d+(?:\.\d+)?)|[_-](?<sign>minus|plus)(?<mp>\d+(?:\.\d+)?)(?:ev)?|_m(?<m>\d+)|_p(?<p>\d+)|_und(?:er)?|_over|_ov|_normal)$/i;

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

/**
 * Camera frame counters (IMG_4031, DSC_0012, shoot-4031) are not EV.
 * Unsigned numeric suffix with 2+ integer digits and no `ev` prefix is a
 * file counter (parity with backend _is_sequence_number).
 */
function isSequenceNumber(m: RegExpMatchArray | null): boolean {
  const ev = m?.groups?.ev;
  if (ev == null || ev === '' || m?.groups?.evp) return false;
  if (ev[0] === '+' || ev[0] === '-') return false;
  return ev.split('.')[0].length >= 2;
}

/** Collapse +/-EV / under / over suffixes so siblings share one stem. */
export function bracketStem(filename: string): string {
  const base = basename(filename);
  const name = base.includes('.') ? base.slice(0, base.lastIndexOf('.')) : base;
  const m = name.match(EV_SUFFIX);
  if (isSequenceNumber(m)) return name;
  if (m?.groups?.stem) return m.groups.stem.replace(/[_-]+$/, '');
  return name.replace(/[_-]0$/i, '');
}

export function exposureBiasFromName(filename: string): number | null {
  const base = basename(filename);
  const name = base.includes('.') ? base.slice(0, base.lastIndexOf('.')) : base;
  const low = name.toLowerCase();
  if (low.endsWith('_normal') || low.endsWith('-normal')) return 0;
  const m = name.match(EV_SUFFIX);
  if (!m?.groups || isSequenceNumber(m)) return null;
  if (m.groups.ev != null && m.groups.ev !== '') {
    const n = Number(m.groups.ev);
    return Number.isFinite(n) ? n : null;
  }
  if (m.groups.mp != null && m.groups.mp !== '') {
    const mag = Number(m.groups.mp);
    if (!Number.isFinite(mag)) return null;
    return (m.groups.sign || '').toLowerCase() === 'minus' ? -mag : mag;
  }
  if (m.groups.m != null && m.groups.m !== '') return -Number(m.groups.m);
  if (m.groups.p != null && m.groups.p !== '') return Number(m.groups.p);
  // Word tags: read only the matched suffix, never the room stem —
  // "laundry_over" / "Sundeck_over" contain "und" and used to read as −2
  // (parity with backend exposure_bias_from_name).
  const suffix = name.slice(m.groups.stem.length).toLowerCase();
  if (suffix.startsWith('_und')) return -2;
  if (suffix === '_over' || suffix === '_ov') return 2;
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

/** True when filenames cover under (<0), mid (~0), and over (>0). */
export function bracketHasUnderMidOver(filenames: string[]): boolean {
  const biases: number[] = [];
  for (const raw of filenames) {
    const b = exposureBiasFromName(basename(raw));
    if (b != null && Number.isFinite(b)) biases.push(b);
  }
  if (!biases.length) return false;
  const hasUnder = biases.some((b) => b < -0.05);
  const hasMid = biases.some((b) => Math.abs(b) <= MID_ABS);
  const hasOver = biases.some((b) => b > 0.05);
  return hasUnder && hasMid && hasOver;
}

/** Listing-decodable stills only — enhance decodes JPEG/PNG/TIFF/WebP/HEIC/HEIF. */
export function isImageName(name: string): boolean {
  return /\.(jpe?g|png|tif{1,2}|webp|heic|heif)$/i.test(name);
}

/** Camera RAW the enhance desk cannot decode (parity with backend CAMERA_RAW_EXTS). */
export function isCameraRawName(name: string): boolean {
  return /\.(cr2|cr3|dng|nef|nrw|arw|srf|sr2|raf|orf|rw2|pef|ptx|srw|x3f|raw|rwl)$/i.test(
    name || '',
  );
}

export const cameraRawRejectionMessage =
  'Camera RAW files (CR2/CR3/DNG/NEF/ARW/…) are not supported for listing stills. ' +
  'Export JPEG (or HEIC) ±EV brackets from your camera or Lightroom, then upload those.';

function isVisitName(name: string): boolean {
  return /\.(splat|ply|spz)$/i.test(name);
}

/**
 * Mirror backend media_intake.infer_media_type — media_type is NOT a DB column,
 * so Mark complete must infer drone/reel/etc. from filename/mime instead of
 * defaulting every image to hdr (that false-blocked complete when DJI frames
 * sat beside a full −/0/+ stills set).
 */
export function inferMediaType(
  filename?: string | null,
  mimeType?: string | null,
  explicit?: string | null,
): string {
  const raw = String(explicit || '')
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, '_');
  const aliases: Record<string, string> = {
    photos: 'hdr',
    still: 'hdr',
    stills: 'hdr',
    photography: 'hdr',
    aerial: 'drone',
    dji: 'drone',
    video: 'reel',
    cinematic: 'reel',
    cinematic_reel: 'reel',
    dusk: 'twilight',
    day_to_dusk: 'twilight',
    virtual_staging: 'staging',
    stage: 'staging',
    photo: 'hdr',
    floor_plan: 'floorplan',
    roomplan: 'floorplan',
    room_plan: 'floorplan',
    cubicasa: 'floorplan',
    plan: 'floorplan',
    plans: 'floorplan',
  };
  const known = new Set(['hdr', 'photo', 'drone', 'reel', 'twilight', 'staging', 'floorplan']);
  if (known.has(raw)) return raw === 'photo' ? 'hdr' : raw;
  if (aliases[raw]) return aliases[raw];

  const name = String(filename || '').toLowerCase();
  const mime = String(mimeType || '').toLowerCase();
  if (name.includes('stag')) return 'staging';
  if (name.includes('twilight') || name.includes('dusk') || name.includes('golden')) {
    return 'twilight';
  }
  // Exact basename plan/plans (CubiCasa) — not substring (airplane/explainer).
  const stem = name.includes('.') ? name.slice(0, name.lastIndexOf('.')) : name;
  if (stem === 'plan' || stem === 'plans') {
    return 'floorplan';
  }
  // Delimited "plan" token — backend media_intake / OS mediaKind parity.
  // "Floor Plan.png" / "level-1-plan.jpg" must be floorplan (not HDR bracket gate).
  // Must NOT match airplane/plant/replant or ground_floor_* listing stills.
  if (/(?:^|[_\s-])plan(?:[_\s.-]|$)/.test(name)) {
    return 'floorplan';
  }
  if (
    name.includes('floorplan') ||
    name.includes('floor_plan') ||
    name.includes('floor-plan') ||
    name.includes('cubicasa') ||
    name.includes('roomplan') ||
    name.includes('room_plan') ||
    name.includes('room-plan')
  ) {
    return 'floorplan';
  }
  if (name.includes('drone') || name.includes('aerial') || name.startsWith('dji')) {
    return 'drone';
  }
  // Backend media_intake parity — durable reel_/cinematic_ stills (JPEG/HEIC)
  // must not fall through to hdr + bracket gate after uploads strip media_type.
  if (name.includes('reel') || name.includes('cinematic')) {
    return 'reel';
  }
  if (mime.startsWith('video/') || /\.(mp4|mov|m4v|webm|avi)$/i.test(name)) {
    return 'reel';
  }
  return 'hdr';
}

/** True when this upload participates in the −/0/+ bracket gate (HDR + twilight). */
export function isHdrStillUpload(u: {
  filename?: string | null;
  media_type?: string | null;
  mime_type?: string | null;
}): boolean {
  const filename = u.filename || '';
  const name = basename(filename);
  if (!name || isVisitName(name) || !isImageName(name)) return false;
  const rawMt = String(u.media_type || '').trim().toLowerCase();
  if (rawMt) {
    return (
      rawMt === 'hdr' ||
      rawMt === 'photo' ||
      rawMt === 'stills' ||
      rawMt === 'twilight' ||
      rawMt === ''
    );
  }
  const inferred = inferMediaType(filename, u.mime_type, null);
  return inferred === 'hdr' || inferred === 'twilight';
}

function issueForStem(stem: string, files: string[], min: number): BracketIssue {
  const n = files.length;
  if (n < min) {
    return {
      stem,
      count: n,
      filenames: files,
      message:
        `Cannot complete: still angle "${stem}" has only ${n} exposure` +
        `${n === 1 ? '' : 's'} (need ≥${min}). ` +
        BRACKET_HINT,
    };
  }
  return {
    stem,
    count: n,
    filenames: files,
    message:
      `Cannot complete: still angle "${stem}" needs under, mid, and over ` +
      `exposures (− / 0 / +), not ${n} same-side frames. ` +
      `Name files with ±EV (e.g. ${stem}_-2.jpg, ${stem}_0.jpg, ${stem}_+2.jpg).`,
  };
}

/**
 * Validate a proposed bracketed stills upload batch against already-registered
 * filenames. Returns null when OK; otherwise an issue describing incomplete brackets.
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
    if (merged.length < min || !bracketHasUnderMidOver(merged)) {
      if (merged.length < min) {
        return {
          stem,
          count: merged.length,
          filenames: merged,
          message:
            `Bracketed stills need a bracket of at least ${min} exposures (− / 0 / +) per angle. ` +
            `"${stem}" has ${merged.length} frame${merged.length === 1 ? '' : 's'}` +
            (merged.length === 1
              ? ' — single frames are not accepted.'
              : ` (need ${min - merged.length} more).`) +
            ` Name files with ±EV (e.g. living_-2.jpg, living_0.jpg, living_+2.jpg).`,
        };
      }
      return {
        stem,
        count: merged.length,
        filenames: merged,
        message:
          `Bracketed stills need under, mid, and over exposures (− / 0 / +) per angle. ` +
          `"${stem}" has ${merged.length} frames but is missing a side of the bracket. ` +
          `Name files with ±EV (e.g. living_-2.jpg, living_0.jpg, living_+2.jpg).`,
      };
    }
  }
  return null;
}

/**
 * Block Mark complete / submit when any HDR/twilight stem on the job is incomplete.
 * Parity with backend bracket_require.validate_hdr_uploads_for_complete:
 * infer media_type when absent (DB has no media_type column) and require −/0/+.
 */
export function validateHdrUploadsForComplete(
  uploads: {
    filename?: string | null;
    media_type?: string | null;
    mime_type?: string | null;
  }[],
  opts?: { allowIncomplete?: boolean; min?: number },
): BracketIssue | null {
  if (opts?.allowIncomplete) return null;
  const min = opts?.min ?? HDR_BRACKET_MIN;
  const hdrNames = (uploads || [])
    .filter(isHdrStillUpload)
    .map((u) => basename(u.filename || ''))
    .filter((n) => n && isImageName(n));

  if (!hdrNames.length) return null;

  const groups = groupByBracketStem(hdrNames);
  for (const [stem, files] of groups) {
    if (files.length < min || !bracketHasUnderMidOver(files)) {
      return issueForStem(stem, files, min);
    }
  }
  return null;
}

export function requiresBracketSet(mediaType: string | null | undefined): boolean {
  const mt = String(mediaType || '').toLowerCase();
  return mt === 'hdr' || mt === 'photo' || mt === 'stills' || mt === 'twilight';
}

/** Mirror backend media_intake.durable_media_filename durable prefixes. */
const DURABLE_PREFIXES: Record<string, string> = {
  drone: 'aerial_',
  reel: 'reel_',
  twilight: 'twilight_',
  staging: 'staging_',
  floorplan: 'floorplan_',
};

export function durableMediaFilename(
  filename?: string | null,
  mediaType?: string | null,
): string {
  const raw = String(filename || 'still.jpg').replace(/\\/g, '/');
  const base = (raw.split('/').pop() || 'still.jpg').replace(/^\.+/, '') || 'still.jpg';
  const intended = inferMediaType(base, null, mediaType);
  if (intended === 'hdr') return base;
  const fromName = inferMediaType(base, null, null);
  if (fromName === intended) return base;
  const prefix = DURABLE_PREFIXES[intended];
  if (!prefix) return base;
  if (base.toLowerCase().startsWith(prefix.toLowerCase())) return base;
  return `${prefix}${base}`;
}
