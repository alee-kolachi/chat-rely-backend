-- Phase 1: Core schema (no vector tables yet)
-- Scope: single-tenant user ownership + multi-agent + plans/subscriptions + chat core

create extension if not exists pgcrypto;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'subscription_status') then
    create type public.subscription_status as enum (
      'trialing',
      'active',
      'past_due',
      'canceled',
      'unpaid',
      'incomplete'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'agent_status') then
    create type public.agent_status as enum ('active', 'paused', 'archived');
  end if;

  if not exists (select 1 from pg_type where typname = 'integration_status') then
    create type public.integration_status as enum ('connected', 'disconnected', 'error');
  end if;

  if not exists (select 1 from pg_type where typname = 'conversation_channel') then
    create type public.conversation_channel as enum (
      'widget',
      'shopify',
      'whatsapp',
      'instagram',
      'email',
      'api',
      'unknown'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'conversation_status') then
    create type public.conversation_status as enum (
      'open',
      'idle_closed',
      'resolved',
      'escalated'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'message_role') then
    create type public.message_role as enum ('user', 'assistant', 'system', 'tool');
  end if;

  if not exists (select 1 from pg_type where typname = 'throttle_tier') then
    create type public.throttle_tier as enum ('normal', 'soft', 'strong');
  end if;
end
$$;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text,
  avatar_url text,
  timezone text not null default 'UTC',
  email_notifications_enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.plans (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  name text not null,
  monthly_price_cents integer not null default 0 check (monthly_price_cents >= 0),
  included_conversations integer not null default 0 check (included_conversations >= 0),
  overage_conversation_cents integer not null default 0 check (overage_conversation_cents >= 0),
  max_agents integer not null check (max_agents >= 1),
  features jsonb not null default '{}'::jsonb check (jsonb_typeof(features) = 'object'),
  throttle_policy jsonb not null default '{}'::jsonb check (jsonb_typeof(throttle_policy) = 'object'),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  plan_id uuid not null references public.plans(id),
  provider text not null default 'stripe',
  provider_customer_id text,
  provider_subscription_id text,
  status public.subscription_status not null default 'active',
  current_period_start timestamptz not null,
  current_period_end timestamptz not null,
  cancel_at_period_end boolean not null default false,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (current_period_end >= current_period_start)
);

create unique index if not exists subscriptions_provider_subscription_id_unique
  on public.subscriptions(provider_subscription_id)
  where provider_subscription_id is not null;

create index if not exists subscriptions_user_id_idx
  on public.subscriptions(user_id);

create index if not exists subscriptions_period_idx
  on public.subscriptions(current_period_start, current_period_end);

create table if not exists public.agents (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  slug text not null,
  public_key text not null unique default encode(extensions.gen_random_bytes(16), 'hex'),
  system_prompt text not null default '',
  model text not null default 'gpt-4o-mini',
  behavior_settings jsonb not null default '{}'::jsonb check (jsonb_typeof(behavior_settings) = 'object'),
  status public.agent_status not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  unique (user_id, slug)
);

create index if not exists agents_user_id_idx
  on public.agents(user_id);

create index if not exists agents_user_id_status_idx
  on public.agents(user_id, status);

create table if not exists public.shopify_connections (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid not null references public.agents(id) on delete cascade,
  shop_domain text not null,
  access_token_encrypted text not null,
  refresh_token_encrypted text,
  token_expires_at timestamptz,
  scopes text[] not null default '{}',
  status public.integration_status not null default 'connected',
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  installed_at timestamptz not null default now(),
  last_synced_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (agent_id),
  check (shop_domain <> '')
);

create index if not exists shopify_connections_status_idx
  on public.shopify_connections(status);

