/**
 * GO web phone (SMS OTP) helpers — E.164, error copy, settings probe, OTP calls.
 * Supabase is mocked: no SMS is ever sent from these tests.
 * Run from web/: node --test src/lib/phone-auth.test.mjs  (self-builds via npx esbuild)
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { execSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = '/tmp/go-phone-auth.mjs';
execSync(
  `npx --yes esbuild ${path.join(here, 'phone-auth.ts')} --bundle --platform=node --format=esm --outfile=${out}`,
  { stdio: 'inherit', cwd: path.join(here, '../..') },
);
const {
  toE164, formatPhoneDisplay, cleanOtp, isPhoneProviderDisabled, phoneAuthErrorMessage,
  addEmailErrorMessage, retryAfterSeconds, fetchPhoneProviderEnabled, sendPhoneOtp, verifyPhoneOtp,
  isValidEmail, PHONE_UNAVAILABLE_MESSAGE, RESEND_COOLDOWN_SECONDS,
} = await import(out);

test('toE164 defaults to +1 and normalises formatting', () => {
  assert.equal(toE164('4165550123'), '+14165550123');
  assert.equal(toE164('(416) 555-0123'), '+14165550123');
  assert.equal(toE164('416.555.0123'), '+14165550123');
  assert.equal(toE164('1 416 555 0123'), '+14165550123');
  assert.equal(toE164('+1 (416) 555-0123'), '+14165550123');
  assert.equal(toE164('+44 20 7946 0958'), '+442079460958');
  assert.equal(toE164('0044 20 7946 0958'), '+442079460958');
});

test('toE164 rejects junk', () => {
  for (const bad of ['', '   ', 'abc', '555-0123', '+1 416 555', '+1416555012345', '+0123456789', '24165550123']) {
    assert.equal(toE164(bad), '', `expected "" for ${bad}`);
  }
});

test('display + otp cleanup', () => {
  assert.equal(formatPhoneDisplay('+14165550123'), '+1 (416) 555-0123');
  assert.equal(formatPhoneDisplay('+442079460958'), '+442079460958');
  assert.equal(cleanOtp(' 12-34 56 7'), '123456');
  assert.ok(RESEND_COOLDOWN_SECONDS >= 30);
});

test('provider-disabled errors map to the friendly message', () => {
  assert.ok(isPhoneProviderDisabled({ code: 'phone_provider_disabled', message: 'Unsupported phone provider' }));
  assert.ok(isPhoneProviderDisabled({ message: 'Unsupported phone provider' }));
  assert.ok(!isPhoneProviderDisabled({ code: 'otp_expired', message: 'Token has expired or is invalid' }));
  assert.equal(phoneAuthErrorMessage({ code: 'phone_provider_disabled', message: 'Unsupported phone provider' }), PHONE_UNAVAILABLE_MESSAGE);
});

test('other phone errors are clear', () => {
  assert.match(phoneAuthErrorMessage({ code: 'otp_expired', message: 'Token has expired or is invalid' }, 'verify'), /wrong or expired/);
  assert.match(phoneAuthErrorMessage({ status: 429, code: 'over_sms_send_rate_limit', message: 'For security purposes, you can only request this after 42 seconds.' }), /42 seconds/);
  assert.equal(retryAfterSeconds({ message: 'only request this after 9 seconds' }), 9);
  assert.match(phoneAuthErrorMessage({ code: 'over_sms_send_rate_limit', status: 429, message: 'Too many' }), /Too many attempts/);
  assert.match(phoneAuthErrorMessage({ code: 'validation_failed', message: 'Invalid phone number format (E.164 required)' }), /valid mobile number/);
  assert.match(phoneAuthErrorMessage({ code: 'sms_send_failed', message: 'Error sending sms' }), /couldn't text/);
  assert.equal(phoneAuthErrorMessage(null), '');
});

test('add-email errors', () => {
  assert.match(addEmailErrorMessage({ code: 'email_exists', message: 'A user with this email address has already been registered' }), /already has a Bricks account/);
  assert.match(addEmailErrorMessage({ code: 'email_address_invalid', message: 'Email address is invalid' }), /valid email/);
  assert.ok(isValidEmail('kam@brxs.ca'));
  assert.ok(!isValidEmail('kam@brxs'));
});

test('fetchPhoneProviderEnabled reads external.phone', async () => {
  const calls = [];
  const mk = (body, ok = true) => async (url, init) => { calls.push([url, init]); return { ok, json: async () => body }; };
  assert.equal(await fetchPhoneProviderEnabled('https://x.supabase.co/', 'anon', mk({ external: { phone: false } })), false);
  assert.equal(calls[0][0], 'https://x.supabase.co/auth/v1/settings');
  assert.equal(calls[0][1].headers.apikey, 'anon');
  assert.equal(await fetchPhoneProviderEnabled('https://x.supabase.co', 'anon', mk({ external: { phone: true } })), true);
  assert.equal(await fetchPhoneProviderEnabled('https://x.supabase.co', 'anon', mk({}, false)), null);
  assert.equal(await fetchPhoneProviderEnabled('https://x.supabase.co', 'anon', async () => { throw new Error('offline'); }), null);
  assert.equal(await fetchPhoneProviderEnabled('', 'anon', mk({})), null);
});

function mockClient({ otpError = null, verify = { data: { session: { access_token: 't' }, user: { id: 'u' } }, error: null } } = {}) {
  const calls = { signInWithOtp: [], verifyOtp: [] };
  return {
    calls,
    auth: {
      signInWithOtp: async (args) => { calls.signInWithOtp.push(args); return { data: {}, error: otpError }; },
      verifyOtp: async (args) => { calls.verifyOtp.push(args); return verify; },
    },
  };
}

test('sendPhoneOtp sends E.164 via signInWithOtp', async () => {
  const c = mockClient();
  const r = await sendPhoneOtp(c, '(416) 555-0123');
  assert.equal(r.phone, '+14165550123');
  assert.equal(r.error, null);
  assert.deepEqual(c.calls.signInWithOtp[0], { phone: '+14165550123', options: { shouldCreateUser: true, channel: 'sms' } });
});

test('sendPhoneOtp never calls Supabase for an invalid number', async () => {
  const c = mockClient();
  const r = await sendPhoneOtp(c, '555');
  assert.equal(r.phone, '');
  assert.ok(r.error);
  assert.equal(c.calls.signInWithOtp.length, 0);
});

test('sendPhoneOtp surfaces provider-disabled', async () => {
  const c = mockClient({ otpError: { code: 'phone_provider_disabled', message: 'Unsupported phone provider' } });
  const r = await sendPhoneOtp(c, '4165550123');
  assert.equal(phoneAuthErrorMessage(r.error), PHONE_UNAVAILABLE_MESSAGE);
});

test('verifyPhoneOtp uses type sms and returns the session', async () => {
  const c = mockClient();
  const r = await verifyPhoneOtp(c, '+14165550123', '123 456');
  assert.equal(r.error, null);
  assert.equal(r.session.access_token, 't');
  assert.deepEqual(c.calls.verifyOtp[0], { phone: '+14165550123', token: '123456', type: 'sms' });
});

test('verifyPhoneOtp rejects short codes locally and passes through errors', async () => {
  const c = mockClient({ verify: { data: {}, error: { code: 'otp_expired', message: 'Token has expired or is invalid' } } });
  const short = await verifyPhoneOtp(c, '+14165550123', '12');
  assert.ok(short.error);
  assert.equal(c.calls.verifyOtp.length, 0);
  const bad = await verifyPhoneOtp(c, '+14165550123', '000000');
  assert.equal(bad.session, null);
  assert.match(phoneAuthErrorMessage(bad.error, 'verify'), /wrong or expired/);
});

test('GO app gates phone-only accounts on a confirmed email, never adopts by email', () => {
  const app = readFileSync(path.join(here, '../App.tsx'), 'utf8');
  const login = readFileSync(path.join(here, '../pages/LoginPage.tsx'), 'utf8');
  const addEmail = readFileSync(path.join(here, '../pages/AddEmailPage.tsx'), 'utf8');
  assert.match(app, /AddEmailPage/);
  assert.match(app, /needsEmailFirst/);
  assert.match(login, /fetchPhoneProviderEnabled/);
  assert.match(login, /phoneEnabled !== false/);
  assert.match(addEmail, /updateUser\(\s*\{ email/);
  // The web client never writes vendor_members or looks members up by email.
  for (const src of [app, login, addEmail]) {
    assert.doesNotMatch(src, /vendor_members/);
  }
});
