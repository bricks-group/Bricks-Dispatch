-- Trade job checklist proof (Kam, 2026-10-31).
--
-- Additive only. Ticks and proof for trade jobs (cleaner, stager, de-staging,
-- handyman, inspector, measurer) are saved on the existing jobs.checklist
-- jsonb column; the photos and reports go in this private bucket.
-- No anon/authenticated policies, so only the service role (Dispatch API) can
-- read or write. Vendors see files through short signed URLs from the API.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('job-checklist-proof', 'job-checklist-proof', false, 10485760,
        array['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif', 'application/pdf'])
on conflict (id) do nothing;
