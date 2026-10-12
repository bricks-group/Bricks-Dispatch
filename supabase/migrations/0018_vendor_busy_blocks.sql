-- Synced free/busy intervals from photographers' linked calendars (Google FreeBusy,
-- iCloud CalDAV, ICS). Times only — no titles/attendees are stored.
create table if not exists public.vendor_busy_blocks (
  id bigserial primary key,
  member_id text not null,
  provider text not null,
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  synced_at timestamptz not null default now()
);
create index if not exists vendor_busy_blocks_member_range on public.vendor_busy_blocks (member_id, starts_at, ends_at);
create index if not exists vendor_busy_blocks_range on public.vendor_busy_blocks (starts_at, ends_at);
alter table public.vendor_busy_blocks enable row level security;
drop policy if exists block_all_vendor_busy_blocks on public.vendor_busy_blocks;
create policy block_all_vendor_busy_blocks on public.vendor_busy_blocks
  for all to anon, authenticated using (false) with check (false);

alter table public.vendor_calendar_connections
  add column if not exists last_synced_at timestamptz,
  add column if not exists last_sync_status text;
