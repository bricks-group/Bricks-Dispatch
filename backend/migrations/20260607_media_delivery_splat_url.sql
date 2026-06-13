-- Add splat_url column to media_delivery.
-- Populated at finalization time when a .splat upload exists for the job
-- (Premium / Luxury / Estate packages with 3D capture).
-- Null for jobs without a Gaussian splat file.
ALTER TABLE media_delivery
  ADD COLUMN IF NOT EXISTS splat_url TEXT;
