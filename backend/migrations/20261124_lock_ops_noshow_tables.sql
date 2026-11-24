-- ops_escalations, agent_noshow_escalations (agent phone numbers) and
-- reconciliation_events were created without RLS, so Supabase's default grants
-- exposed them to the public anon key. Only the Dispatch API (service role or
-- direct Postgres as owner) reads or writes them.
do $$
declare
  t text;
begin
  foreach t in array array['ops_escalations', 'agent_noshow_escalations', 'reconciliation_events']
  loop
    if to_regclass('public.' || t) is not null then
      execute format('alter table public.%I enable row level security', t);
      execute format('revoke all on table public.%I from public, anon, authenticated', t);
      execute format('grant select, insert, update, delete on table public.%I to service_role', t);
    end if;
  end loop;
end
$$;
