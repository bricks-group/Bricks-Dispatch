-- Store-readiness: in-app account deletion for Bricks GO and Bricks OS mobile
-- (App Store Guideline 5.1.1(v); Google Play User Data policy, account deletion).
-- Target: Canada project rbywjnnctjglhcdemnnj. Additive only; safe to re-run.

create table if not exists public.account_deletion_requests (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  email         text,
  app           text not null check (app in ('go', 'os', 'web')),
  platform      text,
  reason        text,
  status        text not null default 'requested'
                check (status in ('requested', 'in_progress', 'completed', 'rejected')),
  requested_at  timestamptz not null default now(),
  completed_at  timestamptz,
  handled_by    text,
  notes         text
);

-- One open request per user (a second tap is a no-op, client treats 23505 as success).
create unique index if not exists account_deletion_requests_one_open
  on public.account_deletion_requests (user_id)
  where status in ('requested', 'in_progress');

alter table public.account_deletion_requests enable row level security;

drop policy if exists adr_insert_own on public.account_deletion_requests;
create policy adr_insert_own on public.account_deletion_requests
  for insert to authenticated
  with check (user_id = auth.uid() and status = 'requested');

drop policy if exists adr_select_own on public.account_deletion_requests;
create policy adr_select_own on public.account_deletion_requests
  for select to authenticated
  using (user_id = auth.uid());

drop policy if exists adr_service_all on public.account_deletion_requests;
create policy adr_service_all on public.account_deletion_requests
  for all to service_role using (true) with check (true);

comment on table public.account_deletion_requests is
  'In-app account deletion requests from Bricks GO / OS mobile. Ops completes within 30 days; keep only legally required payout/tax records.';

-- Bricks OS mobile registers its Expo push token directly (the OS API has no push route yet).
-- Users may insert/update only rows tied to their own auth uid.
drop policy if exists push_tokens_owner_insert on public.push_tokens;
create policy push_tokens_owner_insert on public.push_tokens
  for insert to authenticated
  with check (user_id = auth.uid());

drop policy if exists push_tokens_owner_update on public.push_tokens;
create policy push_tokens_owner_update on public.push_tokens
  for update to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

grant select, insert on public.account_deletion_requests to authenticated;
grant select, insert, update on public.push_tokens to authenticated;
grant usage on sequence public.push_tokens_id_seq to authenticated;
