-- Client no-show evidence (Kam, 2026-10-30). Additive only.
-- Check-in time + GPS on jobs, the no-show photo and call attempt, the same
-- evidence copied onto each claim, the review columns the admin queue already
-- writes, a private evidence bucket (service role only), and a 0 default for
-- the claim amount (set to the half payout on approval).

alter table public.jobs
  add column if not exists checked_in_at timestamptz,
  add column if not exists check_in_lat double precision,
  add column if not exists check_in_lng double precision,
  add column if not exists check_in_accuracy_m double precision,
  add column if not exists check_in_distance_m double precision,
  add column if not exists noshow_photo_path text,
  add column if not exists noshow_photo_taken_at timestamptz,
  add column if not exists noshow_photo_uploaded_at timestamptz,
  add column if not exists noshow_call_attempted_at timestamptz,
  add column if not exists noshow_call_method text;

alter table public.agent_noshow_trip_fees
  add column if not exists checked_in_at timestamptz,
  add column if not exists check_in_lat double precision,
  add column if not exists check_in_lng double precision,
  add column if not exists check_in_accuracy_m double precision,
  add column if not exists check_in_distance_m double precision,
  add column if not exists wait_started_at timestamptz,
  add column if not exists photo_path text,
  add column if not exists photo_taken_at timestamptz,
  add column if not exists photo_uploaded_at timestamptz,
  add column if not exists call_attempted_at timestamptz,
  add column if not exists call_method text,
  add column if not exists review_reason text,
  add column if not exists reviewed_by text,
  add column if not exists reviewed_at timestamptz,
  add column if not exists admin_notes text;

alter table public.agent_noshow_trip_fees
  alter column amount_cents set default 0;

-- Private bucket: no anon/authenticated policies, so only the service role
-- (Dispatch API) can read or write. Admins see photos through short signed URLs.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('noshow-evidence', 'noshow-evidence', false, 10485760,
        array['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif'])
on conflict (id) do update set public = false;
