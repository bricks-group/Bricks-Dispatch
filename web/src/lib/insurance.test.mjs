/**
 * GO Profile drone liability insurance: status copy, limit parsing, form checks.
 * Run from web/: node --test src/lib/insurance.test.mjs  (self-builds via npx esbuild)
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = '/tmp/go-insurance.mjs';
execSync(
  `npx --yes esbuild ${path.join(here, 'insurance.ts')} --bundle --platform=node --format=esm --outfile=${out}`,
  { stdio: 'inherit', cwd: path.join(here, '../..') },
);
const { parseLimit, fmtDay, validateInsuranceForm, insuranceFormNotes, insuranceView } = await import(out);

const TODAY = '2026-10-18';
const verified = { verified: true, valid: true, reason: null, expires_on: '2027-09-01', liability_cad: 2000000, aviation_covered: true, minimum_cad: 2000000, source: 'personal' };

test('parseLimit reads the ways people type a limit', () => {
  assert.equal(parseLimit('2000000'), 2000000);
  assert.equal(parseLimit('2,000,000'), 2000000);
  assert.equal(parseLimit('$2M'), 2000000);
  assert.equal(parseLimit('2 m'), 2000000);
  assert.equal(parseLimit('1.5M'), 1500000);
  assert.equal(parseLimit('500k'), 500000);
  assert.equal(parseLimit('5,000,000 CAD'), 5000000);
  assert.equal(parseLimit(''), null);
  assert.equal(parseLimit('two million'), null);
  assert.equal(parseLimit('0'), null);
  assert.equal(parseLimit('-2M'), null);
});

test('fmtDay is date-only and never shifts a day', () => {
  assert.equal(fmtDay('2027-03-04'), 'Mar 4, 2027');
  assert.equal(fmtDay('2026-10-18T23:30:00+00:00'), 'Oct 18, 2026');
  assert.equal(fmtDay(null), '');
  assert.equal(fmtDay('2027-13-01'), '');
});

test('validateInsuranceForm blocks only what the API would refuse', () => {
  const ok = { file: { size: 1000 }, limit: '2M', expires: '2027-01-01', aviation: true };
  assert.equal(validateInsuranceForm(ok, TODAY), '');
  assert.match(validateInsuranceForm({ ...ok, file: null }, TODAY), /certificate/);
  assert.match(validateInsuranceForm({ ...ok, file: { size: 13 * 1024 * 1024 } }, TODAY), /12 MB/);
  assert.match(validateInsuranceForm({ ...ok, limit: 'lots' }, TODAY), /liability limit/);
  assert.match(validateInsuranceForm({ ...ok, expires: '' }, TODAY), /expiry/);
  assert.match(validateInsuranceForm({ ...ok, expires: '2026-10-17' }, TODAY), /expired/);
  assert.equal(validateInsuranceForm({ ...ok, expires: TODAY }, TODAY), '');
  // A low limit or no drone cover is a note, not a block.
  assert.equal(validateInsuranceForm({ ...ok, limit: '1M', aviation: false }, TODAY), '');
  assert.deepEqual(insuranceFormNotes({ ...ok, limit: '1M', aviation: false }), [
    'Builder site jobs need $2M or more.', 'Builder site jobs need drone liability on the policy.',
  ]);
  assert.deepEqual(insuranceFormNotes(ok), []);
});

test('insuranceView: nothing on file asks for it and says listing jobs are fine', () => {
  const v = insuranceView({ verified: false, valid: false, reason: 'insurance_not_verified', minimum_cad: 2000000, source: 'personal' }, null, TODAY);
  assert.equal(v.headline, 'Not on file');
  assert.match(v.detail, /\$2M or more/);
  assert.match(v.detail, /Listing jobs don’t need it/);
  assert.equal(v.formOpen, true);
});

test('insuranceView: a fresh upload reads as in review after reload', () => {
  const v = insuranceView({ verified: false, valid: false, reason: 'insurance_not_verified', source: 'personal' },
    { status: 'pending', uploaded_at: '2026-10-18T14:00:00+00:00', expires_on: '2027-10-01', liability_cad: 2000000, aviation_covered: true }, TODAY);
  assert.equal(v.tone, 'wait');
  assert.equal(v.headline, 'In review');
  assert.equal(v.detail, 'Sent Oct 18, 2026 · $2M, expires Oct 1, 2027. Bricks checks it before builder jobs open.');
  assert.equal(v.formOpen, false);
});

test('insuranceView: verified, expiring soon, and renewal in review', () => {
  assert.deepEqual(insuranceView(verified, { status: 'verified' }, TODAY), { tone: 'ok', headline: 'Verified · $2M', detail: 'Expires Sep 1, 2027.', formOpen: false });
  const soon = insuranceView({ ...verified, expires_on: '2026-11-01' }, null, TODAY);
  assert.equal(soon.tone, 'warn');
  assert.equal(soon.detail, 'Expires Nov 1, 2026. Upload your renewal.');
  const renewing = insuranceView({ ...verified, expires_on: '2026-11-01' }, { status: 'pending', uploaded_at: '2026-10-18' }, TODAY);
  assert.equal(renewing.tone, 'ok');
  assert.equal(renewing.detail, 'New certificate in review, sent Oct 18, 2026.');
});

test('insuranceView: verified but not enough for builder jobs', () => {
  assert.equal(insuranceView({ ...verified, valid: false, reason: 'insurance_expired', expires_on: '2026-09-01' }, { status: 'verified' }, TODAY).detail, 'Expired Sep 1, 2026. Upload your renewal.');
  const low = insuranceView({ ...verified, valid: false, reason: 'insurance_below_minimum', liability_cad: 1000000 }, { status: 'verified' }, TODAY);
  assert.equal(low.headline, 'Verified at $1M');
  assert.equal(low.detail, 'Builder site jobs need $2M or more.');
  assert.equal(insuranceView({ ...verified, valid: false, reason: 'insurance_no_drone_liability' }, null, TODAY).headline, 'No drone liability');
});

test('insuranceView: rejected upload asks for a new one', () => {
  const v = insuranceView({ verified: false, valid: false, reason: 'insurance_not_verified', source: 'personal' }, { status: 'rejected' }, TODAY);
  assert.equal(v.headline, 'Not accepted');
  assert.equal(v.formOpen, true);
});

test('insuranceView: in-house staff use the company policy and get no form', () => {
  const v = insuranceView({ valid: true, source: 'company' }, null, TODAY);
  assert.equal(v.headline, 'Covered by the Bricks policy');
  assert.equal(v.formOpen, false);
});

test('insuranceView: missing data never throws', () => {
  assert.equal(insuranceView(undefined, undefined, TODAY).headline, 'Not on file');
});
