-- Vendor payout add-on cells: virtual visit (per job, footprint bands) and
-- virtual staging (per image). Nearest $0.05 to Tier C/B/A ≈37/42/48 of the
-- client rack. CAD cents. Does not change package cells from 0012.
-- Legacy standard/premium/luxury/estate rows are left in place. Estate is not revived.

INSERT INTO public.vendor_payout_rates (package_id, sqft_tier, vendor_tier, amount_cents) VALUES
    ('virtual_visit', 'small', 1, 9215), ('virtual_visit', 'small', 2, 10460), ('virtual_visit', 'small', 3, 11950),
    ('virtual_visit', 'mid', 1, 11065), ('virtual_visit', 'mid', 2, 12560), ('virtual_visit', 'mid', 3, 14350),
    ('virtual_visit', 'large', 1, 12915), ('virtual_visit', 'large', 2, 14660), ('virtual_visit', 'large', 3, 16750),
    ('virtual_visit', 'xl', 1, 14765), ('virtual_visit', 'xl', 2, 16760), ('virtual_visit', 'xl', 3, 19150),
    -- staging is per image; the same cents on every footprint band so a band lookup still resolves
    ('virtual_staging', 'small', 1, 1295), ('virtual_staging', 'small', 2, 1470), ('virtual_staging', 'small', 3, 1680),
    ('virtual_staging', 'mid', 1, 1295), ('virtual_staging', 'mid', 2, 1470), ('virtual_staging', 'mid', 3, 1680),
    ('virtual_staging', 'large', 1, 1295), ('virtual_staging', 'large', 2, 1470), ('virtual_staging', 'large', 3, 1680),
    ('virtual_staging', 'xl', 1, 1295), ('virtual_staging', 'xl', 2, 1470), ('virtual_staging', 'xl', 3, 1680)
ON CONFLICT (package_id, sqft_tier, vendor_tier) DO UPDATE
    SET amount_cents = EXCLUDED.amount_cents;
