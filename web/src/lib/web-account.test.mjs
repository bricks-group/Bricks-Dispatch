/**
 * GO web account-deletion + Google provider probe. Supabase is mocked.
 * Run from web/: node --test src/lib/web-account.test.mjs (self-builds via npx esbuild)
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = '/tmp/web-account-test-bundle.mjs';
execSync(`npx --yes esbuild ${path.join(here, 'web-account.ts')} --bundle --platform=node --format=esm --outfile=${out}`, { stdio: 'inherit', cwd: path.join(here, '../..') });
const m = await import(out);

const client = (result, sink = []) => ({ from: (t) => ({ insert: async (row) => { sink.push({ t, row }); return result; } }) });

test('deletion request writes the own-user row (go, platform web)', async () => {
  const sink = [];
  const r = await m.requestAccountDeletion(client({ error: null }, sink), { id: 'u1', email: 'a@b.ca' }, '  moving on  ');
  assert.deepEqual(r, { ok: true, via: 'request' });
  assert.equal(sink[0].t, 'account_deletion_requests');
  assert.deepEqual(sink[0].row, { user_id: 'u1', email: 'a@b.ca', app: 'go', platform: 'web', reason: 'moving on' });
});

test('second tap (unique open request) is success, not an error', async () => {
  const r = await m.requestAccountDeletion(client({ error: { code: '23505', message: 'dup' } }), { id: 'u1' });
  assert.equal(r.ok, true); assert.equal(r.already, true);
});

test('RLS / table errors fall back to a pre-filled email', async () => {
  const r = await m.requestAccountDeletion(client({ error: { code: '42501', message: 'rls' } }), { id: 'u1', email: 'a@b.ca' });
  assert.equal(r.ok, false);
  assert.match(r.mailto, /^mailto:help@brxs\.ca\?subject=Delete%20my%20Bricks%20account/);
  assert.match(decodeURIComponent(r.mailto), /User ID: u1/);
});

test('signed-out user is told to sign in', async () => {
  const r = await m.requestAccountDeletion(client({ error: null }), null);
  assert.equal(r.ok, false); assert.match(r.error, /Sign in again/);
});

test('google provider probe reads external.google', async () => {
  const f = (body, ok = true) => async () => ({ ok, json: async () => body });
  assert.equal(await m.fetchOAuthProviderEnabled('google', 'https://x.supabase.co', 'k', f({ external: { google: false } })), false);
  assert.equal(await m.fetchOAuthProviderEnabled('google', 'https://x.supabase.co', 'k', f({ external: { google: true } })), true);
  assert.equal(await m.fetchOAuthProviderEnabled('google', 'https://x.supabase.co', 'k', f({}, false)), null);
  assert.equal(await m.fetchOAuthProviderEnabled('google', '', 'k', f({})), null);
  assert.ok(m.isProviderDisabledError({ message: 'Unsupported provider: provider is not enabled' }));
  assert.ok(!m.isProviderDisabledError({ message: 'Network error' }));
});
