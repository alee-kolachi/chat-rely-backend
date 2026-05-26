-- Optional structured notification prefs (email digest toggles, etc.)
alter table public.profiles
  add column if not exists notification_preferences jsonb not null default '{}'::jsonb
    check (jsonb_typeof(notification_preferences) = 'object');

comment on column public.profiles.notification_preferences is
  'User-facing toggles e.g. {"daily_leads_report": true, "daily_conversations_report": false}';
