# Bricks GO (Python/FastAPI + Expo)

You are working on **Bricks GO**, the vendor-facing job orchestration layer of the BRICKS ecosystem.

## Ecosystem Context
- **Bricks-OS** — brokerage admin web dashboard (`brokerage.brxs.ca`) + Mac PWA + iOS companion app. Manages vendors. **Also hosts the photographer Dispatch web shell** — photographers log in at `brokerage.brxs.ca` and are routed to `/dispatch/*` by role.
- **Bricks-Companion** — agent mobile app (iOS + Android). Sends confirmed shoot jobs to this service via `POST /api/v1/dispatch/jobs`
- **Bricks GO** (this repo) — vendor mobile app (iOS + Android) + Dispatch backend API. Assigns jobs to vendors, tracks status, processes payouts.

All three repos share one Supabase project: `ctqwxwrcxwruaqojohpf`

## Two-Sided App Note (2026-06-10)
The Dispatch photographer web experience is now built into the Bricks OS React app (`ruletheworld/bricks-os`), NOT as a separate `dispatch.brxs.ca` site. Photographers use `brokerage.brxs.ca` — after login, role-based routing sends them to the dark Dispatch shell at `/dispatch/*`. The Expo `frontend/` in THIS repo remains the primary Android + iOS (Swift) experience for photographers on mobile. The web shell is for desktop/browser access.

## Stack
- Backend: FastAPI (Python 3.14), Supabase, Stripe Connect, Gemini 1.5 Flash (explainability only)
- Frontend (Android): Expo (React Native) + web dashboard (`dispatch.brxs.ca`) — Android primary audience
- Frontend (iOS): **Native Swift — `ios-swift/` directory** — SwiftUI, multi-target Xcode project (see below)
- Deployed: `https://bricks-go-api.vercel.app` / `https://dispatch-api.brxs.ca`

## Database Ownership

All schema migrations live in `ruletheworld/bricks-os/supabase/migrations/` — **never in this repo**. Bricks OS is the DDL authority. The `backend/migrations/` file here was a legacy exception — all future DDL goes to Bricks OS. The live ownership registry is queryable: `SELECT * FROM public.v_service_boundaries;`

