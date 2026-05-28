# Bricks-Dispatch (Python/FastAPI + Expo)

You are working on **Bricks-Dispatch**, the vendor-facing job orchestration layer of the BRICKS ecosystem.

## Ecosystem Context
- **Bricks-Brokerage** — brokerage admin web dashboard. Manages vendors.
- **Bricks-Booking** — sends confirmed shoot jobs to this service via `POST /api/v1/dispatch/jobs`
- **Bricks-Dispatch** (this repo) — assigns jobs to vendors, tracks status, processes payouts

All three repos share one Supabase project: `ctqwxwrcxwruaqojohpf`

## Stack
- Backend: FastAPI (Python 3.14), Supabase, Stripe Connect, Gemini 1.5 Flash (explainability only)
- Frontend: Expo (React Native) + web dashboard (`dispatch.brxs.ca`)
- Deployed: `https://bricks-dispatch-api.vercel.app` / `https://dispatch-api.brxs.ca`

## Key Files
- `backend/app/main.py` — router registration (no outer prefix; routers carry their own `/api/v1/*`)
- `backend/app/routers/dispatch.py` — `POST /api/v1/dispatch/jobs` intake, status updates
- `backend/app/routers/payouts.py` — `POST /api/v1/payouts/trigger`, list, summary
- `backend/app/routers/jobs.py` — Job CRUD (`/dispatch/jobs/*`)
- `backend/app/routers/vendors.py` — Vendor management (`/dispatch/vendors/*`)
- `backend/app/routers/vendor_os.py` — Vendor OS dashboard: overview, team, jobs, earnings, performance
- `backend/app/routers/onboarding.py` — Contractor application, IC agreement, document upload
- `backend/app/routers/post_production.py` — Provider callbacks (Fotello/Autoenhance/BoxBrownie), reconcile
- `backend/app/routers/admin_ops.py` — Leads, applicant vetting
- `backend/app/services/vendor_selection.py` — Primary + fallback vendor assignment
- `backend/app/integrations/bricks_ai_dispatch.py` — **Deterministic weighted scorer** (primary dispatch)
- `backend/app/services/client_comms.py` — Resend emails: booking confirmed + photographer en route
- `backend/app/services/qc_agent.py` — Gemini vision QC agent (scores uploaded photos 0-100)
- `backend/app/services/post_production.py` — BoxBrownie/Fotello/Autoenhance integration
- `backend/app/services/payout_service.py` — Stripe Connect transfers, tier percentages
- `backend/app/shared/schemas.py` — Pydantic models

## Router Prefix Architecture
Routers carry their own prefix internally — do NOT add an outer prefix at `include_router`:
```python
app.include_router(dispatch.router, tags=["dispatch"])        # /api/v1/dispatch internally
app.include_router(payouts.router, tags=["payouts"])          # /api/v1/payouts internally
app.include_router(jobs.router, prefix="/dispatch/jobs")
app.include_router(vendors.router, prefix="/dispatch/vendors")
app.include_router(vendor_os.router)                          # /api/vendor-os internally
app.include_router(onboarding.router)                         # /api/onboarding internally
app.include_router(post_production.router)                    # /api/v1/post-production internally
app.include_router(admin_ops.router)                          # /api/admin internally
```

## Vendor Assignment Logic (Deterministic — NOT Gemini-primary)

**Primary: deterministic weighted scoring (fast, auditable, sub-1ms)**
Score = 100-point system:
- Specialty match (30): vendor.specialty == job.job_type
- Proximity (20): 1 - haversine_km(vendor.home, job.location) / 100
- On-time rate (15): vendor.on_time_rate (0-1, updated on each completion)
- Tier preference (10): vendor.tier == preferred_tier
- Quality (10): (vendor.quality_rating - 1) / 4
- Reliability (10): 1 - vendor.cancel_rate
- Load balance (5): 1 - active_assignments / 4

Repeat-agent bonus: +5 points if vendor completed prior job for this agent.
Drone gate: `vendor.drone_certified=True` required for `job_type="drone"`.

**Gemini 1.5 Flash** used ONLY for explainability string — called async after routing, shown in admin dashboard. Never on the hot dispatch path.

**Fallback: `pick_vendor_rule_based`** — tuple sort with same signals, used if no AI result.

```python
# Dispatch pipeline:
bricks_smart_assign(job, vendors)  →  deterministic_score  →  optional_gemini_explanation
                                   ↓ (if None)
pick_vendor_rule_based(job, vendors)
```

