-- Conversation usage: keep overage counts for visibility but do not accrue billable overage cents.
-- Product policy: no per-conversation overage charges for now (runtime switches to a cheaper model instead).

comment on column public.usage_period_snapshots.estimated_overage_cents is
  'Reserved for future metered billing; currently always 0 (no conversation overage charges).';

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
  overage_cents := 0;

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
  is 'Recomputes usage for a billing period. Overage conversation counts above included remain for analytics; estimated_overage_cents is always 0. Throttle tier strong => billable above included (runtime may downgrade model).';
