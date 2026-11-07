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

test('agree stays locked until both the agreement and privacy policy are read', () => {
  assert.equal(m.allRead(m.REQUIRED_DOCS, {}), false);
  assert.equal(m.allRead(m.REQUIRED_DOCS, { ic: true }), false);
  assert.equal(m.allRead(m.REQUIRED_DOCS, { ic: true, privacy: true }), true);
  assert.equal(m.allRead([], {}), false);
  assert.match(m.lockedReason(m.REQUIRED_DOCS, {}), /independent contractor agreement and the privacy policy, and scroll each/);
  assert.equal(m.lockedReason(m.REQUIRED_DOCS, { ic: true }), 'Open the privacy policy and scroll to the end to continue.');
  assert.equal(m.lockedReason(m.REQUIRED_DOCS, { ic: true, privacy: true }), '');
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
