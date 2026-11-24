-- vendor_calendar_connections holds OAuth tokens, CalDAV app passwords and private
-- ICS URLs. Service-role only (Dispatch API + direct Postgres fallback): RLS on with
-- no policies, and no anon/authenticated grants so the public anon key cannot reach
-- it through PostgREST, GraphQL or Realtime. Do not add FORCE: the pg fallback
-- connects as the table owner.
alter table public.vendor_calendar_connections enable row level security;
revoke all on table public.vendor_calendar_connections from public, anon, authenticated;
grant select, insert, update, delete on table public.vendor_calendar_connections to service_role;
