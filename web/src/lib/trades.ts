// Vendor trades in GO. Every trade is a set-price job (Kam, 21 Nov 2026):
// the client books and pays a set price, and the vendor gets a set cut by
// tier. Only repairs and inspections need a certificate of insurance with
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
  /** Your pay by tier, Tier 1 / 2 / 3. Same numbers as the backend price sheet. */
  pay: string[];
  needsCoi: boolean;
};

const BOOK_STEP = 'The client books and pays a set price. The job comes to you with the time already set.';
const PROOF_STEP = 'Upload before and after photos in the job, so the client sees proof inside Bricks.';

export const TRADES: Trade[] = [
  { id: 'photographer', label: 'Photographer', role: 'Photographer', work: 'Listing photos, video, floor plans and aerials.', limits: [],
    steps: ['Follow the Guide for each package. You capture, Bricks edits.'], pay: ['Set by package and home size. See Payouts.'], needsCoi: false },
  { id: 'stager', label: 'Home stager', role: 'Stager', work: 'Staging for vacant and lived-in homes, timed before the photo visit.', limits: [],
    steps: [BOOK_STEP, 'Finish the install before the photo visit, and note the removal date in the job.', PROOF_STEP], pay: ['Consult: $109.15 / $123.90 / $141.60', 'Install: 80% of the staging price, every tier'], needsCoi: false },
  { id: 'cleaner', label: 'Cleaner', role: 'Cleaner', work: 'Pre-listing cleans, move-in and move-out, rental turnovers and post-construction.', limits: [],
    steps: [BOOK_STEP, 'Work through the checklist in the job, room by room.', PROOF_STEP], pay: ['1 bedroom condo: $84.75 / $96.20 / $109.90', 'Up to 2,500 sq ft house: $166.15 / $188.60 / $215.50'], needsCoi: false },
  { id: 'inspector', label: 'Inspector', role: 'Inspector', work: 'Home and building condition inspections with a written report.', limits: ['Ontario only.'],
    steps: [BOOK_STEP, 'Upload the written report with photos in the job. Bricks keeps it with the property.'], pay: ['Condo: $153.55 / $164.05 / $174.50', 'Detached up to 2,500 sq ft: $197.55 / $211.05 / $224.50'], needsCoi: true },
  { id: 'measurer', label: 'Measurer', role: 'Measurer', work: 'Measured floor area and floor plans.', limits: ['Not a legal survey. Bricks refers legal surveys to an Ontario Land Surveyor.'],
    steps: [BOOK_STEP, 'Note the measuring method and anything you left out of the area.', 'Upload the measurements and plan in the job.'], pay: ['Floor plan: $36.65 / $41.60 / $47.50'], needsCoi: false },
  { id: 'handyman', label: 'Handyman and repairs', role: 'Handyman', work: 'Small repairs and deficiency lists before listing or between tenants.', limits: ['No electrical, plumbing or gas work.'],
    steps: [BOOK_STEP, 'Extra time is billed in 30 minute blocks. Anything new needs the client to approve it in Bricks first.', PROOF_STEP], pay: ['First hour: $55.15 / $62.60 / $71.50', 'Each extra 30 min: $17.60 / $19.95 / $22.80', 'Materials paid back at cost'], needsCoi: true },
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
