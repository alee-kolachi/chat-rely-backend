-- Phase 4 & 5: Reliability guardrails, action audit, usage and throttle helpers

do $$
begin
  if not exists (select 1 from pg_type where typname = 'action_decision') then
    create type public.action_decision as enum (
      'allowed',
      'blocked',
      'needs_confirmation'
    );
  end if;
end
$$;

create table if not exists public.agent_reliability_settings (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid not null unique references public.agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  min_retrieval_similarity double precision not null default 0.72
    check (min_retrieval_similarity >= 0 and min_retrieval_similarity <= 1),
  inactivity_timeout_minutes integer not null default 30
    check (inactivity_timeout_minutes between 5 and 240),
  max_unresolved_turns_before_escalation integer not null default 2
    check (max_unresolved_turns_before_escalation >= 1),
  fallback_message text not null default 'I am not fully sure based on available information. Let me clarify or escalate this.',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger set_agent_reliability_settings_updated_at
before update on public.agent_reliability_settings
for each row execute function public.set_updated_at();

create table if not exists public.action_execution_audits (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid not null references public.agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  conversation_id uuid references public.conversations(id) on delete set null,
  message_id uuid references public.messages(id) on delete set null,
  action_key text not null,
  input_payload jsonb not null default '{}'::jsonb check (jsonb_typeof(input_payload) = 'object'),
  decision public.action_decision not null,
  decision_reason text,
  threshold_snapshot jsonb not null default '{}'::jsonb check (jsonb_typeof(threshold_snapshot) = 'object'),
  result_payload jsonb not null default '{}'::jsonb check (jsonb_typeof(result_payload) = 'object'),
  error_class text,
  latency_ms integer check (latency_ms is null or latency_ms >= 0),
  created_at timestamptz not null default now()
);

create index if not exists action_execution_audits_agent_created_idx
  on public.action_execution_audits(agent_id, created_at desc);

create index if not exists action_execution_audits_user_created_idx
  on public.action_execution_audits(user_id, created_at desc);

create or replace function public.close_idle_conversations(
  p_now timestamptz default now()
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  closed_count integer;
begin
  with updated_rows as (
    update public.conversations c
       set status = 'idle_closed',
           closed_at = p_now,
           updated_at = p_now
     where c.status = 'open'
       and c.last_activity_at <= p_now - interval '30 minutes'
    returning 1
  )
  select count(*) into closed_count from updated_rows;

  return coalesce(closed_count, 0);
end;
$$;

comment on function public.close_idle_conversations(timestamptz)
  is 'Marks open conversations as idle_closed after 30 minutes of inactivity.';

create or replace function public.mark_conversation_billable(
  p_conversation_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  updated_rows integer;
begin
  update public.conversations c
     set counts_toward_plan = true,
         updated_at = now()
   where c.id = p_conversation_id
     and c.counts_toward_plan = false
     and c.customer_message_count > 0
     and c.assistant_message_count >= 2
     and c.status <> 'open';

  get diagnostics updated_rows = row_count;
  return updated_rows > 0;
end;
$$;

comment on function public.mark_conversation_billable(uuid)
  is 'Marks a closed conversation as billable if it meets minimum interaction thresholds.';

create or replace function public.refresh_usage_period_snapshot(
  p_user_id uuid,
  p_period_start date,
  p_period_end date
)
returns public.usage_period_snapshots
language plpgsql
security definer
set search_path = public
as $$
declare
  target_plan public.plans%rowtype;
  billable_count integer;
  projected_count integer;
  overage_count integer;
  overage_cents integer;
  tier public.throttle_tier;
  snapshot_row public.usage_period_snapshots;
begin
  select p.*
    into target_plan
  from public.subscriptions s
  join public.plans p on p.id = s.plan_id
  where s.user_id = p_user_id
    and s.status in ('trialing', 'active', 'past_due')
    and s.current_period_start::date <= p_period_start
    and s.current_period_end::date >= p_period_end
  order by s.current_period_end desc
  limit 1;

  if target_plan.id is null then
    raise exception 'No active plan found for user % in period % to %', p_user_id, p_period_start, p_period_end;
  end if;

  select count(*)
    into billable_count
  from public.conversations c
  where c.user_id = p_user_id
    and c.counts_toward_plan = true
    and c.started_at::date >= p_period_start
    and c.started_at::date <= p_period_end;

  projected_count := billable_count;
  overage_count := greatest(billable_count - target_plan.included_conversations, 0);
  overage_cents := overage_count * target_plan.overage_conversation_cents;

  if billable_count <= target_plan.included_conversations then
    tier := 'normal';
  elsif billable_count <= ceil(target_plan.included_conversations * 1.2) then
    tier := 'soft';
  else
    tier := 'strong';
  end if;

  insert into public.usage_period_snapshots (
    user_id,
    period_start,
    period_end,
    included_conversations,
    billable_conversations,
    overage_conversations,
    estimated_overage_cents,
    projected_conversations,
    throttle_tier,
    last_computed_at
  ) values (
    p_user_id,
    p_period_start,
    p_period_end,
    target_plan.included_conversations,
    billable_count,
    overage_count,
    overage_cents,
    projected_count,
    tier,
    now()
  )
  on conflict (user_id, period_start, period_end)
  do update
    set included_conversations = excluded.included_conversations,
        billable_conversations = excluded.billable_conversations,
        overage_conversations = excluded.overage_conversations,
        estimated_overage_cents = excluded.estimated_overage_cents,
        projected_conversations = excluded.projected_conversations,
        throttle_tier = excluded.throttle_tier,
        last_computed_at = excluded.last_computed_at,
        updated_at = now()
  returning * into snapshot_row;

  return snapshot_row;
end;
$$;

comment on function public.refresh_usage_period_snapshot(uuid, date, date)
  is 'Recomputes one user usage snapshot for a billing period and derives throttle tier.';
