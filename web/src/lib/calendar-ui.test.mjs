import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const page = readFileSync(new URL('../pages/AvailabilityPage.tsx', import.meta.url), 'utf8');
test('connection failures are visible, not silently treated as success', () => {
  assert.match(page, /if \(!res.connected && !res.oauth_url\)/);
});
test('calendar page uses square controls and a wrapping mobile feed form', () => {
  const css = readFileSync(new URL('../styles.css', import.meta.url), 'utf8');
  assert.match(page, /page-content calendar-availability/);
  assert.match(page, /className="row calendar-feed-form"/);
  assert.match(css, /\.calendar-availability[^}]+border-radius: 0/s);
  assert.match(css, /\.calendar-feed-form[^}]+flex-wrap: wrap/s);
});
