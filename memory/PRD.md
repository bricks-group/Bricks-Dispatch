# Bricks Dispatch — Product Reference Document
> Last updated: 2026-06-10

## What This Is

Vendor/photographer-facing job orchestration app. Photographers use this to receive jobs, navigate to shoots, upload photos, and get paid. Part of the Bricks ecosystem (three products, one Supabase project).

## Platforms

| Platform | Stack | Bundle ID | Status |
|---|---|---|---|
| iOS | Native Swift (`ios-swift/`) | `com.bricks.dispatch` | Built |
| Android | Expo React Native (`frontend/`) | `com.bricks.dispatch` | Built |
| watchOS | Swift (`BricksDispatchWatch`) | accept/decline from wrist | Built |
| WidgetKit | Swift (`BricksDispatchWidgets`) | next job + earnings | Built |
| Web shell | React (inside Brokerage repo) | — | Built (at `brokerage.brxs.ca/dispatch/*`) |

## Web Shell Note

The Dispatch photographer web experience is built into the Brokerage React app (`ruletheworld/bricks-brokerage`), not as a separate site. Photographers log in at `brokerage.brxs.ca` and are routed to `/dispatch/*` by their `role === 'photographer'` metadata. The Expo `frontend/` remains the primary mobile experience.

## Core Photographer Flow

1. **Onboarding**: Contractor application → IC agreement v1.1 → RPAS certification (if drone) → document upload
2. **Job offer**: Push notification → accept/decline (or from wrist watch)
3. **Navigate**: MapKit turn-by-turn to property
4. **Shoot + upload**: Photos → Fotello AI edit → QC score → delivery to agent
5. **Payout**: Stripe Connect transfer, tier-based % (C=35%, B=45%, A=55%)
6. **No-show**: If agent absent → escalate via SMS → file claim → $60 trip fee

## Backend API Base: `https://dispatch-api.brxs.ca`

Key routers:
- `POST /api/v1/dispatch/jobs` — intake from Booking (requires `X-Internal-Api-Key`)
- `GET/PUT /api/vendor-os/jobs` — photographer job list + status updates
- `POST /api/vendor-os/jobs/{id}/agent-no-show` — no-show claim
- `POST /api/v1/payouts/trigger` — Stripe Connect transfer
- `GET /api/vendor-os/earnings` — payout history + pending trip fees
- `POST /api/v1/post-production/callback` — Fotello/Autoenhance webhook
- `GET /api/onboarding/ic-agreement` — IC agreement text v1.1
- `GET /api/admin/noshow-claims` — admin review queue

## Vendor Tier System

- **Tier C (1)** — entry, 35% payout, Standard jobs only
- **Tier B (2)** — core, 45% payout, Standard + Premium
- **Tier A (3)** — elite, 55% payout, all packages

`min_tier_required` on job is a hard gate.

## Dispatch Scoring (Deterministic)

100-point weighted score: specialty (30) + proximity (20) + on_time_rate (15) + tier_match (10) + quality (10) + reliability (10) + load (5) + repeat_agent_bonus (+5).
Gemini 1.5 Flash used ONLY for async explainability string shown in admin dashboard.

## Post-Production Pipeline

Fotello (primary) → Autoenhance (fallback) → BoxBrownie (virtual staging) → QC agent (Gemini Vision, 0-100 score) → signed Supabase URLs → Resend email to agent.

## Current Status (2026-06-10)

All backend features complete. Pre-launch checklist:
- [ ] Ontario lawyer review of IC agreement
- [ ] Agent ToS: disclose 100% no-show retention (conspicuously)
- [ ] Confirm HST treatment of forfeited no-show bookings
- [ ] Apply migration: `backend/migrations/20260601_agent_noshow_escalations.sql` ✅
- [ ] Register Fotello/Autoenhance webhook callback URLs in provider dashboards
- [ ] Stripe Connect live E2E with real CAD transfer
- [ ] TestFlight upload (Kam)
- [ ] Play Store internal track (Kam)
- [ ] Verify Advanced RPAS cert for GTA drone photographers
- [ ] Stub dispatch web pages: `/dispatch/notifications`, `/dispatch/settings`, `/dispatch/profile-edit`

## Rules
- `INTERNAL_API_KEY` (`X-Internal-Api-Key`) required on dispatch intake
- `time_window_start` is `"HH:MM"` string — always combine with `scheduled_date` before datetime math
- Trip fees: `pending` → `reserved` → `paid_out` (never skip steps)
- Never hardcode secrets as default values in `os.environ.get()`
- Stripe Connect is Express (CA) — transfers use `stripe_account` parameter
- IC Clause 8 lists Bricks' platform FEE (60%/50%/40%), NOT vendor payout — these numbers are correct