**Tables owned by Bricks GO (write here freely):**
- `jobs` — create, assign, all status transitions. **Corrected 2026-07-27, then corrected again (date unknown): `services/qc_agent.py`'s `dispatch_jobs` lookup was already fixed to `jobs` by the time this session checked** (`qc_agent.py:152`: `db.table("jobs").select("package_name")...`) — a repo-wide grep for `dispatch_jobs` now hits nothing outside this file's own history. The "one exception" this line used to describe no longer exists in code; don't re-apply this fix.
- Assignment is **not** a separate table — it's an `assignee_id` update directly on the `jobs` row (`services/vendor_selection.py:127-131`). No `vendor_assignments` table exists or is ever queried; corrected 2026-07-27.
- Payout records live on the `jobs`/`payouts` tables (`services/payout_service.py` writes to a table literally called `payouts`, not `payout_records`); corrected 2026-07-27.
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
- `backend/app/routers/vendors.py` — **deleted 2026-07-27**, stays deleted. Was a hardcoded stub (`GET /` returned `[]`, no DB access). No client calls its old mount path (`/dispatch/vendors/*`); real vendor logic lives in `vendor_os.py`.
- `backend/app/routers/jobs.py` — **corrected, date unknown: this is NOT the stub deleted 2026-07-27, and IS mounted in `main.py`.** A real `GET /api/jobs` endpoint was added back under this filename — photographer job list scoped to the caller's assigned jobs via `photographer_assignee_ids`, with optional `?status=` filter, for the Expo Schedule/Uploads screens and Swift `Schedule`/`UploadsView`. Verified by reading the file directly. The prior note describing this file as a deleted stub was stale.
- `backend/app/routers/uploads.py` — **corrected, date unknown: this is mounted in `main.py` (`app.include_router(uploads.router, ...)`) and is a real, complete implementation** — signed-URL stills upload (bypasses the 4.5MB Vercel POST cap), base64 small-file create, list/get/delete, each properly scoped to the caller's vendor via `job.vendor_id`/`assignee_id` (two vendor-scoping bugs here were already found and fixed 2026-08-15 per the file's own comments). The prior note claiming this wasn't mounted and that `vendor_os.py:635`'s upload→post-production trigger was unreachable was stale — verified by reading `main.py` and the file directly.
- `backend/app/routers/auth.py` — **corrected, date unknown: this is mounted in `main.py` and is a real, complete implementation** — Supabase OTP login/verify/logout for the Vendor OS web shell (`/api/vendor-os/auth/*`), including `vendor_members` provisioning on first verify and a KAM-email/id canonicalization path. The prior note claiming this wasn't mounted (and that IC-agreement re-prompt-on-login had no live path) was stale — verified by reading `main.py` and the file directly.
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
Brokerage favourite bonus: +15 points if the booking's brokerage has favourited this photographer (reads `brokerage_vendor_favorites` table). **Corrected 2026-08-30 — the 2026-07-27 "cannot fire" note below was itself stale.** `Job`/`JobIntakeRequest` (`app/shared/schemas.py:71,129`) both declare `brokerage_id: Optional[str] = None`, and `bricks_ai_dispatch.py:337-355` reads it via `getattr(job, "brokerage_id", None)` and queries `brokerage_vendor_favorites` when set. This is real, working code, not dead — verified by reading both files directly. Unclear exactly when the schema fix landed relative to the correction below; the doc just never caught up.
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
- IC agreement **v1.8** (effective 2026-10-03) — source of truth is `_IC_AGREEMENT_TEXT` in `backend/app/routers/onboarding.py` (matches www.brxs.ca/legal/ic-agreement.txt). Rate-card language matches www à-la-carte media (Photos only / Essentials / Story / Showcase / Full suite), not Standard–Estate. Virtual Visit is always a paid add-on. Do not restore older clauses or % of rack add-on language.
- Contracting party: **Bricks Group**. Contact: `booking@brxs.ca` (no legal@ mailbox).
- Insurance/WSIB proof is **not a gate** for apply, approval, assignment, or dispatch. Optional COI/WSIB uploads may still be stored (`coi_expires_on` / `wsib_expires_on` are historical only). Missing, expired, or unverified proof must not suspend, reject, or hold a job. RPAS/drone legal duty is unchanged. IC v1.8 Clause 6 states COI/WSIB are not required for apply or first dispatch.
- Payouts are a **fixed vendor rate card** by media package / sqft band / tier, not a % of what the client was charged. www catalog ids alias to existing `vendor_payout_rates` cells (no invented dollars). Add-ons (including Virtual Visit) are fixed published dollar amounts by tier (never % of rack).
- No binding arbitration; Ontario courts. Either party may terminate on written notice (email is enough).
- `GET /api/onboarding/ic-agreement` — `{ version: "1.8", effective_date: "2026-10-03", text }` — live photographer apply form on www.brxs.ca binds applicants to this JSON.
- Stored on `vendor_members`: `ic_agreement_version`, `terms_accepted_at`, `hst_number`. `/apply` stamps the live server version (`1.8`), not a stale client default.
- `VendorApplicationCreate.ic_agreement_version` defaults to `"1.8"`
- `ic_agreement_version='0.0'` = pre-dates new agreement; re-prompt on next login
- `REQUIRE_VENDOR_COMPLIANCE` remains absent from dispatch scoring code. `member_eligible_for_job` does not read `coi_expires_on` or `wsib_expires_on`.

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
4. 3D tours — **Polycam only** (2026-07-14 — Luma Labs pipeline removed, see below). Either a Polycam share URL pasted into `PropertyDetailPage.js` (embed, zero backend processing) or a manually uploaded `.ply`/`.splat` file signed via `_finalize_job_if_complete`'s generic splat_url resolution. No automated video→splat backend pipeline exists.
5. QC vision agent: Gemini 1.5 Flash scores photos 0-100; passed(90+)/flagged(70-89)/rejected(<70)
6. Media delivery: signed Supabase URLs → `media_delivery` row → Resend email to agent
7. Stuck-job alert: reconcile endpoint logs ERROR for jobs stuck in post-production >4h

