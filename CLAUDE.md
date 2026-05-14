# Bricks-Dispatch (Python/FastAPI + Expo)

You are working on **Bricks-Dispatch**, the vendor-facing job orchestration layer of the BRICKS ecosystem.

## Ecosystem context
- **Bricks-Brokerage** (JS) — brokerage admin dashboard. Manages vendors.
- **Bricks-Booking** (Python) — sends confirmed shoot jobs to this service.
- **Bricks-Dispatch** (this repo) — assigns jobs to crews, runs automations, tracks status, processes payouts.

## Stack
- Backend: FastAPI (Python 3.11), Supabase, Stripe Connect, Gemini 1.5 Flash
- Frontend: Expo (React Native) + Web dashboard
- Deployed on Vercel (backend)

## Key Files
- `backend/app/routers/jobs.py` — Job CRUD, status updates
- `backend/app/routers/payouts.py` — Payout trigger, list, summary
- `backend/app/routers/dispatch.py` — Auto-assign logic
- `backend/app/routers/vendors.py` — Vendor management
- `backend/app/services/payout_service.py` — Payout calculation (tier-based)
- `backend/app/services/stripe_connect_service.py` — Stripe Connect transfers
- `backend/app/services/storage.py` — S3 uploads, post-production pipeline

## Rules
- Treat incoming job payloads from Booking as a versioned contract.
- Keep worker code idempotent and retry-safe.
- Prefer typed dataclasses / Pydantic models for job schemas.
- Log all changes in `/Volumes/BRICKS/BRICKS.md`

## Current State (2026-05-14)
- Job state machine ✅
- Auto-assign (Gemini 1.5 Flash) ✅
- Vendor payout flow (calculation → Stripe transfer → record) ✅
- Payout endpoints (trigger, list, summary) ✅
- Post-production pipeline (Autoenhance.ai & BoxBrownie) ✅
- Rate limiting, Sentry, logging ✅
- **Known gap:** Stripe Connect needs test verification
- **Known gap:** EAS build needs fixing
- **Known gap:** Post-production pipeline needs end-to-end validation
- **Test result:** `test_result.md` in this directory