## Vendor Tier System
- Tier 1 (A): 40% payout — Standard routing only
- Tier 2 (B): 50% payout — Standard + Premium routing
- Tier 3 (C): 60% payout — Full routing (Standard + Premium + Luxury + Estate)
- `min_tier_required` on job is a hard gate — never assigned below it

## Vendor Model: Snappr/Homejab IC Model
- No insurance/WSIB blocking at dispatch — contractors self-certify at onboarding
- `REQUIRE_VENDOR_COMPLIANCE` **removed entirely** from dispatch code
- IC agreement v1.0 — 15 clauses covering Ontario law:
  - Independent contractor status (ESA 2000 exclusion explicit)
  - Right to refuse work (no penalty for declining jobs)
  - Right to engage others / no exclusivity / no non-compete
  - HST registration responsibility (>$30K threshold)
  - Insurance/liability disclaimer + contractor indemnity
  - Transport Canada drone compliance
  - IP assignment to Bricks
  - Ontario arbitration governing law
- `GET /api/onboarding/ic-agreement` — returns versioned agreement text
- Stored on `vendor_members`: `ic_agreement_version`, `terms_accepted_at`, `hst_number`
- `ic_agreement_version='0.0'` = pre-dates new agreement; re-prompt on next login

## Drone Certification (Transport Canada RPAS)
- `drone_certified: bool` on `vendor_members` — hard gate for `job_type="drone"` jobs
- `rpas_certificate_level`: "basic" | "advanced" (Advanced required for controlled airspace)
- Stored: `rpas_certificate_number`, `rpas_certificate_level`
- **Collect at onboarding for all photographers who indicate drone capability**
- GTA note: Pearson (CYYZ) and Billy Bishop (CYTZ) create controlled airspace — Advanced cert required

## Weather Context
- Open-Meteo API (free, no key, cached per booking) fetches hourly forecast for job lat/lng + date
- For outdoor/drone jobs: `condition`, `max_wind_kmh`, `max_precip_pct`, `drone_unsafe` flag
- Drone unsafe: precipitation (WMO ≥51) OR wind >25 km/h
- Passed to dispatch scoring context; rescheduling handled separately

## On-Time Rate Tracking
- Updated in `vendor_os.py::update_vendor_job_status` on `completed` transition
- Compares `time_window_end` vs completion timestamp
- Rolling average: `new_rate = (prev_on_time + 1_if_on_time) / new_total_assignments`
- Feeds back into dispatch scoring immediately

## Post-Production Pipeline
1. BoxBrownie — virtual staging (`requires_staging=True` uploads). Polling only (no webhooks).
   - Store `jobReference` (e.g. `VS373735194179`), NOT `jobId` (hex hash)
   - Poll via `GET /api/v2/job/{jobReference}`
   - pg_cron polls every 30 min; reconcile endpoint can trigger manually
2. Fotello — primary AI photo edits (webhook callback)
3. Autoenhance — fallback if Fotello key missing (webhook callback)
4. QC vision agent: Gemini 1.5 Flash scores photos 0-100; passed(90+)/flagged(70-89)/rejected(<70)
5. Media delivery: signed Supabase URLs → `media_delivery` row → Resend email to agent
6. Stuck-job alert: reconcile endpoint logs ERROR for jobs stuck in post-production >4h

## Rules
- `INTERNAL_API_KEY` header (`X-Internal-Api-Key`) required on dispatch intake endpoint
- Keep vendor selection idempotent — same job can be re-run without double-assigning
- Stripe Connect is Express (CA) — transfers use `stripe_account` parameter
- Never hardcode secrets as default values in `os.environ.get()`
- Storage is Supabase Storage (`db.storage.from_(STORAGE_BUCKET)`), bucket `brxs-deliverables` — NOT AWS S3

## No-Show Policy (Snappr model)

**Agent/client no-show** → photographer gets $60 trip fee from Bricks margin (cancellation revenue):
- `POST /api/vendor-os/jobs/{job_id}/agent-no-show` — photographer reports no-show
- 6 validation gates: job ownership, active state, idempotency, time window (T-15→T+90), booking not pre-cancelled, rate limit
- Rate limit: 2nd+ claim in 30 days → `pending_review` (held for admin before payout released)
- On pass: `agent_noshow_trip_fees` record created, Booking notified via status webhook
- Booking applies 100% retention penalty (no refund, no credit returned); sends agent email
- Estate/high-value cap: max $500 retention on no-shows (chargeback protection)
- $60 added to photographer's next payout cycle by `payout_service.py`

