-- 0005_add_missing_contractor_columns.sql
-- Audit finding: ~7 columns on contractors table are not in migration files.
-- They were added via Supabase dashboard directly, creating schema drift.

-- Add missing columns to the 'contractors' table
-- auto_assign (boolean)
-- geo_regions (text[])
-- ical_url (text)
-- connect_status (text)
-- completed_shoots (integer)
-- avg_review (numeric)
-- tier_upgrade_invited (boolean)

BEGIN;

-- UP
ALTER TABLE public.contractors
ADD COLUMN IF NOT EXISTS auto_assign boolean DEFAULT FALSE;

ALTER TABLE public.contractors
ADD COLUMN IF NOT EXISTS geo_regions text[] DEFAULT ARRAY[]::text[];

ALTER TABLE public.contractors
ADD COLUMN IF NOT EXISTS ical_url text DEFAULT '';

ALTER TABLE public.contractors
ADD COLUMN IF NOT EXISTS connect_status text DEFAULT 'disconnected'; -- Assuming a default status

ALTER TABLE public.contractors
ADD COLUMN IF NOT EXISTS completed_shoots integer DEFAULT 0;

ALTER TABLE public.contractors
ADD COLUMN IF NOT EXISTS avg_review numeric DEFAULT 0.0;

ALTER TABLE public.contractors
ADD COLUMN IF NOT EXISTS tier_upgrade_invited boolean DEFAULT FALSE;

-- DOWN
ALTER TABLE public.contractors
DROP COLUMN IF EXISTS auto_assign;

ALTER TABLE public.contractors
DROP COLUMN IF EXISTS geo_regions;

ALTER TABLE public.contractors
DROP COLUMN IF EXISTS ical_url;

ALTER TABLE public.contractors
DROP COLUMN IF EXISTS connect_status;

ALTER TABLE public.contractors
DROP COLUMN IF EXISTS completed_shoots;

ALTER TABLE public.contractors
DROP COLUMN IF EXISTS avg_review;

ALTER TABLE public.contractors
DROP COLUMN IF EXISTS tier_upgrade_invited;

COMMIT;
