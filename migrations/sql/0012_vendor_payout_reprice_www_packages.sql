-- Vendor payout reprice 2026-09-27: five www packages on www footprint bands.
-- Source: /workspace/ship/VENDOR_PAYOUT_REPRICE_20260927.md
--
-- Upserts 60 fixed cells (photos_only / essentials / story / showcase / full)
-- × (small ≤1200 / mid ≤2500 / large ≤4000 / xl) × vendor_tier 1/2/3 (C/B/A).
-- Amounts are CAD × 100 (integer cents), matching existing amount_cents schema.
--
-- Legacy standard / premium / luxury / estate rows are LEFT IN PLACE for
-- historical job reads only. New www catalog lookups must use the five
-- package_ids above (see payout_service._PACKAGE_RATE_ALIASES). Nothing new
-- aliases onto estate; estate is retired for new jobs.

INSERT INTO public.vendor_payout_rates (package_id, sqft_tier, vendor_tier, amount_cents) VALUES
    ('photos_only', 'small', 1, 5515), ('photos_only', 'small', 2, 6260), ('photos_only', 'small', 3, 7150),

    ('photos_only', 'mid', 1, 7365), ('photos_only', 'mid', 2, 8360), ('photos_only', 'mid', 3, 9550),

    ('photos_only', 'large', 1, 9215), ('photos_only', 'large', 2, 10460), ('photos_only', 'large', 3, 11950),

    ('photos_only', 'xl', 1, 11065), ('photos_only', 'xl', 2, 12560), ('photos_only', 'xl', 3, 14350),

    ('essentials', 'small', 1, 9175), ('essentials', 'small', 2, 10415), ('essentials', 'small', 3, 11905),

    ('essentials', 'mid', 1, 11765), ('essentials', 'mid', 2, 13355), ('essentials', 'mid', 3, 15265),

    ('essentials', 'large', 1, 14355), ('essentials', 'large', 2, 16295), ('essentials', 'large', 3, 18625),

    ('essentials', 'xl', 1, 16945), ('essentials', 'xl', 2, 19235), ('essentials', 'xl', 3, 21985),

    ('story', 'small', 1, 14690), ('story', 'small', 2, 16675), ('story', 'small', 3, 19055),

    ('story', 'mid', 1, 18390), ('story', 'mid', 2, 20875), ('story', 'mid', 3, 23855),

    ('story', 'large', 1, 22090), ('story', 'large', 2, 25075), ('story', 'large', 3, 28655),

    ('story', 'xl', 1, 26160), ('story', 'xl', 2, 29695), ('story', 'xl', 3, 33935),

    ('showcase', 'small', 1, 20200), ('showcase', 'small', 2, 22930), ('showcase', 'small', 3, 26210),

    ('showcase', 'mid', 1, 25750), ('showcase', 'mid', 2, 29230), ('showcase', 'mid', 3, 33410),

    ('showcase', 'large', 1, 31300), ('showcase', 'large', 2, 35530), ('showcase', 'large', 3, 40610),

    ('showcase', 'xl', 1, 37220), ('showcase', 'xl', 2, 42250), ('showcase', 'xl', 3, 48290),

    ('full', 'small', 1, 25750), ('full', 'small', 2, 29230), ('full', 'small', 3, 33410),

    ('full', 'mid', 1, 32040), ('full', 'mid', 2, 36370), ('full', 'mid', 3, 41570),

    ('full', 'large', 1, 38145), ('full', 'large', 2, 43300), ('full', 'large', 3, 49490),

    ('full', 'xl', 1, 44620), ('full', 'xl', 2, 50650), ('full', 'xl', 3, 57890)
ON CONFLICT (package_id, sqft_tier, vendor_tier) DO UPDATE
    SET amount_cents = EXCLUDED.amount_cents;

-- estate / standard / premium / luxury rows intentionally retained for legacy
-- package_id lookups. New jobs must use the five www package_ids above.
COMMENT ON TABLE public.vendor_payout_rates IS
  'Fixed vendor payout cells. www: photos_only/essentials/story/showcase/full × Small≤1200/Mid≤2500/Large≤4000/XL × C/B/A. Legacy standard/premium/luxury/estate retained for historical jobs only.';
