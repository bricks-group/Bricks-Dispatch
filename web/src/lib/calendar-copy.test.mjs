import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const read = p => readFileSync(new URL(p, import.meta.url),'utf8');
const web = read('../pages/AvailabilityPage.tsx');
const native = read('../../../frontend/app/settings.tsx');
const sources = [web,native,read('./calendarCopy.ts'),read('../../../frontend/lib/calendarConnect.ts')];
test('calendar copy uses plain words without repeated offer lines or protocol labels', () => {
 for(const text of sources) {
  assert.doesNotMatch(text,/Used for job offers\.|native auto-assign|Busy on any connected calendar =|CalDAV URL|Save Apple|Save ICS|Other calendar \(ICS/);
 }
 assert.match(web,/iCloud calendar address/);
 assert.match(web,/Calendar link/);
 assert.match(native,/iCloud calendar address/);
 assert.match(native,/title="Calendar link"/);
});
test('Availability and native Settings each have one calendar introduction', () => {
 assert.doesNotMatch(web,/CalendarConnectNudge/);
 assert.doesNotMatch(native,/CalendarConnectNudge/);
 assert.match(web,/CALENDAR_INTRO_TITLE/);
 assert.match(native,/CALENDAR_INTRO_TITLE/);
});
test('Apple password hint is available in both interfaces', () => {
 for (const text of [read('./calendarCopy.ts'),read('../../../frontend/lib/calendarConnect.ts')]) {
  assert.match(text,/app-specific password in your Apple Account security settings/);
 }
});
