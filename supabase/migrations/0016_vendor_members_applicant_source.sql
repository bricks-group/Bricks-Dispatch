-- "How did you hear about Bricks?" on the www.brxs.ca/photographers application.
-- Additive only. Existing applicants keep null source / source_detail.
-- Allowed keys live in backend/app/shared/applicant_source.py.

ALTER TABLE public.vendor_members
  ADD COLUMN IF NOT EXISTS source text,
  ADD COLUMN IF NOT EXISTS source_detail text;

COMMENT ON COLUMN public.vendor_members.source IS
  'Applicant answer to "How did you hear about Bricks?": instagram | facebook | linkedin | tiktok | google | indeed | craigslist_kijiji | referral_photographer | referral_realtor | event | other. Null for applicants before 2026-10-12 and for clients that do not ask.';
COMMENT ON COLUMN public.vendor_members.source_detail IS
  'Short free text for source: the Other answer, or an optional referrer name. Null otherwise.';
