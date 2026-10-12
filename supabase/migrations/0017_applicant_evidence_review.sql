-- Evidence-based applicant review (www /photographers + GO apply).
-- Additive only: new nullable columns and two new tables. Nothing is dropped,
-- renamed, or back-filled. Existing applicants keep nulls.

-- Structured application: city picker, equipment fields, RPAS claim + cert
-- photo path, uploaded sample photo paths. equipment_list / home_city stay
-- populated (flattened) for every existing reader.
ALTER TABLE public.vendor_members
  ADD COLUMN IF NOT EXISTS application_profile jsonb;

COMMENT ON COLUMN public.vendor_members.application_profile IS
  'Structured application (2026-10-12+): {location:{city,in_gta}, equipment:{camera_body,lenses,drone_model,lighting,tripod}, rpas:{number,level,cert_path}, samples:[{path,name,bytes,mime}]}. Null for older applicants.';

-- Availability the applicant gave on apply. Upgrades the existing row that
-- /apply already writes (work_start/work_end/jobs_per_day stay the dispatch gate).
ALTER TABLE public.vendor_availability
  ADD COLUMN IF NOT EXISTS days_of_week text[],
  ADD COLUMN IF NOT EXISTS time_windows text[],
  ADD COLUMN IF NOT EXISTS weekends boolean,
  ADD COLUMN IF NOT EXISTS earliest_start_date date,
  ADD COLUMN IF NOT EXISTS shoots_per_week integer,
  ADD COLUMN IF NOT EXISTS notice_hours integer,
  ADD COLUMN IF NOT EXISTS has_car boolean,
  ADD COLUMN IF NOT EXISTS has_licence boolean;

COMMENT ON COLUMN public.vendor_availability.days_of_week IS 'Self-reported on apply: subset of mon,tue,wed,thu,fri,sat,sun.';
COMMENT ON COLUMN public.vendor_availability.time_windows IS 'Self-reported on apply: subset of early_morning (6-9), daytime (9-5), evening (5-9).';

-- One row per automatic review run. result holds verdict, evidence bullets
-- (each with a source: verified | self_reported | couldnt_check), per-criterion
-- rubric words, per-image results, flags, and thumbnails (storage paths).
CREATE TABLE IF NOT EXISTS public.vendor_applicant_reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  member_id text NOT NULL,
  verdict text NOT NULL,            -- recommend | needs_review | not_a_fit
  needs_samples boolean NOT NULL DEFAULT false,
  result jsonb NOT NULL DEFAULT '{}'::jsonb,
  model text,
  cost_usd numeric(10,5),
  trigger text,                     -- apply | rerun | dry_run
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS vendor_applicant_reviews_member_idx
  ON public.vendor_applicant_reviews (member_id, created_at DESC);
ALTER TABLE public.vendor_applicant_reviews ENABLE ROW LEVEL SECURITY;

-- Exact (sha256) and perceptual (64-bit dHash, hex) hashes of every image a
-- review looked at, so a later applicant re-using the same photos is flagged.
CREATE TABLE IF NOT EXISTS public.vendor_applicant_image_hashes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  member_id text NOT NULL,
  source text NOT NULL,             -- upload | portfolio
  ref text,                         -- storage path or page image URL
  sha256 text NOT NULL,
  dhash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS vendor_applicant_image_hashes_sha_idx
  ON public.vendor_applicant_image_hashes (sha256);
CREATE INDEX IF NOT EXISTS vendor_applicant_image_hashes_member_idx
  ON public.vendor_applicant_image_hashes (member_id);
ALTER TABLE public.vendor_applicant_image_hashes ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS vendor_applicant_image_hashes_created_idx
  ON public.vendor_applicant_image_hashes (created_at DESC);
