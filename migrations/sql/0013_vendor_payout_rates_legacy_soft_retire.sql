-- Soft-retire documentation for legacy vendor_payout_rates rows (2026-09-27).
-- DO NOT DELETE standard / premium / luxury / estate cells — historical jobs
-- (and any open payouts) still resolve amount_cents by those package_ids.
-- New job intake rejects those package_ids in Dispatch (see payout_service
-- is_retired_for_new_jobs). www / Companion sell only photos_only, essentials,
-- story, showcase, full|suite (+ floor_plan_only on Companion).

COMMENT ON TABLE public.vendor_payout_rates IS
  'Fixed vendor payout cells. ACTIVE www: photos_only/essentials/story/showcase/full × Small≤1200/Mid≤2500/Large≤4000/XL × C/B/A. LEGACY standard/premium/luxury/estate retained for historical job payout reads ONLY — soft-retired for new job intake (2026-09-27). Do not hard-delete.';
