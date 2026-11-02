/**
 * GO contractor agreement step: read-to-end rules and who is asked.
 * Run from web/: node --test src/lib/legal-docs.test.mjs (self-builds via npx esbuild)
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = '/tmp/legal-docs-test-bundle.mjs';
execSync(`npx --yes esbuild ${path.join(here, 'legal-docs.ts')} --bundle --platform=node --format=esm --outfile=${out}`, { stdio: 'inherit', cwd: path.join(here, '../..') });
const m = await import(out);

test('scroll end within 24px counts as read; short docs are read at once', () => {
  assert.equal(m.isScrolledToEnd({ scrollHeight: 2000, scrollTop: 0, clientHeight: 600 }), false);
  assert.equal(m.isScrolledToEnd({ scrollHeight: 2000, scrollTop: 1380, clientHeight: 600 }), true);
  assert.equal(m.isScrolledToEnd({ scrollHeight: 2000, scrollTop: 1370, clientHeight: 600 }), false);
  assert.equal(m.isScrolledToEnd({ scrollHeight: 400, scrollTop: 0, clientHeight: 600 }), true);
});

test('agree stays locked until the agreement, privacy policy and amendment A1 are read', () => {
  const ICP = ['ic', 'privacy'];
  assert.deepEqual(m.REQUIRED_DOCS, ['ic', 'privacy', 'ic_a1']);
  assert.equal(m.allRead(m.REQUIRED_DOCS, { ic: true, privacy: true }), false);
  assert.equal(m.allRead(m.REQUIRED_DOCS, { ic: true, privacy: true, ic_a1: true }), true);
  assert.equal(m.allRead(ICP, {}), false);
  assert.equal(m.allRead(ICP, { ic: true }), false);
  assert.equal(m.allRead(ICP, { ic: true, privacy: true }), true);
  assert.equal(m.allRead([], {}), false);
  assert.match(m.lockedReason(ICP, {}), /independent contractor agreement and the privacy policy, and scroll each/);
  assert.equal(m.lockedReason(ICP, { ic: true }), 'Open the privacy policy and scroll to the end to continue.');
  assert.equal(m.lockedReason(ICP, { ic: true, privacy: true }), '');
});

test('only members with access and the server flag are asked; missing flag never blocks', () => {
  assert.equal(m.needsTermsStep({ needs_terms: true }, true), true);
  assert.equal(m.needsTermsStep({ needs_terms: false }, true), false);
  assert.equal(m.needsTermsStep({}, true), false);
  assert.equal(m.needsTermsStep({ needs_terms: true }, false), false);
  assert.equal(m.needsTermsStep(null, true), false);
});

test('agreement version comes from the published first line and text is escaped', () => {
  assert.equal(m.icVersionFromText('BRICKS INDEPENDENT CONTRACTOR AGREEMENT  v1.9\nEffective'), '1.9');
  assert.equal(m.icVersionFromText('no version here'), '');
  const d = m.textDoc('AGREEMENT v2.0\n<script>x</script> & more');
  assert.equal(d.version, '2.0');
  assert.match(d.html, /&lt;script&gt;x&lt;\/script&gt; &amp; more/);
  assert.throws(() => m.textDoc('   '));
});

test('amendment A1: who reads what, and its version', () => {
  assert.deepEqual(m.requiredDocs({ needs_terms: true, needs_ic_terms: false, needs_amendment: true }), ['ic_a1']);
  assert.deepEqual(m.requiredDocs({ needs_terms: true, needs_ic_terms: true, needs_amendment: true }), ['ic', 'privacy', 'ic_a1']);
  assert.deepEqual(m.requiredDocs({ needs_terms: true }), ['ic', 'privacy', 'ic_a1']);
  assert.equal(m.amendmentVersionFromText('BRICKS ... AMENDMENT A1: ACCEPTED JOBS  IC-A1 v1.0\n\nVersion: IC-A1 v1.0'), 'IC-A1 v1.0');
  assert.equal(m.textDoc('X  IC-A1 v1.0\nbody', 'ic_a1').version, 'IC-A1 v1.0');
  assert.ok(m.A1_GUIDE_LINE.startsWith('You can hand back a job up to 48 hours before.'));
  assert.equal(m.lockedReason(['ic_a1'], {}), 'Open the amendment A1: accepted jobs and scroll to the end to continue.');
});
