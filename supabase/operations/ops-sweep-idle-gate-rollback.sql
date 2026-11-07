-- Exact original unconditional dispatch; no secret is exported.
select cron.alter_job((select jobid from cron.job where jobname='brxs-ops-sweep'), command := $job$
  select net.http_post(
    url := 'https://dispatch-api.brxs.ca/api/ops/sweep',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || (select decrypted_secret from vault.decrypted_secrets where name = 'ops_cron_secret')
    ), body := '{}'::jsonb, timeout_milliseconds := 120000);
$job$);
