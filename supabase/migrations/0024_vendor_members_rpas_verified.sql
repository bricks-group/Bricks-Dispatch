-- Drone dispatch reads a verified RPAS certificate, not the self-reported
-- rpas_certificate_level / drone_certified / rpas_advanced columns.
-- Written only by validate-rpas (Transport Canada registry) or an admin
-- document verify. Additive.
alter table public.vendor_members add column if not exists rpas_verified_at timestamptz;
alter table public.vendor_members add column if not exists rpas_verified_level text;

-- Backfill from certificates already verified, with the level that check read
-- (registry level first). Self-reported levels are not carried over.
update public.vendor_members m
set rpas_verified_at = coalesce(d.created_at::timestamptz, now()),
    rpas_verified_level = d.level
from (
  select distinct on (member_id)
    member_id,
    created_at,
    lower(coalesce(validation_result->'tc_registry'->>'level', validation_result->>'level')) as level
  from public.vendor_documents
  where doc_type = 'rpas_certificate' and status = 'verified'
  order by member_id, created_at desc
) d
where d.member_id::text = m.id::text
  and d.level in ('basic', 'advanced')
  and m.rpas_verified_at is null;

-- Platform owner (Advanced pilot, seeded in 0007).
update public.vendor_members
set rpas_verified_at = coalesce(rpas_verified_at, now()),
    rpas_verified_level = 'advanced'
where id::text = '0c3fa7c2-d23b-4e44-90ad-ab2d19af2580';
