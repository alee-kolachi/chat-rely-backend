-- In-app notifications for dashboard (persisted, deep-linked).

create table if not exists public.user_notifications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles (id) on delete cascade,
  kind text not null,
  title text not null,
  body text not null default '',
  href text not null,
  metadata jsonb not null default '{}'::jsonb,
  read_at timestamptz,
  dedupe_key text,
  created_at timestamptz not null default now(),
  constraint user_notifications_metadata_object check (jsonb_typeof(metadata) = 'object')
);

create index if not exists user_notifications_user_created_desc
  on public.user_notifications (user_id, created_at desc);

create index if not exists user_notifications_user_unread
  on public.user_notifications (user_id)
  where read_at is null;

create unique index if not exists user_notifications_user_dedupe_key
  on public.user_notifications (user_id, dedupe_key)
  where dedupe_key is not null;

alter table public.user_notifications enable row level security;

create policy user_notifications_select_own
  on public.user_notifications
  for select
  to authenticated
  using (user_id = auth.uid());

create policy user_notifications_update_own
  on public.user_notifications
  for update
  to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());
