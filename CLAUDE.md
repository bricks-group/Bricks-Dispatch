# Bricks GO (Python/FastAPI + Expo)

You are working on **Bricks GO**, the vendor-facing job orchestration layer of the BRICKS ecosystem.

## Ecosystem Context
- **Bricks-Brokerage** — brokerage admin web dashboard (`brokerage.brxs.ca`) + Mac PWA + iOS companion app. Manages vendors. **Also hosts the photographer Dispatch web shell** — photographers log in at `brokerage.brxs.ca` and are routed to `/dispatch/*` by role.
- **Bricks-Booking** — agent mobile app (iOS + Android). Sends confirmed shoot jobs to this service via `POST /api/v1/dispatch/jobs`
- **Bricks GO** (this repo) — vendor mobile app (iOS + Android) + Dispatch backend API. Assigns jobs to vendors, tracks status, processes payouts.

All three repos share one Supabase project: `ctqwxwrcxwruaqojohpf`

## Two-Sided App Note (2026-06-10)
The Dispatch photographer web experience is now built into the Brokerage React app (`ruletheworld/bricks-brokerage`), NOT as a separate `dispatch.brxs.ca` site. Photographers use `brokerage.brxs.ca` — after login, role-based routing sends them to the dark Dispatch shell at `/dispatch/*`. The Expo `frontend/` in THIS repo remains the primary Android + iOS (Swift) experience for photographers on mobile. The web shell is for desktop/browser access.

## Stack
- Backend: FastAPI (Python 3.14), Supabase, Stripe Connect, Gemini 1.5 Flash (explainability only)
- Frontend (Android): Expo (React Native) + web dashboard (`dispatch.brxs.ca`) — Android primary audience
- Frontend (iOS): **Native Swift — `ios-swift/` directory** — SwiftUI, multi-target Xcode project (see below)
- Deployed: `https://bricks-go-api.vercel.app` / `https://dispatch-api.brxs.ca`

## Database Ownership

All schema migrations live in `ruletheworld/bricks-brokerage/supabase/migrations/` — **never in this repo**. Bricks OS is the DDL authority. The `backend/migrations/` file here was a legacy exception — all future DDL goes to Bricks OS. The live ownership registry is queryable: `SELECT * FROM public.v_service_boundaries;`

**Tables owned by Bricks GO (write here freely):**
- `dispatch_jobs` — create, assign, all status transitions
- `vendor_assignments` — written on assignment
- `payout_records` — Stripe Connect transfer records
- `agent_noshow_trip_fees` — $60 trip fee per validated claim
- `agent_noshow_escalations` — Twilio SMS attempt log
- `reconciliation_events` — fee vs booking mismatch log
- `media_delivery` — signed delivery URLs + Resend trigger
- `vendor_members` — shared with Bricks OS; Bricks GO writes onboarding fields only (tier, ic_agreement_version, rpas certs, on_time_rate)

**Tables Bricks GO reads but must not write:**
- `users` — read vendor/agent identity; never write
- `bookings` — read job intake context; status sent back to Bricks via HTTP (`PUT /api/v1/dispatch-jobs/{booking_id}/status`)
- `brokerage_members` — read brokerage_id for favourite bonus; never write
- `brokerage_vendor_favorites` — read for +15pt dispatch scoring; never write

**Rule:** writing to another service's table → use the HTTP API instead.

## iOS Swift App — `ios-swift/`

**This is a fully built native Swift iOS app.** Do not suggest Expo for iOS — the Swift app is the iOS experience.

### Xcode Targets
- `BricksDispatch` — main iPhone app (SwiftUI, `com.bricks.go`)
- `BricksDispatchWatch` — watchOS app: job offer card → accept/decline, active job status
- `BricksDispatchWidgets` — WidgetKit: next job + today's earnings

### Shared Package
- `BricksCore/` — local Swift Package shared with Booking: APIClient, SupabaseAuth, models, WatchConnectivity, LiveActivities, design tokens

