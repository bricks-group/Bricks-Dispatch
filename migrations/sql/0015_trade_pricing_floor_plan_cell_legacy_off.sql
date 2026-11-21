-- Kam t635u (2026-11-21): close pricing gaps. Additive / flag-off only, nothing deleted.
-- Run AFTER the backup in ops/backup_pricing_tables.sh. Needs Sentinel GO first.
BEGIN;
-- 1) Floor plan only gets its own payout cell (37/42/48% of $99), same on every band.
INSERT INTO public.vendor_payout_rates (package_id, sqft_tier, vendor_tier, amount_cents)
SELECT 'floor_plan_only', b, t, c
FROM (VALUES ('small'),('mid'),('large'),('xl')) AS bands(b),
     (VALUES (1,3665),(2,4160),(3,4750)) AS tiers(t,c)
ON CONFLICT DO NOTHING;
-- 2) Legacy packages and the $1 internal test package: switched off, kept for history.
UPDATE public.packages SET active = false, updated_at = now()
WHERE id IN ('standard','premium','luxury','estate','internal_test_1cad') AND active = true;
-- Guard: exactly the expected rows changed state.
DO $$ BEGIN
  IF (SELECT count(*) FROM public.packages WHERE id IN ('standard','premium','luxury','estate','internal_test_1cad') AND active) <> 0
  THEN RAISE EXCEPTION 'legacy packages still active'; END IF;
END $$;
COMMIT;
-- Rollback: UPDATE public.packages SET active = true WHERE id IN (...);
--           DELETE FROM public.vendor_payout_rates WHERE package_id = 'floor_plan_only';  -- only with Kam's yes
