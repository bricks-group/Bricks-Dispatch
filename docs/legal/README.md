# IC agreement texts

The live text is `_IC_AGREEMENT_TEXT` in `backend/app/routers/onboarding.py`. Each
application records `ic_agreement_version`. Earlier texts are kept here so a signer's
version can always be matched to the words they accepted.

| Version | Effective | File | Served |
|---|---|---|---|
| 1.8 | 2026-10-03 | ic-agreement-v1.8.txt | Oct 3 to Oct 23, 2026 (payout disputes and questions: booking@brxs.ca) |
| 1.8 (edited in place) | 2026-10-03 | ic-agreement-v1.8-edited-2026-10-23.txt | From the Oct 23, 2026 edits (live in prod from about 4:22 PM ET) until v1.9 shipped. Only change: payout disputes to raphael@brxs.ca. Still recorded as "1.8" |
| 1.9 | 2026-10-23 | live in onboarding.py | Oct 23, 2026 onward. Same terms as 1.8; disputes and questions to booking@brxs.ca |
