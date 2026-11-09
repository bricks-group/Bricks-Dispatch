-- No confirmed payment, no payout (Kam 2026-11-09). FILE ONLY: needs Kam's go-ahead.
-- Must ship in the SAME batch as the code that writes blocked payout rows (H1).
-- Applying ADD block_reason alone does not let those rows insert: live
-- payouts_amount_positive is CHECK (amount > 0), and a $0 insert is rejected.
-- The application now writes a positive placeholder amount on blocked rows;
-- this also relaxes the check so status='blocked' may store amount 0 later.
BEGIN;

ALTER TABLE public.payouts ADD COLUMN IF NOT EXISTS block_reason text;

ALTER TABLE public.payouts DROP CONSTRAINT IF EXISTS payouts_amount_positive;
ALTER TABLE public.payouts
  ADD CONSTRAINT payouts_amount_positive
  CHECK (amount > 0 OR status = 'blocked');

COMMIT;
