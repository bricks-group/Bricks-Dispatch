-- Multi-provider vendor calendars + booked-slot / duration fields for auto-assign.
-- One visit: selected_slot_* is the single booked clock; estimated_duration_min is on-site minutes.

CREATE TABLE IF NOT EXISTS public.vendor_calendar_connections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  member_id text NOT NULL,
  vendor_id text NOT NULL DEFAULT 'default_vendor',
  provider text NOT NULL CHECK (provider IN ('google', 'microsoft', 'apple', 'ics')),
  access_token text,
  refresh_token text,
  token_expires_at timestamptz,
  caldav_url text,
  caldav_username text,
  caldav_password text,
  ics_url text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (member_id, provider)
);

-- Service role only: holds calendar OAuth / CalDAV secrets. RLS on with no policies.
ALTER TABLE public.vendor_calendar_connections ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.vendor_calendar_connections FROM anon, authenticated;

CREATE INDEX IF NOT EXISTS vendor_calendar_connections_member_idx
  ON public.vendor_calendar_connections (member_id);

ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS selected_slot_start timestamptz;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS selected_slot_end timestamptz;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS estimated_duration_min integer;

COMMENT ON TABLE public.vendor_calendar_connections IS
  'OAuth/CalDAV/ICS calendar links that feed auto-assign FreeBusy. Honest Connected only when tokens/URL present.';
