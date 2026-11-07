-- Preserve the 15-minute SLA; suppress only invocations with no candidates.
do $outer$
declare original text; gated text;
begin
  select command into strict original from cron.job where jobname = 'brxs-ops-sweep';
  if position('cpu-idle-gate-v1' in original) > 0 then
    raise exception 'idle gate already installed';
  end if;
  gated := $prefix$do $gate$
begin
  -- cpu-idle-gate-v1. Superset matching the Python UTC-date string lookbacks.
  if exists (select 1 from public.jobs
             where scheduled_date >= ((now() at time zone 'UTC') - interval '3 days')::date::text)
     or exists (select 1 from public.jobs where status = 'delivered'
                and updated_at >= ((now() at time zone 'UTC') - interval '14 days')::date::text)
     or exists (select 1 from public.uploads
                where created_at >= ((now() at time zone 'UTC') - interval '3 days')::date::text)
     or exists (select 1 from public.vendor_probation_reviews where payout_hold = 'held')
     or exists (select 1 from public.ops_autopilot_events
                where key in ('ic_a1_notice:request_test', 'ic_a1_notice:request_send'))
  then
    $prefix$ || replace(original, 'select net.http_post(', 'perform net.http_post(') || $suffix$
  else
    insert into public.ops_autopilot_runs (kind, ok, summary)
    values ('sweep', true, '{"skipped":"idle_sql_gate","candidates":0}'::jsonb);
  end if;
end $gate$;$suffix$;
  perform cron.alter_job((select jobid from cron.job where jobname='brxs-ops-sweep'), command := gated);
end $outer$;
