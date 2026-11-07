-- Vendor onboarding gate (Kam, 2026-10-31).
-- Approval no longer makes a vendor assignable. They finish a checklist in the
-- Bricks app (signed in, payouts, availability, guide, current agreement) and
-- onboarding_completed_at is stamped; only then does auto-assign pick them.
-- Existing active, approved vendors are grandfathered as complete.

ALTER TABLE public.vendor_members ADD COLUMN IF NOT EXISTS onboarding_completed_at timestamptz;
ALTER TABLE public.vendor_members ADD COLUMN IF NOT EXISTS first_signed_in_at timestamptz;
ALTER TABLE public.vendor_members ADD COLUMN IF NOT EXISTS guide_acknowledged_at timestamptz;
ALTER TABLE public.vendor_members ADD COLUMN IF NOT EXISTS availability_confirmed_at timestamptz;
ALTER TABLE public.vendor_members ADD COLUMN IF NOT EXISTS rejected_at timestamptz;

-- Payouts per photographer: one Stripe Express account per vendor member.
-- member_id NULL keeps the existing per-company row (owner / company payouts).
ALTER TABLE public.contractors ADD COLUMN IF NOT EXISTS member_id text;
CREATE UNIQUE INDEX IF NOT EXISTS contractors_member_id_uidx
  ON public.contractors (member_id) WHERE member_id IS NOT NULL;

-- Grandfather: everyone already active and approved counts as onboarded.
UPDATE public.vendor_members
SET onboarding_completed_at = now(),
    internal_notes = trim(both from coalesce(internal_notes, '') || E'\nOnboarding grandfathered as complete 2026-10-31 (active before the onboarding gate).')
WHERE is_active IS TRUE
  AND onboarding_status = 'approved'
  AND onboarding_completed_at IS NULL;

-- Reapply cooldown starts from the rejection date.
UPDATE public.vendor_members
SET rejected_at = updated_at::timestamptz
WHERE onboarding_status = 'rejected' AND rejected_at IS NULL AND coalesce(updated_at, '') <> '';
