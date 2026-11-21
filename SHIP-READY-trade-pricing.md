# Trade pricing + listing files: ship state (21 Nov 2026)

## Previews (visual; waiting on Kam's "push it live")
- OS listing files: https://listing-timeline-1121.bricks-os.pages.dev/listings?demo_as=agent  (Pages preview branch, demo data; prod untouched)
  - Branch listing-timeline-1121 @ 009f900, on top of live intake 30f9af3. Screenshots: shots/os-*-390.png, shots/os-*-1440.png
- www set prices: https://bricks-site-preview-trades.akashorter.workers.dev/services  (worker bricks-site-preview-trades, version 468f0c0b)
  - Branch trade-pricing-www-1121 @ 306caf0, from live release/www-1121 a1380d7. tsc clean, 373/373 tests. Screenshots: shots/www-*.png

## SHIP-READY, waiting on a narrow Sentinel review (payments / payouts / DB price rows)
Dispatch API, branch trade-pricing-1121 @ df67e528 (child of handyman-on-1121 53850463, which is a child of live 752d12fd). Not deployed.
Scope:
1. app/services/payout_service.py: new _trade_payout branch (trade set cut from trade_price_sheet.json, labour capped 50% / staging install 80% of pre-tax price paid, materials pass through); floor_plan_only cell instead of % fallback; add-on payouts for drone/reel/tour/twilight/rush booked outside the package.
2. app/services/trade_payout_rate_card.py + trade_price_sheet.json (new).
3. app/services/vendor_profile.py: QUOTE_ONLY_TYPES emptied, trades auto-assign; measurer may take floor_plan_only; inspector/handyman need verified unexpired COI with >= $2M CGL (trade_coi_ok).
4. migrations/sql/0015_trade_pricing_floor_plan_cell_legacy_off.sql: INSERT floor_plan_only rows into vendor_payout_rates; UPDATE packages active=false for standard/premium/luxury/estate/internal_test_1cad. Backup first: ops/backup_pricing_tables.sh. Nothing deleted.
5. GO web copy (trades.ts, TradeCard, GuidePage): visual only.
Tests: tests/test_trade_price_sheet.py 11/11; 132 related pass. 5 failures pre-exist on handyman-on-1121 (test_trade_pause x3, phase1 paused repairs, fair_use PG 402), unchanged by this branch.

OS, not yet built (payments): server trades in intake Approve (listing_intake.py PAUSED_TRADES still has handyman; trade booking in Checkout not wired). The OS preview above shows trade copy and timeline only.
OS DB: supabase/migrations/20261121200000_listing_files.sql (listing_files + append-only listing_events, no delete) prepared, not applied. Customer data: Sentinel + backup first.

www payments not changed: propertyCheckout over 20,000 sq ft still says "quoted"; pricing it in checkout needs the leasing_xl formula in create-checkout (Sentinel).

## Stripe
No new products needed: live and test checkout use inline price_data. Nothing created.

## Open
- Trade booking on www is a booking request: Bricks emails the time and a pay link. A self-serve trade checkout (and the multi-vendor, multi-day booking with one payment) is the OS intake trade step, still to build behind Sentinel.
- Companion app rate card and client timeline: not changed yet.
- GO and Companion client timeline views: not built; OS web only.
- Real job rows don't carry vendor name or reschedule fields to /properties yet, so the internal vendor line and "new date" show only in demo until the API adds them.
- Commercial building assessments, post-construction cleans and commercial rentable area were removed from sale (no set price).
