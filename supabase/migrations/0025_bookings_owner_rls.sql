-- Bookings row-level security: owner read only, writes through the service role.
--
-- migrations/004_create_bookings_table.sql created a FOR ALL policy whose only
-- condition was auth.role() = 'authenticated'. Accounts are self-service (OTP
-- signup, /api/vendor-os/auth/verify), so any signed-in user could read,
-- insert, update and delete every client booking through PostgREST.
--
-- This drops that policy and replaces it with a SELECT policy scoped to the
-- booking's owner. Every booking writer found uses the service role, which
-- bypasses RLS and is unaffected:
--   * Companion backend (booking create, payments, cancel/refund)
--   * Dispatch backend (backend/app/shared/database.py, SUPABASE_SERVICE_KEY)
--   * supabase/functions/dispatch-booking (SUPABASE_SERVICE_ROLE_KEY)
-- Other scoped policies on the live table (e.g. "Staff can update bookings")
-- are left as they are; they keep working on their own terms.
--
-- TRUNCATE/REFERENCES/TRIGGER are not governed by RLS, so anon and
-- authenticated lose them too.
--
-- Rollback (re-opens every booking to every account, do not run):
--   CREATE POLICY "Enable all for authenticated users only" ON public.bookings
--   FOR ALL USING (auth.role() = 'authenticated');

BEGIN;

DROP POLICY IF EXISTS "Enable all for authenticated users only" ON public.bookings;

ALTER TABLE public.bookings ENABLE ROW LEVEL SECURITY;

REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLE public.bookings FROM anon, authenticated;

DROP POLICY IF EXISTS bookings_select_own ON public.bookings;
CREATE POLICY bookings_select_own
ON public.bookings
FOR SELECT TO authenticated
USING (user_id = auth.uid()::text);

COMMIT;
