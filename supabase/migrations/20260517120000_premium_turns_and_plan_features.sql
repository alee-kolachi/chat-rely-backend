-- Premium (advanced-resolution) assistant turns per billing period + plan feature keys.

alter table public.usage_period_snapshots
  add column if not exists included_premium_turns integer not null default 0
    check (included_premium_turns >= 0),
  add column if not exists premium_turns_used integer not null default 0
    check (premium_turns_used >= 0);

comment on column public.usage_period_snapshots.included_premium_turns is
  'Plan allowance for assistant replies on premium_chat_model (from plans.features).';
comment on column public.usage_period_snapshots.premium_turns_used is
  'Assistant messages in period whose model matches plans.features.premium_chat_model.';

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
  used_count integer;
  premium_included integer;
  premium_used integer;
  premium_model text;
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

  premium_included := coalesce(
    nullif(trim(target_plan.features->>'included_premium_turns'), '')::integer,
    0
  );
  premium_model := coalesce(
    nullif(trim(target_plan.features->>'premium_chat_model'), ''),
    'gpt-4o'
  );

  select count(*)
    into used_count
  from public.conversations c
  where c.user_id = p_user_id
    and c.counts_toward_plan = true
    and c.started_at::date >= p_period_start
    and c.started_at::date <= p_period_end;

  select count(*)
    into premium_used
  from public.messages m
  join public.conversations c on c.id = m.conversation_id
  where c.user_id = p_user_id
    and m.role = 'assistant'
    and m.model = premium_model
    and m.created_at::date >= p_period_start
    and m.created_at::date <= p_period_end;

  projected_count := used_count;
  overage_count := greatest(used_count - target_plan.included_conversations, 0);
  overage_cents := 0;

  if used_count <= target_plan.included_conversations then
    tier := 'normal';
  else
    tier := 'strong';
  end if;

  insert into public.usage_period_snapshots (
    user_id,
    period_start,
    period_end,
    included_conversations,
    conversations_used,
    overage_conversations,
    estimated_overage_cents,
    projected_conversations,
    throttle_tier,
    included_premium_turns,
    premium_turns_used,
    last_computed_at
  ) values (
    p_user_id,
    p_period_start,
    p_period_end,
    target_plan.included_conversations,
    used_count,
    overage_count,
    overage_cents,
    projected_count,
    tier,
    premium_included,
    premium_used,
    now()
  )
  on conflict (user_id, period_start, period_end)
  do update
    set included_conversations = excluded.included_conversations,
        conversations_used = excluded.conversations_used,
        overage_conversations = excluded.overage_conversations,
        estimated_overage_cents = excluded.estimated_overage_cents,
        projected_conversations = excluded.projected_conversations,
        throttle_tier = excluded.throttle_tier,
        included_premium_turns = excluded.included_premium_turns,
        premium_turns_used = excluded.premium_turns_used,
        last_computed_at = excluded.last_computed_at,
        updated_at = now()
  returning * into snapshot_row;

  return snapshot_row;
end;
$$;

comment on function public.refresh_usage_period_snapshot(uuid, date, date)
  is 'Recomputes conversation and premium-turn usage for a billing period. Strong throttle when conversations exceed included.';

-- Plan catalog: model routing features + hobby 250 conversations.
update public.plans
set
  included_conversations = case slug
    when 'hobby' then 250
    else included_conversations
  end,
  features = features
    || jsonb_build_object(
      'default_chat_model', 'gpt-4o-mini',
      'premium_chat_model', 'gpt-4o',
      'included_premium_turns', case slug
        when 'standard' then 250
        when 'pro' then 800
        when 'scale' then 800
        else 0
      end
    ),
  updated_at = now()
where slug in ('free', 'hobby', 'standard', 'pro', 'scale');

update public.plans
set features = jsonb_set(
  features,
  '{pricing_card_bullets}',
  case slug
    when 'free' then jsonb_build_array(
      '30 conversations / month',
      '1 agent',
      '500 KB training content',
      'Essential AI — always on'
    )
    when 'hobby' then jsonb_build_array(
      '250 conversations / month',
      '1 agent, 3 AI actions',
      '15 MB training content',
      'Shopify · always on'
    )
    when 'standard' then jsonb_build_array(
      '1,000 conversations / month',
      'Smart resolution for complex issues',
      '2 agents, 5 AI actions each',
      '40 MB training content',
      'Always on — may slow during heavy use'
    )
    when 'pro' then jsonb_build_array(
      '5,000 conversations / month',
      'Priority smart resolution (shared capacity)',
      '5 agents, 8 AI actions each',
      '100 MB training content',
      'Always on — may slow during heavy use'
    )
    else features->'pricing_card_bullets'
  end,
  true
),
updated_at = now()
where slug in ('free', 'hobby', 'standard', 'pro');
