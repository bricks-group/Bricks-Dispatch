/**
 * Staging must be a first-class stills upload kind (no brackets).
 * Twilight requires −/0/+ brackets (real dusk honesty). so GO/web can tag vacant/
 * staged rooms. Backend durable staging_ filename + leftover route=staging
 * deliver originals (no enhance / no virtual staging).
 *
 * Run from web/: node src/lib/stills-upload.test.mjs
 */
import assert from 'node:assert/strict';
import { execSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const stillsSrc = readFileSync(path.join(here, 'stills-upload.ts'), 'utf8');

assert.match(stillsSrc, /id:\s*'staging'/);
assert.match(stillsSrc, /label:\s*'Staging'/);
for (const id of ['hdr', 'drone', 'reel', 'twilight', 'staging']) {
  assert.match(stillsSrc, new RegExp(`id:\\s*'${id}'`));
}

const out = '/tmp/bracket-require-stills.mjs';
execSync(
  `npx --yes esbuild ${path.join(here, 'bracket-require.ts')} --bundle --platform=node --format=esm --outfile=${out}`,
  { stdio: 'inherit', cwd: path.join(here, '../..') },
);
const { requiresBracketSet } = await import(`${out}?t=${Date.now()}`);
assert.equal(requiresBracketSet('staging'), false);
assert.equal(requiresBracketSet('floorplan'), false);
assert.equal(requiresBracketSet('hdr'), true);
assert.equal(requiresBracketSet('twilight'), true);

// Reels: no client-side 50 MB pre-block; the API mints the video cap (≤2 GB).
assert.match(stillsSrc, /!isVideoUpload\(file, mediaType\) && file\.size > UPLOAD_MAX_FILE_BYTES/);
assert.match(stillsSrc, /mediaType === 'reel'/);
assert.match(stillsSrc, /fileTooLargeMessage\(file\.name, sign\.max_file_bytes/);

console.log('stills-upload staging/floorplan picker tests passed');
