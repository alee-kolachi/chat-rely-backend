-- Plan catalog: public pricing visibility, sort order, unified knowledge cap feature key,
-- revised conversation limits and overage (paid only above 120% cushion).
-- Replaces refresh_usage_period_snapshot so overage_conversations = billable above cushion.

alter table public.plans
  add column if not exists public_on_pricing_page boolean not null default true;

alter table public.plans
  add column if not exists sort_order integer not null default 100;

comment on column public.plans.public_on_pricing_page is
  'When true, plan is returned by public pricing API (typically 4 of 5 tiers).';

comment on column public.plans.sort_order is
  'Ascending order for pricing page and plan pickers.';

comment on column public.usage_period_snapshots.overage_conversations is
  'Billable conversations above the 120%% cushion (units charged at overage_conversation_cents).';

comment on column public.usage_period_snapshots.billable_conversations is
  'Conversations with counts_toward_plan true in the period (see mark_conversation_billable).';

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
  cushion_limit integer;
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
  cushion_limit := greatest(ceil(target_plan.included_conversations::numeric * 1.2)::int, 0);
  overage_count := greatest(billable_count - cushion_limit, 0);
  overage_cents := overage_count * target_plan.overage_conversation_cents;

  if billable_count <= target_plan.included_conversations then
    tier := 'normal';
  elsif billable_count <= cushion_limit then
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
  is 'Recomputes usage for a billing period. Overage counts only billable conversations above ceil(included*1.2). Throttle: normal <= included, soft <= cushion, strong > cushion.';

insert into public.plans (
  slug,
  name,
  monthly_price_cents,
  included_conversations,
  overage_conversation_cents,
  max_agents,
  features,
  throttle_policy,
  is_active,
  public_on_pricing_page,
  sort_order
)
values
  (
    'free',
    'Free',
    0,
    50,
    0,
    1,
    jsonb_build_object(
      'shopify_enabled', false,
      'max_enabled_actions_per_agent', 2,
      'max_total_knowledge_mb', 5,
      'auto_retrain', false,
      'human_escalation_enabled', true,
      'pricing_card_bullets', jsonb_build_array(
        '50 billable conversations / month',
        '1 agent, 2 actions',
        '5 MB total knowledge storage',
        'Community support'
      )
    ),
    '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 3000, "strong_delay_ms": 10000}'::jsonb,
    true,
    true,
    10
  ),
  (
    'starter',
    'Starter',
    5900,
    500,
    12,
    1,
    jsonb_build_object(
      'shopify_enabled', true,
      'max_enabled_actions_per_agent', 5,
      'max_total_knowledge_mb', 10,
      'auto_retrain', false,
      'human_escalation_enabled', true,
      'pricing_card_bullets', jsonb_build_array(
        '500 billable conversations / month',
        'Shopify actions',
        '10 MB total knowledge storage',
        '2 team seats (invite teammates)'
      )
    ),
    '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 2500, "strong_delay_ms": 8000}'::jsonb,
    true,
    true,
    20
  ),
  (
    'growth',
    'Growth',
    14900,
    2000,
    9,
    3,
    jsonb_build_object(
      'shopify_enabled', true,
      'max_enabled_actions_per_agent', 10,
      'max_total_knowledge_mb', 50,
      'auto_retrain', true,
      'human_escalation_enabled', true,
      'pricing_card_bullets', jsonb_build_array(
        '2,000 billable conversations / month',
        '3 agents, 10 actions each',
        '50 MB total knowledge storage',
        'Auto-retrain on knowledge changes'
      )
    ),
    '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 2000, "strong_delay_ms": 6000}'::jsonb,
    true,
    true,
    30
  ),
  (
    'pro',
    'Pro',
    37900,
    6000,
    7,
    10,
    jsonb_build_object(
      'shopify_enabled', true,
      'max_enabled_actions_per_agent', 16,
      'max_total_knowledge_mb', 150,
      'auto_retrain', true,
      'human_escalation_enabled', true,
      'pricing_card_bullets', jsonb_build_array(
        '6,000 billable conversations / month',
        '10 agents, 16 actions each',
        '150 MB total knowledge storage',
        'Priority-friendly throttling policy'
      )
    ),
    '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 1500, "strong_delay_ms": 5000}'::jsonb,
    true,
    true,
    40
  ),
  (
    'scale',
    'Scale',
    89900,
    20000,
    6,
    25,
    jsonb_build_object(
      'shopify_enabled', true,
      'max_enabled_actions_per_agent', 24,
      'max_total_knowledge_mb', 500,
      'auto_retrain', true,
      'human_escalation_enabled', true,
      'pricing_card_bullets', jsonb_build_array(
        '20,000 billable conversations / month',
        '25 agents, 24 actions each',
        '500 MB total knowledge storage',
        'Best overage rate; contact sales for enterprise'
      )
    ),
    '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 1200, "strong_delay_ms": 4000}'::jsonb,
    true,
    false,
    50
  )
on conflict (slug)
do update
  set name = excluded.name,
      monthly_price_cents = excluded.monthly_price_cents,
      included_conversations = excluded.included_conversations,
      overage_conversation_cents = excluded.overage_conversation_cents,
      max_agents = excluded.max_agents,
      features = excluded.features,
      throttle_policy = excluded.throttle_policy,
      is_active = excluded.is_active,
      public_on_pricing_page = excluded.public_on_pricing_page,
      sort_order = excluded.sort_order,
      updated_at = now();
