-- Delivery robustness (Kam 2026-11-09). FILE ONLY: needs Kam's go-ahead before it is applied.
-- The code on fix/delivery-robust-1109 works without this migration; this adds
-- database-level guarantees behind it.
--
-- 1. One payout row per job. The code already uses a per-job id (uuid5), so a
--    second concurrent insert collides on the primary key; this index makes the
--    rule hold for any other writer too. Checked 2026-11-09: 2 payout rows,
--    2 distinct jobs, so the index builds without touching data.
-- 2. Fast per-order audit trail reads (audit_events.entity_id + action).

BEGIN;

DO $$
BEGIN
  IF EXISTS (SELECT job_id FROM public.payouts WHERE job_id IS NOT NULL GROUP BY job_id HAVING count(*) > 1) THEN
    RAISE EXCEPTION 'payouts has more than one row for a job; resolve by hand before applying';
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS payouts_one_per_job_uq
  ON public.payouts (job_id) WHERE job_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS audit_events_entity_action_idx
  ON public.audit_events (entity_type, entity_id, action);

COMMIT;
