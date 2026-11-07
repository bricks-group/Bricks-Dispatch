// Drone liability insurance (certificate of insurance) for GO Profile.
// www /photographers: builder site jobs need Tier 3, Advanced RPAS and verified
// drone liability of at least $2M on a certificate Bricks verifies. Listing jobs
// never need it. The vendor uploads here; only Bricks marks it verified.
import { fmtMoneyCompact } from './money';

export const MIN_LIABILITY_CAD = 2_000_000;

export type InsuranceStatus = {
  verified?: boolean;
  valid?: boolean;
  reason?: string | null;
  expires_on?: string | null;
  liability_cad?: number | null;
  aviation_covered?: boolean;
  minimum_cad?: number;
  source?: string;
};

export type InsuranceReview = {
  status: 'pending' | 'verified' | 'rejected';
  uploaded_at?: string | null;
  expires_on?: string | null;
  liability_cad?: number | null;
  aviation_covered?: boolean;
  insurer?: string | null;
} | null;

export type InsuranceView = {
  tone: 'ok' | 'wait' | 'warn' | 'none';
  headline: string;
  detail: string;
  /** Show the upload form open (vs behind "Upload a renewal"). */
  formOpen: boolean;
};

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "2027-03-04" → "Mar 4, 2027" (date-only, no timezone shift). */
export function fmtDay(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
  if (!m) return '';
  const mo = Number(m[2]);
  if (mo < 1 || mo > 12) return '';
  return `${MONTHS[mo - 1]} ${Number(m[3])}, ${m[1]}`;
}

/** Accepts "2000000", "2,000,000", "$2M", "2 m", "1.5M", "500k". Whole dollars or null. */
export function parseLimit(raw: string): number | null {
  const s = String(raw || '').trim().toLowerCase().replace(/[$,\s]/g, '').replace(/cad$/, '');
  if (!s) return null;
  const m = /^(\d+(?:\.\d+)?)(m|mm|million|k|thousand)?$/.exec(s);
  if (!m) return null;
  const n = Number(m[1]);
  const mult = m[2] === 'm' || m[2] === 'mm' || m[2] === 'million' ? 1_000_000 : m[2] === 'k' || m[2] === 'thousand' ? 1_000 : 1;
  const v = Math.round(n * mult);
  return Number.isFinite(v) && v > 0 ? v : null;
}

/** YYYY-MM-DD for today in the device's zone. */
export function todayIso(now: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
}

export type InsuranceForm = {
  file: { size: number } | null;
  limit: string;
  expires: string;
  aviation: boolean;
};

/** First problem with the form, or '' when it can be sent. */
export function validateInsuranceForm(f: InsuranceForm, today: string = todayIso()): string {
  if (!f.file) return 'Add your certificate (PDF or photo).';
  if (f.file.size > 12 * 1024 * 1024) return 'That file is over 12 MB.';
  const limit = parseLimit(f.limit);
  if (!limit) return 'Enter the liability limit, like 2,000,000.';
  if (!/^\d{4}-\d{2}-\d{2}$/.test(f.expires)) return 'Enter the expiry date.';
  if (f.expires < today) return 'This certificate has already expired.';
  return '';
}

/** Soft notes shown before upload (never block it). */
export function insuranceFormNotes(f: InsuranceForm): string[] {
  const notes: string[] = [];
  const limit = parseLimit(f.limit);
  if (limit && limit < MIN_LIABILITY_CAD) notes.push(`Builder site jobs need ${fmtMoneyCompact(MIN_LIABILITY_CAD)} or more.`);
  if (!f.aviation) notes.push('Builder site jobs need drone liability on the policy.');
  return notes;
}

/** One status block for the card. Verified state comes from the member; review from the latest upload. */
export function insuranceView(ins: InsuranceStatus | null | undefined, review: InsuranceReview | undefined, today: string = todayIso()): InsuranceView {
  const st = ins || {};
  const min = st.minimum_cad || MIN_LIABILITY_CAD;
  if (st.source === 'company') {
    return st.valid
      ? { tone: 'ok', headline: 'Covered by the Bricks policy', detail: 'In-house staff are covered by the company policy.', formOpen: false }
      : { tone: 'warn', headline: 'Company policy not on file', detail: 'Bricks will update the company policy.', formOpen: false };
  }
  const pending = review && review.status === 'pending';
  const pendingLine = pending
    ? `New certificate in review${review?.uploaded_at ? `, sent ${fmtDay(review.uploaded_at)}` : ''}.`
    : '';
  if (st.valid) {
    const exp = st.expires_on || '';
    const soon = exp && daysBetween(today, exp) <= 30;
    return {
      tone: soon && !pending ? 'warn' : 'ok',
      headline: `Verified · ${fmtMoneyCompact(st.liability_cad || 0)}`,
      detail: pending ? pendingLine : soon ? `Expires ${fmtDay(exp)}. Upload your renewal.` : `Expires ${fmtDay(exp)}.`,
      formOpen: false,
    };
  }
  if (pending) {
    const parts = [review?.liability_cad ? fmtMoneyCompact(review.liability_cad) : '', review?.expires_on ? `expires ${fmtDay(review.expires_on)}` : ''].filter(Boolean);
    return {
      tone: 'wait',
      headline: 'In review',
      detail: `Sent ${fmtDay(review?.uploaded_at) || 'recently'}${parts.length ? ` · ${parts.join(', ')}` : ''}. Bricks checks it before builder jobs open.`,
      formOpen: false,
    };
  }
  if (st.verified) {
    if (st.reason === 'insurance_expired') return { tone: 'warn', headline: 'Expired', detail: `Expired ${fmtDay(st.expires_on)}. Upload your renewal.`, formOpen: true };
    if (st.reason === 'insurance_below_minimum') return { tone: 'warn', headline: `Verified at ${fmtMoneyCompact(st.liability_cad || 0)}`, detail: `Builder site jobs need ${fmtMoneyCompact(min)} or more.`, formOpen: true };
    if (st.reason === 'insurance_no_drone_liability') return { tone: 'warn', headline: 'No drone liability', detail: 'The verified policy doesn’t cover drone liability.', formOpen: true };
    if (st.reason === 'insurance_no_expiry') return { tone: 'warn', headline: 'Expiry missing', detail: 'Upload the certificate again with its expiry date.', formOpen: true };
  }
  if (review && review.status === 'rejected') {
    return { tone: 'warn', headline: 'Not accepted', detail: 'Bricks couldn’t accept the last certificate. Upload a new one.', formOpen: true };
  }
  return { tone: 'none', headline: 'Not on file', detail: `Needed for builder site jobs: drone liability of ${fmtMoneyCompact(min)} or more. Listing jobs don’t need it.`, formOpen: true };
}

function daysBetween(a: string, b: string): number {
  const ta = Date.parse(`${a}T00:00:00Z`);
  const tb = Date.parse(`${String(b).slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(ta) || Number.isNaN(tb)) return Infinity;
  return Math.round((tb - ta) / 86_400_000);
}
