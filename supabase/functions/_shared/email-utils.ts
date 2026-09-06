/** Shared email utilities for Bricks edge functions */

export function escapeHtml(value: string): string {
  return (value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function fmtDate(dateStr: string, timeZone?: string): string {
  if (!dateStr) return '—';
  const tz = timeZone && timeZone.trim() ? timeZone : undefined;
  return new Date(dateStr).toLocaleDateString('en-CA', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
    ...(tz ? { timeZone: tz } : {}),
  });
}
