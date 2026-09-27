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
  bracketHasUnderMidOver,
  inferMediaType,
  isCameraRawName,
  isImageName,
  cameraRawRejectionMessage,
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
assert.equal(exposureBiasFromName('kitchen_normal.jpg'), 0);
assert.equal(exposureBiasFromName('plain.jpg'), null);

assert.equal(inferMediaType('DJI_0123.jpg', 'image/jpeg'), 'drone');
assert.equal(inferMediaType('clip.mp4', 'video/mp4'), 'reel');
assert.equal(inferMediaType('living_0.jpg', 'image/jpeg'), 'hdr');

assert.equal(requiresBracketSet('hdr'), true);
assert.equal(requiresBracketSet('twilight'), true);
assert.equal(requiresBracketSet('drone'), false);
assert.equal(requiresBracketSet('staging'), false);
assert.equal(requiresBracketSet('floorplan'), false);
assert.equal(inferMediaType('floorplan_main.png'), 'floorplan');
assert.equal(inferMediaType('cubicasa_export.png'), 'floorplan');
assert.equal(inferMediaType('plan.pdf', 'application/pdf', 'floor_plan'), 'floorplan');
assert.equal(HDR_BRACKET_MIN, 3);
assert.ok(BRACKET_HINT.includes('bracketed'));

assert.equal(bracketHasUnderMidOver(['a_-2.jpg', 'a_0.jpg', 'a_+2.jpg']), true);
assert.equal(bracketHasUnderMidOver(['a_+1.jpg', 'a_+2.jpg', 'a_+3.jpg']), false);

const alone = validateHdrBracketSelection(['living.jpg'], []);
assert.ok(alone);
assert.match(alone.message, /single frames|at least 3/i);

const complete = validateHdrBracketSelection(
  ['living_-2.jpg', 'living_0.jpg', 'living_+2.jpg'],
  [],
);
assert.equal(complete, null);

const sameSide = validateHdrBracketSelection(
  ['room_+1.jpg', 'room_+2.jpg', 'room_+3.jpg'],
  [],
);
assert.ok(sameSide);
assert.match(sameSide.message, /under, mid, and over/i);

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

// GO/API parity: DJI without media_type must not false-block beside full HDR
const withDrone = validateHdrUploadsForComplete([
  { filename: 'living_-2.jpg', mime_type: 'image/jpeg' },
  { filename: 'living_0.jpg', mime_type: 'image/jpeg' },
  { filename: 'living_+2.jpg', mime_type: 'image/jpeg' },
  { filename: 'DJI_0123.jpg', mime_type: 'image/jpeg' },
]);
assert.equal(withDrone, null);

const overOnly = validateHdrUploadsForComplete([
  { filename: 'room_+1.jpg', mime_type: 'image/jpeg' },
  { filename: 'room_+2.jpg', mime_type: 'image/jpeg' },
  { filename: 'room_+3.jpg', mime_type: 'image/jpeg' },
]);
assert.ok(overOnly);
assert.match(overOnly.message, /under, mid, and over/i);


// Twilight explicit media_type — incomplete blocks Mark complete
const twilightShort = validateHdrUploadsForComplete([
  { filename: 'dusk_-2.jpg', media_type: 'twilight' },
  { filename: 'dusk_0.jpg', media_type: 'twilight' },
]);
assert.ok(twilightShort);
assert.match(twilightShort.message, /still angle|bracket/i);

// Inferred durable twilight_*.jpg (no media_type) also gated
const twilightInferred = validateHdrUploadsForComplete([
  { filename: 'twilight_front.jpg', mime_type: 'image/jpeg' },
]);
assert.ok(twilightInferred);

// Complete twilight −/0/+ passes
const twilightOk = validateHdrUploadsForComplete([
  { filename: 'twilight_front_-2.jpg', media_type: 'twilight' },
  { filename: 'twilight_front_0.jpg', media_type: 'twilight' },
  { filename: 'twilight_front_+2.jpg', media_type: 'twilight' },
]);
assert.equal(twilightOk, null);

// Staging / aerial_ / DJI still skip gate
assert.equal(
  validateHdrUploadsForComplete([{ filename: 'staging_living.jpg', media_type: 'staging' }]),
  null,
);
assert.equal(
  validateHdrUploadsForComplete([{ filename: 'aerial_roof.jpg', mime_type: 'image/jpeg' }]),
  null,
);
assert.equal(
  validateHdrUploadsForComplete([{ filename: 'DJI_0456.jpg', mime_type: 'image/jpeg' }]),
  null,
);


// Camera RAW reject parity with API CAMERA_RAW_EXTS / frontend helpers
assert.equal(isCameraRawName('living_-2.CR3'), true);
assert.equal(isCameraRawName('shot.dng'), true);
assert.equal(isCameraRawName('a.NEF'), true);
assert.equal(isCameraRawName('living_0.jpg'), false);
assert.equal(isCameraRawName('shot.HEIC'), false);
assert.equal(isImageName('living_0.jpg'), true);
assert.equal(isImageName('shot.heic'), true);
assert.equal(isImageName('living_-2.CR3'), false);
assert.equal(isImageName('shot.dng'), false);
assert.match(cameraRawRejectionMessage, /JPEG \(or HEIC\)/i);
assert.match(cameraRawRejectionMessage, /not supported/i);
// RAW must not count as bracket stills (would hollow enhance→listing)
assert.equal(
  validateHdrBracketSelection(['living_-2.CR3', 'living_0.CR3', 'living_+2.CR3'], []),
  null,
);

console.log('bracket-require tests passed');
