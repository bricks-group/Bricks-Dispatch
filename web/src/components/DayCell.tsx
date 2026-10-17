const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "2026-10-17" on wide screens, "Oct 17" in phone one-line rows. Reads the date part only (no TZ shift). */
export function DayCell({ d }: { d?: string | null }) {
  const iso = d?.slice(0, 10);
  if (!iso) return <>—</>;
  const [, m, day] = iso.split('-').map(Number);
  const short = m >= 1 && m <= 12 && day ? `${MON[m - 1]} ${day}` : iso;
  return <><span className="d-full">{iso}</span><span className="d-short">{short}</span></>;
}
