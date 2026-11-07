-- Single-use nonces for vendor calendar OAuth state (launch security audit 2026-11-07).
-- Service-role only: RLS on with no policies; no anon/authenticated grants.
create table if not exists public.calendar_oauth_nonces (
  nonce_sha256 text primary key,
  member_id text not null,
  provider text not null,
  consumed_at timestamptz not null default now()
);
alter table public.calendar_oauth_nonces enable row level security;
revoke all on table public.calendar_oauth_nonces from anon, authenticated;
create index if not exists calendar_oauth_nonces_consumed_at_idx on public.calendar_oauth_nonces (consumed_at);
