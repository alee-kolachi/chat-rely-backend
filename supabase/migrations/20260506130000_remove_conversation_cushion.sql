-- Drop the 20% conversation cushion: overage and throttle_strong apply beyond included only.

comment on column public.usage_period_snapshots.overage_conversations is
  'Billable conversations above the plan included_conversations (units charged at overage_conversation_cents).';

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
  is 'Recomputes usage for a billing period. Overage = billable above included_conversations. Throttle: normal <= included, strong > included.';

-- Align plan metadata (no cushion ratios; delays kept for future policy use).
update public.plans
set throttle_policy = case slug
  when 'free' then '{"strong_delay_ms": 10000}'::jsonb
  when 'starter' then '{"strong_delay_ms": 8000}'::jsonb
  when 'growth' then '{"strong_delay_ms": 6000}'::jsonb
  when 'pro' then '{"strong_delay_ms": 5000}'::jsonb
  when 'scale' then '{"strong_delay_ms": 4000}'::jsonb
  else throttle_policy
end,
updated_at = now()
where slug in ('free', 'starter', 'growth', 'pro', 'scale');
