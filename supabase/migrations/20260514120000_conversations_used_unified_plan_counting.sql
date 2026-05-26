-- Unify plan usage on "conversations" (no separate billable tier):
-- 1) Closed conversations with any visitor message, assistant output, or tool activity count toward the plan.
-- 2) Rename usage_period_snapshots.billable_conversations -> conversations_used.

create or replace function public.mark_conversation_counts_toward_plan(
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
     and c.status <> 'open'
     and (
       c.customer_message_count > 0
       or c.assistant_message_count > 0
       or coalesce(c.tool_call_count, 0) > 0
     );

  get diagnostics updated_rows = row_count;
  return updated_rows > 0;
end;
$$;

comment on function public.mark_conversation_counts_toward_plan(uuid)
  is 'When a conversation is terminal, marks counts_toward_plan if there was any customer message, assistant message, or tool call (single-reply and tool-heavy sessions count).';

drop function if exists public.mark_conversation_billable(uuid);

create or replace function public.close_idle_conversations(
  p_now timestamptz default now()
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  closed_count integer := 0;
  conv_id uuid;
begin
  for conv_id in
    with updated as (
      update public.conversations c
         set status = 'idle_closed',
             closed_at = p_now,
             updated_at = p_now
       where c.status = 'open'
         and c.last_activity_at <= p_now - interval '30 minutes'
      returning c.id
    )
    select id from updated
  loop
    closed_count := closed_count + 1;
    perform public.mark_conversation_counts_toward_plan(conv_id);
  end loop;

  return closed_count;
end;
$$;

comment on function public.close_idle_conversations(timestamptz)
  is 'Marks open conversations idle_closed after 30m inactivity, then evaluates mark_conversation_counts_toward_plan for each.';

-- Align historical rows with the new rule (terminal + any activity).
update public.conversations c
   set counts_toward_plan = true,
       updated_at = now()
 where c.counts_toward_plan = false
   and c.status <> 'open'
   and (
     c.customer_message_count > 0
     or c.assistant_message_count > 0
     or coalesce(c.tool_call_count, 0) > 0
   );

alter table public.usage_period_snapshots
  rename column billable_conversations to conversations_used;

comment on column public.usage_period_snapshots.conversations_used is
  'Conversations in the period with counts_toward_plan = true (closed sessions with any measured activity).';

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
    into used_count
  from public.conversations c
  where c.user_id = p_user_id
    and c.counts_toward_plan = true
    and c.started_at::date >= p_period_start
    and c.started_at::date <= p_period_end;

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
        last_computed_at = excluded.last_computed_at,
        updated_at = now()
  returning * into snapshot_row;

  return snapshot_row;
end;
$$;

comment on function public.refresh_usage_period_snapshot(uuid, date, date)
  is 'Recomputes usage for a billing period from conversations_used. Overage counts above included remain for analytics; estimated_overage_cents is always 0. Throttle tier strong => usage above included (runtime may downgrade model).';

-- Marketing copy stored in plan features (no "billable" wording).
update public.plans
set features = replace(features::text, 'billable conversations', 'conversations')::jsonb,
    updated_at = now()
where features::text ilike '%billable conversations%';