## CGI Render Pipeline (developer/REIT vertical, added 2026-09-13)

`backend/app/services/cgi_render.py` — **deliberately NOT mounted in `main.py`, and this is correct, not a bug.** Unlike `uploads.py`/`auth.py` (found unmounted-by-accident and fixed 2026-09-13, see Database Ownership section above), this file is a standalone worker script, meant to run only via `cgi_render.Dockerfile` (provisions Blender + an Xvfb virtual display + fluxbox, then execs `python -m app.services.cgi_render` — running the bare module elsewhere fails on the pyautogui import, no DISPLAY). It needs a persistent host, not Vercel serverless, since the render loop can run minutes per job and needs that live desktop for Astra's computer-use tool to actually drive. **Before mounting this as a router or otherwise "fixing" it: don't.** It polls Bricks-OS's `GET /api/dev/cgi-assets/queued` and reports back via `PATCH /api/dev/cgi-assets/{id}/status`, both `X-Internal-Api-Key` gated like every other cross-service call in this repo.

Two things actually block this from running for real (both infra/ops decisions, not code):
1. `OPENAI_API_KEY` env var (model `gpt-6-astra`, Responses API — tool calling requires `/v1/responses`, not Chat Completions, per OpenAI's own docs as of 2026-09-13). Not currently configured anywhere in this repo.
2. A deploy target for `cgi_render.Dockerfile` — small always-on VM vs. a container platform supporting long-running non-HTTP workers. Not provisioned or chosen.

`_execute_computer_action` is real, complete code now (not a stub) — drives pyautogui (click/type/keypress/scroll/drag/wait/screenshot) against the Dockerfile's Xvfb display, auto-acknowledges `pending_safety_checks` (safe here since it's driving an isolated render VM with no real user credentials on it, not a real user's desktop).

**Verified 2026-09-13 against a real local Xvfb + fluxbox + Blender 4.0 stack** — everything except the actual OpenAI call was tested for real, not just written to spec: `_run_blender_headless` (the real function) genuinely renders via `blender --background --python`; Blender's full interactive GUI genuinely launches under Xvfb with no GPU (Mesa software/surfaceless EGL); a real pyautogui click on Blender's splash screen registered and changed the app's state (confirmed via before/after screenshots and Blender's own "Preferences saved" log line) — the screenshot→action→changed-state→new-screenshot loop this pipeline depends on is real. Also **found and fixed a real bug this way**: `cgi_render.Dockerfile` originally installed `scrot` for pyautogui's screenshot backend; testing showed pyscreeze actually requires `gnome-screenshot` specifically (scrot alone fails) — corrected.

Only the OpenAI side remains unverified: the computer-use tool's exact JSON schema in `_run_astra_session` (action types, `environment` field, safety-check fields) is written against OpenAI's documented `computer-use-preview` shape plus their own Astra architectural-visualization case study, but no real `gpt-6-astra` Responses API call has been made — do that once #1 and #2 above are resolved. Since the local half (`_execute_computer_action`, `_run_blender_headless`) is now confirmed working, a failure at that point will be about the OpenAI integration specifically, not the underlying Blender/display plumbing. The control flow around all of it (queue poll, `MAX_ITERATIONS` cost cap, status reporting, upload to `brxs-deliverables`) is real and unaffected by that remaining check.

## Real Progress-Capture Job Type (developer/REIT vertical, added 2026-09-13)

