// Run with: npm test  (node --import ./tests/stubs/register.mjs --test tests/*.test.mjs)
import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { state, reset } from './stubs/state.mjs';
import { BRICKS_GO_AUTH_CALLBACK, AUTH_CALLBACK_MESSAGES, parseAuthCallback } from '../lib/oauthCallback.ts';
import { supabaseAuthOptions, SSR_AUTH_OPTIONS, calendarOAuthAuthOptions } from '../lib/supabaseAuthOptions.ts';
import { completeGoogleSignIn, GOOGLE_SIGN_IN_FAILED } from '../lib/googleSignIn.ts';

const CB = BRICKS_GO_AUTH_CALLBACK;
const API = 'https://dispatch-api.brxs.ca';

// One fake network: Dispatch API + the isolated client's real auth-js calls to Supabase Auth.
globalThis.fetch = async (input, init = {}) => {
  const url = String(input instanceof Request ? input.url : input);
  state.fetchCalls.push(url);
  const json = (body, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
  if (url === `${API}/api/vendor-os/calendars/google/connect`) return json({ connected: false, mode: 'supabase' });
  if (url === `${API}/api/vendor-os/calendars/google/complete-session`) return json({ connected: true });
  if (url === `${API}/api/vendor-os/calendars/sync`) return json({});
  if (url.startsWith('https://supabase.test/auth/v1/token?grant_type=pkce')) {
    state.exchangeBodies.push(JSON.parse(init.body));
    if (state.onExchange) state.onExchange();
    if (state.exchangeMode === 'error') return json({ error: 'invalid_grant', error_description: 'bad code' }, 400);
    return json({
      access_token: 'google-temp-access',
      refresh_token: 'google-temp-refresh',
      expires_in: 3600,
      token_type: 'bearer',
      provider_token: 'pt',
      provider_refresh_token: state.exchangeMode === 'no_refresh' ? null : 'prt',
      user: { id: 'google-temp-user', aud: 'authenticated', app_metadata: {}, user_metadata: {}, created_at: '2026-01-01T00:00:00Z' },
    });
  }
  if (url.startsWith('https://supabase.test/auth/v1/logout')) {
    state.logoutCalls += 1;
    return new Response(null, { status: 204 });
  }
  throw new Error(`unexpected fetch ${url}`);
};

const { connectGoogleCalendar, CALENDAR_SIGN_IN_AGAIN } = await import('../lib/calendarConnect.ts');

beforeEach(() => reset());

const completed = () => state.fetchCalls.some((u) => u.endsWith('/google/complete-session'));
const vendorUntouched = () => {
  assert.equal(state.vendorSetSessionCalls, 0, 'vendor session must never be replaced or restored');
  assert.equal(state.vendorExchangeCalls, 0, 'vendor client must never exchange a calendar code');
  assert.equal(state.vendorOAuthCalls, 0, 'vendor client must never start calendar OAuth');
};

// ---------- PKCE configuration ----------
test('PKCE: app client uses flowType pkce with the given persistent storage', () => {
  const storage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
  const opts = supabaseAuthOptions(storage);
  assert.equal(opts.flowType, 'pkce');
  assert.equal(opts.storage, storage);
  assert.equal(opts.persistSession, true);
  assert.equal(opts.detectSessionInUrl, false);
  assert.equal(SSR_AUTH_OPTIONS.flowType, 'pkce');
});
test('PKCE: calendar client is in-memory, PKCE, and has its own storage key', () => {
  const opts = calendarOAuthAuthOptions('bricks-go-calendar-oauth-7');
  assert.equal(opts.flowType, 'pkce');
  assert.equal(opts.persistSession, false);
  assert.equal(opts.autoRefreshToken, false);
  assert.equal(opts.storageKey, 'bricks-go-calendar-oauth-7');
  assert.equal('storage' in opts, false);
});
test('PKCE: supabase.ts wires the PKCE options with AsyncStorage for every client', () => {
  const src = readFileSync(new URL('../lib/supabase.ts', import.meta.url), 'utf8');
  assert.match(src, /supabaseAuthOptions\(AsyncStorage\)/);
  assert.match(src, /auth: SSR_AUTH_OPTIONS/);
  assert.doesNotMatch(src, /createClient\(supabaseUrl, supabaseAnonKey\)\s*;/);
});

// ---------- callback parsing ----------
const unexpected = { ok: false, reason: 'unexpected_callback' };
test('callback: valid production redirect yields the code', () => {
  assert.deepEqual(parseAuthCallback(`${CB}?code=abc123`, CB), { ok: true, code: 'abc123' });
  assert.deepEqual(parseAuthCallback(`${CB}/?code=abc123`, CB), { ok: true, code: 'abc123' });
});
test('callback: valid Expo dev redirect with its own port yields the code', () => {
  const dev = 'exp://127.0.0.1:8081/--/auth-callback';
  assert.deepEqual(parseAuthCallback(`${dev}?code=dev`, dev), { ok: true, code: 'dev' });
});
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
test('callback: code with token-bearing fragment is rejected', () => {
  for (const frag of [
    'access_token=a&refresh_token=r',
    'refresh_token=r',
    'id_token=i',
    'provider_token=p',
    'provider_refresh_token=p',
    'code=other',
  ]) {
    assert.deepEqual(parseAuthCallback(`${CB}?code=good#${frag}`, CB), unexpected, frag);
  }
  // A harmless fragment does not block a valid code.
  assert.deepEqual(parseAuthCallback(`${CB}?code=good#`, CB), { ok: true, code: 'good' });
});
test('callback: provider error is rejected even with a code', () => {
  const err = { ok: false, reason: 'provider_error' };
  assert.deepEqual(parseAuthCallback(`${CB}?error=access_denied&code=still`, CB), err);
  assert.deepEqual(parseAuthCallback(`${CB}?error_description=nope`, CB), err);
  assert.deepEqual(parseAuthCallback(`${CB}?code=x#error=server_error`, CB), err);
});
test('callback: missing code is rejected', () => {
  const missing = { ok: false, reason: 'missing_code' };
  assert.deepEqual(parseAuthCallback(CB, CB), missing);
  assert.deepEqual(parseAuthCallback(`${CB}?code=`, CB), missing);
  assert.deepEqual(parseAuthCallback(`${CB}?state=only`, CB), missing);
  assert.deepEqual(parseAuthCallback(`${CB}?code=${'a'.repeat(2049)}`, CB), missing);
});
test('callback: implicit hash tokens without a code are rejected', () => {
  assert.equal(parseAuthCallback(`${CB}#access_token=implicit`, CB).ok, false);
});
test('callback messages are plain language, not code', () => {
  for (const msg of [...Object.values(AUTH_CALLBACK_MESSAGES), CALENDAR_SIGN_IN_AGAIN, GOOGLE_SIGN_IN_FAILED]) {
    assert.doesNotMatch(msg, /[_{}<>`]|code=|error=|https?:/);
    assert.match(msg, /again\.$/);
  }
});

// ---------- calendar connect (real auth-js in an isolated client) ----------
const respond = (url) => { state.authResult = async () => ({ type: 'success', url }); };

test('connect: valid callback exchanges once on the isolated client with its own PKCE verifier', async () => {
  respond(`${CB}?code=good`);
  const res = await connectGoogleCalendar();
  assert.deepEqual(res, { connected: true });
  assert.equal(state.exchangeBodies.length, 1);
  assert.equal(state.exchangeBodies[0].auth_code, 'good');
  // Verifier sent on exchange matches the challenge in the URL we opened.
  const challenge = new URL(state.lastAuthUrl).searchParams.get('code_challenge');
  const expected = createHash('sha256').update(state.exchangeBodies[0].code_verifier).digest('base64url');
  assert.equal(challenge, expected);
  assert.ok(completed());
  assert.equal(state.logoutCalls, 1, 'temporary Google session is signed out');
  assert.deepEqual(state.vendorSignOutCalls, []);
  assert.equal(state.vendorSession.user.id, 'vendor-1');
  vendorUntouched();
});

for (const [name, url, reason] of [
  ['wrong scheme', 'https://auth-callback?code=x', 'unexpected_callback'],
  ['wrong host', 'bricks-go://evil.example?code=x', 'unexpected_callback'],
  ['wrong path', 'bricks-go://auth-callback/evil?code=x', 'unexpected_callback'],
  ['unexpected port', 'bricks-go://auth-callback:4444?code=x', 'unexpected_callback'],
  ['token fragment', `${CB}?code=good#access_token=a&refresh_token=r`, 'unexpected_callback'],
  ['provider error', `${CB}?error=access_denied&code=x`, 'provider_error'],
  ['missing code', `${CB}?state=only`, 'missing_code'],
]) {
  test(`connect: ${name} never exchanges and shows a friendly message`, async () => {
    respond(url);
    const res = await connectGoogleCalendar();
    assert.equal(res.connected, false);
    assert.equal(res.error, AUTH_CALLBACK_MESSAGES[reason]);
    assert.equal(state.exchangeBodies.length, 0);
    assert.ok(!completed());
    vendorUntouched();
  });
}

test('connect: failing token exchange reports failure and leaves the vendor signed in', async () => {
  state.exchangeMode = 'error';
  const res = await connectGoogleCalendar();
  assert.equal(res.connected, false);
  assert.match(res.error, /permission is required/);
  assert.ok(!completed());
  assert.equal(state.vendorSession.user.id, 'vendor-1');
  vendorUntouched();
});

test('connect: restore failure cannot leave a temporary session — vendor setSession is never called', async () => {
  // Sentinel's repro: setSession errors after the exchange. With the isolated client the
  // vendor client is never swapped, so a failing setSession has nothing to restore.
  state.vendorSetSessionError = { message: 'restore failed', status: 500 };
  const res = await connectGoogleCalendar();
  assert.deepEqual(res, { connected: true });
  assert.equal(state.vendorSession.user.id, 'vendor-1');
  vendorUntouched();
});

test('connect: vendor session replaced by another user during connect fails closed and signs out locally', async () => {
  state.onExchange = () => { state.vendorSession = { access_token: 'other', user: { id: 'google-temp-user' } }; };
  const res = await connectGoogleCalendar();
  assert.deepEqual(res, { connected: false, error: CALENDAR_SIGN_IN_AGAIN });
  assert.deepEqual(state.vendorSignOutCalls, ['local']);
  assert.equal(state.vendorSession, null);
  assert.ok(!completed(), 'never completes under another account');
});

test('connect: vendor session unreadable after connect fails closed and signs out locally', async () => {
  state.onExchange = () => { state.vendorGetSessionError = { message: 'storage failed' }; };
  const res = await connectGoogleCalendar();
  assert.deepEqual(res, { connected: false, error: CALENDAR_SIGN_IN_AGAIN });
  assert.deepEqual(state.vendorSignOutCalls, ['local']);
  assert.ok(!completed());
});

test('connect: concurrent starts join one attempt and one exchange', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  state.authResult = async () => { await gate; return { type: 'success', url: `${CB}?code=good` }; };
  const a = connectGoogleCalendar();
  const b = connectGoogleCalendar();
  await new Promise((r) => setTimeout(r, 10));
  release();
  assert.deepEqual(await a, { connected: true });
  assert.deepEqual(await b, { connected: true });
  assert.equal(state.openAuthCalls, 1);
  assert.equal(state.exchangeBodies.length, 1);
});

test('connect: separate attempts never share a PKCE verifier', async () => {
  await connectGoogleCalendar();
  await connectGoogleCalendar();
  assert.equal(state.exchangeBodies.length, 2);
  assert.notEqual(state.exchangeBodies[0].code_verifier, state.exchangeBodies[1].code_verifier);
});

// ---------- Google sign-in (login screen handler, executed) ----------
const exchanger = {
  async exchangeCodeForSession(code) {
    state.loginExchangeCalls.push(code);
    if (state.loginExchangeThrows) throw new Error('network');
    return { error: state.loginExchangeError };
  },
};

test('login: valid callback exchanges once and succeeds', async () => {
  const res = await completeGoogleSignIn(exchanger, { type: 'success', url: `${CB}?code=good` }, CB);
  assert.deepEqual(res, { ok: true });
  assert.deepEqual(state.loginExchangeCalls, ['good']);
});
for (const [name, url, reason] of [
  ['wrong scheme', 'https://auth-callback?code=x', 'unexpected_callback'],
  ['wrong host', 'bricks-go://evil.example?code=x', 'unexpected_callback'],
  ['wrong path', 'bricks-go://auth-callback/x?code=x', 'unexpected_callback'],
  ['unexpected port', 'bricks-go://auth-callback:1?code=x', 'unexpected_callback'],
  ['token fragment', `${CB}?code=good#refresh_token=r`, 'unexpected_callback'],
  ['provider error', `${CB}?error=access_denied`, 'provider_error'],
  ['missing code', CB, 'missing_code'],
]) {
  test(`login: ${name} never exchanges`, async () => {
    const res = await completeGoogleSignIn(exchanger, { type: 'success', url }, CB);
    assert.deepEqual(res, { ok: false, message: AUTH_CALLBACK_MESSAGES[reason] });
    assert.deepEqual(state.loginExchangeCalls, []);
  });
}
test('login: exchange error or throw shows a friendly failure', async () => {
  state.loginExchangeError = { message: 'invalid_grant' };
  assert.deepEqual(await completeGoogleSignIn(exchanger, { type: 'success', url: `${CB}?code=a` }, CB), { ok: false, message: GOOGLE_SIGN_IN_FAILED });
  state.loginExchangeError = null;
  state.loginExchangeThrows = true;
  assert.deepEqual(await completeGoogleSignIn(exchanger, { type: 'success', url: `${CB}?code=b` }, CB), { ok: false, message: GOOGLE_SIGN_IN_FAILED });
});
test('login: cancel is quiet and never exchanges', async () => {
  assert.deepEqual(await completeGoogleSignIn(exchanger, { type: 'cancel' }, CB), { ok: false });
  assert.deepEqual(state.loginExchangeCalls, []);
});
test('login: screen routes Google sign-in through completeGoogleSignIn', () => {
  const src = readFileSync(new URL('../app/(auth)/login.tsx', import.meta.url), 'utf8');
  assert.match(src, /completeGoogleSignIn\(supabase\.auth, result, redirectTo\)/);
  assert.doesNotMatch(src, /exchangeCodeForSession/);
});
