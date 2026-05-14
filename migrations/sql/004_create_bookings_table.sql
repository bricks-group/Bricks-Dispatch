-- MIGRATION: 004_create_bookings_table.sql

-- UP
CREATE TABLE IF NOT EXISTS bookings (
  id text primary key,
  booking_id text unique not null,
  user_id text not null,
  address text not null,
  property_type text default '',
  package_id text default '',
  package_name text default '',
  package_price int default 0,
  scheduled_date text default '',
  scheduled_time text default '',
  notes text default '',
  add_ons text[] default '{}',
  sqft int,
  is_rush bool default false,
  status text default 'pending',
  dispatch_job_id text,
  lat float8,
  lng float8,
  complexity_score int,
  complexity_band text,
  ai_flags text[] default '{}',
  completed_at text,
  delivered_at text,
  created_at text not null,
  updated_at text not null
);

-- DOWN
DROP TABLE IF EXISTS bookings;
