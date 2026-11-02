/**
 * Vendor guide: package matching, applies_to filtering, clip lists, dark diagrams.
 * Run from web/: node --test src/guide/guide-model.test.mjs  (self-builds via npx esbuild)
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { execSync } from 'node:child_process';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = '/tmp/guide-model.mjs';
execSync(`npx --yes esbuild ${path.join(here, 'guide-model.ts')} --bundle --platform=node --format=esm --outfile=${out}`,
  { stdio: 'inherit', cwd: path.join(here, '../..') });
const m = await import(out);
const guide = JSON.parse(readFileSync(path.join(here, 'data/guide.json'), 'utf8'));

test('guide data: 7 phases, 43 steps, every diagram bundled', () => {
  assert.equal(guide.phases.length, 7);
  assert.equal(guide.steps.length, 43);
  const files = new Set(readdirSync(path.join(here, 'data/svg')).map((f) => `svg/${f}`));
  for (const s of guide.steps) if (s.diagram) assert.ok(files.has(s.diagram), s.diagram);
});

test('capture only: no step tells a vendor to edit, grade or export stills', () => {
  for (const s of guide.steps) {
    const text = [s.title, s.body, ...s.do].join(' ').toLowerCase();
    assert.ok(!/edit with|start every edit|upload edited|export (jpeg|jpg)|bricks preset/.test(text), s.id);
  }
  const ids = guide.steps.map((s) => s.id);
  assert.ok(ids.includes('stills-08') && ids.includes('wrap-05'));
});

test('jobPackage maps the rate card names and ids, nothing else', () => {
  assert.equal(m.jobPackage({ package_id: 'essentials' }), 'essentials');
  assert.equal(m.jobPackage({ package_id: 'showcase', package_name: 'Showcase' }), 'showcase');
  assert.equal(m.jobPackage({ package_name: 'Full suite' }), 'suite');
  assert.equal(m.jobPackage({ package_name: 'Photos only' }), 'photos');
  assert.equal(m.jobPackage({ package_id: 'standard', package_name: 'Standard' }), null);
});

test('jobPropertyType reads free-text property details', () => {
  assert.equal(m.jobPropertyType({ property_details: 'condo · paid www.brxs.ca/book' }), 'condo');
  assert.equal(m.jobPropertyType({ property_details: 'Detached · lead x' }), 'house');
  assert.equal(m.jobPropertyType({ property_details: 'listing · paid' }), null);
});

test('steps follow package services', () => {
  const ids = (pkg, extra) => m.stepsForServices(guide, m.jobServices(pkg, guide, extra)).map((s) => s.id);
  const photos = ids('photos');
  assert.ok(!photos.some((x) => x.startsWith('clips-') || x.startsWith('drone-') || x.startsWith('vv-')));
  const ess = ids('essentials');
  assert.ok(ess.includes('drone-01') && ess.includes('before-04') && !ess.includes('drone-03') && !ess.includes('clips-01'));
  assert.ok(!ess.includes('wrap-04'));
  const story = ids('story');
  assert.ok(story.includes('clips-01') && story.includes('drone-04') && story.includes('wrap-04') && !story.includes('vv-01'));
  assert.ok(ids('showcase').includes('vv-04'));
  assert.ok(ids('essentials', { visit: true }).includes('vv-01'));
  assert.equal(m.stepsForServices(guide, null).length, 43);
});

test('clip plans per package and property type', () => {
  assert.equal(m.clipPlan(guide, 'essentials', 'house'), null);
  const condo = m.clipPlan(guide, 'story', 'condo');
  assert.deepEqual(condo.drone.map((r) => r.id), ['DRN-02', 'DRN-04']);
  assert.ok(condo.droneNote);
  assert.ok(condo.vertical.every((r) => r.id.endsWith('_V') && r.clip));
  const lux = m.clipPlan(guide, 'story', 'luxury');
  assert.ok(lux.horizontal.some((r) => r.id === 'INT-12'));
  assert.equal(lux.drone.length, 5);
  const showCondo = m.clipPlan(guide, 'showcase', 'condo');
  assert.ok(showCondo.horizontal.some((r) => r.id === 'INT-05' && /Showcase/.test(r.note)));
  const suite = m.clipPlan(guide, 'suite', 'house');
  assert.deepEqual(suite.horizontal.filter((r) => r.id.startsWith('TWI-')).map((r) => r.id), ['TWI-01', 'TWI-02', 'TWI-03', 'TWI-04']);
  assert.ok(suite.horizontal.every((r) => r.clip), 'every clip id resolves');
  assert.equal(suite.reference.length, 2);
});

test('darkDiagram drops the bg layer, recolours ink, keeps ids unique', () => {
  const svg = readFileSync(path.join(here, 'data/svg/push-in.svg'), 'utf8');
  const d = m.darkDiagram(svg, 'gd-push-in');
  assert.ok(!/id="bg"/.test(d));
  assert.ok(!/#14110E/i.test(d));
  assert.ok(/id="gd-push-in-t"/.test(d) && /aria-labelledby="gd-push-in-t"/.test(d));
  assert.ok(!/<svg[^>]*\swidth="/.test(d));
});
