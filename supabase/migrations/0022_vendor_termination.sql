-- Vendor termination on no-show / cancel after accepting (Kam, 2026-11-02). Additive only.
alter table public.vendor_members add column if not exists terminated_at timestamptz;
alter table public.vendor_members add column if not exists terminated_reason text;
alter table public.jobs add column if not exists assigned_at timestamptz;
alter table public.jobs add column if not exists vendor_accepted_at timestamptz;

create table if not exists public.vendor_terminations (
  id uuid primary key default gen_random_uuid(),
  member_id text not null,
  job_id text,
  trigger text not null check (trigger in ('cancelled_after_accept', 'no_show')),
  reason text,
  email text,
  phone text,
  id_hashes text[] not null default '{}',
  prior jsonb not null default '{}'::jsonb,
  reassigned jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  undone_at timestamptz,
  undone_by text
);
create index if not exists vendor_terminations_member_idx on public.vendor_terminations (member_id);
create index if not exists vendor_terminations_active_email_idx on public.vendor_terminations (lower(email)) where undone_at is null;
alter table public.vendor_terminations enable row level security;
comment on table public.vendor_terminations is 'Ledger of vendor terminations; active rows block reapplying with the same email, phone or ID.';