**Admin review queue:**
- `GET /api/admin/noshow-claims?status=pending_review`
- `POST /api/admin/noshow-claims/{id}/review?action=approve|reject`

**Contractor no-show** (photographer ghosts) → `cancel_rate` penalty only, no cash, job re-dispatched.

**Known gaps (pre-launch):**
- No GPS/geofence check at claim time (mobile app change needed)
- No automated SMS/call escalation before flag button activates
- No in-app agent dispute flow (frontend TODO)
- No per-agent rate limit (only per-photographer) — collusion axis not covered
- Timezone: verify `scheduled_time` is UTC before T-15/T+90 window math (risk of 4-5h drift)

## Current Status (2026-05-28)
- Job state machine (pending → assigned → in_progress → completed → delivered) ✅
- **Deterministic weighted dispatch scorer (specialty/proximity/on_time/quality/reliability/load)** ✅
- **Repeat-agent bonus (+5pts if vendor has shot for this agent before)** ✅
- **Drone certification hard gate (drone_certified required for drone jobs)** ✅
- **Gemini 1.5 Flash: explainability only (async, non-blocking, dashboard display)** ✅
- Rule-based fallback with tier gate ✅
- Payout flow (tier % → Stripe Connect transfer → record) ✅
- Post-production pipeline: BoxBrownie + Fotello (primary) + Autoenhance (fallback) ✅
- Media delivery: signed URLs → media_delivery row → Resend email to agent ✅
- QC vision agent: Gemini 1.5 Flash gates delivery; rejected photos excluded ✅
- On-time rate: updated on every job completion (rolling avg, feeds dispatch score) ✅
- Client comms: booking confirmed + photographer en route emails ✅
- Rate limiting + Sentry ✅
- Custom domains: `dispatch-api.brxs.ca`, `dispatch.brxs.ca` ✅
- Vendor tier payouts: Tier A=40%, B=50%, C=60% ✅
- **Snappr/Homejab IC model: no compliance gate, IC agreement v1.0 (15 clauses, Ontario law)** ✅
- **AI RPAS certificate validator (Gemini Vision + TC registry, outcomes: verified/extracted/suspicious/failed)** ✅
- **Agent no-show: claim validation gates + admin review queue + Booking penalty callback** ✅
- **Payout service: trip fees added to photographer payout (not deducted); correct table name** ✅
- **Stripe Connect E2E test suite (idempotency, zero-amount, trip fee addition logic)** ✅
- **Weather context: Open-Meteo API for outdoor/drone jobs** ✅
- app.json bundle IDs + EAS project ID configured (`com.bricks.dispatch`) ✅

## Remaining Before Live Traffic

**Vercel env vars (bricks-dispatch-api):**
- `BOXBROWNIE_PUBLIC_KEY` + `BOXBROWNIE_PRIVATE_KEY` ✅
- `RESEND_API_KEY` ✅ (set)
- `POST_PRODUCTION_CALLBACK_SECRET` ✅ (set)
- `GEMINI_API_KEY` — for QC agent + dispatch explainability
- `FOTELLO_API_KEY` — add once Fotello plan is active
- `INTERNAL_API_KEYS` — rotated; current value stored in app_secrets table

**Ops tasks (pre-launch):**
- [ ] **Ontario employment lawyer review of IC agreement** — budget $2-4K (non-negotiable)
- [ ] **Agent ToS must explicitly disclose 100% no-show retention** — conspicuous at booking, not buried
- [ ] **Confirm HST treatment of forfeited no-show bookings** with accountant (service never rendered)
- [ ] **GPS/geofence gate at claim time** — photographer must be within ~100m of property
- [ ] **Automated escalation before flag** — T+10 SMS to agent, T+20 call; no flag button until T+30
- [ ] **Agent in-app dispute flow** — 72hr window; reply-to email presently only fallback
- [ ] **No-show reconciliation nightly job** — match `agent_noshow_trip_fees` vs `bookings.status`
- [ ] **Trip fee amount in photographer payout summary UI** — they should see pending trip fees
- [ ] Register Fotello/Autoenhance webhook URLs
- [ ] Stripe Connect live E2E verification
- [ ] TestFlight upload (Kam)
- [ ] Play Store internal track (Kam)
- [ ] Verify Transport Canada Advanced RPAS for GTA drone photographers
- [ ] Define ops escalation for dispatch failures (3x retry → manual queue)
