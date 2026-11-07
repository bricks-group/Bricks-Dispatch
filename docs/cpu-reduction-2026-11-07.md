# Idle ops sweep gate — November 7, 2026

The existing Supabase `brxs-ops-sweep` cron still runs every 15 minutes. Its SQL now dispatches the existing HTTP request only when a conservative superset of sweep candidates exists: jobs in the Python 3-day UTC-date lookback (including future jobs), delivered jobs in the 14-day updated lookback, recent uploads, any held probation review, or an IC-A1 request marker. Idle runs record `ops_autopilot_runs` with `skipped=idle_sql_gate` without waking Vercel. Existing automatic assignment, reminders, reconciliation and payout gates are unchanged when work exists.

Applied config: `supabase/operations/ops-sweep-idle-gate.sql`. Rollback: sibling `ops-sweep-idle-gate-rollback.sql` restores the exact original command, retaining its vaulted secret reference without exporting a secret. This is a scheduler config change, not a schema migration. The gate intentionally prefers false positives; extend its candidate superset whenever sweep gains another work source.

Do not replace the API's `/health` with a static success file: readiness and application failure signals must stay meaningful. Python media fidelity, QC/editor/payout gates and package versions are unchanged. NumPy is now loaded inside actual stitching operations, and empty reconcile loops no longer import the enhancement pipeline.
