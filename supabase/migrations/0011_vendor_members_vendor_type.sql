-- Wave A vendor spine: photographer | stager | cleaner
-- Drone/RPAS remains a capability on photographer (rpas_advanced / drone_certified),
-- not a separate vendor_type or app. Human stager ≠ BoxBrownie virtual staging.

ALTER TABLE public.vendor_members
  ADD COLUMN IF NOT EXISTS vendor_type text;

UPDATE public.vendor_members
SET vendor_type = 'photographer'
WHERE vendor_type IS NULL
   OR btrim(vendor_type) = '';

-- Infer from legacy role labels when present.
UPDATE public.vendor_members
SET vendor_type = 'stager'
WHERE lower(coalesce(role, '')) IN ('stager', 'staging')
  AND vendor_type = 'photographer';

UPDATE public.vendor_members
SET vendor_type = 'cleaner'
WHERE lower(coalesce(role, '')) IN ('cleaner', 'cleaning')
  AND vendor_type = 'photographer';

ALTER TABLE public.vendor_members
  ALTER COLUMN vendor_type SET DEFAULT 'photographer';

ALTER TABLE public.vendor_members
  ALTER COLUMN vendor_type SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'vendor_members_vendor_type_check'
  ) THEN
    ALTER TABLE public.vendor_members
      ADD CONSTRAINT vendor_members_vendor_type_check
      CHECK (vendor_type IN ('photographer', 'stager', 'cleaner'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS vendor_members_vendor_type_idx
  ON public.vendor_members (vendor_type);

COMMENT ON COLUMN public.vendor_members.vendor_type IS
  'Wave A vendor class: photographer | stager | cleaner. Drone/RPAS is a photographer capability, not a type.';
