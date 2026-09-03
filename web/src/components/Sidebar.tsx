import { NavLink } from 'react-router-dom';

const items = [
  { path: '/',              label: 'Overview',    end: true },
  { path: '/jobs',         label: 'Jobs' },
  { path: '/profile',      label: 'Profile' },
  { path: '/team',         label: 'Team' },
  { path: '/earnings',     label: 'Earnings' },
  { path: '/availability', label: 'Availability' },
  { path: '/performance',  label: 'Performance' },
  { path: '/settings',     label: 'Settings' },
];

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

export function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-row">
          <BrickMark size={20} />
          <span className="brand-word">bricks</span>
        </div>
        <span className="brand-sub">GO</span>
      </div>
      <nav className="nav">
        {items.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.end}
            className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