The other half of the developer/REIT vertical alongside CGI rendering above — real photo/video capture of a site under construction, reusing this repo's existing vendor dispatch network rather than building a second one. A job with `job_type="progress_capture"` is created the same way any Booking job is (`POST /api/v1/dispatch/jobs`, same `assign_vendor_for_job` scorer — `"progress_capture"` just needs to match a vendor's `specialty` string like any other job type, no scorer changes needed), except it originates from Bricks-OS's `developer_projects.py` (`POST /dev/projects/{id}/capture-visits`) instead of Bricks-Companion, and carries `dev_project_id`/`dev_phase_id`/`dev_capture_visit_id` instead of `booking_id`.

- `JobIntakeRequest.booking_id` is now `Optional` (was required) — `create_dispatch_job` (`routers/dispatch.py`) instead requires **exactly one** of `booking_id` / `dev_capture_visit_id` to be set (400 otherwise), so every job still traces back to exactly one caller that can look it up and receive its status.
- `update_job_status` branches the same way: `booking_id` set → `send_dispatch_status_update_webhook` (unchanged, existing Booking path); `dev_capture_visit_id` set instead → new `send_dev_capture_status_update_webhook` (`services/webhooks.py`), which `PATCH`es Bricks-OS's `/api/dev/capture-visits/{id}/status` with `X-Internal-Api-Key`, same convention as every other cross-service call in this repo. Only terminal statuses round-trip (`completed`/`delivered` → `completed`, `cancelled`/`dispatch_failed` → `failed`) since `dev_capture_visits` has no column for an in-progress state — mid-flight updates are a no-op, not a bug.
- On `completed`, best-effort attaches the job's first `media_delivery.delivery_urls` entry as `media_delivery_url` on the capture-visit record (wrapped in try/except — a lookup failure must never block reporting the status itself).
- `OS_API_URL` env var (default `https://bricks-brokerage-api.vercel.app`) — same name/default `cgi_render.py` already uses for the same purpose (calling Bricks-OS), reused here rather than inventing a second variable.
- **Known, deliberate gap**: `trigger_payout_for_job` still fires on `completed`/`delivered` same as any job, but a progress-capture job has no `package_id`/`rack_price` (no package — there's no shoot-package pricing model for recurring site visits yet), so `calculate_payout_amount` correctly returns 0 and the payout attempt short-circuits with a logged `zero_payout_amount` (see `payout_service.py:52`) rather than crashing or paying an undefined amount. A real pricing/billing model for capture visits (recurring cadence, likely a subscription or per-visit fee — see Bricks-OS CLAUDE.md's developer/REIT roadmap item 6) is separately-scoped work; don't stub a payout number to make this "work" in the meantime.
- Schema: `dev_project_id`/`dev_phase_id`/`dev_capture_visit_id` uuid columns added to this repo's `jobs` table via Bricks-OS's `supabase/migrations/20260913170000_dev_project_location.sql` (DDL lives there per this repo's own rule even though this repo writes the row) — applied to the live "Bricks Canada" Supabase project (`rbywjnnctjglhcdemnnj`), confirmed via direct query.
- **Not built yet**: no frontend UI to schedule a capture visit (API only — Bricks-OS's `DeveloperProjectsPage.js` doesn't have this yet) or to set a project's new `address`/`lat`/`lng` fields.

## Rules
- `INTERNAL_API_KEY` header (`X-Internal-Api-Key`) required on dispatch intake endpoint
- Keep vendor selection idempotent — same job can be re-run without double-assigning
- Stripe Connect is Express (CA) — transfers use `stripe_account` parameter
- Never hardcode secrets as default values in `os.environ.get()`
- Storage is Supabase Storage (`db.storage.from_(STORAGE_BUCKET)`), bucket `brxs-deliverables` — NOT AWS S3
- `time_window_start` is stored as `"HH:MM"` string — **always combine with `scheduled_date` before datetime math**. `datetime.fromisoformat("HH:MM")` raises ValueError. Gate 4 is fail-closed.
- Trip fees flow: `pending` → `reserved` (before Stripe) → `paid_out` (after Stripe). Never go pending→paid_out directly.
- `assign_vendor_for_job(job_id)` has an internal 3-attempt retry loop — callers do not need to retry
- IC Clause 8 lists Bricks' platform FEE (45%/55%/65% for A/B/C, corrected 2026-07-20), not vendor payout — intentionally inverse of TIER_PERCENTAGES (35%/45%/55% for C/B/A)

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
- **Centralized pricing tables (2026-06-13)** ✅ — `public.packages` table in Supabase is now the source of truth for package inclusions, sqft pricing, and delivery SLAs. Standard=photos+drone stills (24h, no splat); Premium=Standard+video+splat (24h); Luxury=Premium+twilight+extended video+specialist edit+custom domain (12h rush); Estate=Luxury+4K drone+reels+account manager ($2,499 flat, 12h rush).
- Job state machine (pending → assigned → in_progress → completed → delivered) ✅
- **Deterministic weighted dispatch scorer (specialty/proximity/on_time/quality/reliability/load)** ✅
- **Repeat-agent bonus (+5pts if vendor has shot for this agent before)** ✅
- **Brokerage favourite bonus (+15pts if brokerage has favourited the photographer)** ✅ — **re-corrected 2026-08-30: this is real and working, see line 117 above. The 2026-07-27 "dead code" correction was itself stale by the time this session found it.**
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
- **Snappr/Homejab IC model: IC agreement v1.8 (Bricks Group, Ontario; www à-la-carte rate card; Virtual Visit paid add-on). Insurance/WSIB proof does not block apply or first dispatch.** ✅
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
- ~~Gaussian splat processing via Luma Labs (2026-06-13)~~ — **REMOVED 2026-07-14**. Never functional: the endpoint called Luma's Dream Machine `generation_type` field, which only ever accepted `"video"` — structurally incapable of producing a splat. No live job had gone through it. `splat_service.py` deleted; `/splat-upload/{job_id}` and `_process_splat_background` removed from `routers/post_production.py`; the Luma routing block removed from `process_job_assets()` in `services/post_production.py`. 3D tours are Polycam-only now (embed URL or manual `.ply`/`.splat` upload) — see Post-Production Pipeline above.

## Remaining Before Live Traffic

**Vercel env vars (bricks-go-api):**
- `BOOKING_API_URL` — Booking service base URL for internal comms delegation (e.g. `https://booking-api.brxs.ca`); falls back to `BOOKING_CALLBACK_URL` if unset
- `OS_API_URL` — Bricks-OS base URL for `cgi_render.py`'s queue polling (defaults to `https://bricks-brokerage-api.vercel.app`) — ⬜ not yet needed live, that worker isn't deployed anywhere yet
- `OPENAI_API_KEY` — for `cgi_render.py` (Astra/Blender CGI pipeline) — ⬜ not set, and no deploy target chosen yet either (see CGI Render Pipeline section above)
- `RESEND_API_KEY` ✅ (set)
- `POST_PRODUCTION_CALLBACK_SECRET` ✅ (set)
- `GEMINI_API_KEY` ✅ (set)
- `FOTELLO_API_KEY` ✅ (set)
- `INTERNAL_API_KEYS` — rotated; current value read via `os.environ.get(...)` at every call site (`vendor_os.py`, `onboarding.py`, `dispatch.py`, `post_production.py`, `payouts.py`, `shared/auth.py`) ✅ (set). **Corrected 2026-07-27**: there is no `app_secrets` table — nothing in this codebase reads Supabase for this value, it's a plain env var.
- `TWILIO_ACCOUNT_SID` ✅ (set)
- `TWILIO_AUTH_TOKEN` ✅ (set)
- `TWILIO_FROM_NUMBER` ✅ (set)
- `CALENDAR_TOKEN_ENCRYPTION_KEY` — app-level Fernet (or any secret) for encrypting `vendor_calendar_connections` OAuth/CalDAV secrets at rest; fail-soft if unset (FreeBusy → unknown). See `memory/calendar-token-encryption.md`

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
- [x] **Agent in-app dispute flow** ✅ — UI lives in Booking app (`frontend/app/job/[id].tsx`); dispute form + 72hr window + backend handler all complete in Bricks-Companion
