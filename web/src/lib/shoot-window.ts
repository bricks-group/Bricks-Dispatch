/** Paid /book windows (America/Toronto). No Cal.com — HH:MM on the job. */
const CANONICAL: Record<string, { start: string; end: string; label: string }> = {
  morning: { start: '09:00', end: '12:00', label: 'Morning' },
  afternoon: { start: '12:00', end: '16:00', label: 'Afternoon' },
  evening: { start: '16:00', end: '19:00', label: 'Evening' },
};

export type ShootWindowFields = {
  preferred_window?: string | null;
  time_window_start?: string | null;
  time_window_end?: string | null;
  scheduled_date?: string | null;
};

function clock(raw?: string | null): string {
  const s = (raw || '').trim();
  if (!s) return '';
  // HH:MM or HH:MM:SS — keep HH:MM
  const m = s.match(/^(\d{1,2}):(\d{2})/);
  if (m) return `${m[1].padStart(2, '0')}:${m[2]}`;
  // ISO datetime → clock in Toronto
  const d = new Date(s);
  if (!Number.isNaN(d.getTime())) {
    return d.toLocaleTimeString('en-GB', {
      timeZone: 'America/Toronto',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
  }
  return s.slice(0, 5);
}

function windowLabel(preferred?: string | null, start?: string, end?: string): string {
  const key = (preferred || '').trim().toLowerCase();
  if (key && CANONICAL[key]) return CANONICAL[key].label;
  if (start && end) {
    for (const w of Object.values(CANONICAL)) {
      if (w.start === start && w.end === end) return w.label;
    }
  }
  if (key) return key.charAt(0).toUpperCase() + key.slice(1);
  return '';
}

/**
 * Clear shoot window for GO Today / job detail.
 * e.g. "Afternoon · 12:00–16:00 ET"
 * Honest when fields are missing — never invent times.
 */
export function formatShootWindow(job: ShootWindowFields): string {
  const start = clock(job.time_window_start);
  const end = clock(job.time_window_end);
  const label = windowLabel(job.preferred_window, start, end);

  if (start && end) {
    const range = `${start}–${end} ET`;
    return label ? `${label} · ${range}` : range;
  }
  if (start && !end) {
    const range = `${start} ET`;
    return label ? `${label} · from ${range}` : `From ${range}`;
  }
  if (!start && end) {
    const range = `${end} ET`;
    return label ? `${label} · until ${range}` : `Until ${range}`;
  }
  if (label) return `${label} · times not set`;
  return 'Shoot window not set';
}

export function formatShootDate(scheduled_date?: string | null): string {
  const d = (scheduled_date || '').slice(0, 10);
  return d || '';
}

/** Date + window when both useful (upcoming lists). */
export function formatShootWhen(job: ShootWindowFields): string {
  const date = formatShootDate(job.scheduled_date);
  const window = formatShootWindow(job);
  if (date && window !== 'Shoot window not set') return `${date} · ${window}`;
  if (date) return `${date} · Shoot window not set`;
  return window;
}
