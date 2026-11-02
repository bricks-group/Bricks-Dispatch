-- Late-cancel exceptions held for Kam, and termination appeals (Kam, 2026-11-02 10:18 ET). Additive only.
create table if not exists public.vendor_termination_reviews (
  id uuid primary key default gen_random_uuid(),
  member_id text not null,
  job_id text,
  kind text not null check (kind in ('cancel_exception', 'appeal')),
  decision text,
  reason_code text,
  note text,
  evidence jsonb not null default '{}'::jsonb,
  status text not null default 'pending' check (status in ('pending', 'terminated', 'excused', 'reinstated', 'kept')),
  created_at timestamptz not null default now(),
  decided_at timestamptz,
  decided_by text
);
create index if not exists vendor_termination_reviews_member_idx on public.vendor_termination_reviews (member_id);
create index if not exists vendor_termination_reviews_pending_idx on public.vendor_termination_reviews (status) where status = 'pending';
alter table public.vendor_termination_reviews enable row level security;
comment on table public.vendor_termination_reviews is 'Vendor late-cancel exception claims paused for Kam (Terminate / Excuse) and 7-day termination appeals.';

-- IC amendment A1 (accepted jobs: no-show or cancellation), version string 'IC-A1 v1.0'.
alter table public.vendor_members add column if not exists ic_amendment_version text;
alter table public.vendor_members add column if not exists ic_amendment_accepted_at timestamptz;
