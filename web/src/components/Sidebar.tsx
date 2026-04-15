import { NavLink } from 'react-router-dom';

const items = [
  { path: '/', label: 'Overview' },
  { path: '/team', label: 'Team' },
  { path: '/jobs', label: 'Jobs' },
  { path: '/availability', label: 'Availability' },
  { path: '/performance', label: 'Performance' },
  { path: '/settings', label: 'Settings' },
];

export function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-accent" />
        <div>
          <p className="brand-title">BRICKS</p>
          <p className="brand-subtitle">VENDOR OS</p>
        </div>
      </div>
      <nav className="nav">
        {items.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.path === '/'}
            className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
