-- Owner profile fields, RPAS upload URL, Kam top-tier Owner, Aman stays pending.
-- Vendor pay: ICs = vendor_payout_rates card (tier 1/2/3). Owner = full rack, no cut.

ALTER TABLE public.vendor_members ADD COLUMN IF NOT EXISTS rpas_advanced boolean DEFAULT false;
ALTER TABLE public.vendor_members ADD COLUMN IF NOT EXISTS rpas_certificate_url text;
ALTER TABLE public.vendor_members ADD COLUMN IF NOT EXISTS skills text[] DEFAULT '{}'::text[];
ALTER TABLE public.vendor_members ADD COLUMN IF NOT EXISTS avatar_url text;
ALTER TABLE public.vendor_members ADD COLUMN IF NOT EXISTS title text;
ALTER TABLE public.vendor_members ADD COLUMN IF NOT EXISTS payout_mode text;

UPDATE public.vendor_members SET rpas_advanced = true
WHERE lower(coalesce(rpas_certificate_level, '')) = 'advanced' AND rpas_advanced IS NOT TRUE;

-- Re-home any jobs that used Kam's member UUID as vendor_id so GO lists them on the org.
UPDATE public.jobs
SET vendor_id = 'default_vendor'
WHERE vendor_id = '0c3fa7c2-d23b-4e44-90ad-ab2d19af2580';

INSERT INTO public.vendor_members (
  id, vendor_id, name, email, phone, role, title, payout_mode, specialty, skills, status, is_active,
  onboarding_status, tier, drone_certified, rpas_advanced, rpas_certificate_level,
  polycam_equipped, service_areas, home_city, home_lat, home_lng, max_travel_km,
  created_at, updated_at
) VALUES (
  '0c3fa7c2-d23b-4e44-90ad-ab2d19af2580',
  'default_vendor',
  'Ajani Kamali Akio',
  'akashorter@icloud.com',
  '',
  'Owner',
  'Owner',
  'owner_full',
  'photo',
  ARRAY['photo','drone','reel','tour','twilight']::text[],
  'available',
  true,
  'approved',
  3,
  true,
  true,
  'advanced',
  true,
  ARRAY['Mississauga','GTA','Toronto','Peel','Halton','York','Durham']::text[],
  'Mississauga',
  43.5890,
  -79.6441,
  80,
  (now() at time zone 'utc')::text,
  (now() at time zone 'utc')::text
)
ON CONFLICT (id) DO UPDATE SET
  vendor_id = 'default_vendor',
  name = EXCLUDED.name,
  email = EXCLUDED.email,
  role = 'Owner',
  title = 'Owner',
  payout_mode = 'owner_full',
  specialty = 'photo',
  skills = EXCLUDED.skills,
  status = 'available',
  is_active = true,
  onboarding_status = 'approved',
  tier = 3,
  drone_certified = true,
  rpas_advanced = true,
  rpas_certificate_level = 'advanced',
  polycam_equipped = true,
  service_areas = EXCLUDED.service_areas,
  home_city = 'Mississauga',
  home_lat = 43.5890,
  home_lng = -79.6441,
  max_travel_km = 80,
  updated_at = EXCLUDED.updated_at;

-- Also match if Kam already exists under a different id with his email.
UPDATE public.vendor_members SET
  role = 'Owner',
  title = 'Owner',
  payout_mode = 'owner_full',
  tier = 3,
  is_active = true,
  onboarding_status = 'approved',
  status = 'available',
  specialty = 'photo',
  skills = ARRAY['photo','drone','reel','tour','twilight']::text[],
  drone_certified = true,
  rpas_advanced = true,
  rpas_certificate_level = 'advanced',
  polycam_equipped = true,
  service_areas = ARRAY['Mississauga','GTA','Toronto','Peel','Halton','York','Durham']::text[],
  home_city = 'Mississauga',
  home_lat = 43.5890,
  home_lng = -79.6441,
  max_travel_km = 80,
  vendor_id = 'default_vendor',
  updated_at = (now() at time zone 'utc')::text
WHERE lower(email) IN ('akashorter@icloud.com', 'akashorter@gmail.com');

-- Aman Sammy: never assign.
UPDATE public.vendor_members SET
  is_active = false,
  onboarding_status = 'pending',
  updated_at = (now() at time zone 'utc')::text
WHERE lower(name) LIKE '%aman%sammy%';
