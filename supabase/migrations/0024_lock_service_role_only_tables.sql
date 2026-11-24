-- Lock backend-only tables that were created without row level security.
-- Supabase's default public-schema privileges grant anon and authenticated
-- full access to new tables, so without this the Data API serves and accepts
-- writes to these rows for anyone holding the public anon key.
-- The Dispatch backend uses the service role (bypasses RLS) or the table
-- owner over POSTGRES_URL, so it keeps working. RLS on with no policies.
-- Safe to re-run; tables that do not exist yet are skipped (their create
-- migrations lock them at creation).
do $$
declare
  t text;
begin
  foreach t in array array[
    'agent_noshow_escalations',
    'reconciliation_events',
    'ops_escalations',
    'vendor_calendar_connections'
  ]
  loop
    if to_regclass(format('public.%I', t)) is not null then
      execute format('alter table public.%I enable row level security', t);
      execute format('revoke all on table public.%I from anon, authenticated', t);
    end if;
  end loop;
end
$$;
