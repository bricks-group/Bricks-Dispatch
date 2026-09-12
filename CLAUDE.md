# Bricks GO (Python/FastAPI + Expo)

You are working on **Bricks GO**, the vendor-facing job orchestration layer of the BRICKS ecosystem.

## Ecosystem Context
- **Bricks-OS** — brokerage admin web dashboard (`brokerage.brxs.ca`) + Mac PWA + iOS companion app. Manages vendors. **Also hosts the photographer Dispatch web shell** — photographers log in at `brokerage.brxs.ca` and are routed to `/dispatch/*` by role.
- **Bricks-Companion** — agent mobile app (iOS + Android). Sends confirmed shoot jobs to this service via `POST /api/v1/dispatch/jobs`
- **Bricks GO** (this repo) — vendor mobile app (iOS + Android) + Dispatch backend API. Assigns jobs to vendors, tracks status, processes payouts.

All three repos share one Supabase project: `ctqwxwrcxwruaqojohpf`

**Cross-repo status:** `BRICKS_STATUS.md` in `bricks-group/Bricks-OS` (repo root) is the
shared status doc across all three apps -- read it for anything that spans repos (billing
gaps, cross-service contract drift, launch-readiness findings).

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
- `jobs` — create, assign, all status transitions. **Corrected 2026-07-27, re-verified 2026-09-07**: this table is called `jobs` in code, not `dispatch_jobs` as written here for who knows how long. The one documented exception, `services/qc_agent.py`'s `run_qc_for_job` querying `dispatch_jobs` for `package_name`, **is already fixed** — it uses `.table("jobs")` (currently line 152). Confirmed via a repo-wide grep: zero remaining references to `dispatch_jobs` anywhere in this repo's code. Package-based QC checks (exterior-shot heuristics, deliverable-count verification) get the real `package_name` again.
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
- `backend/app/routers/vendors.py` — **deleted 2026-07-27**, stays deleted. Was a hardcoded stub (`GET /` returned `[]`, no DB access). No real caller ever used its mount path (`/dispatch/vendors/*`) — real vendor logic lives in `vendor_os.py`, unaffected.
- `backend/app/routers/jobs.py` — **corrected 2026-09-07: this is not the deleted 2026-07-27 stub.** A real `GET /api/jobs` router was (re)built since then — scoped to the caller's assigned jobs via `photographer_assignee_ids`, real DB query, `?status=` filter, used by the Expo Schedule/Uploads views and Swift's Schedule/UploadsView. Mounted in `main.py`. Do not confuse this with the old `/dispatch/jobs/*` stub path, which is still gone for good.
- `backend/app/routers/uploads.py` — **corrected 2026-09-07: this line was stale.** Mounted in `main.py` (per that file's own 2026-07-27 comment) — `vendor_os.py:635`'s photo-upload → post-production trigger endpoint is reachable.
- `backend/app/routers/auth.py` — **corrected 2026-09-07: this line was stale.** Mounted in `main.py` alongside `uploads.py` — the IC-agreement re-prompt-on-login path described elsewhere in this file has a live route.
- `backend/app/routers/vendor_os.py` — Vendor OS dashboard: overview, team, jobs, earnings, performance
- `backend/app/routers/onboarding.py` — Contractor application, IC agreement, document upload (photographer-facing)
- `backend/app/routers/post_production.py` — Provider callbacks (Fotello/Autoenhance/BoxBrownie), reconcile
- `backend/app/routers/admin_ops.py` — **Bricks HR tool**: applicant vetting + approval, no-show review, reconciliation
- `backend/app/services/vendor_selection.py` — Primary + fallback vendor assignment
- `backend/app/integrations/bricks_ai_dispatch.py` — **Deterministic weighted scorer** (primary dispatch)
- `backend/app/services/client_comms.py` — Resend emails: booking confirmed + photographer en route
- `backend/app/services/qc_agent.py` — Gemini vision QC agent (scores uploaded photos 0-100)
- `backend/app/services/post_production.py` — Fotello/Autoenhance/BoxBrownie integration
- `backend/video_worker/grade.py` — standalone ffmpeg-based video grade/trim/stabilize worker. **Not imported by anything in `app/` or deployed** — see Post-Production Pipeline's "Motion (Video) Enhancement" section below.
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
- IC agreement **v1.6** (effective 2026-09-09) — source of truth is `_IC_AGREEMENT_TEXT` in `backend/app/routers/onboarding.py`. Rate-card language matches www à-la-carte media (Photos only / Essentials / Story / Showcase / Full suite), not Standard–Estate. Virtual Visit is always a paid add-on. Do not restore v1.1 clauses or % of rack add-on language.
- Contracting party: **Bricks Group**. Contact: `booking@brxs.ca` (no legal@ mailbox).
- Insurance/WSIB (CGL $2M naming Bricks Group additional insured, drone aviation $1M if drone jobs, auto liability, WSIB clearance or exemption declaration) is the contractor's own ongoing responsibility to carry — **not gated on proof before the first dispatched job as of v1.6** (was in v1.0–v1.5; reversed 2026-09-09, see below). Bricks can still ask for proof at any time and suspend/decline to dispatch on a real lapse.
- Payouts are a **fixed vendor rate card** by media package / sqft band / tier, not a % of what the client was charged. www catalog ids alias to existing `vendor_payout_rates` cells (no invented dollars). Add-ons (including Virtual Visit) are fixed published dollar amounts by tier (never % of rack).
- No binding arbitration; Ontario courts. Either party may terminate on written notice (email is enough).
- `GET /api/onboarding/ic-agreement` — `{ version: "1.5", effective_date: "2026-09-06", text }` — live photographer apply form on www.brxs.ca binds applicants to this JSON.
- Stored on `vendor_members`: `ic_agreement_version`, `terms_accepted_at`, `hst_number`. `/apply` stamps the live server version (`1.5`), not a stale client default.
- `VendorApplicationCreate.ic_agreement_version` defaults to `"1.5"`
- `ic_agreement_version='0.0'` = pre-dates new agreement; re-prompt on next login
- **Built then reversed, both 2026-09-09 — code and contract now both fixed.** A first pass made the contract's "required before first job" clause an actual dispatch gate (`lacks_required_compliance_for_first_job()`, wired into `member_eligible_for_job()` alongside `has_lapsed_insurance()`), blocking any vendor without both a verified COI and WSIB on file. Reversed same day per direct instruction, after checking how comparable platforms actually operate — this was stricter than industry norm, not just stricter than convenient. Snappr (the closest direct comparable: on-demand photography marketplace) vets photographers on portfolio/equipment/customer-service interview + peer review, and carries its own liability insurance for shoots rather than gating on individually-verified contractor coverage; TaskRabbit doesn't gate task access on proof of insurance at all; the Uber/DoorDash-style contingent-liability model exists specifically because most gig workers don't carry a full commercial policy, so the platform's own policy is the backstop, not a pre-work requirement. The gate function and its test were removed; `member_eligible_for_job()` now only checks `has_lapsed_insurance()` again (a *previously verified* COI/WSIB going stale — unaffected by this reversal, not in question). `vendor_members.coi_verified`/`wsib_verified` (migration `20260909000000_vendor_compliance_verified_flags.sql` in Bricks-OS, still applied live) are kept as an admin-visibility signal only — set unconditionally the moment `admin_ops.py::verify_document` marks a coi/wsib doc verified — not as a dispatch gate.

  **The contract text itself was flagged as a mismatch and then fixed the same day, on the same direct instruction repeated.** Clause 6 in `_IC_AGREEMENT_TEXT` was rewritten from "INSURANCE AND WSIB (REQUIRED BEFORE FIRST JOB)" — "Before your first dispatched booking you must provide..." — to "6. INSURANCE AND WSIB": the contractor is solely responsible for carrying and maintaining the same coverage (unchanged minimums: $2M CGL naming Bricks as additional insured, $1M drone aviation if applicable, Ontario auto liability, WSIB clearance or exemption declaration), Bricks can ask for proof at any time, and can suspend/decline to dispatch on a real lapse or inability to show current coverage when asked — but nothing is required to be shown before the first job. Clause 16's termination trigger was reworded to match ("fail to maintain required insurance or WSIB coverage after being asked to show proof," not "lack ... proof"). Version bumped **1.5 → 1.6**, effective date **2026-09-06 → 2026-09-09** (`_IC_AGREEMENT_VERSION`/`_IC_AGREEMENT_EFFECTIVE_DATE` in `onboarding.py`, `VendorApplicationCreate.ic_agreement_version` default in `schemas.py`) — same versioning discipline as every prior material change to this text. `tests/test_ic_agreement.py` updated to assert the v1.6 text and lock in the reversal with an explicit negative check (`"REQUIRED BEFORE FIRST JOB" not in text`), the same pattern this file already used for other retired clauses.

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

**Corrected 2026-09-11 — in-house CV enhancement promoted to the default primary path.** Lines 1-2 below described Fotello/Autoenhance as the primary photo-enhancement path for who knows how long; that's no longer accurate as of this date. Business direction: replace the three paid third-party post-production vendors (Fotello, Autoenhance, BoxBrownie) with in-house processing, phased, starting with the classical-CV-replaceable work. Phase 0/1 of that effort is done:

- `select_enhancement_provider(asset)` (`app/services/post_production.py`) is the single place routing is decided for a non-staging photo asset: `bricks_inhouse_cv` (gated by `INHOUSE_CV_ENHANCEMENT_ENABLED`, default `"true"`) is now primary, not Fotello. Only when that flag is off does routing fall back to the old config-time chain (Fotello if `FOTELLO_API_KEY` set, else Autoenhance if `AUTOENHANCE_API_KEY` set).
- **Real runtime failover, not just a refactor.** Before this date, a config-time-selected provider whose actual API call failed at runtime had no fallback for that asset — it was silently dropped (e.g. Fotello's key configured but the call itself erroring never tried Autoenhance). `_select_and_run_enhancement()` now builds the full fallback chain up front (in-house → Fotello → Autoenhance, or Fotello → Autoenhance, whichever vendors are actually configured) and walks it within the same attempt on any failure/exception, logging each attempt (`provider`/`job_id`/`upload_id`/`outcome`/`duration_ms`) — this closes what was previously zero volume/cost signal for these vendor calls anywhere in the codebase.
- In-house enhancement (`app/services/photo_enhance_service.py::enhance_image`) runs **synchronously inline** inside `process_job_assets` (no webhook needed — it's local CPU work: CLAHE + edge-aware detail enhancement + grey-world white balance + unsharp mask, plus two new independently-flagged conservative refinements — `window_pull` (`INHOUSE_WINDOW_PULL_ENABLED`) locally recovers a hint of detail in blown-highlight window regions via a masked exposure pull, and `enhance_sky` (`INHOUSE_SKY_ENHANCE_ENABLED`) mildly boosts saturation/contrast of an already-present sky region — neither is sky replacement or any generative capability, both no-op safely when their target region isn't detected). The actual pixel work is dispatched via `asyncio.to_thread` so it doesn't block the shared event loop; `process_job_assets` itself now also calls `_finalize_job_if_complete` directly at the end of its own run (idempotent, no-ops on any still-in-flight vendor upload) since an all-in-house job has no webhook to trigger finalization the way a Fotello/Autoenhance job always did.
- Writes `provider="bricks_inhouse_cv"` — a new, distinct tag from the older `"bricks_inhouse"` tag used by the pre-existing Tier-3 `/reconcile` last-resort path (stuck-upload timeout with *no* vendor key configured at all). That older path is unchanged, still last-resort, still real — it just now sits behind an already-primary in-house path instead of behind two primary vendors.
- BoxBrownie / virtual staging routing is completely untouched by any of the above — `select_enhancement_provider()` is explicitly out of scope for `asset.get("requires_staging")` assets; those still go straight to `trigger_boxbrownie_staging` first, before this function is ever consulted.
- No generative capability (object removal, day-to-dusk, full sky replacement/compositing) was touched or added — explicitly out of scope for this phase.

**Researched 2026-09-12 — day-to-dusk digital transform: not built, and the research says it shouldn't be.** Before extending Phase 1 with a `day_to_dusk()` in-house transform, checked whether "day-to-dusk" is actually an unfulfilled product need or already solved a different way:
- Companion's `ADD_ONS`/`PACKAGES` (`backend/app/config.py`) already sell dusk imagery as **real on-site photography, not a digital effect**: the `twilight` add-on ($175) is literally "4 twilight exterior photos," and Luxury/Estate's own feature line calls it a "Twilight / blue-hour exterior session" — a session, i.e. the photographer physically shoots it at golden hour/dusk. This repo's own `capture_entitlements.py` treats `"twilight"` as a standalone real-world `job_type` alongside `"drone"`/`"video"`/`"reel"` (`_is_non_photo_standalone`), i.e. it's dispatched as an actual shoot activity, not a post-processing flag. There is no field anywhere in `jobs.add_ons`/`jobs.checklist`/`job_type` that means "take this daytime photo and make it look like dusk" — the only "twilight" signal that exists means "a photographer was there at dusk."
- The "BoxBrownie handles day-to-dusk" line in this file (Post-Production Pipeline #4, and the old Current-Status bullet below) is **doc-only, not live code** — reconfirmed directly: `trigger_boxbrownie_staging()` is the only function that ever calls BoxBrownie's `createVirtualStaging` API, and it only ever builds `VIRTUAL_STAGING` XML. `routers/post_production.py`'s callback handler accepts an arbitrary `service_type` (its own comment lists `"day_to_dusk"` as a hypothetical value) but nothing in this codebase ever originates a day-to-dusk order to get a callback for. This was already suspected stale before this session and is now confirmed: zero call sites, not a wiring bug to fix.
- Conclusion: building a digital day-to-dusk transform would solve a problem Bricks doesn't have. Agents who want a dusk look already get one — a real photo taken at golden hour by the dispatched photographer, billed as its own add-on/session. No `INHOUSE_DAY_TO_DUSK_ENABLED` flag or `day_to_dusk()` function was added. If a genuine need shows up later (e.g. an agent wants the *look* applied to an existing daytime shot without re-dispatching a photographer, or the `twilight` add-on ever gets reframed as a digital deliverable), that would be a real trigger to revisit this — not speculatively building it now.

Pipeline, as it now runs for a non-staging photo asset:
1. **In-house CV enhancement** (`bricks_inhouse_cv`) — primary by default. Synchronous, no webhook. Falls through to the vendor chain below on any exception.
2. Fotello — config-time fallback if `FOTELLO_API_KEY` set (webhook callback). Also the runtime fallback destination if in-house fails.
3. Autoenhance — fallback if Fotello key missing, or if a configured Fotello call itself fails at runtime (webhook callback).
4. BoxBrownie — photo editing, retouching, day-to-dusk, item removal, and virtual staging. Untouched by the above; polling only (no webhooks).
5. 3D tours — **Polycam only** (2026-07-14 — Luma Labs pipeline removed, see below). Either a Polycam share URL pasted into `PropertyDetailPage.js` (embed, zero backend processing) or a manually uploaded `.ply`/`.splat` file signed via `_finalize_job_if_complete`'s generic splat_url resolution. No automated *video*→splat backend pipeline exists — that's still true. But do not read this as "no automated tour pipeline at all": see **Capture Pipeline** below for a separate, real, automated phone-capture → server-side-stitched tour/floorplan pipeline, added after this line was written.
6. QC vision agent: Gemini 1.5 Flash scores photos 0-100; passed(90+)/flagged(70-89)/rejected(<70)
7. Media delivery: signed Supabase URLs → `media_delivery` row → Resend email to agent
8. Stuck-job alert: reconcile endpoint logs ERROR for jobs stuck in post-production >4h

**Video uploads are currently delivered completely raw — traced precisely, 2026-09-12.**
Before building anything for the "motion" capability, confirmed exactly what happens to a
`.mp4`/`.mov`/`.avi`/`.zip`/`.tar` upload today, since it wasn't obvious from the code
alone which of two very different things was happening. `process_job_assets()` (line ~377)
does skip these extensions from the enhancement-provider loop entirely (`continue`, no
`processing_status` ever set on that upload row) — that much matched what a prior research
pass had flagged. What that pass didn't trace further: an unset `processing_status` is
`None`, and `_finalize_job_if_complete()`'s in-flight gate only blocks on `"processing"`/
`"pending"` — `None` sails through. `qc_agent.py::run_qc_for_job` then explicitly matches
`.mp4`/`.mov` (not `.avi` — pre-existing gap, out of scope here) and marks them
`qc_status="passed"` with a "Non-image asset — QC skipped" note, without ever inspecting
the file. Delivery's own filter only excludes `.splat`/`.ply` by filename and
`processing_status == "failed"` — neither applies — so `path = processed_url or file_url`
resolves to the raw uploaded `file_url`, gets signed, and lands in `media_delivery.
delivery_urls` and the agent's delivery email exactly as uploaded. **So: video is
delivered, not dropped — just never touched.** This matters for scoping "motion
enhancement" as a real gap (grading a raw upload before delivery), not a missing feature
on top of nothing.

## Motion (Video) Enhancement — researched 2026-09-12, built standalone, NOT deployed

Phase 2 of the in-house post-production effort (Phase 0/1 = photos, above) was scoped as
color grading + trim + stabilization for real camera-shot walkthrough/drone video —
explicitly **not** AI-generated video, classical ffmpeg-filtergraph work on footage that
already exists. Researched feasibility on this repo's actual deployment target before
writing any processing code, per instruction, rather than assuming either way:

- **Bundle size**: `backend/vercel.json` uses the legacy `@vercel/python` Build Output API
  v2 builder with `maxLambdaSize: "250mb"` already configured — this repo hasn't opted into
  the newer Vercel Functions format that Python's 500MB default / 5GB "large functions"
  (Fluid Compute + Active CPU) apply to. A static ffmpeg binary (~70-100MB, e.g. via
  `imageio-ffmpeg`) stacked on top of this function's already-bundled
  opencv-python-headless/numpy/Pillow is a real risk against that 250MB cap, and Vercel's
  own current guidance is that bundling ffmpeg into a Python serverless function is "not
  recommended" for exactly this reason.
- **Execution time**: `maxDuration: 300` in the same file matches Vercel Hobby's Fluid
  Compute ceiling (fixed, not raisable); Pro/Enterprise can reach 800s standard / 1800s
  beta, but nothing in this repo's config or docs says which plan is actually live. Even a
  generous budget is a real risk for genuine on-site footage (this task's own framing
  included raw drone footage, not only the packaged 60-90s cinematic cut) — two-pass
  stabilization in particular is a real CPU-heavy motion-analysis step (timed locally: ~0.3s
  for a 3-second 320×240 test clip, which does not extrapolate safely to minutes of 4K
  drone footage), and a mid-encode kill on any hard ceiling leaves a stuck job with no
  partial-output recovery path — unlike the photo pipeline's synchronous-but-fast per-image
  CV pass.
- **This repo already has a `backend/Dockerfile`** (apt-get system deps, runs
  `uvicorn app.main:app` directly, no serverless wrapper or size/duration ceiling) — but
  CLAUDE.md's own "Deployed:" line names only the two Vercel-fronted hosts
  (`bricks-go-api.vercel.app` / `dispatch-api.brxs.ca`), and nothing in this repo says that
  Dockerfile is an actual live deployment target today. Flagged as the most likely home for
  this capability if/when it becomes one — not assumed, not wired to.

**Decision: built the actual grading/trim/stabilization logic, but as a standalone module,
not wired into the live pipeline** (`process_job_assets`/`_finalize_job_if_complete` above
are untouched by this work) — per the explicit instruction not to force a half-working
serverless integration. `backend/video_worker/grade.py` (own module docstring has the full
detail, including the exact photo→video filter mapping):

- `process_video(input_path, output_path, *, max_duration_s=None, stabilize=False, ...)` —
  the one entry point a future integration would call. Shells out to a system `ffmpeg`/
  `ffprobe` via subprocess; pure local-file-path in/out, zero dependency on anything in
  `app/` (no Supabase/storage.py wiring — that's left for whoever eventually hosts this,
  the same way `post_production.py::_run_inhouse_cv_enhancement` wraps
  `photo_enhance_service.py` for photos).
- Grading filtergraph translates photo_enhance_service.py's *intent*, not its per-pixel
  algorithm (confirmed too slow for video, per instruction): CLAHE → ffmpeg's `clahe`
  filter, **falling back to `histeq`** (global, not tile-based — weaker but same intent)
  when `clahe` isn't compiled in — confirmed this actually matters: the ffmpeg build tested
  against locally (Ubuntu 24.04's `apt` package, ffmpeg 6.1.1) has `libvidstab` but **no
  `clahe` filter at all**, so the fallback path is the one that actually runs today, not a
  defensive dead branch. Grey-world white balance → a fixed mild `colorbalance` cool-cast
  reduction (not a true per-shot analysis — same "too slow" reasoning). Final `unsharp` +
  a mild `eq` saturation lift for crispness/richness.
- Stabilization: real two-pass `vidstabdetect`/`vidstabtransform`, feature-detected via
  `ffmpeg -filters` (not assumed present) and chained into the same final encode as the
  grading filters rather than a separate re-encode pass. Degrades to graded-but-not-
  stabilized with a logged warning (never an exception) when the ffmpeg build lacks
  `libvidstab`.
- Trim: `max_duration_s` is accepted but never hardcoded — this repo has no per-package
  target video length constant (that lives in Companion's config, e.g. its "60-90s
  cinematic walkthrough" package copy); a caller passes the length in. `trim_only()` is
  also exposed standalone (lossless stream-copy, no re-encode) for a trim-without-grading
  case.
- Tested in isolation (`backend/tests/test_video_grade_worker.py`) against an
  ffmpeg-`lavfi`-generated synthetic test-pattern clip, the same "no external fixture"
  convention `test_photo_enhance_service.py` uses for images — filtergraph fallback
  selection, grade+trim+stabilize end-to-end, graceful degradation without libvidstab, and
  the ffmpeg-unavailable error path. Skips (not fails) if `ffmpeg`/`ffprobe` aren't on
  PATH, so it collects cleanly in an environment without them (this sandbox didn't have
  ffmpeg until installed for this research; GitHub Actions' `ubuntu-latest` runner ships it
  preinstalled, so CI exercises these for real).
- **Open decision for the user, not made here**: where this actually runs in production —
  the Dockerfile above, a dedicated worker, or the Mac-inference-server infra a parallel
  image-generation effort is reportedly building. Whichever it is, that integration still
  needs to be built (download from Storage → `process_video()` → upload result →
  `media_delivery`/`uploads` row, mirroring the photo path's conventions) — not assumed or
  started here.

## Capture Pipeline (undocumented until 2026-09-07 — real, substantial, not a stub)

Phone-capture flow for GO's guided 360°-station camera (Matterport-style), separate
from the photographer's professional-camera post-production pipeline above. Merged in
from the now-archived `Bricks-Capture` repo (frontend) plus a real backend built
since that merge — `Bricks-Capture`'s own README describes the review screen's
"Submit to pipeline" as a no-op stub; that's now stale, it's fully wired.

- `backend/app/routers/capture.py` — `/api/capture/*`: `sessions`, `rooms`, `stations`,
  `frames/sign` + `frames/commit` (signed uploads), `submit` (compiles stations into a
  real deliverable — refuses if no stations/equirects), `rebuild-tour`,
  `rebuild-floorplan`, `visit/sign` + `visit/deliver` (Virtual Visit add-on).
- `backend/app/services/capture_worker.py` — stitches pan-burst frames into equirects
  server-side and compiles stations into a hosted tour / 2D floor plan. Failures raise
  `CaptureWorkerError`, never silently mark a job Delivered.
- `backend/app/services/capture_entitlements.py` — which GO in-app capture modes a job
  actually purchased (`splat`/`floorplan`/`visit`), detected from real `jobs` columns
  (`package_id`, `package_name`, `job_type`, `add_ons`, `checklist`,
  `client_instructions`, `property_details`) — not a separate purchase table.
- `backend/app/services/equirect_stitcher.py`, `tour_builder.py`, `capture_layout.py` —
  supporting geometry/stitching/path-layout logic. `equirect_stitcher.py` needs
  `numpy`/`opencv-python-headless` (both in `requirements.txt`).
- Storage tables (`capture_sessions`/`rooms`/`pipeline`/`frames`) live in Bricks-OS,
  service-role-only, same convention as `jobs`/`uploads`.
- Frontend: `frontend/app/(capture)/` — `capture.tsx` (camera + coverage dome),
  `mode.tsx`, `visit.tsx`, `floorplan.tsx`, `review.tsx` (coverage-by-room grid, real
  submit/retry-compile/rebuild actions via `frontend/lib/capture-api.ts`, not stubs).
  Requires `expo-camera` (in `frontend/package.json`).
- Design rule: phone-mode capture is splat/tour frames only — professional stills stay
  vendor-camera-only, never on a phone (`feedback_capture_app_no_stills_cannibalization`).
  This is why there's no USB-C RAW ingest path here even though one existed in the old
  standalone Capture repo prototype — dropping it was the intended fix, not a gap.

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
- Post-production pipeline: Fotello (primary) + Autoenhance (fallback) ✅ — **superseded 2026-09-11**: in-house CV enhancement (`bricks_inhouse_cv`) is now the default primary path with real runtime failover to Fotello → Autoenhance; see Post-Production Pipeline section above.
- Media delivery: signed URLs → media_delivery row → Resend email to agent ✅
- QC vision agent: Gemini 1.5 Flash gates delivery; rejected photos excluded ✅
- On-time rate: updated on every job completion (rolling avg, feeds dispatch score) ✅
- Client comms: booking confirmed + photographer en route emails ✅
- Rate limiting + Sentry ✅
- Custom domains: `dispatch-api.brxs.ca`, `dispatch.brxs.ca` ✅
- Vendor tier payouts: Tier C=35% (entry), B=45% (core), A=55% (elite) ✅
- **Snappr/Homejab IC model: IC agreement v1.5 (Bricks Group, Ontario; www à-la-carte rate card; Virtual Visit paid add-on; insurance/WSIB required before first job)** ✅
- **AI RPAS certificate validator (Gemini Vision + TC registry, outcomes: verified/extracted/suspicious/failed)** ✅
- **Agent no-show: claim validation gates + admin review queue + Booking penalty callback** ✅
- **Payout service: trip fees added to photographer payout (not deducted); correct table name** ✅
- **Stripe Connect E2E test suite (idempotency, zero-amount, trip fee addition logic)** ✅
- **Weather context: Open-Meteo API for outdoor/drone jobs** ✅
- **BoxBrownie virtual staging: fully active** ✅ — virtual staging only; "photo editing, retouching, day-to-dusk, item removal" in this bullet's original wording described BoxBrownie's platform capabilities, not live call sites in this repo — **see the 2026-09-12 research note in Post-Production Pipeline above**, only `trigger_boxbrownie_staging()` (virtual staging) is ever actually called.
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
- [ ] **Get accountant sign-off on the HST treatment already implemented for forfeited no-shows** (service never rendered) — this is Companion's `_apply_noshow_penalty`/`booking_cancel` logic, not this repo's; **verified 2026-09-09** it already retains the full gross (pre-tax + HST) on full forfeiture per Excise Tax Act s. 182, consistently, and is now tested (`Bricks-Companion/backend/tests/test_noshow_hst.py`). The remaining task is a real accountant confirming that read, not a code gap.
- [x] **Apply migration**: `backend/migrations/20260601_agent_noshow_escalations.sql` ✅ (applied to ctqwxwrcxwruaqojohpf 2026-06-03)
- [x] **Stripe Connect E2E test suite** ✅ (all 11 tests pass; conftest stubs + correct patch target — 2026-06-03)
- [ ] Register Fotello/Autoenhance webhook callback URLs in each provider dashboard
- [ ] Stripe Connect live E2E verification (test real CAD transfer end-to-end with live credentials)
- [ ] TestFlight upload (Kam)
- [ ] Play Store internal track (Kam)
- [ ] Verify Transport Canada Advanced RPAS for GTA drone photographers
- [x] **Ops escalation for dispatch failures** ✅ — `GET /api/admin/dispatch-failures` lists failed jobs + escalation record; `POST /api/admin/dispatch-failures/{job_id}/reassign` resets to pending and reruns `assign_vendor_for_job`
- [x] **Agent in-app dispute flow** ✅ — UI lives in Booking app (`frontend/app/job/[id].tsx`); dispute form + 72hr window + backend handler all complete in Bricks-Companion
