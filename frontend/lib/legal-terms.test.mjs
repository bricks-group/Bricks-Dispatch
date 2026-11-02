// Run from frontend/: node --test lib/legal-terms.test.mjs  (Node 22.6+ strips the TS types)
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as m from './legal-terms.ts';

test('scroll end within 24px counts as read; short docs read once measured', () => {
  assert.equal(m.isScrolledToEnd({ contentHeight: 2000, offsetY: 0, viewHeight: 600 }), false);
  assert.equal(m.isScrolledToEnd({ contentHeight: 2000, offsetY: 1380, viewHeight: 600 }), true);
  assert.equal(m.isScrolledToEnd({ contentHeight: 2000, offsetY: 1370, viewHeight: 600 }), false);
  assert.equal(m.isScrolledToEnd({ contentHeight: 400, offsetY: 0, viewHeight: 600 }), true);
  assert.equal(m.isScrolledToEnd({ contentHeight: 0, offsetY: 0, viewHeight: 600 }), false, 'not measured yet');
  assert.equal(m.isScrolledToEnd({ contentHeight: 2000, offsetY: 0, viewHeight: 0 }), false, 'not laid out yet');
});

test('v1.9 vendors see amendment A1 only; new vendors see all three; older API sees all', () => {
  assert.deepEqual(m.requiredDocs({ needs_terms: true, needs_ic_terms: false, needs_amendment: true }), ['ic_a1']);
  assert.deepEqual(m.requiredDocs({ needs_terms: true, needs_ic_terms: true, needs_amendment: true }), ['ic', 'privacy', 'ic_a1']);
  assert.deepEqual(m.requiredDocs({ needs_terms: true, needs_ic_terms: true, needs_amendment: false }), ['ic', 'privacy']);
  assert.deepEqual(m.requiredDocs({ needs_terms: true }), ['ic', 'privacy', 'ic_a1']);
  assert.deepEqual(m.requiredDocs(null), ['ic', 'privacy', 'ic_a1']);
});

test('gate shows only for approved vendors with needs_terms true', () => {
  assert.equal(m.needsTermsStep({ needs_terms: true }, true), true);
  assert.equal(m.needsTermsStep({ needs_terms: true }, false), false);
  assert.equal(m.needsTermsStep({ needs_terms: false }, true), false);
  assert.equal(m.needsTermsStep({}, true), false);
  assert.equal(m.needsTermsStep(null, true), false);
});

test('I agree stays locked until every required doc is read, with web copy', () => {
  assert.equal(m.allRead(['ic_a1'], {}), false);
  assert.equal(m.allRead(['ic_a1'], { ic_a1: true }), true);
  assert.equal(m.allRead([], {}), false);
  assert.equal(m.lockedReason(['ic_a1'], {}), 'Open the amendment A1: accepted jobs and scroll to the end to continue.');
  assert.equal(m.lockedReason(['ic_a1'], { ic_a1: true }), '');
  assert.equal(m.lockedReason(['ic', 'privacy', 'ic_a1'], { privacy: true }),
    'Open the independent contractor agreement and the amendment A1: accepted jobs, and scroll each to the end to continue.');
});

test('A1 version comes from the first line of the final legal text, not v2.0', () => {
  const md = readFileSync('/workspace/legal/final/ic-amendment-A1-v1.0.md', 'utf8');
  const ver = (md.match(/\*\*Version:\*\*\s*(.+)/) || [])[1].trim();
  assert.equal(ver, 'IC-A1 v1.0');
  assert.equal(m.amendmentVersionFromText(`AMENDMENT A1: ACCEPTED JOBS  ${ver}`), ver);
  assert.equal(m.amendmentVersionFromText('BRICKS INDEPENDENT CONTRACTOR AGREEMENT - AMENDMENT A1: ACCEPTED JOBS  IC-A1 v1.0\n\nVersion: IC-A1 v1.0'), 'IC-A1 v1.0');
  assert.equal(m.amendmentVersionFromText('nothing\nIC-A1 v1.0'), '');
  assert.equal(m.icVersionFromText('BRICKS INDEPENDENT CONTRACTOR AGREEMENT  v1.9\nEffective'), '1.9');
  assert.equal(m.textDoc('X  IC-A1 v1.0\nbody', 'ic_a1').version, 'IC-A1 v1.0');
  assert.throws(() => m.textDoc('   ', 'ic_a1'));
});

test('accept body records the amendment only for v1.9 vendors (same as web)', () => {
  assert.deepEqual(m.acceptTermsBody(['ic_a1'], { ic_a1: true }, '1.9', 'IC-A1 v1.0'),
    { ic_agreement_version: null, ic_amendment_version: 'IC-A1 v1.0', read: ['ic_a1'] });
  assert.deepEqual(m.acceptTermsBody(['ic', 'privacy', 'ic_a1'], { ic: true, privacy: true, ic_a1: true }, '1.9', 'IC-A1 v1.0'),
    { ic_agreement_version: '1.9', ic_amendment_version: 'IC-A1 v1.0', read: ['ic', 'privacy', 'ic_a1'] });
});

test('privacy page: only .legal-body text, scripts dropped, entities decoded', () => {
  const html = '<html><script>alert(1)</script><div class="updated">Effective &middot; 27 August 2026</div>'
    + '<div class="legal-body"><section class="block"><h2>BRICKS PRIVACY POLICY</h2><div><p>A &amp; B<br/>C</p></div><script>x()</script><ul><li>one</li></ul></section></div><footer>nav</footer>';
  const d = m.htmlDoc(html);
  assert.equal(d.updated, 'Effective · 27 August 2026');
  assert.ok(d.text.startsWith('BRICKS PRIVACY POLICY'));
  assert.ok(d.text.includes('A & B\nC'));
  assert.ok(d.text.includes('• one'));
  assert.ok(!d.text.includes('alert') && !d.text.includes('x()') && !d.text.includes('nav'));
  assert.throws(() => m.htmlDoc('<p>no body</p>'));
});

test('hand-back reasons match the backend and the A1 exceptions', () => {
  assert.deepEqual(m.CANCEL_REASONS.map(([k]) => k), ['medical', 'bereavement', 'accident', 'weather', 'client_site', 'app_outage', 'other']);
  const labels = Object.fromEntries(m.CANCEL_REASONS);
  assert.equal(labels.accident, 'Accident or breakdown on the way');
  assert.equal(labels.weather, 'Severe weather or unsafe drone conditions');
  assert.equal(labels.client_site, 'Unsafe site or no access caused by the client');
  assert.equal(labels.app_outage, 'Bricks app or GPS outage');
  assert.match(m.HANDBACK_RULE_COPY, /^Free until 48 hours before the start\./);
  assert.match(m.IMMEDIATE_FAMILY_COPY, /spouse or partner, children, parents and siblings/);
  assert.deepEqual(m.handbackBody('weather', '  '), { status: 'declined', cancel_reason: 'weather', cancel_note: undefined });
  assert.equal(m.handbackBody('other', 'x'.repeat(1200)).cancel_note.length, 1000);
});

test('vendor-facing copy says Bricks only, no GO / Bricks OS, sentence case', () => {
  const copy = [m.A1_GUIDE_LINE, m.HANDBACK_RULE_COPY, m.IMMEDIATE_FAMILY_COPY, ...m.CANCEL_REASONS.map(([, l]) => l), ...Object.values(m.LEGAL_DOCS).map((d) => d.title)];
  for (const c of copy) {
    assert.ok(!/\bGO\b|Bricks OS|Kam/.test(c), c);
    assert.equal(c[0], c[0].toUpperCase(), c);
  }
});
