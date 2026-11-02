-- Probation payout hold (additive only). A vendor's first N jobs hold their
-- payout until the client rating lands or release_after passes with no
-- complaint. Driven by the daily Vercel cron + OS rating webhook; no bot.
alter table public.vendor_probation_reviews
  add column if not exists delivered_at timestamptz,
  add column if not exists release_after timestamptz,
  add column if not exists payout_hold text,            -- held | released | blocked (null = legacy row, never held)
  add column if not exists payout_released_at timestamptz,
  add column if not exists hold_reason text,
  add column if not exists alerted_at timestamptz;

create index if not exists vendor_probation_reviews_hold_idx
  on public.vendor_probation_reviews (payout_hold, release_after)
  where payout_hold = 'held';
