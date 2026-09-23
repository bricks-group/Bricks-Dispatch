/**
 * Node smoke test for HDR bracket requirement (mirrors backend grouping).
 * Run from web/: npx esbuild src/lib/bracket-require.ts --bundle --platform=node --format=esm --outfile=/tmp/bracket-require.mjs && node src/lib/bracket-require.test.mjs
 * Or: node src/lib/bracket-require.test.mjs  (self-builds via npx esbuild)
 */
import assert from 'node:assert/strict';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = '/tmp/bracket-require.mjs';
execSync(
  `npx --yes esbuild ${path.join(here, 'bracket-require.ts')} --bundle --platform=node --format=esm --outfile=${out}`,
  { stdio: 'inherit', cwd: path.join(here, '../..') },
);

const {
  bracketStem,
  exposureBiasFromName,
  validateHdrBracketSelection,
  validateHdrUploadsForComplete,
  requiresBracketSet,
  HDR_BRACKET_MIN,
  BRACKET_HINT,
} = await import(`${out}?t=${Date.now()}`);

assert.equal(bracketStem('living_-2.jpg'), bracketStem('living_+1.jpg'));
assert.equal(bracketStem('living_-2.jpg'), bracketStem('living_0.jpg'));
assert.equal(exposureBiasFromName('living_-2.jpg'), -2);
assert.equal(exposureBiasFromName('living_+1.5.jpg'), 1.5);
assert.equal(exposureBiasFromName('kitchen_under.jpg'), -2);
assert.equal(exposureBiasFromName('kitchen_over.jpg'), 2);
assert.equal(exposureBiasFromName('plain.jpg'), null);

assert.equal(requiresBracketSet('hdr'), true);
assert.equal(requiresBracketSet('drone'), false);
assert.equal(HDR_BRACKET_MIN, 3);
assert.ok(BRACKET_HINT.includes('bracketed'));

const alone = validateHdrBracketSelection(['living.jpg'], []);
assert.ok(alone);
assert.match(alone.message, /single frames|at least 3/i);

const complete = validateHdrBracketSelection(
  ['living_-2.jpg', 'living_0.jpg', 'living_+2.jpg'],
  [],
);
assert.equal(complete, null);

const topUp = validateHdrBracketSelection(
  ['living_+2.jpg'],
  ['living_-2.jpg', 'living_0.jpg'],
);
assert.equal(topUp, null);

const stillShort = validateHdrBracketSelection(
  ['living_+2.jpg'],
  ['living_-2.jpg'],
);
assert.ok(stillShort);
assert.equal(stillShort.count, 2);

const adminBypass = validateHdrBracketSelection(['solo.jpg'], [], { allowIncomplete: true });
assert.equal(adminBypass, null);

const blockComplete = validateHdrUploadsForComplete([
  { filename: 'a_-1.jpg', media_type: 'hdr' },
  { filename: 'a_0.jpg', media_type: 'hdr' },
]);
assert.ok(blockComplete);

const okComplete = validateHdrUploadsForComplete([
  { filename: 'a_-1.jpg', media_type: 'hdr' },
  { filename: 'a_0.jpg', media_type: 'hdr' },
  { filename: 'a_+1.jpg', media_type: 'hdr' },
  { filename: 'clip.mp4', media_type: 'reel' },
]);
assert.equal(okComplete, null);

console.log('bracket-require tests passed');
