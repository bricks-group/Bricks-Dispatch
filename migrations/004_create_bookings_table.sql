-- 004_create_bookings_table.sql
-- Migration for creating the bookings table

-- Up migration
CREATE FUNCTION public.fn_sync_updated_at() RETURNS trigger AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS public.bookings (
  id uuid primary key default uuid_generate_v4(),
  booking_id text unique not null,
  user_id text not null,
  address text not null,
  property_type text default '',
  package_id text default '',
  package_name text default '',
  package_price int default 0,
  scheduled_date text default '',
  scheduled_time text default '',
  notes text default '',
  add_ons text[] default '{}',
  sqft int,
  is_rush bool default false,
  status text default 'pending',
  dispatch_job_id text,
  lat float8,
  lng float8,
  complexity_score int,
  complexity_band text,
  ai_flags text[] default '{}',
  completed_at text,
  delivered_at text,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null,
  updated_at timestamp with time zone default timezone('utc'::text, now()) not null
);

CREATE TRIGGER sync_bookings_updated_at BEFORE UPDATE ON public.bookings FOR EACH ROW EXECUTE FUNCTION public.fn_sync_updated_at();

ALTER TABLE public.bookings ENABLE ROW LEVEL SECURITY;

-- A signed-in user sees only their own bookings. There is deliberately no
-- insert/update/delete policy for anon or authenticated: every booking write
-- goes through the service role (Companion/Dispatch backends, the
-- dispatch-booking edge function). Never add a policy keyed only on
-- auth.role() = 'authenticated' here: anyone can self-register an account.
-- TRUNCATE/REFERENCES/TRIGGER are not governed by RLS, so they are revoked.
REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLE public.bookings FROM anon, authenticated;

CREATE POLICY bookings_select_own
ON public.bookings
FOR SELECT TO authenticated
USING (user_id = auth.uid()::text);

-- Down migration
DROP TRIGGER IF EXISTS sync_bookings_updated_at ON public.bookings;
DROP TABLE IF EXISTS public.bookings;
DROP FUNCTION IF EXISTS public.fn_sync_updated_at();
