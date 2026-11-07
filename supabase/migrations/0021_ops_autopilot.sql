-- Ops autopilot (Kam, 2026-11-02): recurring ops run without the assistant.
-- Additive only.
--   ops_autopilot_events: one row per alert/action already done (dedupe keys)
--   ops_autopilot_runs:   heartbeat of each sweep/daily run (health check reads it)
--   db-backups bucket:    private nightly logical backups (service role only)
--   brxs-ops-sweep:       pg_cron job, every 15 min, POSTs Dispatch /api/ops/sweep
--                         with the vault secret ops_cron_secret (made in the DB,
--                         never copied out; Dispatch reads it from vault)
create table if not exists public.ops_autopilot_events (
  key text primary key,
  data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create table if not exists public.ops_autopilot_runs (
  id bigserial primary key,
  kind text not null,
  ok boolean not null,
  summary jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists ops_autopilot_runs_kind_created on public.ops_autopilot_runs (kind, created_at desc);
alter table public.ops_autopilot_events enable row level security;
alter table public.ops_autopilot_runs enable row level security;

insert into storage.buckets (id, name, public, file_size_limit)
values ('db-backups', 'db-backups', false, 52428800)
on conflict (id) do nothing;

do $$
begin
  if not exists (select 1 from vault.secrets where name = 'ops_cron_secret') then
    perform vault.create_secret(encode(extensions.gen_random_bytes(32), 'hex'), 'ops_cron_secret',
                                'Bearer for pg_cron -> Dispatch /api/ops/sweep');
  end if;
end $$;

select cron.schedule(
  'brxs-ops-sweep',
  '*/15 * * * *',
  $job$
  select net.http_post(
    url := 'https://dispatch-api.brxs.ca/api/ops/sweep',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || (select decrypted_secret from vault.decrypted_secrets where name = 'ops_cron_secret')
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 120000
  );
  $job$
);

-- Trim the heartbeat table (keep 30 days).
select cron.schedule(
  'brxs-ops-runs-trim',
  '17 4 * * *',
  $job$ delete from public.ops_autopilot_runs where created_at < now() - interval '30 days'; $job$
);
