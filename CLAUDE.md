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
- Tier 1: 50% payout
- Tier 2: 60% payout
- Tier 3: 70% payout
- `min_tier_required` on job is a hard gate — never assigned below it

## Rules
- `USE_AI_DISPATCH=true` env var enables AI path; falls back to rule-based if false or no GEMINI_API_KEY
- `INTERNAL_API_KEY` header (`X-Internal-Api-Key`) required on dispatch intake endpoint
- Keep vendor selection idempotent — same job can be re-run without double-assigning
- Stripe Connect is Express (CA) — transfers use `stripe_account` parameter

## Current Status (2026-05-15)
- Job state machine (pending → assigned → in_progress → completed) ✅
- AI smart assign (Gemini 1.5 Flash) with tier enforcement ✅
- Rule-based fallback with tier gate ✅
- Payout flow (tier % → Stripe Connect transfer → record) ✅
- Payout router registered and mounted ✅
- Router prefix fixed (no double `/dispatch/api/v1/dispatch/`) ✅
- `VendorMember.tier` field added to schema ✅
- Post-production pipeline (Autoenhance.ai + BoxBrownie stubs) ✅
- Rate limiting + Sentry ✅
- Custom domains: `dispatch-api.brxs.ca`, `dispatch.brxs.ca` (DNS propagating)

## Remaining Before Live Traffic
- Stripe Connect end-to-end verification (Scenario C)
- E2E test: job received from Booking → vendor assigned → payout triggered
- BoxBrownie + Autoenhance API keys (from BRXS repo → Vercel env vars)
- TestFlight / Play Store uploads (Kam)
