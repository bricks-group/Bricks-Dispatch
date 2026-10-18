/**
 * Node test for AEB EXIF auto-naming. Run from web/: node src/lib/aeb-exif.test.mjs
 * Also covers rename output parity with bracket-require (renamed files must group + pass).
 */
import assert from 'node:assert/strict';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
for (const m of ['aeb-exif', 'bracket-require']) {
  execSync(
    `npx --yes esbuild ${path.join(here, m + '.ts')} --bundle --platform=node --format=esm --outfile=/tmp/${m}.mjs`,
    { stdio: 'inherit', cwd: path.join(here, '../..') },
  );
}
const { parseJpegExif, parseRawExif, planAebRenames, formatEv } = await import('/tmp/aeb-exif.mjs');
const { exposureBiasFromName, bracketStem, validateHdrUploadsForComplete } = await import('/tmp/bracket-require.mjs');

/** Minimal JPEG: SOI + APP1 Exif (IFD0 Model + ExifIFD ptr; ExifIFD bias/DTO/SubSec) + EOI. */
function jpeg({ bias, dto, sub, model = 'Canon EOS R6', le = true }) {
  const parts = [];
  const tiff = new Uint8Array(512);
  const v = new DataView(tiff.buffer);
  const w16 = (o, x) => v.setUint16(o, x, le);
  const w32 = (o, x) => v.setUint32(o, x, le);
  tiff[0] = tiff[1] = le ? 0x49 : 0x4d;
  w16(2, 42); w32(4, 8);
  // IFD0 @8: 2 entries
  let data = 200;
  w16(8, 2);
  const modelB = new TextEncoder().encode(model + '\0');
  w16(10, 0x0110); w16(12, 2); w32(14, modelB.length); w32(18, data);
  tiff.set(modelB, data); data += modelB.length;
  w16(22, 0x8769); w16(24, 4); w32(26, 1); w32(30, 60);
  w32(34, 0);
  // Exif IFD @60
  const ents = [];
  if (bias != null) ents.push([0x9204, 10, 1, () => { v.setInt32(data, Math.round(bias * 3), le); v.setInt32(data + 4, 3, le); const o = data; data += 8; return o; }]);
  if (dto) ents.push([0x9003, 2, 20, () => { const b = new TextEncoder().encode(dto + '\0'); tiff.set(b, data); const o = data; data += 20; return o; }]);
  if (sub) ents.push([0x9291, 2, sub.length + 1, () => { const b = new TextEncoder().encode(sub + '\0'); if (b.length <= 4) return { inline: b }; tiff.set(b, data); const o = data; data += b.length; return o; }]);
  w16(60, ents.length);
  ents.forEach(([tag, type, count, put], i) => {
    const e = 62 + i * 12;
    w16(e, tag); w16(e + 2, type); w32(e + 4, count);
    const r = put();
    if (r && r.inline) tiff.set(r.inline, e + 8); else w32(e + 8, r);
  });
  const app1Body = new Uint8Array(6 + tiff.length);
  app1Body.set(new TextEncoder().encode('Exif\0\0'), 0);
  app1Body.set(tiff, 6);
  const len = app1Body.length + 2;
  const out = new Uint8Array(2 + 4 + app1Body.length + 2);
  out.set([0xff, 0xd8, 0xff, 0xe1, len >> 8, len & 0xff], 0);
  out.set(app1Body, 6);
  out.set([0xff, 0xd9], out.length - 2);
  return out.buffer;
}

// Parser: LE + BE, bias in thirds, sub-seconds.
for (const le of [true, false]) {
  const x = parseJpegExif(jpeg({ bias: -2 / 3, dto: '2025:06:01 14:03:07', sub: '45', le }));
  assert.equal(x.bias, -0.67);
  assert.equal(x.model, 'Canon EOS R6');
  assert.ok(Math.abs(x.time - (Date.UTC(2025, 5, 1, 14, 3, 7) / 1000 + 0.45)) < 1e-6);
}
assert.deepEqual(parseJpegExif(new Uint8Array([1, 2, 3]).buffer), { bias: null, time: null, model: null });
assert.equal(parseJpegExif(jpeg({ dto: '2025:06:01 14:03:07' })).bias, null);
assert.equal(formatEv(-2), '-2'); assert.equal(formatEv(0), '0'); assert.equal(formatEv(1.33), '+1.3'); assert.equal(formatEv(0.01), '0');

const needs = (n) => exposureBiasFromName(n) == null;
const frame = (name, bias, s, model = 'Canon EOS R6') => ({
  name, exif: { bias, time: s == null ? null : 1000 + s, model },
});

