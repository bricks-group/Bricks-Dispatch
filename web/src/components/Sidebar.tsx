import { useEffect, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';

const items = [
  { path: '/',                label: 'Today',          end: true },
  { path: '/jobs',           label: 'Jobs' },
  { path: '/guide',          label: 'Guide' },
  { path: '/notifications',  label: 'Notifications' },
  { path: '/profile',        label: 'Profile' },
  { path: '/team',           label: 'Team' },
  { path: '/earnings',       label: 'Earnings' },
  { path: '/availability',   label: 'Availability' },
  { path: '/performance',    label: 'Performance' },
  { path: '/settings',       label: 'Settings' },
];

/* Phone: four key destinations in a bottom tab bar, the rest under More. */
const TAB_PATHS = ['/', '/jobs', '/guide', '/earnings'];
const tabs = TAB_PATHS.map((p) => items.find((i) => i.path === p)!);
const moreItems = items.filter((i) => !TAB_PATHS.includes(i.path));

function BrickMark({ size = 18 }: { size?: number }) {
  const h = Math.round(size * 0.85);
  const w = Math.round(h * (27 / 16));
  return (
    <svg width={w} height={h} viewBox="0 0 27 16" fill="none">
      <rect x="1" y="1"  width="12" height="6" rx="1.2" fill="#D2542B" />
      <rect x="1" y="9"  width="12" height="6" rx="1.2" fill="#D2542B" />
      <rect x="15" y="9" width="11" height="6" rx="1.2" fill="#D2542B" />
    </svg>
  );
}

const HELP_URL = 'https://www.brxs.ca/help/photographers';

const linkClass = (base: string) => ({ isActive }: { isActive: boolean }) => `${base}${isActive ? ' active' : ''}`;

export function Sidebar() {
  const { pathname } = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);
  const inMore = moreItems.some((i) => pathname === i.path || pathname.startsWith(i.path + '/'));

  useEffect(() => { setMoreOpen(false); }, [pathname]);
  useEffect(() => {
    if (!moreOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMoreOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [moreOpen]);

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-row">
          <BrickMark size={20} />
          <span className="brand-word">bricks</span>
        </div>
      </div>
      <nav className="nav" aria-label="Main">
        {items.map((item) => (
          <NavLink key={item.path} to={item.path} end={item.end} className={linkClass('nav-link')}>
            {item.label}
          </NavLink>
        ))}
        <a className="nav-link" href={HELP_URL} target="_blank" rel="noopener noreferrer" aria-label="Help centre (opens in a new tab)">
          Help centre
        </a>
      </nav>

      <nav className="tabbar" aria-label="Main">
        {tabs.map((item) => (
          <NavLink key={item.path} to={item.path} end={item.end} className={linkClass('tabbar-link')}>
            {item.label}
          </NavLink>
        ))}
        <button
          type="button"
          className={`tabbar-link${inMore || moreOpen ? ' active' : ''}`}
          aria-expanded={moreOpen}
          aria-controls="tabbar-more"
          onClick={() => setMoreOpen((o) => !o)}
        >
          More
        </button>
      </nav>

      {moreOpen && (
        <>
          <div className="more-scrim" onClick={() => setMoreOpen(false)} aria-hidden="true" />
          <div className="more-sheet" id="tabbar-more" role="dialog" aria-label="More">
            {moreItems.map((item) => (
              <NavLink key={item.path} to={item.path} className={linkClass('more-link')}>
                {item.label}
              </NavLink>
            ))}
            <a className="more-link" href={HELP_URL} target="_blank" rel="noopener noreferrer" aria-label="Help centre (opens in a new tab)">
              Help centre
            </a>
          </div>
        </>
      )}
    </aside>
  );
}
