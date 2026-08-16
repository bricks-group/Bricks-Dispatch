-- 0006_add_vendor_members_application_token.sql
-- Onboarding's GET /status/{member_id} and POST /documents relied on
-- member_id (a UUID) alone as an implicit bearer credential, with no
-- separate secret. UUIDv4 entropy makes brute force impractical, but a
-- leaked member_id (logs, screenshots, a forwarded support email) would
-- have let anyone view an applicant's compliance documents or upload new
-- ones to their file with zero additional verification. Adding a distinct,
-- application-specific token (returned once at /apply, required on both
-- endpoints thereafter) closes that off without requiring a real login
-- session pre-approval.

BEGIN;

-- UP
ALTER TABLE public.vendor_members
ADD COLUMN IF NOT EXISTS application_token text;

-- DOWN
ALTER TABLE public.vendor_members
DROP COLUMN IF EXISTS application_token;

COMMIT;
