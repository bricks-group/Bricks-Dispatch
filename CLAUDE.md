# Bricks-Dispatch (Python/FastAPI + Expo)

You are working on **Bricks-Dispatch**, the vendor-facing job orchestration layer of the BRICKS ecosystem.

## Ecosystem Context
- **Bricks-Brokerage** — brokerage admin web dashboard. Manages vendors.
- **Bricks-Booking** — sends confirmed shoot jobs to this service via `POST /api/v1/dispatch/jobs`
- **Bricks-Dispatch** (this repo) — assigns jobs to vendors, tracks status, processes payouts

All three repos share one Supabase project: `ctqwxwrcxwruaqojohpf`

## Stack
- Backend: FastAPI (Python 3.14), Supabase, Stripe Connect, Gemini 1.5 Flash
- Frontend: Expo (React Native) + web dashboard (`dispatch.brxs.ca`)
- Deployed: `https://bricks-dispatch-api.vercel.app` / `https://dispatch-api.brxs.ca`

## Key Files
- `backend/app/main.py` — router registration (no outer prefix; routers carry their own `/api/v1/*`)
- `backend/app/routers/dispatch.py` — `POST /api/v1/dispatch/jobs` intake, status updates
- `backend/app/routers/payouts.py` — `POST /api/v1/payouts/trigger`, list, summary
- `backend/app/routers/jobs.py` — Job CRUD (`/dispatch/jobs/*`)
- `backend/app/routers/vendors.py` — Vendor management (`/dispatch/vendors/*`)
- `backend/app/services/vendor_selection.py` — AI + rule-based vendor assignment
- `backend/app/integrations/bricks_ai_dispatch.py` — Gemini smart assign
- `backend/app/shared/schemas.py` — Pydantic models (Job, VendorMember with tier field)

## Router Prefix Architecture
Routers carry their own prefix internally — do NOT add an outer prefix at `include_router`:
```python
app.include_router(dispatch.router, tags=["dispatch"])   # has /api/v1/dispatch internally
app.include_router(payouts.router, tags=["payouts"])     # has /api/v1/payouts internally
app.include_router(jobs.router, prefix="/dispatch/jobs")
app.include_router(vendors.router, prefix="/dispatch/vendors")
```

## Vendor Assignment Logic
1. `bricks_smart_assign` (Gemini 1.5 Flash) — filters by `min_tier_required`, sends eligible vendors
2. Validates AI result is in `eligible_ids` (tier-gated)
3. Falls back to `pick_vendor_rule_based` if AI fails or returns ineligible vendor
4. Rule-based score: `(specialty_match, tier_match, -active_assignments, quality_rating)`

## Vendor Tier System
- Tier 1 (A): 40% payout — Standard routing only
- Tier 2 (B): 50% payout — Standard + Premium routing
- Tier 3 (C): 60% payout — Full routing (Standard + Premium + Luxury)
- `min_tier_required` on job is a hard gate — never assigned below it

## Rules
- `USE_AI_DISPATCH=true` env var enables AI path; falls back to rule-based if false or no GEMINI_API_KEY
- `INTERNAL_API_KEY` header (`X-Internal-Api-Key`) required on dispatch intake endpoint
- Keep vendor selection idempotent — same job can be re-run without double-assigning
- Stripe Connect is Express (CA) — transfers use `stripe_account` parameter

## Current Status (2026-05-28)
- Job state machine (pending → assigned → in_progress → completed → delivered) ✅
- AI smart assign (Gemini 1.5 Flash) with tier enforcement ✅
- Rule-based fallback with tier gate ✅
- Payout flow (tier % → Stripe Connect transfer → record) ✅
- Payout router registered and mounted ✅
- Router prefix fixed (no double `/dispatch/api/v1/dispatch/`) ✅
- `VendorMember.tier` field added to schema ✅
- Post-production pipeline: BoxBrownie + Fotello (primary) + Autoenhance (fallback) ✅
- **Post-production callbacks: Fotello/Autoenhance/BoxBrownie webhook handlers** ✅
- **Media delivery: signed URLs → media_delivery row → Resend email to agent** ✅
- **Reconciliation endpoint: clears stuck uploads (>2h) and re-attempts finalization** ✅
- **QC vision agent: Gemini 1.5 Flash gates delivery; rejected photos excluded** ✅
- **Earnings endpoint: reads real payouts.status (not inferred from job.status)** ✅
- **Vendor compliance gate: insurance/WSIB check (REQUIRE_VENDOR_COMPLIANCE flag)** ✅
- **Client comms agent: booking confirmed + photographer en route emails** ✅
- **Vendor system consolidation Phase 1: contractor_id/user_id/tier on vendor_members** ✅
- Rate limiting + Sentry ✅
- Custom domains: `dispatch-api.brxs.ca`, `dispatch.brxs.ca` ✅
- Vendor tier payouts aligned to brxs.ca/vendors (Tier A=40%, B=50%, C=60%) ✅
- Bouncing logo (brickPop on press) + 3D rotating brick loader ✅
- E2E payout test suite (calculate, idempotency, Stripe happy path, Stripe failure) ✅
- app.json bundle IDs + EAS project ID configured (`com.bricks.dispatch`) ✅

## Remaining Before Live Traffic
**Vercel env vars to add (bricks-dispatch-api):**
- `BOXBROWNIE_PUBLIC_KEY` + `BOXBROWNIE_PRIVATE_KEY` ✅ (added)
- `FOTELLO_API_KEY` → add once plan is active
- `RESEND_API_KEY` → for delivery + booking confirmation emails
- `POST_PRODUCTION_CALLBACK_SECRET` → set same secret in BoxBrownie/Fotello/Autoenhance webhook URL
- `REQUIRE_VENDOR_COMPLIANCE=true` → flip when insurance records are populated
- **ROTATE `INTERNAL_API_KEYS` immediately** — old values `br-ef7ddf0e84582e6746668881873f61cc` and `bk-5274a1f72baf20a0699c405` are compromised (were hardcoded)

**Ops tasks:**
- Populate contractors.insurance_verified + wsib_verified for each vendor; then set `REQUIRE_VENDOR_COMPLIANCE=true`
- Register BoxBrownie/Fotello/Autoenhance webhook URLs to `{dispatch-api.brxs.ca}/api/v1/post-production/callback/{provider}?secret=<SECRET>`
- Stripe Connect live end-to-end verification with real CAD transfer
- TestFlight upload (Kam — `eas build --platform ios --profile production` + `eas submit`)
- Play Store internal track (Kam — `eas build --platform android --profile production` + `eas submit`)
