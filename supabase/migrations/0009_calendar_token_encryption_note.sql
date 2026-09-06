-- Calendar token encryption is application-level (CALENDAR_TOKEN_ENCRYPTION_KEY).
-- No schema change: access_token / refresh_token / caldav_password remain text.
-- Ciphertext is stored as enc:v1:<fernet> when the env key is set.
-- Plaintext rows are encrypted on next write or on FreeBusy load (best-effort backfill).
-- If the key is missing, FreeBusy fails soft (calendar unknown) and assign continues.

COMMENT ON COLUMN public.vendor_calendar_connections.access_token IS
  'OAuth access token; app-encrypted (enc:v1:) when CALENDAR_TOKEN_ENCRYPTION_KEY is set';
COMMENT ON COLUMN public.vendor_calendar_connections.refresh_token IS
  'OAuth refresh token; app-encrypted (enc:v1:) when CALENDAR_TOKEN_ENCRYPTION_KEY is set';
COMMENT ON COLUMN public.vendor_calendar_connections.caldav_password IS
  'CalDAV app password; app-encrypted (enc:v1:) when CALENDAR_TOKEN_ENCRYPTION_KEY is set';
