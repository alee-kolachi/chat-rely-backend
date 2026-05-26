-- Idempotent Stripe webhook processing (backend inserts after successful handling).

create table if not exists public.stripe_webhook_events (
  id uuid primary key default gen_random_uuid(),
  stripe_event_id text not null unique,
  event_type text not null,
  processed_at timestamptz not null default now()
);

create index if not exists stripe_webhook_events_processed_at_idx
  on public.stripe_webhook_events (processed_at desc);

comment on table public.stripe_webhook_events is
  'Deduplication for Stripe webhooks; insert after handling each event.';