create table if not exists public.agent_actions (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid not null references public.agents(id) on delete cascade,
  action_key text not null,
  enabled boolean not null default false,
  config jsonb not null default '{}'::jsonb check (jsonb_typeof(config) = 'object'),
  safety_policy jsonb not null default '{}'::jsonb check (jsonb_typeof(safety_policy) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (agent_id, action_key)
);

create index if not exists agent_actions_agent_id_idx
  on public.agent_actions(agent_id);

create table if not exists public.conversations (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid not null references public.agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  visitor_id text not null,
  channel public.conversation_channel not null default 'widget',
  status public.conversation_status not null default 'open',
  started_at timestamptz not null default now(),
  last_activity_at timestamptz not null default now(),
  closed_at timestamptz,
  customer_message_count integer not null default 0 check (customer_message_count >= 0),
  assistant_message_count integer not null default 0 check (assistant_message_count >= 0),
  tool_call_count integer not null default 0 check (tool_call_count >= 0),
  total_input_tokens integer not null default 0 check (total_input_tokens >= 0),
  total_output_tokens integer not null default 0 check (total_output_tokens >= 0),
  counts_toward_plan boolean not null default false,
  billed_at timestamptz,
  overage_applied boolean not null default false,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (closed_at is null or closed_at >= started_at)
);

create unique index if not exists conversations_open_session_per_visitor_idx
  on public.conversations(agent_id, visitor_id)
  where status = 'open';

create index if not exists conversations_user_id_started_at_idx
  on public.conversations(user_id, started_at desc);

create index if not exists conversations_agent_id_last_activity_idx
  on public.conversations(agent_id, last_activity_at desc);

create table if not exists public.messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  agent_id uuid not null references public.agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role public.message_role not null,
  content text not null default '',
  tool_name text,
  tool_call_id text,
  tool_call_payload jsonb not null default '{}'::jsonb check (jsonb_typeof(tool_call_payload) = 'object'),
  tool_result_payload jsonb not null default '{}'::jsonb check (jsonb_typeof(tool_result_payload) = 'object'),
  model text,
  input_tokens integer not null default 0 check (input_tokens >= 0),
  output_tokens integer not null default 0 check (output_tokens >= 0),
  latency_ms integer check (latency_ms is null or latency_ms >= 0),
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now()
);

create index if not exists messages_conversation_id_created_at_idx
  on public.messages(conversation_id, created_at);

create index if not exists messages_agent_id_created_at_idx
  on public.messages(agent_id, created_at);

create table if not exists public.usage_period_snapshots (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  period_start date not null,
  period_end date not null,
  included_conversations integer not null default 0 check (included_conversations >= 0),
  billable_conversations integer not null default 0 check (billable_conversations >= 0),
  overage_conversations integer not null default 0 check (overage_conversations >= 0),
  estimated_overage_cents integer not null default 0 check (estimated_overage_cents >= 0),
  projected_conversations integer not null default 0 check (projected_conversations >= 0),
  throttle_tier public.throttle_tier not null default 'normal',
  last_computed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, period_start, period_end),
  check (period_end >= period_start)
);

create index if not exists usage_period_snapshots_user_id_idx
  on public.usage_period_snapshots(user_id);

create index if not exists usage_period_snapshots_period_idx
  on public.usage_period_snapshots(period_start, period_end);

create trigger set_profiles_updated_at
before update on public.profiles
for each row execute function public.set_updated_at();

create trigger set_plans_updated_at
before update on public.plans
for each row execute function public.set_updated_at();

create trigger set_subscriptions_updated_at
before update on public.subscriptions
for each row execute function public.set_updated_at();

create trigger set_agents_updated_at
before update on public.agents
for each row execute function public.set_updated_at();

create trigger set_shopify_connections_updated_at
before update on public.shopify_connections
for each row execute function public.set_updated_at();

create trigger set_agent_actions_updated_at
before update on public.agent_actions
for each row execute function public.set_updated_at();

create trigger set_conversations_updated_at
before update on public.conversations
for each row execute function public.set_updated_at();

create trigger set_usage_period_snapshots_updated_at
before update on public.usage_period_snapshots
for each row execute function public.set_updated_at();
