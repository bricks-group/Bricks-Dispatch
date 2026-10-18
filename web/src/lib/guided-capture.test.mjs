/**
 * Guided capture: guide data, device policy, motion coach, upload byte ranges, web/native parity.
 * Run from web/: node src/lib/guided-capture.test.mjs
 */
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import os from 'node:os';

const here = path.dirname(fileURLToPath(import.meta.url));
const tmp = mkdtempSync(path.join(os.tmpdir(), 'gc-test-'));

async function load(rel, stubs = {}) {
  const out = path.join(tmp, rel.replace(/[\\/]/g, '_') + '.mjs');
  await build({
    entryPoints: [path.join(here, rel)], bundle: true, format: 'esm', platform: 'node', outfile: out, logLevel: 'silent',
    plugins: [{
      name: 'stubs',
      setup(b) {
        for (const [mod, src] of Object.entries(stubs)) {
          b.onResolve({ filter: new RegExp(`^${mod.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')}$`) }, () => ({ path: mod, namespace: 'stub' }));
          b.onLoad({ filter: /.*/, namespace: 'stub' }, (a) => ({ contents: stubs[a.path], loader: 'js' }));
        }
      },
    }],
  });
  return import(pathToFileURL(out).href);
}

const G = await load('guided-capture/guides.ts');
const P = await load('guided-capture/device-policy.ts');
const M = await load('guided-capture/motion.ts');
const U = await load('guided-capture/visit-video-upload.ts', {
  '../api': 'export const API_BASE = "https://api.test";',
  '../visit-upload': 'export const apiErrorMessage = (b, f) => f;',
  '../supabase': 'export const supabase = { auth: { getSession: async () => ({ data: { session: null } }) } };',
});

/* guides */
const vv = G.getGuide('virtual-visit');
assert.ok(vv, 'virtual-visit guide exists');
assert.deepEqual(G.validateGuide(vv), [], 'virtual-visit guide validates');
assert.equal(vv.camera.lens, 'ultra-wide');
assert.equal(vv.camera.longEdge, 3840);
assert.equal(vv.camera.fps, 30);
assert.equal(vv.camera.hdr, false);
assert.equal(vv.camera.stabilization, 'standard');
assert.deepEqual(vv.locks, ['exposure', 'focus', 'whiteBalance']);
assert.deepEqual(vv.steps.map((s) => s.id), ['door', 'chest', 'slow', 'pause', 'turn']);
assert.equal(vv.limits.pauseSec, 2);
assert.equal(G.getGuide('film-clip'), null, 'film clips are not phone guides yet');
assert.equal(G.getGuide(null), null);
// Copy rules: sentence case, never GO / Bricks OS.
const copy = JSON.stringify(vv);
assert.doesNotMatch(copy, /\bGO\b|Bricks OS/);
for (const s of vv.steps) assert.match(s.title, /^[A-Z][^A-Z]*$/, `sentence case: ${s.title}`);
// A JSON guide from bricks-standard can be validated before plugging in.
assert.ok(G.validateGuide({ id: 'still', version: 1, title: 'X', device: 'dedicated-camera', steps: [], locks: ['iso'] }).length >= 2);
assert.ok(G.validateGuide(null).length === 1);
assert.equal(Math.round(G.estimateBytes(vv, 60) / 1e6), 225);

