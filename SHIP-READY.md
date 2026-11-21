# Trade pricing: SHIP-READY (updated after Sentinel GO WITH CONDITIONS)

Branch: trade-pricing-1121
- Code: Musts 1–3 fixed in this commit (see below). **Not deployed.**
- Migrations: **held**. Do not apply until Kam approves backup + apply.

## Sentinel Musts

| # | Status | Note |
|---|--------|------|
| 1 | **Fixed in code** | `internal_test_1cad` added to `_RETIRED_FOR_NEW_JOBS`. New jobs cannot use it even before 0015. |
| 2 | **Fixed in code** | `_trade_payout` caps materials to client-paid materials (or net); labour against remaining net; absolute payout ≤ net. Test: 500000 materials + 14900 subtotal → ≤ 14900. |
| 3 | **Fixed in code** | `photographer_payout_cents` calls `_trade_payout` first so GO `payout_display_cad` matches Monday payouts. |
| 4 | **Held** | Migration 0015: Kam must approve → run `ops/backup_pricing_tables.sh` → apply SQL. Sentinel / this agent did not apply. |
| 5 | **Held** | OS `listing_files` migration: do not apply until a scoped API exists (`_allowed_agent_ids`). Listing UI runs on jobs/deals synthesis without the tables. |

## Tests
`tests/test_trade_price_sheet.py` 15/15 pass (includes Must #1–3).

## Deploy
Do **not** deploy Dispatch until Musts 1–3 re-checked if needed and Kam says push for the API. Visual www/OS may ship separately.
