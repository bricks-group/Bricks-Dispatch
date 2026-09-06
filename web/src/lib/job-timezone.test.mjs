/**
 * Node smoke test for Canada-wide GO clocks.
 * Run: node --experimental-strip-types web/src/lib/job-timezone.test.mjs
 */
import assert from 'node:assert/strict';

const { resolveJobIana, tzAbbrev } = await import('./job-timezone.ts');

assert.equal(resolveJobIana({ timezone: 'America/Vancouver' }), 'America/Vancouver');
assert.equal(tzAbbrev({ timezone: 'America/Vancouver', scheduled_date: '2026-09-25' }), 'PT');
assert.equal(resolveJobIana({ state: 'BC' }), 'America/Vancouver');
assert.equal(resolveJobIana({ address: '200 Granville St, Vancouver, BC' }), 'America/Vancouver');
assert.equal(resolveJobIana({ zip_code: 'V6B 1A1' }), 'America/Vancouver');
assert.equal(resolveJobIana({ postal_code: 'T2P 0A1' }), 'America/Edmonton');
assert.equal(tzAbbrev({ postal_code: 'T2P 0A1', scheduled_date: '2026-09-25' }), 'MT');
assert.equal(resolveJobIana({}), 'America/Toronto');
assert.equal(tzAbbrev({ scheduled_date: '2026-09-25' }), 'ET');
assert.equal(resolveJobIana({ timezone: 'PT' }), 'America/Vancouver');
assert.equal(resolveJobIana({ timezone: 'Not/AZone', state: 'AB' }), 'America/Edmonton');

console.log('job-timezone tests passed');