/* device policy */
const ok = (d) => P.checkDevice(d).status;
assert.equal(ok({ os: 'ios', modelId: 'iPhone14,2' }), 'approved');            // 13 Pro
assert.equal(ok({ os: 'ios', modelId: 'iPhone14,3' }), 'approved');            // 13 Pro Max
assert.equal(ok({ os: 'ios', modelId: 'iPhone14,5' }), 'not-approved');        // 13
assert.equal(ok({ os: 'ios', modelId: 'iPhone14,4' }), 'not-approved');        // 13 mini
assert.equal(ok({ os: 'ios', modelId: 'iPhone14,6' }), 'not-approved');        // SE 3
assert.equal(ok({ os: 'ios', modelId: 'iPhone13,3' }), 'not-approved');        // 12 Pro
assert.equal(ok({ os: 'ios', modelId: 'iPhone16,1' }), 'approved');            // 15 Pro
assert.equal(ok({ os: 'ios', modelId: 'iPhone17,5' }), 'not-approved');        // 16e
assert.equal(ok({ os: 'ios', modelId: 'iPhone18,4' }), 'not-approved');        // Air
assert.equal(ok({ os: 'ios', modelId: 'iPhone19,1' }), 'approved');            // future
assert.equal(ok({ os: 'ios', modelId: 'iPad14,1' }), 'not-approved');
assert.equal(ok({ os: 'ios', modelName: 'iPhone 13 Pro' }), 'approved');
assert.equal(ok({ os: 'ios', modelName: 'iPhone 12' }), 'not-approved');
assert.equal(ok({ os: 'ios' }), 'unknown');                                     // Safari never shares the model
assert.equal(P.checkDevice({ os: 'ios', modelId: 'iPhone15,2' }).label, 'iPhone 14 Pro');
assert.equal(ok({ os: 'android', modelId: 'Pixel 8' }), 'approved');
assert.equal(ok({ os: 'android', modelId: 'Pixel 8a' }), 'approved');
assert.equal(ok({ os: 'android', modelId: 'Pixel 9 Pro Fold' }), 'approved');
assert.equal(ok({ os: 'android', modelId: 'Pixel 7 Pro' }), 'not-approved');
assert.equal(ok({ os: 'android', modelId: 'Pixel Fold' }), 'not-approved');
assert.equal(ok({ os: 'android', modelId: 'SM-S911B' }), 'approved');           // S23
assert.equal(ok({ os: 'android', modelId: 'SM-S928U' }), 'approved');           // S24 Ultra
assert.equal(ok({ os: 'android', modelId: 'SM-S711B' }), 'approved');           // S23 FE
assert.equal(ok({ os: 'android', modelId: 'SM-S901B' }), 'not-approved');       // S22
assert.equal(P.checkDevice({ os: 'android', modelId: 'SM-S921B' }).label, 'Galaxy S24');
assert.equal(ok({ os: 'android', modelName: 'Galaxy S23 Ultra' }), 'approved');
assert.equal(ok({ os: 'android', modelId: 'SM-A546B' }), 'not-approved');       // A54
assert.equal(ok({ os: 'android' }), 'unknown');
assert.equal(ok({ os: 'other' }), 'not-approved');
assert.match(P.POLICY_LINE, /iPhone 13 Pro or newer, Pixel 8 or newer, or Galaxy S23 or newer/);

/* motion */
const lvl = M.levelFromGravity({ x: 0, y: -9.81, z: 0 });
assert.ok(Math.abs(lvl.rollDeg) < 0.01 && Math.abs(lvl.pitchDeg) < 0.01, 'upright portrait is level');
const tipped = M.levelFromGravity({ x: Math.sin(0.35) * 9.81, y: -Math.cos(0.35) * 9.81, z: 0 });
assert.ok(Math.abs(tipped.rollDeg - 20.05) < 0.2, `roll ≈ 20°, got ${tipped.rollDeg}`);
assert.equal(M.levelFromGravity({ x: 0, y: 0, z: 0.5 }), null);
// Android sign convention (gravity +y) gives the same magnitude.
assert.ok(Math.abs(Math.abs(M.levelFromGravity({ x: Math.sin(0.35) * 9.81, y: Math.cos(0.35) * 9.81, z: 0 }).rollDeg) - 20.05) < 0.2);
// Pan = rotation about gravity; spinning about the phone's own y axis while upright is panning.
assert.ok(Math.abs(M.panRate({ alpha: 0, beta: 0, gamma: 50 }, { x: 0, y: -9.8, z: 0 }) - 50) < 0.01);
assert.ok(M.panRate({ alpha: 50, beta: 0, gamma: 0 }, { x: 0, y: -9.8, z: 0 }) < 0.01, 'roll is not pan');

