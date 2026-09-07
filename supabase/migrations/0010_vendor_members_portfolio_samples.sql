-- 0010_vendor_members_portfolio_samples.sql
-- Portfolio samples keyed by Bricks catalog media type (HDR, drone, floor plan, tour, twilight, reel).

BEGIN;

ALTER TABLE public.vendor_members
ADD COLUMN IF NOT EXISTS portfolio_samples jsonb DEFAULT '[]'::jsonb;

COMMENT ON COLUMN public.vendor_members.portfolio_samples IS
  'Array of {media_type, url?, link?, thumb_url?} for hiring review by catalog type.';

COMMIT;