### Key Swift Files
- `BricksDispatch/App.swift` — app entry, APNs delegate, push → job offer routing
- `BricksDispatch/Views/Jobs/JobListView.swift` — job list with status filters
- `BricksDispatch/Views/Jobs/JobDetailView.swift` — job detail, Navigate button, action buttons
- `BricksDispatch/Views/Jobs/JobNavigationView.swift` — MapKit turn-by-turn: MKDirections, polyline, LookAroundPreview, step tracking
- `BricksDispatch/Services/JobStore.swift` — API calls, Watch sync, Live Activity
- `BricksDispatch/Services/PushNotificationService.swift` — APNs routing: job_assigned → watch sync
- `BricksDispatchWatch/Views/JobOfferView.swift` — accept/decline from wrist
- `BricksDispatchWatch/Views/ActiveJobView.swift` — in-progress job status, arrived action

### Build
```bash
cd ios-swift && xcodegen generate   # generates BricksDispatch.xcodeproj from project.yml
```
API base: `https://dispatch-api.brxs.ca`

## Key Files
- `backend/app/main.py` — router registration (no outer prefix; routers carry their own `/api/v1/*`)
- `backend/app/routers/dispatch.py` — `POST /api/v1/dispatch/jobs` intake, status updates
- `backend/app/routers/payouts.py` — `POST /api/v1/payouts/trigger`, list, summary
- `backend/app/routers/jobs.py` — Job CRUD (`/dispatch/jobs/*`)
- `backend/app/routers/vendors.py` — Vendor management (`/dispatch/vendors/*`)
- `backend/app/routers/vendor_os.py` — Vendor OS dashboard: overview, team, jobs, earnings, performance
- `backend/app/routers/onboarding.py` — Contractor application, IC agreement, document upload (photographer-facing)
- `backend/app/routers/post_production.py` — Provider callbacks (Fotello/Autoenhance/BoxBrownie), reconcile
- `backend/app/routers/admin_ops.py` — **Bricks HR tool**: applicant vetting + approval, no-show review, reconciliation
- `backend/app/services/vendor_selection.py` — Primary + fallback vendor assignment
- `backend/app/integrations/bricks_ai_dispatch.py` — **Deterministic weighted scorer** (primary dispatch)
- `backend/app/services/client_comms.py` — Resend emails: booking confirmed + photographer en route
- `backend/app/services/qc_agent.py` — Gemini vision QC agent (scores uploaded photos 0-100)
- `backend/app/services/post_production.py` — Fotello/Autoenhance/BoxBrownie integration
- `backend/app/services/payout_service.py` — Stripe Connect transfers, tier percentages
- `backend/app/services/sms_escalation.py` — Twilio SMS: agent no-show escalation (T+15 before claim)
- `backend/app/shared/schemas.py` — Pydantic models
- `backend/migrations/20260601_agent_noshow_escalations.sql` — new tables: agent_noshow_escalations, reconciliation_events

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
Brokerage favourite bonus: +15 points if the booking's brokerage has favourited this photographer (reads `brokerage_vendor_favorites` table).
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
- Tier 1 (C): 35% payout — entry, Standard routing only
- Tier 2 (B): 45% payout — core, Standard + Premium routing
- Tier 3 (A): 55% payout — elite, full routing (Standard + Premium + Luxury + Estate)
- `min_tier_required` on job is a hard gate — never assigned below it

