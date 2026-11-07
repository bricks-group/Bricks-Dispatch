import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { state, reset } from './stubs/state.mjs';
import { BRICKS_GO_AUTH_CALLBACK, AUTH_CALLBACK_MESSAGES, parseAuthCallback } from '../lib/oauthCallback.ts';
import { supabaseAuthOptions, SSR_AUTH_OPTIONS } from '../lib/supabaseAuthOptions.ts';

// Run with: node --import ./tests/stubs/register.mjs --test tests/
const { connectGoogleCalendar } = await import('../lib/calendarConnect.ts');

const CB = BRICKS_GO_AUTH_CALLBACK;

// Backend replies: connect says "use Supabase", complete-session confirms access.
globalThis.fetch = async (url, init = {}) => {
  state.fetchCalls.push(String(url));
  const body = String(url).endsWith('/google/connect')
    ? { connected: false, mode: 'supabase' }
    : String(url).endsWith('/complete-session')
      ? { connected: true }
      : {};
  return { ok: true, status: 200, json: async () => body };
};

beforeEach(() => reset());

test('PKCE: native/web client uses flowType pkce with the given persistent storage', () => {
  const storage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
  const opts = supabaseAuthOptions(storage);
  assert.equal(opts.flowType, 'pkce');
  assert.equal(opts.storage, storage);
  assert.equal(opts.persistSession, true);
  assert.equal(opts.detectSessionInUrl, false);
  assert.equal(SSR_AUTH_OPTIONS.flowType, 'pkce');
});

test('PKCE: supabase.ts wires the PKCE options with AsyncStorage for every client', () => {
  const src = readFileSync(new URL('../lib/supabase.ts', import.meta.url), 'utf8');
  assert.match(src, /supabaseAuthOptions\(AsyncStorage\)/);
  assert.match(src, /auth: SSR_AUTH_OPTIONS/);
  assert.doesNotMatch(src, /createClient\(supabaseUrl, supabaseAnonKey\)\s*;/);
});

test('callback: valid production redirect yields the code', () => {
  assert.deepEqual(parseAuthCallback(`${CB}?code=abc123`, CB), { ok: true, code: 'abc123' });
  assert.deepEqual(parseAuthCallback(`${CB}/?code=abc123`, CB), { ok: true, code: 'abc123' });
});

test('callback: valid Expo dev redirect with its own port yields the code', () => {
  const dev = 'exp://127.0.0.1:8081/--/auth-callback';
  assert.deepEqual(parseAuthCallback(`${dev}?code=dev`, dev), { ok: true, code: 'dev' });
});

const unexpected = { ok: false, reason: 'unexpected_callback' };
test('callback: wrong scheme is rejected', () => {
  assert.deepEqual(parseAuthCallback('https://auth-callback?code=x', CB), unexpected);
  assert.deepEqual(parseAuthCallback('evil-go://auth-callback?code=x', CB), unexpected);
});
test('callback: wrong host is rejected', () => {
  assert.deepEqual(parseAuthCallback('bricks-go://evil.example?code=x', CB), unexpected);
  assert.deepEqual(parseAuthCallback('bricks-go://auth-callback.evil?code=x', CB), unexpected);
});
test('callback: wrong path is rejected', () => {
  assert.deepEqual(parseAuthCallback('bricks-go://auth-callback/other?code=x', CB), unexpected);
  const dev = 'exp://127.0.0.1:8081/--/auth-callback';
  assert.deepEqual(parseAuthCallback('exp://127.0.0.1:8081/--/other?code=x', dev), unexpected);
});
test('callback: unexpected or different port is rejected', () => {
  assert.deepEqual(parseAuthCallback('bricks-go://auth-callback:8081?code=x', CB), unexpected);
  const dev = 'exp://127.0.0.1:8081/--/auth-callback';
  assert.deepEqual(parseAuthCallback('exp://127.0.0.1:9999/--/auth-callback?code=x', dev), unexpected);
  assert.deepEqual(parseAuthCallback('exp://127.0.0.1/--/auth-callback?code=x', dev), unexpected);
});
test('callback: credentials, duplicate codes and garbage are rejected', () => {
  assert.deepEqual(parseAuthCallback('bricks-go://u:p@auth-callback?code=x', CB), unexpected);
  assert.deepEqual(parseAuthCallback(`${CB}?code=a&code=b`, CB), unexpected);
  assert.deepEqual(parseAuthCallback('not a url', CB), unexpected);
  assert.deepEqual(parseAuthCallback('', CB), unexpected);
});
test('callback: provider error is rejected even with a code', () => {
  const err = { ok: false, reason: 'provider_error' };
  assert.deepEqual(parseAuthCallback(`${CB}?error=access_denied&code=still`, CB), err);
  assert.deepEqual(parseAuthCallback(`${CB}?error_description=nope`, CB), err);
  assert.deepEqual(parseAuthCallback(`${CB}?code=x#error=server_error`, CB), err);
});
test('callback: missing code (including implicit hash tokens) is rejected', () => {
  const missing = { ok: false, reason: 'missing_code' };
  assert.deepEqual(parseAuthCallback(CB, CB), missing);
  assert.deepEqual(parseAuthCallback(`${CB}?code=`, CB), missing);
  assert.deepEqual(parseAuthCallback(`${CB}#access_token=implicit`, CB), missing);
  assert.deepEqual(parseAuthCallback(`${CB}?code=${'a'.repeat(2049)}`, CB), missing);
});
test('callback messages are plain language, not code', () => {
  for (const msg of Object.values(AUTH_CALLBACK_MESSAGES)) {
    assert.doesNotMatch(msg, /[_{}<>`]|code=|error=|https?:/);
    assert.match(msg, /try again\.$/);
  }
});

async function connectWith(url) {
  state.authResult = { type: 'success', url };
  return connectGoogleCalendar();
}

test('connect: valid callback exchanges exactly once and completes', async () => {
  const res = await connectWith(`${CB}?code=good`);
  assert.deepEqual(res, { connected: true });
  assert.deepEqual(state.exchangeCalls, ['good']);
  assert.ok(state.fetchCalls.some((u) => u.endsWith('/google/complete-session')));
});

for (const [name, url, reason] of [
  ['wrong scheme', 'https://auth-callback?code=x', 'unexpected_callback'],
  ['wrong host', 'bricks-go://evil.example?code=x', 'unexpected_callback'],
  ['wrong path', 'bricks-go://auth-callback/evil?code=x', 'unexpected_callback'],
  ['unexpected port', 'bricks-go://auth-callback:4444?code=x', 'unexpected_callback'],
  ['provider error', `${CB}?error=access_denied&code=x`, 'provider_error'],
  ['missing code', `${CB}?state=only`, 'missing_code'],
]) {
  test(`connect: ${name} never exchanges and shows a friendly message`, async () => {
    const res = await connectWith(url);
    assert.equal(res.connected, false);
    assert.equal(res.error, AUTH_CALLBACK_MESSAGES[reason]);
    assert.deepEqual(state.exchangeCalls, []);
    assert.ok(!state.fetchCalls.some((u) => u.endsWith('/complete-session')));
  });
}

test('login: Google sign-in validates the callback before exchanging', () => {
  const src = readFileSync(new URL('../app/(auth)/login.tsx', import.meta.url), 'utf8');
  const guard = src.indexOf('parseAuthCallback(result.url, redirectTo)');
  const exchange = src.indexOf('exchangeCodeForSession(callback.code)');
  assert.ok(guard > 0 && exchange > guard);
  assert.doesNotMatch(src, /searchParams\.get\('code'\)/);
});
