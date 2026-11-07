// Vendor trades in GO. Phase 1 trades beyond photography are quote-request
// jobs: Bricks quotes the client in writing and only sends the job once they
// approve. Only repairs and inspections need a certificate of insurance with
// $2M commercial general liability (backend CGL_REQUIRED_TYPES).
export type TradeId = 'photographer' | 'stager' | 'cleaner' | 'inspector' | 'measurer' | 'handyman';

export type Trade = {
  id: TradeId;
  label: string;
  role: string;
  /** What Bricks sends this trade. */
  work: string;
  /** Limits on the work, shown as written. */
  limits: string[];
  /** How a Bricks job runs for this trade. */
  steps: string[];
  needsCoi: boolean;
};

const QUOTE_STEP = 'Bricks quotes the client in writing. The job only reaches you once they approve it.';
const PROOF_STEP = 'Upload before and after photos in the job, so the client sees proof inside Bricks.';

export const TRADES: Trade[] = [
  { id: 'photographer', label: 'Photographer', role: 'Photographer', work: 'Listing photos, video, floor plans and aerials.', limits: [],
    steps: ['Follow the Guide for each package. You capture, Bricks edits.'], needsCoi: false },
  { id: 'stager', label: 'Home stager', role: 'Stager', work: 'Staging for vacant and lived-in homes, timed before the photo visit.', limits: [],
    steps: [QUOTE_STEP, 'Finish the install before the photo visit, and note the removal date in the job.', PROOF_STEP], needsCoi: false },
  { id: 'cleaner', label: 'Cleaner', role: 'Cleaner', work: 'Pre-listing cleans, move-in and move-out, rental turnovers and post-construction.', limits: [],
    steps: [QUOTE_STEP, 'Work through the checklist in the job, room by room.', PROOF_STEP], needsCoi: false },
  { id: 'inspector', label: 'Inspector', role: 'Inspector', work: 'Home and building condition inspections with a written report.', limits: ['Ontario only.'],
    steps: [QUOTE_STEP, 'Upload the written report with photos in the job. Bricks keeps it with the property.'], needsCoi: true },
  { id: 'measurer', label: 'Measurer', role: 'Measurer', work: 'Measured floor area and floor plans.', limits: ['Not a legal survey. Bricks refers legal surveys to an Ontario Land Surveyor.'],
    steps: [QUOTE_STEP, 'Note the measuring method and anything you left out of the area.', 'Upload the measurements and plan in the job.'], needsCoi: false },
  { id: 'handyman', label: 'Handyman and repairs', role: 'Handyman', work: 'Small repairs and deficiency lists before listing or between tenants.', limits: ['No electrical, plumbing or gas work.'],
    steps: [QUOTE_STEP, 'Anything outside the approved quote needs a change order Bricks approves first.', PROOF_STEP], needsCoi: true },
];

const BY_WORD: [RegExp, TradeId][] = [
  [/inspect/i, 'inspector'], [/measur|survey/i, 'measurer'], [/handy|repair/i, 'handyman'],
  [/stag/i, 'stager'], [/clean|turnover/i, 'cleaner'], [/photo|pilot|drone/i, 'photographer'],
];

export function tradeById(id: string | null | undefined): Trade | undefined {
  return TRADES.find((t) => t.id === id);
}

/** Trade from a member's title or role (the backend titles them "Inspector IC" etc.). */
export function tradeOf(m: { title?: string | null; role?: string | null; vendor_type?: string | null }): Trade {
  for (const text of [m.title, m.role]) {
    const hit = BY_WORD.find(([re]) => re.test(text || ''));
    if (hit) return tradeById(hit[1])!;
  }
  return tradeById(m.vendor_type || '') || TRADES[0];
}

export const COI_LINE = '$2M commercial general liability insurance is required for repair and inspection vendors. Send your certificate of insurance to help@brxs.ca before your first job.';
