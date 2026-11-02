-- Trade vetting (Kam, 2026-11-01). Additive only: new nullable columns and one new table.
alter table public.vendor_members
  add column if not exists paused_at timestamptz,
  add column if not exists paused_reason text;

comment on column public.vendor_members.paused_at is
  'Set when a vendor is paused (expired credential, probation fail, admin). Paused vendors are never assigned.';

alter table public.vendor_applicant_reviews
  add column if not exists trade text,
  add column if not exists rubric_version text;

-- First N jobs per vendor are held for admin review before delivery or payout.
create table if not exists public.vendor_probation_reviews (
  id uuid primary key default gen_random_uuid(),
  job_id text not null,
  member_id text not null,
  status text not null default 'pending' check (status in ('pending', 'approved', 'failed')),
  client_rating integer check (client_rating between 1 and 5),
  qc_result text,
  notes text,
  reviewed_by text,
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  unique (job_id, member_id)
);
create index if not exists vendor_probation_reviews_member_idx on public.vendor_probation_reviews (member_id);
create index if not exists vendor_probation_reviews_status_idx on public.vendor_probation_reviews (status);
alter table public.vendor_probation_reviews enable row level security;
-- Service role only (Dispatch / OS backends). No anon or authenticated policies.
