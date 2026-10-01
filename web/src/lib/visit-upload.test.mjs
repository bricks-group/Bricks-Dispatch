/**
 * GO Visit file upload must reach the same /visit/deliver finalize as Polycam URL.
 * Run from web/: node src/lib/visit-upload.test.mjs
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = readFileSync(path.join(here, 'visit-upload.ts'), 'utf8');
const page = readFileSync(path.join(here, '../pages/VisitCapturePage.tsx'), 'utf8');

assert.match(src, /\.ply/);
assert.match(src, /\.splat/);
assert.match(src, /\.spz/);
assert.match(src, /\/api\/capture\/visit\/sign/);
assert.match(src, /\/api\/capture\/visit\/deliver/);
assert.match(src, /uploadToSignedUrl/);
assert.match(src, /polycam_url/);
assert.match(src, /storage_path/);
assert.match(src, /export async function deliverVisitFile/);
assert.match(src, /export async function deliverVisitUrl/);
assert.match(src, /apiErrorMessage/);

assert.match(page, /deliverVisitFile/);
assert.match(page, /deliverVisitUrl/);
assert.match(page, /formatVisitFileLabel/);
assert.match(page, /VISIT_EXTS/);

console.log('visit-upload Polycam URL + .ply/.splat/.spz parity tests passed');