// 5-shot Canon AEB like 20 George "untitled shoot-4031…4035" + second angle 4036…4038.
const burst = [
  frame('untitled shoot-4031.jpg', 0, 0), frame('untitled shoot-4032.jpg', -1, 0.4),
  frame('untitled shoot-4033.jpg', 1, 0.8), frame('untitled shoot-4034.jpg', -2, 1.2),
  frame('untitled shoot-4035.jpg', 2, 1.6),
  frame('untitled shoot-4036.jpg', 0, 40), frame('untitled shoot-4037.jpg', -2, 40.3),
  frame('untitled shoot-4038.jpg', 2, 40.6),
];
const plan = planAebRenames([...burst].reverse(), needs);
assert.equal(plan.size, 8);
assert.equal(plan.get('untitled shoot-4034.jpg'), 'untitled shoot-4031_-2.jpg');
assert.equal(plan.get('untitled shoot-4035.jpg'), 'untitled shoot-4031_+2.jpg');
assert.equal(plan.get('untitled shoot-4031.jpg'), 'untitled shoot-4031_0.jpg');
assert.equal(plan.get('untitled shoot-4037.jpg'), 'untitled shoot-4036_-2.jpg');
const renamed = burst.map((f) => plan.get(f.name));
assert.equal(new Set(renamed.map(bracketStem)).size, 2);
assert.equal(bracketStem('untitled shoot-4031_+2.jpg'), 'untitled shoot-4031');
assert.equal(validateHdrUploadsForComplete(renamed.map((filename) => ({ filename, media_type: 'hdr' })), {}), null);

// Back-to-back bursts within the gap still split on repeated bias.
const tight = [
  frame('IMG_0001.JPG', 0, 0), frame('IMG_0002.JPG', -2, 0.3), frame('IMG_0003.JPG', 2, 0.6),
  frame('IMG_0004.JPG', 0, 1.5), frame('IMG_0005.JPG', -2, 1.8), frame('IMG_0006.JPG', 2, 2.1),
];
const tp = planAebRenames(tight, needs);
assert.equal(tp.get('IMG_0003.JPG'), 'IMG_0001_+2.JPG');
assert.equal(tp.get('IMG_0004.JPG'), 'IMG_0004_0.JPG');

// Not provable → untouched: no bias, all same bias, gap too large, different camera, already EV-named, run of 2.
assert.equal(planAebRenames([frame('A_0101.jpg', null, 0), frame('A_0102.jpg', -2, 0.3), frame('A_0103.jpg', 2, 0.6)], needs).size, 0);
assert.equal(planAebRenames([frame('A_0101.jpg', 0, 0), frame('A_0102.jpg', 0, 0.3), frame('A_0103.jpg', 0, 0.6)], needs).size, 0);
assert.equal(planAebRenames([frame('A_0101.jpg', 0, 0), frame('A_0102.jpg', -2, 10), frame('A_0103.jpg', 2, 20)], needs).size, 0);
assert.equal(planAebRenames([frame('A_0101.jpg', 0, 0), frame('A_0102.jpg', -2, 0.3, 'Sony A7'), frame('A_0103.jpg', 2, 0.6)], needs).size, 0);
assert.equal(planAebRenames([frame('K_-2.jpg', -2, 0), frame('K_0.jpg', 0, 0.3), frame('K_+2.jpg', 2, 0.6)], needs).size, 0);
assert.equal(planAebRenames([frame('A_0101.jpg', -2, 0), frame('A_0102.jpg', 2, 0.3)], needs).size, 0);
assert.equal(planAebRenames([frame('A_0101.jpg', 0, 0), frame('A_0102.jpg', -2, 0.3), frame('A_0103.jpg', -1, 0.6)], needs).size, 0, 'no over → untouched');

console.log('aeb-exif tests passed');

// Camera RAW heads (real samples from raw.pixls.us when present on this box).
{
  const fs = await import('node:fs');
  const dir = process.env.RAW_SAMPLES_DIR || '/workspace/samples-raw';
  const want = {
    'Canon_EOS_R6_RAW_ISO_100_nocrop_nodual.CR3': 'Canon EOS R6',
    'Nikon-D850-14bit-lossless-compressed.NEF': 'NIKON D850',
    '_DSC0009.ARW': 'ILCE-7M3',
    'AFXT2720.RAF': 'X-T3',
    'iPhone12Pro_IMG_1361.DNG': 'iPhone 12 Pro',
    'B13A0729.CR2': 'Canon EOS 5D Mark IV',
  };
  let checked = 0;
  for (const [name, model] of Object.entries(want)) {
    const p = path.join(dir, name);
    if (!fs.existsSync(p)) continue;
    const fd = fs.openSync(p, 'r');
    const buf = Buffer.alloc(1024 * 1024);
    fs.readSync(fd, buf, 0, buf.length, 0);
    fs.closeSync(fd);
    const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.length);
    const x = parseRawExif(ab);
    assert.equal(x.model, model, name);
    assert.equal(x.bias, 0, name);
    assert.ok(typeof x.time === 'number' && x.time > 1e9, name);
    checked++;
  }
  console.log(`aeb-exif: RAW EXIF checked on ${checked} sample files`);
}
console.log('aeb-exif RAW tests ok');
