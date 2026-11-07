import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../lib/calendarConnect.ts', import.meta.url), 'utf8');
test('direct OAuth lets the guarded server callback complete before returning to the app', () => {
  assert.doesNotMatch(source, /finishOauthInBrowser\(start\.oauth_url, start\.redirect_uri/);
  assert.match(source, /openAuthSessionAsync\(oauthUrl, WEB_AVAILABILITY\)/);
});
test('native connect exposes all four provider actions without claiming success from stored tokens', () => {
  for (const name of ['Google', 'Microsoft', 'Apple', 'Ics']) assert.match(source, new RegExp(`export async function connect${name}Calendar`));
  assert.match(source, /if \(row\?\.connected\)/);
});
