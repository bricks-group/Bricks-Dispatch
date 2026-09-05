// In-app GO capture: purchased 3D tour (path-confined), floor plan, and/or
// Virtual Visit add-on (Polycam URL / .ply / .splat bridge). HDR stills stay
// on the vendor camera.
//
// Prefer the server `job.capture` block (authoritative). Fallback infers
// from real jobs columns: package_id, package_name, job_type, add_ons,
// checklist, client_instructions.

export type CaptureFlags = {
  tour: boolean;
  floorplan: boolean;
  visit: boolean;
};

const PHOTO_PACKAGES_WITH_FLOORPLAN = new Set([
  'essentials', 'story', 'showcase', 'suite', 'full',
  'standard', 'premium', 'luxury', 'estate',
]);
const PACKAGES_WITH_TOUR = new Set(['showcase', 'suite', 'full']);
const TOUR_ADDON_KEYS = new Set(['tour', 'virtual_tour', 'virtualtour', '3d_tour', '3dtour', 'path_tour']);
const FLOORPLAN_ADDON_KEYS = new Set([
  'floorplan', 'floorplan_2d', 'floor_plan', 'floor_plan_only', '2d_floor_plan', 'floorplans',
]);
const VISIT_ADDON_KEYS = new Set([
  'visit', 'virtual_visit', 'virtualvisit', 'gaussian_splat', 'supersplat', 'extended3d', 'extended3d_addon',
]);
const PHOTOS_ONLY_IDS = new Set(['photosonly', 'photos_only', 'photo_only', 'photos']);
const TOUR_JOB_TYPES = new Set(['3d_tour', '3dtour', 'virtual_tour', 'virtualtour']);
const FLOORPLAN_JOB_TYPES = new Set(['floorplan', 'floor_plan', 'floor_plan_only']);

function canon(raw: unknown): string {
  return String(raw ?? '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').replace(/\s+/g, ' ').trim();
}
function compact(raw: unknown): string {
  return canon(raw).replace(/ /g, '');
}

type AddonRec = { key: string; text: string };

function addonRecords(addOns: unknown): AddonRec[] {
  let data: unknown = addOns;
  if (typeof data === 'string') {
    try { data = JSON.parse(data); } catch { data = [data]; }
  }
  if (data && typeof data === 'object' && !Array.isArray(data)) data = [data];
  if (!Array.isArray(data)) return [];
  const out: AddonRec[] = [];
  for (const item of data) {
    if (typeof item === 'string') {
      out.push({ key: compact(item), text: canon(item) });
      continue;
    }
    if (!item || typeof item !== 'object') continue;
    const rec = item as Record<string, unknown>;
    if (rec.included === false || rec.enabled === false) continue;
    const texts: string[] = [];
    const keys: string[] = [];
    for (const field of ['key', 'id', 'sku', 'name', 'label', 'type', 'kind']) {
      const val = rec[field];
      if (val) { texts.push(canon(val)); keys.push(compact(val)); }
    }
    if (keys.length || texts.length) out.push({ key: keys[0] || '', text: texts.join(' ') });
  }
  return out;
}

function checklistLabels(checklist: unknown): string[] {
  if (!Array.isArray(checklist)) return [];
  return checklist.map((item) => {
    if (typeof item === 'string') return canon(item);
    if (item && typeof item === 'object') {
      const rec = item as Record<string, unknown>;
      return canon(rec.label || rec.name || '');
    }
    return '';
  }).filter(Boolean);
}

export type CaptureJobLike = {
  capture?: Partial<CaptureFlags> | null;
  package_id?: string | null;
  packageId?: string | null;
  package_name?: string | null;
  packageName?: string | null;
  job_type?: string | null;
  jobType?: string | null;
  add_ons?: unknown;
  addOns?: unknown;
  checklist?: unknown;
  client_instructions?: string | null;
  clientInstructions?: string | null;
  property_details?: string | null;
};

