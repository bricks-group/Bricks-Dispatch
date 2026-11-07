-- Fulfillment gate (launch review 2026-11-07). Additive only. NOT APPLIED.
-- Apply BEFORE deploying the fulfillment-gate code: without these columns
-- every media job reads as "not editor-approved" and delivery/payout hold.
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS editor_approved_at timestamptz;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS editor_approved_by text;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS fulfillment_gate jsonb;

COMMENT ON COLUMN public.jobs.editor_approved_at IS
  'Editor sign-off: complete purchased package creatively approved. Delivery + payout wait for it.';
COMMENT ON COLUMN public.jobs.fulfillment_gate IS
  'Last gate verdict {ready, required, present, missing, editor_approved, reasons}.';

-- uploads.provider is free text; new values: source_clip (raw vendor video,
-- never delivered) and editor_final (editor-approved finished film).
