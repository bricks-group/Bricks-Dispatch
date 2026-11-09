-- No confirmed payment, no payout (Kam 2026-11-09). FILE ONLY: needs Kam's go-ahead.
-- The code works without it (block reason then lives only in audit_events,
-- action delivery.payout_blocked); with it, the reason is also on the payout row.
BEGIN;
ALTER TABLE public.payouts ADD COLUMN IF NOT EXISTS block_reason text;
COMMIT;
