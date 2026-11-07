import { useState } from 'react';
import { A1_GUIDE_LINE } from '../lib/legal-docs';

const KEY = 'bricks.go.a1-banner-dismissed';
// Notice period for amendment A1: shown from 2 Nov until it binds everyone (16 Nov 2026, 00:00 ET).
const UNTIL = Date.parse('2026-11-16T05:00:00Z');

/** In-app notice of IC amendment A1 (accepted jobs). Dismissible; gone after the notice period. */
export default function AmendmentBanner() {
  const [hidden, setHidden] = useState(() => {
    try { return localStorage.getItem(KEY) === '1'; } catch { return false; }
  });
  if (hidden || Date.now() >= UNTIL) return null;
  const close = () => {
    try { localStorage.setItem(KEY, '1'); } catch { /* private mode */ }
    setHidden(true);
  };
  return (
    <div className="offline-bar" role="status" data-testid="amendment-banner"
      style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
      <span>
        <strong>Contractor agreement update.</strong> {A1_GUIDE_LINE} It applies from 16 November 2026, or from when
        you accept it. Questions? Email <a href="mailto:help@brxs.ca">help@brxs.ca</a>.
      </span>
      <button type="button" className="btn-tertiary" onClick={close} aria-label="Dismiss" data-testid="amendment-banner-close">
        Got it
      </button>
    </div>
  );
}
