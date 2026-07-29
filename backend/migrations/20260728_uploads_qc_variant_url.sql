-- Add qc_variant_url to uploads: a downscaled (max 1600px, q82) JPEG generated
-- alongside the thumbnail at upload time (and again after in-house enhancement).
-- qc_agent.py fetches this instead of the full-res original/processed file for
-- vision review, cutting Supabase Storage egress per QC pass.
-- Null falls back to processed_url / file_url (see qc_agent.py:run_qc_for_job).
ALTER TABLE uploads
  ADD COLUMN IF NOT EXISTS qc_variant_url TEXT;