## Vendor Model: Snappr/Homejab IC Model
- No insurance/WSIB blocking at dispatch — contractors self-certify at onboarding
- `REQUIRE_VENDOR_COMPLIANCE` **removed entirely** from dispatch code
- IC agreement **v1.1** (effective 2026-06-01) — 15 clauses covering Ontario law:
  - Independent contractor status (ESA 2000 exclusion explicit)
  - Right to refuse work (no penalty for declines within 40% rolling-30d threshold)
  - Right to engage others / no exclusivity / no non-compete
  - HST registration responsibility (>$30K threshold)
  - Insurance/liability disclaimer + contractor indemnity
  - Transport Canada drone compliance
  - IP assignment to Bricks
  - Ontario arbitration governing law; Heller compliance
  - Clause 8 payout rates: "Bricks retains platform fee (Tier A: 60%; Tier B: 50%; Tier C: 40%); your payout is the remainder" — these are Bricks' FEES, the inverse of vendor payout (40%/50%/60% → vendor gets 60%/50%/40%). **Do not "fix" these numbers** — they are correct.
- `GET /api/onboarding/ic-agreement` — returns versioned agreement text
- Stored on `vendor_members`: `ic_agreement_version`, `terms_accepted_at`, `hst_number`
- `VendorApplicationCreate.ic_agreement_version` defaults to `"1.1"`
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
1. Fotello — primary AI photo edits (webhook callback)
2. Autoenhance — fallback if Fotello key missing (webhook callback)
3. BoxBrownie — photo editing, retouching, day-to-dusk, item removal, and virtual staging. Polling only (no webhooks).
4. QC vision agent: Gemini 1.5 Flash scores photos 0-100; passed(90+)/flagged(70-89)/rejected(<70)
5. Media delivery: signed Supabase URLs → `media_delivery` row → Resend email to agent
6. Stuck-job alert: reconcile endpoint logs ERROR for jobs stuck in post-production >4h

## Rules
- `INTERNAL_API_KEY` header (`X-Internal-Api-Key`) required on dispatch intake endpoint
- Keep vendor selection idempotent — same job can be re-run without double-assigning
- Stripe Connect is Express (CA) — transfers use `stripe_account` parameter
- Never hardcode secrets as default values in `os.environ.get()`
- Storage is Supabase Storage (`db.storage.from_(STORAGE_BUCKET)`), bucket `brxs-deliverables` — NOT AWS S3
- `time_window_start` is stored as `"HH:MM"` string — **always combine with `scheduled_date` before datetime math**. `datetime.fromisoformat("HH:MM")` raises ValueError. Gate 4 is fail-closed.
- Trip fees flow: `pending` → `reserved` (before Stripe) → `paid_out` (after Stripe). Never go pending→paid_out directly.
- `assign_vendor_for_job(job_id)` has an internal 3-attempt retry loop — callers do not need to retry
- IC Clause 8 lists Bricks' platform FEE (60%/50%/40%), not vendor payout — the numbers are correct and intentionally inverse of TIER_PERCENTAGES

## No-Show Policy (Snappr model)

**Agent/client no-show** → photographer gets $60 trip fee from Bricks margin:
- `POST /api/vendor-os/jobs/{job_id}/escalate-absent-agent` — sends Twilio SMS to agent (attempt 1 + 2); must be called before claiming when Twilio is configured
- `POST /api/vendor-os/jobs/{job_id}/agent-no-show` — files the claim (accepts JSON body: notes, photographer_lat, photographer_lng)
- 8 validation gates: ownership, active state, idempotency, time window (T-15→T+90), GPS proximity (>500m → hold), escalation check (15 min wait after SMS), booking not pre-cancelled, rate limits
- GPS gate: if photographer submits lat/lng and is >500m from property → held for review (not hard rejected)
- Escalation gate: if `TWILIO_ACCOUNT_SID` set, must have called `escalate-absent-agent` + waited 15 min; gracefully degrades if Twilio not configured
- Rate limit: 2nd+ claim in 30 days → `pending_review` (held for admin before payout released)
- On pass: `agent_noshow_trip_fees` record created, Booking notified via status webhook
- Booking applies 100% retention penalty (no refund, no credit returned); sends agent email
- Estate/high-value cap: max $500 retention on no-shows (chargeback protection)
- $60 added to photographer's next payout cycle by `payout_service.py`
- Payout earnings endpoint returns `pending_trip_fees` list and `pending_trip_fees_total` ✅

