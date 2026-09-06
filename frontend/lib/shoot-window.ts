/** Booked clock times in the property timezone. No Cal.com — HH:MM on the job. */

import { resolveJobIana, tzAbbrev, type JobTzFields } from './job-timezone';

const CANONICAL: Record<string, { start: string }> = {
  morning: { start: '09:00' },
  afternoon: { start: '12:00' },
  evening: { start: '16:00' },
};

const UNSET = 'Shoot time not set';

export type ShootWindowFields = JobTzFields & {
  preferred_window?: string | null;
  time_window_start?: string | null;
  time_window_end?: string | null;
  confirmed_arrive_at?: string | null;
  scheduled_date?: string | null;
};

function clock(raw?: string | null, job?: ShootWindowFields): string {
  const s = (raw || '').trim();
  if (!s) return '';
  const m = s.match(/^(\d{1,2}):(\d{2})/);
  if (m && !s.includes('T') && !s.includes('-')) {
    return `${m[1].padStart(2, '0')}:${m[2]}`;
  }
  const d = new Date(s);
  if (!Number.isNaN(d.getTime())) {
    return d.toLocaleTimeString('en-GB', {
      timeZone: resolveJobIana(job),
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
  }
  return '';
}

function clock12(hhmm: string): string {
  if (!hhmm || hhmm[2] !== ':') return '';
  const hour = Number(hhmm.slice(0, 2));
  const minute = Number(hhmm.slice(3, 5));
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return '';
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return '';
  const suffix = hour < 12 ? 'AM' : 'PM';
  const hour12 = hour % 12 || 12;
  return `${hour12}:${String(minute).padStart(2, '0')} ${suffix}`;
}

function bookedClock(job: ShootWindowFields): string {
  const confirmed = clock(job.confirmed_arrive_at, job);
  if (confirmed) return confirmed;
  const start = clock(job.time_window_start, job);
  if (start) return start;
  const key = (job.preferred_window || '').trim().toLowerCase();
  if (CANONICAL[key]) return CANONICAL[key].start;
  return '';
}

/**
 * Booked arrive/start for GO Today / job detail.
 * e.g. "2:00 PM PT" — never a window label or 12:00–16:00 range.
 */
export function formatShootWindow(job: ShootWindowFields): string {
  const hhmm = bookedClock(job);
  if (!hhmm) return UNSET;
  const pretty = clock12(hhmm);
  const abbrev = tzAbbrev(job);
  return pretty ? `${pretty} ${abbrev}` : `${hhmm} ${abbrev}`;
}

const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function formatShootDate(scheduled_date?: string | null): string {
  const raw = (scheduled_date || '').slice(0, 10);
  if (!raw || raw[4] !== '-') return raw || '';
  const y = Number(raw.slice(0, 4));
  const m = Number(raw.slice(5, 7));
  const d = Number(raw.slice(8, 10));
  if (!y || !m || !d) return raw;
  const dt = new Date(y, m - 1, d);
  if (Number.isNaN(dt.getTime()) || dt.getFullYear() !== y || dt.getMonth() !== m - 1 || dt.getDate() !== d) {
    return raw;
  }
  return `${DOW[dt.getDay()]}, ${MON[m - 1]} ${d}`;
}

/** Date + booked clock: "Fri, Sep 25 · 2:00 PM PT". */
export function formatShootWhen(job: ShootWindowFields): string {
  const date = formatShootDate(job.scheduled_date);
  const window = formatShootWindow(job);
  if (window === UNSET) return date || UNSET;
  if (date) return `${date} · ${window}`;
  return window;
}

/** Minutes until booked start from scheduled_date + time_window_start (property-local wall clock). */
export function minutesUntilShoot(job: ShootWindowFields): number | null {
  const date = (job.scheduled_date || '').slice(0, 10);
  const start = clock(job.confirmed_arrive_at, job) || clock(job.time_window_start, job);
  if (!date || !start) return null;
  const d = new Date(`${date}T${start}:00`);
  if (Number.isNaN(d.getTime())) return null;
  const diff = d.getTime() - Date.now();
  if (diff <= 0) return null;
  return Math.round(diff / 60000);
}
