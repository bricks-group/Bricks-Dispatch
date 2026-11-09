-- Lock booking payment columns to service_role (Kam 2026-11-09: "Yes, lock
-- the booking payment fields once Sentinel checks it"). FILE ONLY — do not apply
-- until Sentinel reviews it.
--
-- Why: the row-level policy "Staff can update bookings" lets an approved
-- photographer (private.is_photographer() AND assigned_photographer = auth.email(),
-- or any admin) UPDATE the whole row. Column UPDATE grants on payment fields
-- are currently true for anon AND authenticated. Combined, a staff JWT can
-- write stripe_payment_status='paid' and a cs_live_ session id — which used
-- to be enough to release a vendor payout. The payout code no longer trusts
-- those fields; this migration removes the write path too.
--
-- RLS cannot be column-specific, so this does two things:
--   1. REVOKE UPDATE on the payment columns from anon and authenticated.
--      service_role and postgres keep UPDATE.
--   2. A BEFORE UPDATE trigger that rejects changes to those columns unless
--      auth.role() is service_role (belt and suspenders if grants drift).
-- The "Staff can update bookings" policy itself stays: staff can still edit
-- address, schedule, notes, assignment, etc.
--
-- Who writes these columns today (all via the service-role key, so they keep
-- working after this ships):
--   * Companion backend/app/routers/payments.py
--       update_booking_payment_status      -> stripe_payment_status, status
--       cleanup_expired_checkout_booking   -> stripe_payment_status='expired'
--       mark_checkout_booking_paid         -> stripe_payment_status, stripe_session_id
--   * Companion backend/server.py
--       checkout.session.completed webhook -> payment_intent_id, status, and
--         checkout_session_id (app field; the table column is stripe_session_id)
--       booking insert (overage PI)        -> payment_intent_id
--       cancel / refund / no-show paths    -> refunded_at, refund_amount
--   * Dispatch payout_source.py READS only; it no longer treats these columns
--     as proof of payment.
--   * OS booking_refunds.py updates media_booking_leads / commercial_bookings
--     by stripe_session_id, not public.bookings — unaffected.
--
-- No frontend or vendor-app writer was found. A staff user updating a booking
-- from the dashboard (non-payment fields) is unaffected.
--
-- Rollback: drop the trigger + function; GRANT UPDATE (cols) ON public.bookings
-- TO anon, authenticated.

BEGIN;

REVOKE UPDATE (
  stripe_session_id,
  stripe_payment_status,
  paid_at,
  payment_intent_id,
  refunded_at,
  refund_amount,
  stripe_tax_transaction_id
) ON public.bookings FROM anon, authenticated;

GRANT UPDATE (
  stripe_session_id,
  stripe_payment_status,
  paid_at,
  payment_intent_id,
  refunded_at,
  refund_amount,
  stripe_tax_transaction_id
) ON public.bookings TO service_role;

CREATE OR REPLACE FUNCTION public.bookings_payment_columns_service_role_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF current_setting('request.jwt.claim.role', true) IS NOT DISTINCT FROM 'service_role'
     OR auth.role() = 'service_role' THEN
    RETURN NEW;
  END IF;
  IF NEW.stripe_session_id        IS DISTINCT FROM OLD.stripe_session_id
     OR NEW.stripe_payment_status IS DISTINCT FROM OLD.stripe_payment_status
     OR NEW.paid_at               IS DISTINCT FROM OLD.paid_at
     OR NEW.payment_intent_id     IS DISTINCT FROM OLD.payment_intent_id
     OR NEW.refunded_at           IS DISTINCT FROM OLD.refunded_at
     OR NEW.refund_amount         IS DISTINCT FROM OLD.refund_amount
     OR NEW.stripe_tax_transaction_id IS DISTINCT FROM OLD.stripe_tax_transaction_id THEN
    RAISE EXCEPTION 'booking payment fields can only be changed by the service role';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS bookings_payment_columns_service_role_only ON public.bookings;
CREATE TRIGGER bookings_payment_columns_service_role_only
  BEFORE UPDATE ON public.bookings
  FOR EACH ROW
  EXECUTE FUNCTION public.bookings_payment_columns_service_role_only();

COMMIT;