**Admin review queue:**
- `GET /api/admin/noshow-claims?status=pending_review`
- `POST /api/admin/noshow-claims/{id}/review?action=approve|reject`
- `GET /api/admin/reconcile-noshow?dry_run=true` — matches trip fees vs booking retention status

**Contractor no-show** (photographer ghosts) → `cancel_rate` penalty only, no cash, job re-dispatched.

**No-show escalation tables (apply migration: backend/migrations/20260601_agent_noshow_escalations.sql):**
- `agent_noshow_escalations` — SMS attempt log (job_id, attempt_number, sms_sid, sent_at)
- `reconciliation_events` — fee vs booking mismatch log (written by reconcile-noshow dry_run=false)

## Current Status (2026-06-10)
- Job state machine (pending → assigned → in_progress → completed → delivered) ✅
- **Deterministic weighted dispatch scorer (specialty/proximity/on_time/quality/reliability/load)** ✅
- **Repeat-agent bonus (+5pts if vendor has shot for this agent before)** ✅
- **Brokerage favourite bonus (+15pts if brokerage has favourited the photographer)** ✅
- **Drone certification hard gate (drone_certified required for drone jobs)** ✅
- **Gemini 1.5 Flash: explainability only (async, non-blocking, dashboard display)** ✅
- Rule-based fallback with tier gate ✅
- Payout flow (tier % → Stripe Connect transfer → record) ✅
- Post-production pipeline: Fotello (primary) + Autoenhance (fallback) ✅
- Media delivery: signed URLs → media_delivery row → Resend email to agent ✅
- QC vision agent: Gemini 1.5 Flash gates delivery; rejected photos excluded ✅
- On-time rate: updated on every job completion (rolling avg, feeds dispatch score) ✅
- Client comms: booking confirmed + photographer en route emails ✅
- Rate limiting + Sentry ✅
- Custom domains: `dispatch-api.brxs.ca`, `dispatch.brxs.ca` ✅
- Vendor tier payouts: Tier C=35% (entry), B=45% (core), A=55% (elite) ✅
- **Snappr/Homejab IC model: no compliance gate, IC agreement v1.0 (15 clauses, Ontario law)** ✅
- **AI RPAS certificate validator (Gemini Vision + TC registry, outcomes: verified/extracted/suspicious/failed)** ✅
- **Agent no-show: claim validation gates + admin review queue + Booking penalty callback** ✅
- **Payout service: trip fees added to photographer payout (not deducted); correct table name** ✅
- **Stripe Connect E2E test suite (idempotency, zero-amount, trip fee addition logic)** ✅
- **Weather context: Open-Meteo API for outdoor/drone jobs** ✅
- **BoxBrownie virtual staging: fully active (photo editing, retouching, day-to-dusk, item removal, virtual staging)** ✅
- app.json bundle IDs + EAS project ID configured (`com.bricks.go`) ✅
- **GPS geofence gate: >500m from property → hold_for_review (soft gate, coordinates optional)** ✅
- **SMS escalation (Twilio): POST /api/vendor-os/jobs/{id}/escalate-absent-agent sends 2 attempts; gate in no-show claim flow (15-min wait after first SMS); graceful no-op if Twilio not configured** ✅
- **Reconcile-noshow: GET /api/admin/reconcile-noshow detects fee/booking mismatches; writes reconciliation_events on dry_run=false** ✅
- **Trip fee pending total in earnings API (pending_trip_fees + pending_trip_fees_total)** ✅
- **PR #2 bug fixes (2026-06-01)** ✅
  - Gate 4 fail-closed: `time_window_start` ("HH:MM") combined with `scheduled_date` before fromisoformat; missing/invalid → 400 (not silent pass)
  - Trip fee double-payout prevented: fees reserved before Stripe, released on failure, promoted to paid_out on success
  - `assign_vendor_for_job` has internal 3-attempt retry loop; escalation is now reachable
  - `_get_agent_email_for_job` annotation fixed: `Optional[str]` → `Optional[tuple]`
  - `VendorApplicationCreate.ic_agreement_version` default `"1.0"` → `"1.1"`
  - IC agreement `effective_date` `"2026-01-01"` → `"2026-06-01"`
  - `test_e2e_payout_flow.py`: tier percentages updated to 35/45/55%; result key `amount` → `base_amount`