function simulate({ seconds = 8, hz = 50, stepHz = 1.3, amp = 1.2, pan = 5, roll = 0 }) {
  const coach = new M.MotionCoach(vv.limits);
  let r;
  for (let i = 0; i < seconds * hz; i++) {
    const t = (i * 1000) / hz;
    const s = t / 1000;
    const bounce = stepHz ? Math.sin(2 * Math.PI * stepHz * s) * amp : 0;
    r = coach.push({
      t,
      rotationRate: { alpha: 0, beta: 0, gamma: pan },
      acceleration: { x: 0, y: -bounce, z: 0 },
      gravity: { x: Math.sin(roll) * 9.81, y: -Math.cos(roll) * 9.81, z: 0 },
    });
  }
  return { r, coach };
}
const calm = simulate({});
assert.equal(calm.r.cue, null, `calm walk has no cue, got ${calm.r.cue?.title}`);
assert.ok(calm.r.walkMps > 0.5 && calm.r.walkMps < 1.0, `slow walk ≈ 0.7 m/s, got ${calm.r.walkMps}`);
assert.ok(Math.abs(calm.r.cadenceSpm - 78) <= 4, `cadence ≈ 78 spm, got ${calm.r.cadenceSpm}`);
const fast = simulate({ stepHz: 2.0, amp: 1.6 });
assert.equal(fast.r.cue?.title, 'Slow down');
const turning = simulate({ pan: 80 });
assert.equal(turning.r.cue?.title, 'Turn slower');
const tilted = simulate({ roll: 0.4 });
assert.equal(tilted.r.cue?.title, 'Hold level');
const paused = simulate({ stepHz: 0, pan: 0, seconds: 3 });
assert.equal(paused.r.paused, true, 'a 2 s pause is detected');
assert.equal(paused.r.pauses, 1);
const s = turning.coach.summary();
assert.ok(s.turn_fast_pct > 50 && s.max_turn_dps >= 70, JSON.stringify(s));
assert.equal(M.coachVerdict(calm.coach.summary()).tone, 'ok');
assert.equal(M.coachVerdict(s).tone, 'warn');
assert.match(M.coachVerdict(s).body, /^Fast turns/);
assert.equal(M.coachVerdict(null).title, 'No motion data');
for (const c of Object.values(M.CUES)) assert.doesNotMatch(c.title + c.body, /\bGO\b|Bricks OS/);

/* upload byte ranges (8 MB parts, last part short) */
const MB = 1024 * 1024;
assert.deepEqual(U.partRange(1, 8 * MB, 3, 20 * MB + 123), { start: 0, end: 8 * MB });
assert.deepEqual(U.partRange(3, 8 * MB, 3, 20 * MB + 123), { start: 16 * MB, end: 20 * MB + 123 });
assert.equal(U.contentTypeFor(new Blob([], { type: 'video/quicktime' })), 'video/quicktime');
assert.equal(U.contentTypeFor(new Blob([], { type: 'video/webm;codecs=vp9' })), 'video/webm');
assert.equal(U.contentTypeFor(new Blob([])), 'video/mp4');

/* web ↔ native parity: shared files must be identical */
for (const f of ['guides.ts', 'motion.ts', 'device-policy.ts']) {
  const a = readFileSync(path.join(here, 'guided-capture', f), 'utf8');
  const b = readFileSync(path.join(here, '../../../frontend/lib/guided-capture', f), 'utf8');
  assert.equal(a, b, `frontend/lib/guided-capture/${f} drifted from web/src/lib/guided-capture/${f}`);
}

/* page honesty: never claims a lock the browser didn't confirm; no GO / Bricks OS in UI copy */
const page = readFileSync(path.join(here, '../pages/GuidedCapturePage.tsx'), 'utf8');
const cam = readFileSync(path.join(here, 'guided-capture/web-camera.ts'), 'utf8');
assert.match(cam, /getCapabilities/);
assert.match(cam, /getSettings/);
assert.match(cam, /exposureMode/);
assert.match(cam, /whiteBalanceMode/);
assert.match(page, /Safari can’t lock exposure or focus/);
assert.match(page, /Use the Camera app instead/);
assert.match(page, /import\.meta\.env\.DEV/, 'demo mode is dev-only');
assert.doesNotMatch(page.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, ''), />[^<]*\b(GO|Bricks OS)\b[^<]*</);

console.log('guided-capture.test.mjs: all assertions passed');