function infer(job: CaptureJobLike): CaptureFlags {
  const packageId = compact(job.package_id ?? job.packageId);
  const packageName = canon(job.package_name ?? job.packageName);
  const jobType = compact(job.job_type ?? job.jobType);
  const addons = addonRecords(job.add_ons ?? job.addOns);
  const labels = checklistLabels(job.checklist);
  const blob = [
    canon(job.package_id ?? job.packageId), packageName, canon(job.job_type ?? job.jobType),
    canon(job.property_details), canon(job.client_instructions ?? job.clientInstructions),
    addons.map((a) => a.text).join(' '), labels.join(' '),
  ].filter(Boolean).join(' ');

  const photosOnly = PHOTOS_ONLY_IDS.has(packageId)
    || blob.includes('photos only') || blob.includes('photo only') || blob.includes('no floor plan');

  const visit = addons.some((a) => VISIT_ADDON_KEYS.has(a.key) || a.text.includes('virtual visit') || a.text.includes('gaussian splat') || a.text.includes('supersplat'))
    || blob.includes('virtual visit') || blob.includes('gaussian splat') || blob.includes('supersplat');

  const tourFromAddon = addons.some((a) => {
    if (TOUR_ADDON_KEYS.has(a.key)) return true;
    if (a.text.includes('virtual visit') || a.text.includes('gaussian') || a.text.includes('supersplat')) return false;
    return a.text.includes('virtual tour') || a.text.replace(/ /g, '') === '3dtour' || a.text.replace(/ /g, '') === 'tour';
  });
  const tourFromPkg = PACKAGES_WITH_TOUR.has(packageId)
    || packageName.split(' ').includes('showcase')
    || packageName.split(' ').includes('suite')
    || packageName.startsWith('full suite');
  const tourFromType = TOUR_JOB_TYPES.has(jobType);
  const tourFromLabel = labels.some((l) => {
    if (l.includes('virtual visit') || l.includes('gaussian')) return false;
    return l.includes('virtual tour') || l.includes('3d tour');
  });
  let cleaned = blob;
  for (const phrase of ['virtual visit', 'gaussian splat 3d virtual tour', 'gaussian splat', 'supersplat', 'free roam', 'free roam']) {
    cleaned = cleaned.split(phrase).join(' ');
  }
  const tourFromBlob = cleaned.includes('path confined') || cleaned.includes('virtual tour') || cleaned.includes('3d tour');
  const tour = tourFromType || tourFromAddon || tourFromPkg || tourFromLabel || tourFromBlob;

  const explicitFp = addons.some((a) => FLOORPLAN_ADDON_KEYS.has(a.key) || a.text.includes('floor plan') || a.text.replace(/ /g, '').includes('floorplan'))
    || labels.some((l) => {
      const c = l.replace(/ /g, '');
      if (c === 'floorplancapturesubmitted') return false;
      return l.includes('floor plan') || c.startsWith('floorplan');
    })
    || blob.includes('floor plan only');

  const photoPkg = PHOTO_PACKAGES_WITH_FLOORPLAN.has(packageId)
    || PHOTO_PACKAGES_WITH_FLOORPLAN.has(packageName.split(' ')[0] || '')
    || /\b(small|mid|large|xl)\b.*\bphoto/.test(packageName)
    || jobType === 'photo'
    || blob.includes('hdr photo') || blob.includes('hdr photography');

  let floorplan = false;
  if (photosOnly && !explicitFp) floorplan = false;
  else if (explicitFp) floorplan = true;
  else if (FLOORPLAN_JOB_TYPES.has(jobType)) floorplan = true;
  else if (photoPkg) floorplan = true;

  return { tour: !!tour, floorplan: !!floorplan, visit: !!visit };
}

export function jobCaptureModes(job: CaptureJobLike | null | undefined): CaptureFlags {
  if (!job) return { tour: false, floorplan: false, visit: false };
  const server = job.capture;
  if (server && typeof server.tour === 'boolean' && typeof server.floorplan === 'boolean') {
    return {
      tour: !!server.tour,
      floorplan: !!server.floorplan,
      visit: !!server.visit,
    };
  }
  return infer(job);
}

export function captureNeitherCopy(flags: CaptureFlags): string {
  if (flags.visit && !flags.tour && !flags.floorplan) {
    return 'This job has Virtual Visit only — open Deliver Visit to paste a Polycam URL or upload a .ply / .splat. HDR stills stay on the camera.';
  }
  return 'This job has no in-app 3D tour, floor plan, or Virtual Visit. HDR stills stay on the camera.';
}

export function hasAnyCaptureMode(flags: CaptureFlags): boolean {
  return !!(flags.tour || flags.floorplan || flags.visit);
}

export function captureStatusCopy(status: string | null | undefined, kind: 'splat' | 'floorplan' | 'visit' = 'splat'): string {
  const label = kind === 'floorplan' ? 'Floor plan' : kind === 'visit' ? 'Virtual Visit' : '3D tour';
  switch ((status || '').toLowerCase()) {
    case 'delivered':
      return `${label} ready.`;
    case 'failed':
      return `${label} compile failed — open review to retry or recapture.`;
    case 'submitted':
    case 'splatting':
    case 'tracing':
      return `${label} compiling… usually a few minutes.`;
    case 'in_progress':
      return `${label} capture in progress.`;
    default:
      return '';
  }
}