- **PR #2 bug fixes Round 2 (2026-06-03)** ✅
  - `decline_rate` denominator: uses actual offer count (completed + declined + 1), not just `total_assignments`
  - `_escalate_no_vendor` receives actual attempt count instead of hardcoded max
  - Fallback vendor scorer: decline_rate above 40% neutral band penalises reliability score
  - Upload guard: `file_url=None` from storage → HTTP 500 instead of inserting bad record
- **Stripe Connect E2E test suite (2026-06-03)** ✅ — `tests/conftest.py` stubs external deps; all 11 tests pass
- **Dispatch web shell in Brokerage (2026-06-10)** ✅ — photographer Dispatch experience lives at `brokerage.brxs.ca/dispatch/*`; role-based routing after login; no separate dispatch.brxs.ca web needed
- **Dispatch failure manual queue (2026-06-11)** ✅ — `GET /api/admin/dispatch-failures` + `POST /api/admin/dispatch-failures/{job_id}/reassign`; ops escalation closed on retry; new escalation written on second failure

## Remaining Before Live Traffic

**Vercel env vars (bricks-go-api):**
- `BOOKING_API_URL` — Booking service base URL for internal comms delegation (e.g. `https://booking-api.brxs.ca`); falls back to `BOOKING_CALLBACK_URL` if unset
- `RESEND_API_KEY` ✅ (set)
- `POST_PRODUCTION_CALLBACK_SECRET` ✅ (set)
- `GEMINI_API_KEY` ✅ (set)
- `FOTELLO_API_KEY` ✅ (set)
- `INTERNAL_API_KEYS` — rotated; current value stored in app_secrets table ✅ (set)
- `TWILIO_ACCOUNT_SID` ✅ (set)
- `TWILIO_AUTH_TOKEN` ✅ (set)
- `TWILIO_FROM_NUMBER` ✅ (set)

**Ops tasks (pre-launch):**
- [ ] **Ontario employment lawyer review of IC agreement** — budget $2-4K (non-negotiable)
- [ ] **Agent ToS must explicitly disclose 100% no-show retention** — conspicuous at booking, not buried
- [ ] **Confirm HST treatment of forfeited no-show bookings** with accountant (service never rendered)
- [x] **Apply migration**: `backend/migrations/20260601_agent_noshow_escalations.sql` ✅ (applied to ctqwxwrcxwruaqojohpf 2026-06-03)
- [x] **Stripe Connect E2E test suite** ✅ (all 11 tests pass; conftest stubs + correct patch target — 2026-06-03)
- [ ] Register Fotello/Autoenhance webhook callback URLs in each provider dashboard
- [ ] Stripe Connect live E2E verification (test real CAD transfer end-to-end with live credentials)
- [ ] TestFlight upload (Kam)
- [ ] Play Store internal track (Kam)
- [ ] Verify Transport Canada Advanced RPAS for GTA drone photographers
- [x] **Ops escalation for dispatch failures** ✅ — `GET /api/admin/dispatch-failures` lists failed jobs + escalation record; `POST /api/admin/dispatch-failures/{job_id}/reassign` resets to pending and reruns `assign_vendor_for_job`
- [x] **Agent in-app dispute flow** ✅ — UI lives in Booking app (`frontend/app/job/[id].tsx`); dispute form + 72hr window + backend handler all complete in Bricks-Booking
